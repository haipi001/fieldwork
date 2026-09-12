#!/usr/bin/env python3
"""Configure an OpenAI-compatible Strix relay without leaking its API key."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_BASE = "https://api.openai-next.com/v1"


def normalized_base(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("中转站地址必须是无内嵌凭据的 HTTPS URL")
    return value if value.endswith("/v1") else f"{value}/v1"


def fetch_models(base: str, key: str) -> list[str]:
    request = urllib.request.Request(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15, context=ssl.create_default_context()) as response:
        payload = json.load(response)
    models = payload.get("data", []) if isinstance(payload, dict) else []
    return sorted({str(item.get("id", "")).strip() for item in models if isinstance(item, dict) and item.get("id")})


def choose_model(models: list[str]) -> str:
    if models:
        print("\n中转站可用模型：")
        for index, model in enumerate(models, 1):
            print(f"  {index:>2}. {model}")
    while True:
        selected = input("\n输入用于 Strix 的模型 ID: ").strip()
        if selected and (not models or selected in models):
            return selected
        print("请输入列表中完整、精确的模型 ID。")


def write_config(base: str, key: str, model: str) -> Path:
    destination = Path.home() / ".strix" / "cli-config.json"
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "env": {
            "STRIX_LLM": f"openai/{model}",
            "LLM_API_BASE": base,
            "LLM_API_KEY": key,
            "STRIX_REASONING_EFFORT": "medium",
            "LLM_TIMEOUT": "600",
            "STRIX_TELEMETRY": "false",
        }
    }
    descriptor, temporary_name = tempfile.mkstemp(prefix="cli-config-", suffix=".json", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="安全配置 Strix OpenAI-compatible 中转站")
    parser.add_argument("--base", default=DEFAULT_BASE, help="中转站 API base，默认已配置为当前项目地址")
    args = parser.parse_args()
    try:
        base = normalized_base(args.base)
    except ValueError as error:
        parser.error(str(error))
    key = getpass.getpass("输入新的中转站 API Key（不会显示）: ").strip()
    if not key:
        parser.error("API Key 不能为空")
    try:
        models = fetch_models(base, key)
    except urllib.error.HTTPError as error:
        print(f"认证或模型列表请求失败：HTTP {error.code}")
        return 1
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"中转站连接失败：{type(error).__name__}")
        return 1
    if not models:
        print("中转站未返回任何模型，未写入配置。")
        return 1
    model = choose_model(models)
    destination = write_config(base, key, model)
    key = ""
    print(f"\n配置完成：{destination}（权限 600）")
    print(f"Strix 模型：openai/{model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
