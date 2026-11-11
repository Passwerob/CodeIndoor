#!/usr/bin/env python3
"""Probe the REAL DeepSeek account before implementing the compatibility gateway.

The gateway must be written against observed behaviour, not documentation guesses.
Checks: /models, plain chat, JSON output, tool calls, thinking/reasoning_content,
long output, image input. No API key is ever printed, logged or written to output.

Usage:
    python E:/3DUse/SceneCode_audit/bridge/probe_deepseek.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.deepseek.com"
ENV_FILE = Path(r"E:/3DUse/SceneCode_audit/runtime/.env")
OUT = Path(r"E:/3DUse/SceneCode_audit/DEEPSEEK_PROBE_RESULT.json")
MODEL = "deepseek-flash"

# 1x1 PNG, only used to verify that the image content block is accepted.
TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)


def load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key and key.strip():
        return key.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DEEPSEEK_API_KEY is unavailable; put it in runtime/.env (gitignored).")


KEY = load_key()


def call(path: str, payload: dict | None = None, timeout: int = 90):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method="GET" if payload is None else "POST",
        headers={
            "Authorization": "Bearer " + KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = raw[:400]
            return resp.status, body, round(time.time() - t0, 2), None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return exc.code, raw[:400], round(time.time() - t0, 2), "http_error"
    except Exception as exc:  # noqa: BLE001
        return -1, type(exc).__name__ + ": " + str(exc)[:160], round(time.time() - t0, 2), "transport_error"


def clean(value, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text[:limit] + ("..." if len(text) > limit else "")


def summarize(name: str, status, body, elapsed, err, extractor=None) -> dict:
    entry = {"name": name, "status": status, "seconds": elapsed, "error": err}
    if isinstance(body, dict):
        entry["keys"] = sorted(body.keys())
        if "data" in body and isinstance(body["data"], list):
            entry["models"] = [m.get("id") for m in body["data"] if isinstance(m, dict)]
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message", {}) if isinstance(choice, dict) else {}
        if msg:
            entry["message_keys"] = sorted(msg.keys())
            entry["has_reasoning_content"] = bool(msg.get("reasoning_content"))
            entry["has_tool_calls"] = bool(msg.get("tool_calls"))
            entry["content_preview"] = clean(msg.get("content") or "", 120)
            if msg.get("tool_calls"):
                entry["tool_call_names"] = [
                    (tc.get("function") or {}).get("name") for tc in msg["tool_calls"]
                ]
        usage = body.get("usage")
        if isinstance(usage, dict):
            entry["usage"] = {
                k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
    else:
        entry["body_preview"] = clean(body, 200)
    if extractor:
        entry.update(extractor(body))
    print(f"[{name}] http={status} {elapsed}s err={err or '-'}")
    for k in ("models", "keys", "message_keys", "has_reasoning_content",
              "has_tool_calls", "tool_call_names", "usage", "content_preview", "body_preview"):
        if k in entry:
            print(f"    {k}: {clean(entry[k], 220)}")
    return entry


def main() -> int:
    results = []

    results.append(summarize("models /models", *call("/models")))
    results.append(summarize("models /v1/models", *call("/v1/models")))

    base = {"model": MODEL, "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
            "max_tokens": 16, "thinking": {"type": "disabled"}}
    results.append(summarize("plain chat", *call("/chat/completions", base)))

    json_payload = dict(base)
    json_payload["messages"] = [
        {"role": "user", "content": 'Return a JSON object with exactly one key "probe" whose value is "ok".'}
    ]
    json_payload["response_format"] = {"type": "json_object"}
    json_payload["max_tokens"] = 64

    def check_json(body):
        out = {"json_parsed": False}
        if isinstance(body, dict):
            content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            if content:
                try:
                    parsed = json.loads(content)
                    out["json_parsed"] = isinstance(parsed, dict)
                    out["json_value"] = clean(parsed, 80)
                except (json.JSONDecodeError, TypeError):
                    pass
        return out

    results.append(summarize("json output", *call("/chat/completions", json_payload),
                             extractor=check_json))

    tools_payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "What is the weather in Shenzhen? Use the tool."}],
        "max_tokens": 128,
        "thinking": {"type": "disabled"},
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                               "required": ["city"]},
            },
        }],
        "tool_choice": "auto",
    }
    results.append(summarize("tool calls", *call("/chat/completions", tools_payload)))

    think_payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Is 1001 prime? Answer briefly."}],
        "max_tokens": 128,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
    }
    results.append(summarize("thinking mode", *call("/chat/completions", think_payload)))

    long_payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Write a 300-word description of a modern living room."}],
        "max_tokens": 700,
        "thinking": {"type": "disabled"},
    }
    results.append(summarize("long output", *call("/chat/completions", long_payload)))

    image_payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + TINY_PNG}},
                {"type": "text", "text": "What color is this image? Answer in one word."},
            ],
        }],
        "max_tokens": 32,
        "thinking": {"type": "disabled"},
    }
    results.append(summarize("image input", *call("/chat/completions", image_payload)))

    OUT.write_text(json.dumps({
        "base_url": BASE,
        "model_requested": MODEL,
        "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nWrote desensitized probe result ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
