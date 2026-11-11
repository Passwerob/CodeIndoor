#!/usr/bin/env python3
"""Prove that each forged furniture program came off the wire from the model.

The claim under audit is narrow and checkable: "this geometry was written by
the upstream model, not by hand." Four independently stored artefacts are
needed to close it, and this script reads only those, never the code's own
say-so.

  1. runtime/outputs/gateway_calls.jsonl
     One line per upstream call, written by the gateway before the response
     reaches the caller. Holds model, status, prompt hash, response hash and
     token usage. No prompt body is stored -- a hash is enough to prove the
     call existed without keeping user content on disk.

  2. runtime/outputs/forge/<id>_prov/llm_attempt<n>.json
     The full upstream body, saved next to the response hash it was addressed
     by. Append-only: acceptance never deletes this directory.

  3. runtime/outputs/forge/<id>/program.py
     The exact file Blender executed. Written with LF newlines so its digest
     is stable across platforms.

  4. runtime/outputs/forge/<id>_result.json
     The run log binding response hash -> code hash -> Blender's own metrics.

The decisive check is `code_derives_from_body`: strip the fenced block out of
the stored response body and hash it. If that equals the logged code_sha, the
program is a literal substring of what the model sent. A human editing the
program afterwards breaks this equality, which is exactly the point.

Hashing note: an earlier version hashed the raw bytes of program.py. On
Windows that folds in CR characters the model never emitted, so every digest
mismatched and all assets were reported as forged. Newlines are normalised
before hashing here and at write time.

Usage:
    python adsl_adapter/verify_provenance.py [--json]
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(r"E:/3DUse/SceneCode_audit")
GATEWAY_LOG = ROOT / "runtime/outputs/gateway_calls.jsonl"
FORGE = ROOT / "runtime/outputs/forge"

sys.path.insert(0, str(ROOT / "adsl_adapter"))


def sha16(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def message_text(body) -> str:
    """Pull the assistant content out of a stored upstream response body.

    The stored body is the whole completion envelope, not just the answer, so
    the fence has to be found inside choices[0].message.content. Anything else
    (a hand-written file, an OpenAI-style stream chunk) yields an empty value
    and cannot accidentally match a code hash.
    """
    payload = body
    if isinstance(body, str):
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return ""
    if not isinstance(payload, dict):
        return ""
    try:
        return payload["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""


def strip_fence(text: str) -> str:
    """Same extraction the forge used, so the digest is comparable."""
    import re
    m = re.search(r"```(?:python)?\s*([\s\S]*?)\s*```", text or "")
    return (m.group(1) if m else (text or "")).strip()


def load_gateway_log() -> dict[str, dict]:
    calls: dict[str, dict] = {}
    if not GATEWAY_LOG.is_file():
        return calls
    for line in GATEWAY_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("resp_sha"):
            calls[record["resp_sha"]] = record
    return calls


def find_bodies(oid: str) -> list[dict]:
    """Every stored upstream body for this object, wherever it landed.

    Bodies live in `<id>_prov/` now and in `<id>_attempt<n>/` if they predate
    the archive. Both are searchable; neither is assumed.
    """
    out = []
    for path in sorted(FORGE.glob(f"{oid}*/llm_attempt*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        payload["_path"] = str(path.relative_to(FORGE))
        out.append(payload)
    return out


def verify_one(oid: str, calls: dict[str, dict]) -> dict:
    row: dict = {"id": oid, "checks": {}, "ok": False}
    result_path = FORGE / f"{oid}_result.json"
    program = FORGE / oid / "program.py"

    if not result_path.is_file() or not program.is_file():
        row["checks"]["files"] = "missing result or program"
        return row

    result = json.loads(result_path.read_text(encoding="utf-8"))
    entries = [e for e in result.get("log", []) if e.get("code_sha")]
    if not entries:
        row["checks"]["log"] = "no attempt carries a code hash"
        return row

    # Bind by hash, not by position: an accepted program can come from an
    # earlier attempt than the last one logged (resume runs, retries that fail
    # the gate afterwards). Position-based binding reported a false mismatch.
    code_hash = sha16(program.read_text(encoding="utf-8"))
    row["code_sha"] = code_hash
    accepted = next((e for e in reversed(entries) if e.get("code_sha") == code_hash), None)
    if accepted is None:
        row["checks"]["program_matches_logged_code"] = False
        row["logged_code_shas"] = [e.get("code_sha") for e in entries]
        return row
    row["checks"]["program_matches_logged_code"] = True
    row["attempt"] = accepted.get("attempt")
    row["objects"] = accepted.get("objects")

    resp_sha = accepted.get("llm_resp_sha")
    row["llm_resp_sha"] = resp_sha
    record = calls.get(resp_sha or "")
    row["checks"]["response_seen_by_gateway"] = record is not None
    if record:
        row["gateway_model"] = record.get("model")
        row["upstream_model"] = record.get("upstream_model")
        row["gateway_status"] = record.get("status")
        row["gateway_ts"] = record.get("ts")
        row["checks"]["upstream_model_is_deepseek"] = (
            str(record.get("upstream_model", "")).startswith("deepseek"))
        row["checks"]["http_200"] = record.get("status") == 200
    row["llm_usage"] = accepted.get("llm_usage")

    bodies = find_bodies(oid)
    matched = next((b for b in bodies
                    if (b.get("meta") or {}).get("resp_sha") == resp_sha), None)
    row["checks"]["stored_body_matches_gateway_hash"] = matched is not None
    row["bodies_on_disk"] = len(bodies)
    row["body_missing"] = matched is None
    if matched:
        row["stored_body"] = matched["_path"]
        row["stored_model"] = (matched.get("meta") or {}).get("upstream_model")
        # The decisive link: the program must be a literal substring of the
        # bytes the model sent. Hand edits break this.
        text = message_text(matched.get("raw_response"))
        derived = sha16(strip_fence(text))
        row["derived_code_sha"] = derived
        row["checks"]["code_derives_from_body"] = derived == code_hash
        # The envelope names its own model; independent of the gateway log.
        envelope_model = ""
        try:
            envelope_model = json.loads(str(matched.get("raw_response"))).get("model", "")
        except (json.JSONDecodeError, TypeError):
            pass
        row["body_model"] = envelope_model
        row["checks"]["body_declares_deepseek"] = envelope_model.startswith("deepseek")

    hard = {k: v for k, v in row["checks"].items() if isinstance(v, bool)}
    row["ok"] = all(hard.values())
    return row


def main() -> int:
    calls = load_gateway_log()
    # Only real assets: probe scratch dirs and superseded manual variants carry
    # a program.py but were never a delivered piece.
    skip = {"_curve_probe", "sofa_mat", "sofa_proc", "sofa_v3", "review"}
    ids = sorted(p.parent.name for p in FORGE.glob("*/program.py")
                 if p.parent.name not in skip and not p.parent.name.endswith(tuple(
                     f"_attempt{i}" for i in range(1, 10))))
    rows = [verify_one(oid, calls) for oid in ids]
    ok = [r for r in rows if r["ok"]]

    print(f"gateway log: {GATEWAY_LOG.name}  responses={len(calls)}")
    for r in rows:
        if r["ok"]:
            print(f"OK   {r['id']:16s} attempt={r.get('attempt')} "
                  f"model={r.get('upstream_model')} code={r.get('code_sha')} "
                  f"resp={r.get('llm_resp_sha')} body={r.get('stored_body')}")
            continue
        print(f"FAIL {r['id']:16s} attempt={r.get('attempt')} "
              f"model={r.get('upstream_model')} code={r.get('code_sha')}")
        for key, value in r["checks"].items():
            if value is not True:
                print(f"       - {key}: {value}")
        if r.get("body_missing"):
            print("       - no stored response body: it was deleted by the old "
                  "acceptance rmtree and cannot be reconstructed. Re-forge this "
                  "asset to produce a verifiable chain.")

    print(f"\nVERIFIED {len(ok)}/{len(rows)} programs are literal output of an upstream response")
    payload = {"gateway_responses_logged": len(calls), "verified": len(ok),
               "total": len(rows), "rows": rows}
    (FORGE / "PROVENANCE.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if "--json" in sys.argv:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if len(ok) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
