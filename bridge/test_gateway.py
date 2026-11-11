#!/usr/bin/env python3
"""Smoke tests for the local OpenAI-compatible gateway.

Verifies the exact request shapes SceneCode emits, without contacting any
external image-generation service. Run while gateway.py is listening.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765/v1"


def request(path: str, payload: dict | None = None, timeout: int = 120):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 method="GET" if payload is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def request_raw(path: str, payload: dict, timeout: int = 120):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8", "replace")


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f" :: {detail}" if detail else ""))
    return condition


def main() -> int:
    ok = []

    for attempt in range(20):
        try:
            request("/models", timeout=3)
            break
        except Exception:
            time.sleep(0.4)

    status, body = request("/models")
    ids = [m.get("id") for m in body.get("data", [])]
    ok.append(check("GET /v1/models", status == 200 and "deepseek-flash" in ids,
                    f"http={status} models={ids}"))

    status, body = request("/chat/completions", {
        "model": "gpt-5.4",  # SceneCode room-level agent
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 16, "thinking": {"type": "disabled"},
        "reasoning_effort": "high", "verbosity": "low", "service_tier": "default",
    })
    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
    ok.append(check("chat with gpt-5.4 + OpenAI-only params",
                    status == 200 and bool(content),
                    f"http={status} model={body.get('model')} content={content!r}"))

    status, body = request("/chat/completions", {
        "model": "gemini-3.1-pro-preview",  # SceneCode code_object agent
        "messages": [{"role": "user", "content": "Use the tool."}],
        "max_tokens": 32, "thinking": {"type": "enabled"}, "tool_choice": "required",
        "tools": [{"type": "function", "function": {
            "name": "ping", "description": "ping",
            "parameters": {"type": "object", "properties": {}}}}],
    })
    ok.append(check("mandatory rewrite: tool_choice required + thinking",
                    status == 200, f"http={status} body_keys={sorted(body.keys())}"))

    status, body = request("/responses", {
        "model": "gpt-5.4", "input": "Say OK", "max_output_tokens": 32,
    })
    ok.append(check("POST /v1/responses (openai-agents path)",
                    status == 200 and body.get("object") == "response",
                    f"http={status} object={body.get('object')}"))

    status, body = request("/images/generations", {
        "model": "gpt-image-1.5", "prompt": "a detailed two-seat sofa",
        "size": "256x256", "n": 1, "output_format": "png",
    })
    b64 = ((body.get("data") or [{}])[0].get("b64_json")) if isinstance(body, dict) else None
    png_ok = False
    if b64:
        raw = base64.b64decode(b64)
        png_ok = raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) > 100
    ok.append(check("POST /v1/images/generations -> local PNG (b64_json)",
                    status == 200 and png_ok,
                    f"http={status} png_bytes={len(base64.b64decode(b64)) if b64 else 0}"))

    status, body = request("/images/edits", {"prompt": "edit", "size": "128x128"})
    ok.append(check("POST /v1/images/edits -> local PNG",
                    status == 200 and bool((body.get("data") or [{}])[0].get("b64_json")),
                    f"http={status}"))

    status, body = request("/audio/transcriptions", {"model": "whisper-1"})
    ok.append(check("unsupported-path guard returns 404",
                    status == 404 and "error" in body, f"http={status}"))

    status, ctype, raw = request_raw("/chat/completions", {
        "model": "gpt-5.4", "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 16, "stream": True, "thinking": {"type": "disabled"},
    })
    ok.append(check("stream:true SSE passthrough",
                    status == 200 and "data:" in raw,
                    f"http={status} ctype={ctype[:30]} chunks={raw.count('data:')}"))

    status, body = request("/responses", {
        "model": "gpt-5.4", "input": "Use the ping tool.", "max_output_tokens": 64,
        "tool_choice": {"type": "required"},
        "tools": [{"type": "function", "name": "ping", "description": "ping",
                   "parameters": {"type": "object", "properties": {}}}],
    })
    ok.append(check("responses tool_choice object form (with tools)",
                    status == 200 and body.get("object") == "response",
                    f"http={status} body={json.dumps(body, ensure_ascii=False)[:160]}"))

    print(f"\n{sum(ok)}/{len(ok)} passed")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
