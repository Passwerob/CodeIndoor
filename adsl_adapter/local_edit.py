"""Critic-driven LOCAL edit of the assembled room (aDSL AST as Source of Truth).

This proves aDSL is an editable representation, not an export format:
  * FineScene's scene-scope visual QA returns blocking findings naming specific
    component ids (repair_target == "layout").
  * The LLM emits ONLY numeric pose corrections for those components.
  * Only the named components' xform matrices are rewritten in the aDSL AST.
  * Every non-target component's JSON is verified byte-identical (SHA-256).
  * The room is re-rendered from the edited AST and re-audited.

No geometry is written, no ObjectPlan is regenerated, no other furniture is
rebuilt. This is the "editing phase" of the agreed stage-isolation model.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from assemble_scene import (OUT, load_lounge_objects, render_interior,  # noqa: E402
                            scene_visual_audit)

GATEWAY = "http://127.0.0.1:8765/v1/chat/completions"
AST = OUT / "living_room.adsl.json"
QA = OUT / "scene_visual_qa.json"
LAYOUT = Path(r"E:/3DUse/SceneCode_audit/runtime/outputs/scene_layout/layout.json")
EDITED_AST = OUT / "living_room_edited.adsl.json"
REPORT = OUT / "LOCAL_EDIT_REPORT.json"

EDIT_PROMPT = """You are the SceneCode Designer performing a LOCAL layout repair.

FineScene's scene-scope visual QA rejected the assembled room with these findings:
{findings}

The aDSL geometry of every object is already accepted and MUST NOT change.
Your only available action is to adjust the yaw rotation (degrees) of the named
components. Current object poses (x, y, z, yaw_deg):
{poses}

Room frame: +Z up, +X right, -Y front. The coffee table is at the centre of the
seating group; both armchairs should face inward toward it and be mirror-symmetric.

Return ONLY JSON:
{{"edits": [{{"id": "<component id>", "yaw_deg": <number>, "reason": "<one sentence>"}}],
  "unchanged": ["<every other component id>"]}}
Include ONLY the component ids named in the findings. Do not output geometry,
code, positions or explanations outside the JSON.
"""


def call_llm(prompt: str, retries: int = 4) -> dict:
    payload = {"model": "gpt-5.4",
               "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 1200,
               "response_format": {"type": "json_object"},
               "thinking": {"type": "disabled"}}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(GATEWAY, data=raw, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            if content:
                return json.loads(content)
        except Exception as exc:  # noqa: BLE001
            print(f"[local-edit] LLM attempt {attempt+1}: {type(exc).__name__}", flush=True)
            import time
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("LLM unavailable for local edit")


def mat4(pos, yaw_deg):
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    x, y, z = (float(v) for v in pos)
    return [[c, -s, 0.0, x], [s, c, 0.0, y],
            [0.0, 0.0, 1.0, z], [0.0, 0.0, 0.0, 1.0]]


def stamp_pose(node: dict, matrix: list) -> None:
    """Exactly FineScene's apply_pose(), on the serialized aDSL tree."""
    for prim in node.get("primitives", []):
        prim["xform"] = copy.deepcopy(matrix)
    for child in node.get("children", {}).values():
        stamp_pose(child, matrix)


def component_hash(child: dict) -> str:
    return hashlib.sha256(json.dumps(child, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def main() -> int:
    ast = json.loads(AST.read_text(encoding="utf-8"))
    qa = json.loads(QA.read_text(encoding="utf-8"))
    layout = json.loads(LAYOUT.read_text(encoding="utf-8"))["layout"]
    poses = {o["id"]: o["placement"] for o in layout.get("objects") or []}

    decision = qa.get("decision") or {}
    findings = [f for f in (decision.get("findings") or [])
                if f.get("severity") == "blocking"]
    targets = sorted({cid for f in findings for cid in (f.get("component_ids") or [])
                      if cid in ast.get("children", {})})
    print(f"[local-edit] blocking findings={len(findings)} target components={targets}",
          flush=True)
    if not targets:
        print("[local-edit] nothing to repair locally", flush=True)
        return 0

    prompt = EDIT_PROMPT.format(
        findings=json.dumps([{k: f.get(k) for k in
                              ("severity", "component_ids", "repair_target",
                               "evidence", "correction", "view")}
                             for f in findings], ensure_ascii=False),
        poses=json.dumps({cid: poses.get(cid) for cid in poses}, ensure_ascii=False))
    plan = call_llm(prompt)
    edits = [e for e in (plan.get("edits") or [])
             if e.get("id") in targets and isinstance(e.get("yaw_deg"), (int, float))]
    print("[local-edit] corrections=" + json.dumps(edits, ensure_ascii=False), flush=True)
    if not edits:
        raise RuntimeError("LLM returned no applicable yaw corrections")

    before = {oid: component_hash(child) for oid, child in ast["children"].items()}
    applied = []
    for edit in edits:
        cid = edit["id"]
        p = poses.get(cid, {})
        matrix = mat4((p.get("x", 0), p.get("y", 0), p.get("z", 0)),
                      float(edit["yaw_deg"]))
        stamp_pose(ast["children"][cid], matrix)
        applied.append({"id": cid, "yaw_before": p.get("yaw_deg"),
                        "yaw_after": float(edit["yaw_deg"]),
                        "reason": edit.get("reason", "")})
    after = {oid: component_hash(child) for oid, child in ast["children"].items()}
    changed = sorted(oid for oid in before if before[oid] != after[oid])
    unexpected = sorted(set(changed) - {e["id"] for e in applied})
    non_target_preserved = not unexpected and set(changed) == {e["id"] for e in applied}

    EDITED_AST.write_text(json.dumps(ast, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[local-edit] changed components={changed} "
          f"non_target_preserved={non_target_preserved}", flush=True)

    interior = render_interior(adsl_json=EDITED_AST, subdir="interior_edited",
                               blend_name="living_room_edited.blend")
    print(f"[local-edit] re-render views={len(interior['views'])} "
          f"blend={interior['blend_exists']}", flush=True)
    audit = scene_visual_audit([Path(v) for v in interior["views"]], layout,
                               qa_path=OUT / "scene_visual_qa_edited.json")
    print(f"[local-edit] re-audit accepted={audit['accepted']} "
          f"verdict={audit['verdict']}", flush=True)

    report = {
        "trigger": "FineScene review_images(scope='scene') blocking finding",
        "blocking_findings": findings,
        "targets": targets,
        "edits": applied,
        "changed_components": changed,
        "non_target_violations": unexpected,
        "non_target_preserved": non_target_preserved,
        "localized_update_passed": non_target_preserved and bool(changed),
        "program_persistence": "edited aDSL AST written to " + str(EDITED_AST),
        "render": interior,
        "audit_before": {"accepted": qa.get("accepted"), "status": qa.get("status"),
                         "verdict": decision.get("verdict")},
        "audit_after": audit,
        "stage_isolation": "editing phase: aDSL AST is the sole writable SoT; "
                           "upstream_snapshot.json stays frozen",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("LOCAL_EDIT_RESULT=" + json.dumps({
        "targets": targets, "changed": changed,
        "non_target_preserved": non_target_preserved,
        "verdict_before": decision.get("verdict"), "verdict_after": audit["verdict"],
        "accepted_after": audit["accepted"],
    }, ensure_ascii=False))
    return 0 if report["localized_update_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
