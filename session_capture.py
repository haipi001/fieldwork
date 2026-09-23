"""Ephemeral, visible Chrome session capture for authorized test identities.

The browser context is non-persistent. Cookie material travels from the worker
to the parent through an anonymous pipe and is immediately written to macOS
Keychain; it is never written to SQLite, artifacts, events, or report files.
"""

from __future__ import annotations

import json
import queue
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


def start(identity_id: str, login_url: str, allowed_hosts: list[str], max_requests: int, *, run_id=None, target_url=None) -> dict[str, Any]:
    if not CHROME.is_file():
        raise RuntimeError("system_chrome_unavailable")
    with _LOCK:
        if any(item["identity_id"] == identity_id and item["process"].poll() is None for item in _CAPTURES.values()):
            raise RuntimeError("identity_capture_already_running")
        capture_id = f"capture-{uuid.uuid4().hex[:12]}"
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", login_url, json.dumps(allowed_hosts), str(max_requests), json.dumps({"run_id":run_id,"target_url":target_url or login_url})],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        _CAPTURES[capture_id] = {
            "id": capture_id, "identity_id": identity_id, "login_url": login_url,
            "allowed_hosts": allowed_hosts, "max_requests": max_requests, "process": process, "run_id":run_id,
        }
    return {"id": capture_id, "identity_id": identity_id, "status": "browser_open", "login_url": login_url}


def status(capture_id: str) -> dict[str, Any]:
    with _LOCK:
        item = _CAPTURES.get(capture_id)
    if not item:
        raise KeyError(capture_id)
    code = item["process"].poll()
    return {"id": capture_id, "identity_id": item["identity_id"], "status": "browser_open" if code is None else "ended", "run_id":item.get("run_id")}


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
        "responses": result.get("responses",[])[:20],
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


def _keychain_command(operation, identity_id, headers=None):
    payload = {"operation": operation, "account": identity_id}
    if headers is not None:
        payload['headers'] = headers
    helper = Path(__file__).resolve().parent / "build/macos/Fieldwork.app/Contents/MacOS/FieldworkKeychain"
    if not helper.is_file():
        helper = Path("/Applications/Fieldwork.app/Contents/MacOS/FieldworkKeychain")
    if not helper.is_file():
        raise RuntimeError("keychain_helper_unavailable")
    try:
        return subprocess.run([str(helper)], input=json.dumps(payload, ensure_ascii=False),
                              capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("keychain_helper_unavailable") from error


def store_keychain(identity_id: str, headers: dict[str, str]) -> str:
    result = _keychain_command('store', identity_id, headers)
    if result.returncode != 0:
        raise RuntimeError("keychain_store_failed")
    return f"keychain://fieldwork-session/{identity_id}"


def read_keychain(identity_id: str) -> str:
    result = _keychain_command('read', identity_id)
    if result.returncode != 0:
        raise RuntimeError("keychain_session_unavailable")
    return result.stdout


def delete_keychain(identity_id: str) -> bool:
    """Remove only the Keychain item owned by Fieldwork for this identity."""
    result = _keychain_command('delete', identity_id)
    if result.returncode not in {0, 44}:
        raise RuntimeError("keychain_delete_failed")
    return result.returncode == 0


def _worker(login_url: str, allowed_hosts_json: str, max_requests_text: str, options_json="{}") -> int:
    from playwright.sync_api import sync_playwright

    allowed_hosts = {str(item).lower().rstrip(".") for item in json.loads(allowed_hosts_json)}
    max_requests = int(max_requests_text)
    counts = {"seen": 0, "blocked": 0}
    options=json.loads(options_json)
    responses=[]
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
        def record_response(response):
            if not options.get('run_id') or len(responses)>=20:return
            try:
                headers=response.headers
                length=int(headers.get('content-length','0'))
                if not 0<length<=65536 or 'application/json' not in headers.get('content-type',''):return
                from guided_capture import project_response
                value=project_response(response.url,options.get('target_url') or login_url,response.request.method,response.status,response.body())
                if value and not any(x['url']==value['url'] and x['sha256']==value['sha256'] for x in responses):responses.append(value)
            except Exception:
                pass
        pending_responses=[]
        def queue_response(response):
            if not options.get('run_id') or len(pending_responses)>=100:return
            try:
                headers=response.headers
                if response.request.method=='GET' and response.status==200 and 'application/json' in headers.get('content-type','') and 0<int(headers.get('content-length','0'))<=65536:
                    pending_responses.append(response)
            except Exception:
                pass
        context.on('response',queue_response)
        page = context.new_page()
        print("READY", flush=True)
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass
        # Keep Playwright's event loop running while the user interacts with Chrome.
        commands = queue.Queue()
        threading.Thread(target=lambda: commands.put(sys.stdin.readline().strip()), daemon=True).start()
        while commands.empty() and browser.is_connected():
            page.wait_for_timeout(100)
        command = commands.get_nowait() if not commands.empty() else ""
        if command != "FINISH":
            browser.close()
            return 2
        # Finish pending protocol events before reading bodies. Reading a body
        # inside the response-header callback can race the last login request.
        try:
            page.wait_for_load_state('networkidle',timeout=3000)
        except Exception:
            pass
        for response in list(pending_responses):
            record_response(response)
        cookies = context.cookies([options.get('target_url') or login_url])
        print("FIELDWORK_SESSION=" + json.dumps({
            "cookies": cookies, "responses":responses, "requests_seen": counts["seen"], "requests_blocked": counts["blocked"],
        }, ensure_ascii=False, separators=(",", ":")), flush=True)
        browser.close()
    return 0


if __name__ == "__main__" and len(sys.argv) in (5,6) and sys.argv[1] == "--worker":
    raise SystemExit(_worker(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv)==6 else "{}"))
