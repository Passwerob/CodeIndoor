"""Render -> Visual Critic -> localized program regeneration -> re-render -> re-critic.

Closes the loop with the AGENT, not with hand edits. The Critic's issues are fed
back to the Constructor Agent for the parts that can be fixed locally (armrests).
Layout-level issues (three seat sections vs two-seat brief) belong to the
GENERATION phase / Planner, so they are reported but not patched here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agentic_coder import BLENDER, GEN, RUNTIME, code_part

BASELINE = RUNTIME / "generated_baseline"
from agentic_pipeline import default_params, visual_critic

ROOT = Path(r"E:/3DUse/New/CodeIndoor")
ADAPTER = ROOT / "adsl_adapter"
RESULTS = ROOT / "AGENTIC_RESULTS.json"
SCENE_REPORT = ROOT / "runtime/outputs/agent_scene/report.json"
OUT = ROOT / "CRITIC_REPAIR.json"

TARGETS = ("left_armrest", "right_armrest")


def main() -> int:
    prior = json.loads(RESULTS.read_text(encoding="utf-8"))
    critic = prior.get("visual_critic") or {}
    issues = critic.get("issues") or []
    feedback = "Visual Critic review of the assembled scene:\n" + "\n".join(
        "- " + str(i) for i in issues)

    # Always roll back to the IMMUTABLE baseline, not to the mutable working
    # copy (which drifts when the repair is run more than once).
    backups = {cid: (BASELINE / f"{cid}.py").read_bytes() for cid in TARGETS
               if (BASELINE / f"{cid}.py").exists()}

    repair_feedback = (
        feedback + "\n\nSpecifically: make the armrest slimmer, straighter and "
        "stylistically consistent with the sofa's rectilinear body. "
        "NOTE: Blender 5.2 removed Mesh.use_auto_smooth; do not use it. "
        "Keep the build_part contract and high geometric detail. "
        "CRITICAL: this SAME program will be reused for both left and right "
        "armrests with different params['origin']. Never vary the geometry based "
        "on the `name` argument; all shape decisions must come from params."
    )

    # A symmetric pair must share ONE generated program. Calling the Agent
    # separately for left/right produced two different shapes (visibly
    # asymmetric arms), so generate once and reuse the source for both ids.
    primary = TARGETS[0]
    result = code_part(primary, default_params(primary), {"part_type": "armrest"},
                       feedback=repair_feedback, max_attempts=3)
    shared_code = None
    if result["ok"]:
        shared_code = Path(result["source_path"]).read_bytes()

    repairs = {}
    applied = 0
    for cid in TARGETS:
        entry = {
            "ok": bool(shared_code), "attempts": result["attempts"],
            "execution_guided_repairs": max(0, result["attempts"] - 1),
            "log": result["log"], "shared_program": bool(shared_code),
            "applied": False,
        }
        if shared_code:
            (GEN / f"{cid}.py").write_bytes(shared_code)
            entry["applied"] = True
            applied += 1
        repairs[cid] = entry
        print(f"[critic-repair] {cid}: applied={entry['applied']} "
              f"(shared program, attempts={result['attempts']})", flush=True)

    # If regeneration failed, restore the previous accepted programs intact.
    symmetry_rollback = False
    if applied != len(TARGETS):
        for cid, data in backups.items():
            (GEN / f"{cid}.py").write_bytes(data)
        for cid in TARGETS:
            path = GEN / f"{cid}.py"
            if cid not in backups and path.exists():
                path.unlink()
        symmetry_rollback = True
        applied = 0
        print("[critic-repair] regeneration failed -> restored both programs", flush=True)
    proc = subprocess.run(
        [BLENDER, "-b", "--python", str(ADAPTER / "run_agent_scene.py")],
        capture_output=True, timeout=600)

    scene = json.loads(SCENE_REPORT.read_text(encoding="utf-8")) if SCENE_REPORT.exists() else {}
    before = (scene.get("before_renders") or [None])[0]
    after_critic = {}
    if before and Path(before).exists():
        try:
            after_critic = visual_critic(Path(before))
        except Exception as exc:  # noqa: BLE001
            after_critic = {"error": type(exc).__name__ + ": " + str(exc)[:160]}

    # Acceptance gate: a repair that lowers the Critic score is rejected and
    # the previous agent-generated programs are restored. The Agent does not
    # get to keep a regression just because it produced new code.
    score_before = critic.get("score")
    score_after = after_critic.get("score")
    rolled_back = False
    decision = "accepted"
    if (isinstance(score_before, (int, float))
            and isinstance(score_after, (int, float))
            and score_after < score_before):
        for cid, data in backups.items():
            (GEN / f"{cid}.py").write_bytes(data)
        # Parts absent from the immutable baseline must not keep a rejected program.
        for cid in TARGETS:
            path = GEN / f"{cid}.py"
            if cid not in backups and path.exists():
                path.unlink()
        subprocess.run([BLENDER, "-b", "--python", str(ADAPTER / "run_agent_scene.py")],
                       capture_output=True, timeout=600)
        rolled_back = True
        decision = "rolled_back (Critic score decreased)"
        scene = (json.loads(SCENE_REPORT.read_text(encoding="utf-8"))
                 if SCENE_REPORT.exists() else {})
    elif not isinstance(score_after, (int, float)):
        decision = "inconclusive (no numeric score)"
    if symmetry_rollback:
        decision = "rejected (symmetric pair restored to previous programs)"

    report = {
        "symmetry_rollback": symmetry_rollback,
        "critic_score_before": score_before,
        "critic_score_after": score_after,
        "decision": decision,
        "rolled_back": rolled_back,
        "issues_before": issues,
        "issues_after": after_critic.get("issues"),
        "parts_regenerated": applied,
        "repairs": repairs,
        "layout_issues_deferred_to_planner": [
            i for i in issues if "two-seat" in str(i) or "three seat" in str(i)],
        "scene_object_count": scene.get("object_count"),
        "localized_edit_after_repair": scene.get("localized_edit"),
        "before_renders": scene.get("before_renders"),
        "blender_stderr_tail": (proc.stderr or b"").decode("utf-8", "replace")[-300:],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("CRITIC_REPAIR_RESULT=" + json.dumps({
        "score_before": report["critic_score_before"],
        "score_after": report["critic_score_after"],
        "parts_regenerated": applied,
        "decision": report["decision"],
        "deferred": report["layout_issues_deferred_to_planner"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
