#!/usr/bin/env python3
"""Local OpenAI-compatible gateway: lets SceneCode run UNMODIFIED on DeepSeek.

Design (all rules justified by bridge/probe_deepseek.py + probe_compat.py):
  * Text paths (/models, /chat/completions, /responses) are transparently
    forwarded to DeepSeek, with the API key injected in-process only.
  * /images/generations and /images/edits are served LOCALLY with a
    procedural placeholder PNG. No FLUX / SAM3D / TRELLIS / OpenAI image API
    is ever contacted, yet SceneCode's router.py:1411 still gets a valid image.
  * Only ONE rewrite is mandatory: tool_choice="required" + thinking mode
    returns HTTP 400 upstream, so it is downgraded to "auto".
  * Model names from SceneCode (gpt-5.4 / gemini-3.1-pro-preview / ...) are
    mapped to deepseek-flash.

No key, prompt body or image content is written to logs or reports.

Usage:
    python E:/3DUse/New/CodeIndoor/bridge/gateway.py [--port 8765]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "https://api.deepseek.com"
ENV_FILE = Path(r"E:/3DUse/New/CodeIndoor/runtime/.env")
LOG_FILE = Path(r"E:/3DUse/New/CodeIndoor/runtime/outputs/gateway_calls.jsonl")

KNOWN_MODELS = {"deepseek-flash", "deepseek-v4-pro"}
DEFAULT_MODEL = "deepseek-flash"
DROP_IF_STRICT = ("verbosity", "service_tier", "vision_detail")


def load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key and key.strip():
        return key.strip()
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DEEPSEEK_API_KEY missing; put it in runtime/.env (gitignored).")


KEY = load_key()


def log_call(method: str, path: str, model: str | None, status: int,
            seconds: float, note: str = "", extra: dict | None = None) -> None:
    """Desensitized call log: no key, no prompt, no image bytes.

    Hashes are recorded so a downstream consumer can prove which upstream
    response produced its artifact without either side storing the prompt.
    """
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": method, "path": path,
        "model": model, "status": status,
        "seconds": round(seconds, 3), "note": note,
    }
    if extra:
        record.update({k: v for k, v in extra.items() if v is not None})
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def normalize_path(path: str) -> str:
    """SceneCode uses OPENAI_API_BASE=/v1; DeepSeek exposes both forms."""
    if path.startswith("/v1/"):
        return "/" + path[4:]
    return path


def rewrite_payload(payload: dict, is_responses: bool = False) -> tuple[dict, list[str]]:
    notes: list[str] = []
    body = dict(payload)

    model = body.get("model")
    if model and model not in KNOWN_MODELS:
        notes.append(f"model {model}->{DEFAULT_MODEL}")
        body["model"] = DEFAULT_MODEL
    elif not model:
        body["model"] = DEFAULT_MODEL
        notes.append("model defaulted")

    # The ONLY mandatory rewrite (observed HTTP 400 in probe_compat).
    thinking = body.get("thinking")
    thinking_on = isinstance(thinking, dict) and thinking.get("type") == "enabled"
    if thinking_on:
        choice = body.get("tool_choice")
        if choice == "required":
            body["tool_choice"] = "auto"
            notes.append("tool_choice required->auto (thinking mode rejects required)")
        elif isinstance(choice, dict) and choice.get("type") == "required":
            # Responses API uses an object form instead of the chat string form.
            body["tool_choice"] = {**choice, "type": "auto"}
            notes.append("responses tool_choice required->auto")

    # Unknown OpenAI-only params are currently IGNORED upstream (probe 200),
    # so we keep them by default. Set STRICT_PARAMS=1 to strip defensively.
    # /responses uses a DIFFERENT tool_choice vocabulary: observed HTTP 422
    # "unknown variant `required`, expected one of `function`, `web_search`...".
    # OpenAI-style variants cannot be expressed upstream, so drop them and let
    # the server apply its default instead of failing the whole request.
    if is_responses:
        choice = body.get("tool_choice")
        if isinstance(choice, dict) and choice.get("type") in ("required", "auto", "none"):
            body.pop("tool_choice")
            notes.append("responses tool_choice OpenAI-variant dropped (422 upstream)")

    if os.environ.get("STRICT_PARAMS") == "1":
        for name in DROP_IF_STRICT:
            if name in body:
                body.pop(name)
                notes.append("stripped " + name)

    return body, notes


def make_png(width: int, height: int, seed_text: str) -> bytes:
    """Pure-stdlib PNG encoder: a neutral studio backdrop with simple furniture
    silhouette blocks. Deterministic per prompt, no ML model involved."""
    digest = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:8], 16)
    accent = (90 + digest % 60, 110 + (digest >> 8) % 60, 130 + (digest >> 16) % 60)
    bg = (238, 238, 234)
    dark = (55, 60, 66)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    left, right = int(width * 0.20), int(width * 0.80)
    seat_l, seat_r = int(width * 0.26), int(width * 0.74)
    leg_l, leg_r = int(width * 0.28), int(width * 0.34)

    def row(kind: int) -> bytes:
        buf = bytearray()
        for x in range(width):
            if kind == 0:
                color = bg
            elif kind == 1:
                color = accent if left <= x <= right else bg
            elif kind == 2:
                color = accent if seat_l <= x <= seat_r else bg
            else:
                inside_leg = (leg_l <= x <= leg_r) or (width - leg_r <= x <= width - leg_l)
                color = dark if inside_leg else bg
            buf.extend(color)
        return bytes(buf)

    rows = {k: row(k) for k in range(4)}
    raw = bytearray()
    for y in range(height):
        ratio = y / max(height, 1)
        kind = 0 if ratio < 0.30 else (1 if ratio < 0.55 else (2 if ratio < 0.78 else 3))
        raw.append(0)
        raw.extend(rows[kind])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def parse_size(value) -> tuple[int, int]:
    if isinstance(value, str) and "x" in value.lower():
        try:
            w, h = value.lower().split("x", 1)
            return max(64, min(1024, int(w))), max(64, min(1024, int(h)))
        except ValueError:
            pass
    return 512, 512


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # silence default stderr access log
        return

    def _send_json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _forward(self, method: str, path: str, payload: dict | None) -> None:
        started = time.time()
        upstream_path = normalize_path(path)
        if payload is None:
            body, notes = {}, []
        else:
            body, notes = rewrite_payload(payload, is_responses=(upstream_path == "/responses"))
        raw = (json.dumps(body, ensure_ascii=False).encode("utf-8")
               if payload is not None else None)
        req = urllib.request.Request(
            UPSTREAM + upstream_path, data=raw,
            method="GET" if payload is None else "POST",
            headers={"Authorization": "Bearer " + KEY,
                     "Content-Type": "application/json",
                     "Accept": "application/json"},
        )
        streaming = bool(payload and payload.get("stream"))
        upstream = None
        status, content_type, data = 502, "application/json", b""
        prompt_sha = hashlib.sha256(raw).hexdigest()[:16] if raw else None
        for attempt in range(3):
            try:
                upstream = urllib.request.urlopen(req, timeout=300)
                status = upstream.status
                content_type = upstream.headers.get("Content-Type", "application/json")
                break
            except urllib.error.HTTPError as exc:
                status, content_type, data = exc.code, "application/json", exc.read()
                notes.append("upstream_http_error")
                break
            except Exception:  # noqa: BLE001
                # This host's proxy intermittently returns "Tunnel failed: 502".
                notes.append("transport_retry_" + str(attempt + 1))
                time.sleep(1 + attempt)
        else:
            self._send_json(502, {"error": {"message": "gateway upstream failure after 3 attempts",
                                            "type": "gateway_error"}})
            log_call(method, path, body.get("model"), 502, time.time() - started, "transport")
            return

        if streaming and upstream is not None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            stream_hash = hashlib.sha256()
            try:
                while True:
                    chunk = upstream.read(512)
                    if not chunk:
                        break
                    stream_hash.update(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            finally:
                upstream.close()
            self.close_connection = True
            log_call(method, path, body.get("model"), status,
                     time.time() - started, ",".join(notes + ["streamed"]),
                     extra={"prompt_sha": prompt_sha,
                            "resp_sha": stream_hash.hexdigest()[:16]})
            return

        if upstream is not None:
            data = upstream.read()
            upstream.close()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        extra = {"prompt_sha": prompt_sha}
        if data:
            extra["resp_sha"] = hashlib.sha256(data).hexdigest()[:16]
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict):
                    if parsed.get("usage"):
                        extra["usage"] = parsed["usage"]
                    if parsed.get("model"):
                        extra["upstream_model"] = parsed["model"]
            except Exception:  # noqa: BLE001
                pass
        log_call(method, path, body.get("model"), status, time.time() - started,
                 ",".join(notes), extra=extra)

    def _serve_procedural_image(self, path: str) -> None:
        started = time.time()
        payload = self._read_json()
        width, height = parse_size(payload.get("size"))
        prompt = str(payload.get("prompt") or payload.get("n") or "scenecode-asset")
        png = make_png(width, height, prompt)
        result = {"created": int(time.time()),
                  "data": [{"b64_json": base64.b64encode(png).decode("ascii"),
                            "revised_prompt": None}]}
        self._send_json(200, result)
        log_call("POST", path, "local-procedural", 200, time.time() - started,
                 f"{width}x{height} no-external-image-service")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if normalize_path(path) in ("/models",):
            self._forward("GET", path, None)
        else:
            self._send_json(404, {"error": {"message": "unsupported gateway path: " + path,
                                            "type": "gateway_error"}})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        normalized = normalize_path(path)
        if normalized in ("/images/generations", "/images/edits"):
            self._serve_procedural_image(path)
            return
        if normalized in ("/chat/completions", "/responses", "/embeddings"):
            self._forward("POST", path, self._read_json())
            return
        self._send_json(404, {"error": {"message": "unsupported gateway path: " + path,
                                        "type": "gateway_error"}})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"gateway listening on 127.0.0.1:{args.port} (no key in output)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
