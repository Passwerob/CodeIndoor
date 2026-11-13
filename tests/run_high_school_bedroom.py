"""Run the high-school-bedroom test case defined in high_school_bedroom_case.json.

Per-piece generation (reference image -> VLM reading -> Agent build program ->
Blender -> visual gate). Pieces flagged "local" use a photo already dropped into
runtime/reference/<id>/ (no image model needed); "generate" pieces call the
image model, so IMAGE_API_KEY + a reachable image endpoint are required.

Usage:
    # 1) start the gateway in another shell (or same shell, before this call)
    python bridge/gateway.py --port 8765
    # 2) run, optionally limiting to one piece
    python tests/run_high_school_bedroom.py [--object study_chair]

Pass CODEINDOOR_GATEWAY to point the Agent/VLM calls at a different gateway.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adsl_adapter"))
import furniture_forge as ff  # noqa: E402

SPEC = ROOT / "tests/high_school_bedroom_case.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--object", help="limit to one object id")
    ap.add_argument("--attempts", type=int, default=3)
    args = ap.parse_args()

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    targets = spec["objects"]
    if args.object:
        targets = [o for o in targets if o["id"] == args.object]
    if not targets:
        print("no matching objects")
        return 2

    print(f"[case] {spec['case']}: {len(targets)} piece(s) to forge", flush=True)
    summary = []
    for o in targets:
        print(f"[forge] {o['id']} (mode={o.get('reference_mode', 'generate')}) ...", flush=True)
        try:
            res = ff.forge(
                {"id": o["id"], "dimensions": o["dimensions"], "brief": o["brief"],
                 "mount": o.get("mount", "floor")},
                max_attempts=args.attempts,
                reference_mode=o.get("reference_mode", "generate"),
                use_trellis=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[forge] {o['id']}: EXCEPTION {type(exc).__name__}: {exc}", flush=True)
            summary.append({"id": o["id"], "ok": False, "error": str(exc)[:160]})
            continue
        summary.append({"id": res["id"], "ok": res["ok"], "attempts": res["attempts"],
                        "objects": res.get("objects"),
                        "gate": (res.get("gate") or {}).get("verdict"),
                        "reference": bool(res.get("reference_image"))})
        print(f"[forge] {res['id']}: ok={res['ok']} attempts={res['attempts']} "
              f"objects={res.get('objects')}", flush=True)

    print("FORGE_SUMMARY=" + json.dumps(summary, ensure_ascii=False))
    return 0 if all(s["ok"] for s in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
