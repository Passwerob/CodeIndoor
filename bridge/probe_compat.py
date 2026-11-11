#!/usr/bin/env python3
"""Probe protocol-compatibility edges that decide gateway translation rules.

Every rule in gateway.py must be justified by an observed response here.
No API key is printed or written.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import probe_deepseek as p

OUT = Path(r"E:/3DUse/SceneCode_audit/DEEPSEEK_COMPAT_RESULT.json")


def probe(name, path, payload, note=""):
    status, body, elapsed, err = -1, "", 0, "not_run"
    for attempt in range(5):
        status, body, elapsed, err = p.call(path, payload)
        if err != "transport_error" and status > 0:
            break
        # This host's proxy intermittently returns "Tunnel connection failed: 502".
        # Retry with backoff; never record a transport failure as an API verdict.
        time.sleep(2 + attempt)
    preview = p.clean(body if isinstance(body, str) else body, 260)
    if isinstance(body, dict):
        err_field = body.get("error")
        if err_field:
            preview = p.clean(err_field, 260)
        else:
            choice = (body.get("choices") or [{}])[0]
            msg = choice.get("message", {}) if isinstance(choice, dict) else {}
            preview = "msg_keys=" + ",".join(sorted(msg.keys())) + " content=" + p.clean(msg.get("content") or "", 90)
            if body.get("object") == "response" or "output" in body:
                preview = "response_keys=" + ",".join(sorted(body.keys())) + " " + p.clean(body, 150)
    print(f"[{name}] http={status} {elapsed}s err={err or '-'} :: {preview}")
    return {"name": name, "path": path, "note": note, "status": status,
            "seconds": elapsed, "error_kind": err, "preview": preview}


def main():
    results = []
    m = p.MODEL

    results.append(probe(
        "openai-only params", "/chat/completions",
        {"model": m, "messages": [{"role": "user", "content": "Say OK"}],
         "max_tokens": 16, "thinking": {"type": "disabled"},
         "reasoning_effort": "high", "verbosity": "low",
         "service_tier": "default", "vision_detail": "high"},
        "Do unknown OpenAI params cause 400? If yes, gateway must strip them."))

    results.append(probe(
        "responses endpoint", "/responses",
        {"model": m, "input": "Say OK", "max_output_tokens": 16},
        "Does DeepSeek expose OpenAI Responses API? openai-agents may use it."))

    results.append(probe(
        "tool_choice required + thinking", "/chat/completions",
        {"model": m, "messages": [{"role": "user", "content": "Use the tool."}],
         "max_tokens": 32, "thinking": {"type": "enabled"}, "tool_choice": "required",
         "tools": [{"type": "function", "function": {
             "name": "ping", "description": "ping",
             "parameters": {"type": "object", "properties": {}}}}]},
        "Docs claim 400; gateway must rewrite to auto + thinking off."))

    results.append(probe(
        "temperature + thinking", "/chat/completions",
        {"model": m, "messages": [{"role": "user", "content": "Say OK"}],
         "max_tokens": 16, "temperature": 0.3, "thinking": {"type": "enabled"}},
        "Docs say temperature has no effect in thinking mode; check for hard error."))

    results.append(probe(
        "max_completion_tokens alias", "/chat/completions",
        {"model": m, "messages": [{"role": "user", "content": "Say OK"}],
         "max_completion_tokens": 16, "thinking": {"type": "disabled"}},
        "Newer SDKs send max_completion_tokens; check acceptance."))

    results.append(probe(
        "gemini thought_signature residue", "/chat/completions",
        {"model": m, "messages": [
            {"role": "user", "content": "Use the tool."},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "ping", "arguments": "{}"},
                "extra_content": {"google": {"thought_signature": "skip_thought_signature_validator"}},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "content": "pong"},
        ], "max_tokens": 32, "thinking": {"type": "disabled"}},
        "SceneCode injects this for gemini-3; check whether DeepSeek rejects it."))

    results.append(probe(
        "json_object + thinking", "/chat/completions",
        {"model": m, "messages": [{"role": "user", "content": 'Return {"a":1} as JSON.'}],
         "max_tokens": 64, "response_format": {"type": "json_object"},
         "thinking": {"type": "enabled"}},
        "Check JSON mode compatibility with thinking enabled."))

    OUT.write_text(json.dumps({
        "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": m,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nWrote ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
