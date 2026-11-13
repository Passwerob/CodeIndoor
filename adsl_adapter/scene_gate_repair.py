"""Route the visual gate's findings back to the stage that owns them.

The gate already names what is wrong, which components are involved and which
stage should fix it (`repair_target`), but nothing used to read that field: the
findings were flattened into prose in the assembly report and the run stopped
there. That is the last break in the loop -- a judge with no authority to send
anything back.

This module is the return path, and it deliberately does very little:

  * `layout` findings go to the Designer, one object at a time, with the other
    component ids frozen. A change is committed only if every measurable rule in
    the finished room still passes afterwards, so a repair cannot quietly trade
    one violation for another.
  * every other target is reported as a work order naming the owning stage. No
    stage invents a repair for a target it does not own.

    python adsl_adapter/scene_gate_repair.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scene_layout import (LAYOUT_PATH, ROOM_PATH, expect, load_assets,  # noqa: E402
                          load_room, store, design_pose, OBJECT_LIBRARY)

ROOT = Path(r"E:/3DUse/New/CodeIndoor")
LAYOUT_DIR = ROOT / "runtime/outputs/scene_layout"
QA = ROOT / "runtime/outputs/living_room/assets/scene_visual_qa.json"
OUT = LAYOUT_DIR / "layout_gate_repaired.json"
REPORT = LAYOUT_DIR / "GATE_ROUTING.json"

# Which stage owns which repair target. Anything absent is not auto-repaired.
OWNER = {
    "layout": "scene_layout (Designer) -- handled here",
    "decomposition": "scene_layout (floor_plan inventory) -- rerun scene_layout",
    "geometry": "furniture_forge -- rerun the builder for the named object",
    "source": "asset library -- the object needs to be re-sourced",
    "materials": "asset library -- material rebuild, not a pose change",
    "camera": "render rig -- no scene change",
}


def placed_from_layout(layout: dict, library: dict) -> dict:
    placed = {}
    for o in layout.get("objects") or []:
        spec = library[o["id"]]
        p = dict(o["placement"])
        p["size"] = spec["size"]
        p["_relations"] = list(o.get("relations") or [])
        p["_exempt"] = o["id"] == "area_rug" or spec.get("mount") != "floor"
        store(placed, o["id"], p, spec, p["_relations"], o.get("reason", ""))
    return placed


def room_is_clean(placed: dict, library: dict, room: dict) -> tuple[bool, list]:
    issues = []
    for oid, p in placed.items():
        for i in expect(oid, p, placed, library[oid], room, p.get("_relations") or []):
            issues.append({"object": oid, **i})
    return not issues, issues


def main() -> int:
    payload = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    layout = payload["layout"]
    room = load_room()
    library = load_assets({o["id"]: o for o in OBJECT_LIBRARY})
    qa = json.loads(QA.read_text(encoding="utf-8"))
    findings = (qa.get("decision") or {}).get("findings") or []

    routes: dict[str, list] = {}
    for f in findings:
        target = f.get("repair_target") or "decomposition"
        routes.setdefault(target, []).append(f)

    print(f"[gate-repair] findings={len(findings)} targets="
          f"{ {k: len(v) for k, v in routes.items()} }", flush=True)

    placed = placed_from_layout(layout, library)
    clean_before, issues_before = room_is_clean(placed, library, room)
    print(f"[gate-repair] deterministic rules clean before repair={clean_before} "
          f"({len(issues_before)} issues)", flush=True)

    # Evidence per object, in the gate's own words. Only `layout` findings carry
    # a pose correction; everything else is somebody else's work order.
    evidence: dict[str, list[str]] = {}
    for f in routes.get("layout", []):
        text = str(f.get("evidence") or f.get("note") or "")[:220]
        fix = str(f.get("correction") or "")[:160]
        for cid in f.get("component_ids") or []:
            if cid in placed:
                evidence.setdefault(cid, []).append(text + (f" -> FIX: {fix}" if fix else ""))

    intents = {o["id"]: o.get("intent", "") for o in layout.get("objects") or []}
    applied, rejected = [], []
    for oid, notes in evidence.items():
        spec = library[oid]
        if spec.get("fixed"):
            continue
        rel = placed[oid].get("_relations") or []
        others = {k: v for k, v in placed.items() if k != oid}
        item = {"id": oid, "stage": "furniture", "relations": rel,
                "mount": {"surface": spec.get("mount")},
                "intent": intents.get(oid, "") + " | VISUAL EVIDENCE: " + " | ".join(notes)}
        pose, reason, attempts, problems = design_pose(
            oid, spec, item, rel, others, room, attempts=3)
        if not pose:
            rejected.append({"id": oid, "why": "Designer could not satisfy the "
                                                "rules with this evidence",
                             "violations": problems})
            continue
        trial = dict(placed)
        store(trial, oid, pose, spec, rel, reason)
        ok, trial_issues = room_is_clean(trial, library, room)
        if not ok:
            # Atomic commit: a pose that fixes one thing and breaks another is
            # not a repair, and the gate would only report it next render.
            rejected.append({"id": oid, "why": "would break other rules",
                             "violations": trial_issues})
            continue
        before = {k: placed[oid][k] for k in ("x", "y", "yaw_deg")}
        placed = trial
        applied.append({"id": oid, "before": before,
                        "after": {k: placed[oid][k] for k in ("x", "y", "z", "yaw_deg")},
                        "evidence": notes, "attempts": attempts})

    clean_after, issues_after = room_is_clean(placed, library, room)

    # Every non-layout target becomes a named work order, never a silent drop.
    work_orders = []
    for target, items in routes.items():
        if target == "layout":
            continue
        work_orders.append({
            "stage": target,
            "owner": OWNER.get(target, "unowned -- needs a human decision"),
            "findings": [{"component_ids": f.get("component_ids"),
                          "evidence": str(f.get("evidence") or "")[:220],
                          "correction": str(f.get("correction") or "")[:160]}
                         for f in items],
        })

    report = {
        "schema": "scenecode.gate_routing.v1",
        "qa_source": str(QA),
        "findings": len(findings),
        "by_target": {k: len(v) for k, v in routes.items()},
        "deterministic_clean_before": clean_before,
        "deterministic_issues_before": issues_before,
        "deterministic_clean_after": clean_after,
        "deterministic_issues_after": issues_after,
        "applied": applied,
        "rejected": rejected,
        "work_orders": work_orders,
        "requires_rerender": bool(applied),
        "note": ("poses are authoritative; this file records what the gate asked "
                 "for and what the Designer agreed to change"),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if applied:
        objects = []
        for o in layout["objects"]:
            p = placed[o["id"]]
            objects.append({**o, "placement": {k: round(p[k], 4)
                                               for k in ("x", "y", "z", "yaw_deg")}})
        payload["layout"] = {**layout, "objects": objects}
        payload["gate_repair"] = {"applied": [a["id"] for a in applied],
                                  "report": str(REPORT)}
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[gate-repair] applied {[a['id'] for a in applied]} -> {OUT}", flush=True)

    print(f"[gate-repair] applied={len(applied)} rejected={len(rejected)} "
          f"work_orders={[w['stage'] for w in work_orders]}", flush=True)
    print("GATE_ROUTING=" + json.dumps({k: report[k] for k in
                                        ("by_target", "deterministic_clean_after",
                                         "requires_rerender")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
