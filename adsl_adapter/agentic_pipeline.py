"""DEPRECATED — superseded by finescene_bridge.py.

This pipeline let the LLM emit raw bpy code strings, which ROOT_CAUSE_ANALYSIS.md
shows is the wrong abstraction layer (poor geometry, no per-part visual gate).
Kept only as evidence of a real run. The supported path is:
    structured plan -> FineScene FACTORIES -> aDSL Asset -> per-part QA gate.

Original description (superseded):
End-to-end agentic coding orchestrator (NO hand-written geometry anywhere).

    ObjectPlan (upstream, untouched)
      -> Constructor Agent generates part-wise Blender Python (DeepSeek via gateway)
      -> static contract check
      -> Blender sandbox execution
      -> error fed back to the Agent for a full rewrite (execution-guided repair)
      -> accepted programs registered into aDSL ProgramComponent AST
      -> Blender assembles ONLY agent-generated programs
      -> multi-view render -> Visual Critic (deepseek-flash image input)
      -> localized re-execution of one generated program

Run with the local gateway listening on 127.0.0.1:8765.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from agentic_coder import BLENDER, GEN, RUNTIME, code_part
from program_component import ProgramComponent, make_schema

ROOT = Path(r"E:/3DUse/SceneCode_audit")
UPSTREAM = ROOT / "runtime/outputs/ingested/scene_a2.upstream.json"
ADAPTER = ROOT / "adsl_adapter"
SPECS_OUT = RUNTIME / "part_specs.json"
AST_OUT = RUNTIME / "program_ast.json"
SCENE_REPORT = ROOT / "runtime/outputs/agent_scene/report.json"
GATEWAY = "http://127.0.0.1:8765/v1/chat/completions"

FABRIC = [0.24, 0.33, 0.46]
WOOD = [0.32, 0.17, 0.08]
FRAME = [0.15, 0.17, 0.20]

# Symmetric parts must share ONE agent-generated program. Generating them
# independently produces visibly different left/right shapes.
SYMMETRY_GROUPS = [
    ("left_armrest", "right_armrest"),
    ("leg_front_left", "leg_front_right", "leg_back_left", "leg_back_right"),
]
GROUP_REP = {cid: group[0] for group in SYMMETRY_GROUPS for cid in group}
REPRESENTATIVES = {group[0] for group in SYMMETRY_GROUPS}
SHARED_FEEDBACK = (
    "This SAME program will be reused for several symmetric parts with different "
    "params['origin']. Never vary geometry based on the `name` argument; all shape "
    "decisions must come from params so the mirrored parts stay identical."
)


def default_params(cid: str) -> dict:
    """Pure DATA spec handed to the Agent. No geometry is implemented here."""
    name = cid.lower()
    p = {"width": 0.6, "depth": 0.6, "height": 0.25, "bevel": 0.03,
         "color": list(FABRIC), "roughness": 0.82,
         "origin": [0.0, 0.0, 0.4], "bulge": 0.03, "seam": 0.008,
         "radius_top": 0.035, "radius_bottom": 0.022}
    if name.startswith("plinth"):
        p.update(width=1.92, depth=0.88, height=0.10, bevel=0.022,
                 color=list(WOOD), roughness=0.42, origin=[0, 0, 0.22])
    elif name.startswith("seat_base"):
        p.update(width=1.92, depth=0.88, height=0.14, bevel=0.025,
                 color=list(FRAME), roughness=0.65, origin=[0, 0, 0.34])
    elif name.startswith("backrest"):
        p.update(width=1.92, depth=0.13, height=0.56, bevel=0.035,
                 color=list(FRAME), roughness=0.7, origin=[0, 0.40, 0.66])
    elif name.startswith("seat_cushion"):
        i = int(name[-1]) - 1 if name[-1].isdigit() else 1
        p.update(width=0.60, depth=0.72, height=0.15, bevel=0.05,
                 origin=[[-0.62, 0.0, 0.62][i], -0.03, 0.48])
    elif name.startswith("back_cushion"):
        i = int(name[-1]) - 1 if name[-1].isdigit() else 1
        p.update(width=0.58, depth=0.14, height=0.42, bevel=0.045,
                 origin=[[-0.62, 0.0, 0.62][i], 0.31, 0.69])
    elif "armrest" in name:
        side = -1.0 if "left" in name else 1.0
        p.update(width=0.14, depth=0.88, height=0.30, bevel=0.055,
                 origin=[0.98 * side, 0.0, 0.55])
    elif "leg" in name:
        x = -0.86 if "left" in name else 0.86
        y = 0.36 if "back" in name else -0.36
        p.update(width=0.07, depth=0.07, height=0.20, bevel=0.006,
                 color=list(WOOD), roughness=0.42, origin=[x, y, 0.11])
    return p


def visual_critic(image_path: Path) -> dict:
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = (
        "You are the Visual Critic Agent for an agentic indoor-scene coding system. "
        "Inspect this render and return JSON with keys: score (1-10), "
        "parts_visible (list), issues (list), part_to_fix (string), reason (string). "
        "Judge whether it is a recognizable detailed two-seat sofa with back cushions, "
        "seat cushions, armrests, a base and legs, and whether proportions are sane."
    )
    payload = {
        "model": "gemini-3.1-pro-preview",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": 700,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    req = urllib.request.Request(
        GATEWAY, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"raw": content[:400]}


def main() -> int:
    upstream = json.loads(UPSTREAM.read_text(encoding="utf-8"))
    all_specs = {p["component_id"]: {"params": default_params(p["component_id"]),
                                      "meta": p} for p in upstream["parts"]}

    results = {}
    shared_results = {}
    started = time.time()
    for cid, entry in all_specs.items():
        rep = GROUP_REP.get(cid, cid)
        if cid != rep:
            base = shared_results.get(rep)
            if base and base["ok"]:
                dest = GEN / f"{cid}.py"
                dest.write_bytes(Path(base["source_path"]).read_bytes())
                results[cid] = {"ok": True, "cid": cid, "source_path": str(dest),
                                "attempts": 0, "log": [{"reused_from": rep}]}
            else:
                results[cid] = {"ok": False, "cid": cid, "attempts": 0,
                                "log": [{"reused_from": rep, "representative_failed": True}]}
            print(f"[agent] {cid}: reused shared program from {rep} "
                  f"({'OK' if results[cid]['ok'] else 'FAILED'})", flush=True)
            continue

        print(f"[agent] generating {cid} ...", flush=True)
        feedback = SHARED_FEEDBACK if cid in REPRESENTATIVES else None
        result = code_part(cid, entry["params"], entry["meta"],
                           max_attempts=3, feedback=feedback)
        if cid in REPRESENTATIVES:
            shared_results[cid] = result
        results[cid] = result
        print(f"[agent] {cid}: {'OK' if result['ok'] else 'FAILED'} "
              f"attempts={result['attempts']}", flush=True)

    accepted = {cid: entry for cid, entry in all_specs.items()
                if results[cid]["ok"]}
    SPECS_OUT.write_text(json.dumps(accepted, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    ast = {}
    for cid, entry in accepted.items():
        src = Path(results[cid]["source_path"])
        comp = ProgramComponent(
            component_id=cid,
            program="agent_generated:" + src.name,
            params=make_schema("agent"),
            values=entry["params"],
            local_origin=tuple(entry["params"].get("origin", (0, 0, 0))),
            upstream_refs={"part_type": entry["meta"].get("part_type"),
                           "upstream_base_shape": entry["meta"].get("upstream_base_shape")},
            source_path=str(src),
            source_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
        )
        ast[cid] = comp.to_dict()
    AST_OUT.write_text(json.dumps(ast, ensure_ascii=False, indent=2), encoding="utf-8")

    # Immutable baseline: every later Critic-repair rollback must restore to
    # THIS generation, never to whatever the last experiment left behind.
    BASELINE = RUNTIME / "generated_baseline"
    BASELINE.mkdir(parents=True, exist_ok=True)
    for cid in accepted:
        (BASELINE / f"{cid}.py").write_bytes(
            Path(results[cid]["source_path"]).read_bytes())
    print(f"[agent] immutable baseline saved: {len(accepted)} programs", flush=True)

    print(f"[agent] assembling {len(accepted)} generated programs in Blender ...", flush=True)
    proc = subprocess.run(
        [BLENDER, "-b", "--python", str(ADAPTER / "run_agent_scene.py")],
        capture_output=True, timeout=600)
    scene_report = {}
    if SCENE_REPORT.exists():
        scene_report = json.loads(SCENE_REPORT.read_text(encoding="utf-8"))

    critic = {}
    before = (scene_report.get("before_renders") or [None])[0]
    if before and Path(before).exists():
        try:
            critic = visual_critic(Path(before))
        except Exception as exc:  # noqa: BLE001
            critic = {"error": type(exc).__name__ + ": " + str(exc)[:160]}

    summary = {
        "elapsed_seconds": round(time.time() - started, 1),
        "parts_total": len(all_specs),
        "parts_accepted": len(accepted),
        "parts_failed": sorted(set(all_specs) - set(accepted)),
        "total_agent_attempts": sum(r["attempts"] for r in results.values()),
        "repair_attempts": sum(max(0, r["attempts"] - 1) for r in results.values()),
        "per_part": {cid: {"ok": r["ok"], "attempts": r["attempts"],
                           "log": r["log"]} for cid, r in results.items()},
        "a_dsl_program_ast": str(AST_OUT),
        "scene_report": {k: scene_report.get(k) for k in
                         ("blender", "engine", "agent_generated_parts",
                          "failed_parts", "object_count", "localized_edit",
                          "before_renders", "after_renders", "blend_path")},
        "visual_critic": critic,
        "blender_stderr_tail": (proc.stderr or b"").decode("utf-8", "replace")[-400:],
    }
    out = ROOT / "AGENTIC_RESULTS.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AGENTIC_SUMMARY=" + json.dumps({
        k: summary[k] for k in ("elapsed_seconds", "parts_total", "parts_accepted",
                                "parts_failed", "total_agent_attempts", "repair_attempts")
    }, ensure_ascii=False))
    print("critic=", json.dumps(critic, ensure_ascii=False)[:400])
    print("Wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
