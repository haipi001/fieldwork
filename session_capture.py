"""Ephemeral, visible Chrome session capture for authorized test identities.

The browser context is non-persistent. Cookie material travels from the worker
to the parent through an anonymous pipe and is immediately written to macOS
Keychain; it is never written to SQLite, artifacts, events, or report files.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
_LOCK = threading.Lock()
_CAPTURES: dict[str, dict[str, Any]] = {}


def start(identity_id: str, login_url: str, allowed_hosts: list[str], max_requests: int) -> dict[str, Any]:
    if not CHROME.is_file():
        raise RuntimeError("system_chrome_unavailable")
    with _LOCK:
        if any(item["identity_id"] == identity_id and item["process"].poll() is None for item in _CAPTURES.values()):
            raise RuntimeError("identity_capture_already_running")
        capture_id = f"capture-{uuid.uuid4().hex[:12]}"
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", login_url, json.dumps(allowed_hosts), str(max_requests)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        _CAPTURES[capture_id] = {
            "id": capture_id, "identity_id": identity_id, "login_url": login_url,
            "allowed_hosts": allowed_hosts, "max_requests": max_requests, "process": process,
        }
    return {"id": capture_id, "identity_id": identity_id, "status": "browser_open", "login_url": login_url}


def status(capture_id: str) -> dict[str, Any]:
    with _LOCK:
        item = _CAPTURES.get(capture_id)
    if not item:
        raise KeyError(capture_id)
    code = item["process"].poll()
    return {"id": capture_id, "identity_id": item["identity_id"], "status": "browser_open" if code is None else "ended"}


def complete(capture_id: str) -> dict[str, Any]:
    with _LOCK:
        item = _CAPTURES.pop(capture_id, None)
    if not item:
        raise KeyError(capture_id)
    process = item["process"]
    if process.poll() is not None:
        raise RuntimeError("capture_browser_ended")
    try:
        stdout, _ = process.communicate("FINISH\n", timeout=20)
    except subprocess.TimeoutExpired as error:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
        raise RuntimeError("capture_completion_timeout") from error
    line = next((value for value in reversed(stdout.splitlines()) if value.startswith("FIELDWORK_SESSION=")), "")
    if not line:
        raise RuntimeError("capture_result_missing")
    result = json.loads(line.removeprefix("FIELDWORK_SESSION="))
    cookies = result.pop("cookies", [])
    if not cookies:
        raise RuntimeError("capture_has_no_cookies")
    cookie_header = "; ".join(f"{item['name']}={item['value']}" for item in cookies if item.get("name") and item.get("value"))
    if not cookie_header:
        raise RuntimeError("capture_has_no_usable_cookies")
    return {
        "headers": {"Cookie": cookie_header},
        "cookie_count": len(cookies),
        "domain_count": len({item.get("domain") for item in cookies if item.get("domain")}),
        "requests_seen": int(result.get("requests_seen", 0)),
        "requests_blocked": int(result.get("requests_blocked", 0)),
    }


def cancel(capture_id: str) -> bool:
    with _LOCK:
        item = _CAPTURES.pop(capture_id, None)
    if not item:
        return False
    process = item["process"]
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    return True


def store_keychain(identity_id: str, headers: dict[str, str]) -> str:
    payload = json.dumps({"headers": headers}, ensure_ascii=False, separators=(",", ":"))
    # `-w` as the final option prompts on stdin, so cookie material never
    # appears in argv/process listings.
    result = subprocess.run(
        ["/usr/bin/security", "add-generic-password", "-U", "-s", "fieldwork-session", "-a", identity_id, "-w"],
        input=payload + "\n", capture_output=True, text=True, timeout=8,
    )
    if result.returncode != 0:
        raise RuntimeError("keychain_store_failed")
    return f"keychain://fieldwork-session/{identity_id}"


def delete_keychain(identity_id: str) -> bool:
    """Remove only the Keychain item owned by Fieldwork for this identity."""
    result = subprocess.run(
        ["/usr/bin/security", "delete-generic-password", "-s", "fieldwork-session", "-a", identity_id],
        capture_output=True, text=True, timeout=8,
    )
    # security(1) returns 44 when the item is already absent.
    if result.returncode not in {0, 44}:
        raise RuntimeError("keychain_delete_failed")
    return result.returncode == 0


def _worker(login_url: str, allowed_hosts_json: str, max_requests_text: str) -> int:
    from playwright.sync_api import sync_playwright

    allowed_hosts = {str(item).lower().rstrip(".") for item in json.loads(allowed_hosts_json)}
    max_requests = int(max_requests_text)
    counts = {"seen": 0, "blocked": 0}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=False)
        context = browser.new_context(ignore_https_errors=False)

        def guard(route):
            counts["seen"] += 1
            host = (urlparse(route.request.url).hostname or "").lower().rstrip(".")
            if counts["seen"] > max_requests or host not in allowed_hosts:
                counts["blocked"] += 1
                route.abort("blockedbyclient")
            else:
                route.continue_()

        context.route("**/*", guard)
        page = context.new_page()
        print("READY", flush=True)
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass
        command = sys.stdin.readline().strip()
        if command != "FINISH":
            browser.close()
            return 2
        cookies = context.cookies()
        print("FIELDWORK_SESSION=" + json.dumps({
            "cookies": cookies, "requests_seen": counts["seen"], "requests_blocked": counts["blocked"],
        }, ensure_ascii=False, separators=(",", ":")), flush=True)
        browser.close()
    return 0


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "--worker":
    raise SystemExit(_worker(sys.argv[2], sys.argv[3], sys.argv[4]))
