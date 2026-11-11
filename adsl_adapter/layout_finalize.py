"""Turn the agent's layout into the file every downstream stage consumes.

This replaces clearance_solver.py, which computed poses: it snapped the bookshelf
to the wall, aimed the armchairs at the screen, hung the TV above the cabinet and
moved the door leaf. Every one of those was a *design decision* taken after the
agent had already decided -- a second designer, and the reason the pipeline never
closed: the gate judged a scene nobody had planned.

What legitimately belongs here is derivation. A door leaf is not a design choice,
it is the opening plus a hinge offset; a supply plan is not a choice either, it is
the decided inventory resolved against the asset library. So this module only:

    * appends the door leaf, whose pose follows from the opening the planner chose;
    * resolves each object's supply mode (via asset_supply) and its measured size;
    * RE-DERIVES the yaw of every backed floor object from its wall (see
      `derive_against_wall_yaw`) so a reversed "bookshelf facing the wall" defect
      can never survive into the deliverable -- the orientation is geometry, not a
      design choice, and is therefore computed here rather than copied;
    * copies every other pose component through untouched, and fails if any is
      missing.

    python adsl_adapter/layout_finalize.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# scene_layout is pure Python (no bpy import), so it is safe to reuse the same
# derivation + validation the planner uses.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scene_layout import (OBJECT_LIBRARY, load_assets, validate_layout,  # noqa: E402
                          yaw_for_normal)  # noqa: E402

ROOT = Path(r"E:/3DUse/SceneCode_audit")
LAYOUT_DIR = ROOT / "runtime/outputs/scene_layout"
LAYOUT = LAYOUT_DIR / "layout.json"
ROOM = LAYOUT_DIR / "room_measured.json"
ASSETS = LAYOUT_DIR / "assets_measured.json"
OUT = LAYOUT_DIR / "layout_finalized.json"

# The leaf asset is built to its own size; the opening is the authority, so a
# mismatch is reported rather than silently scaled away.
REVEAL_TOL = 0.06


def door_leaf(room: dict, leaf_size: list[float]) -> dict:
    """Pose of a closed leaf inside the opening the planner chose.

    Root at floor level, centred on the opening, pushed back to the middle of the
    wall so the reveal reads on both sides; front face turned into the room.
    """
    d = room["openings"]["door"]
    wall = room["walls"][d["wall"]]
    wall_id = d["wall"]
    thickness = room["wall_thickness"]
    nx, ny = wall["normal"]
    pose = {"x": round(d["center_x"], 4), "y": round(wall["plane"] - ny * thickness / 2, 4),
            "z": 0.0, "yaw_deg": 0.0}
    if nx:                      # door on a side wall: the leaf is pushed along x
        pose["x"] = round(wall["plane"] - nx * thickness / 2, 4)
        pose["y"] = round(d["center_x"], 4)
        yaw = 90.0 if nx > 0 else -90.0
    else:
        yaw = 180.0 if ny > 0 else 0.0
    pose["yaw_deg"] = yaw
    return {
        "id": "entry_door",
        "stage": "floor_plan",
        "derived": True,
        "derived_from": f"opening:{wall_id}",
        "placement": pose,
        "asset_item": {"dimensions": leaf_size, "object_type": "DOOR",
                       "short_name": "EntryDoor",
                       "description": "closed interior door leaf filling the opening"},
        "relations": [],
        "intent": "closed leaf in the opening the floor-plan stage placed",
        "mount": {"surface": "opening", "wall": wall_id},
    }


def derive_against_wall_yaw(objects: list, room: dict) -> list:
    """Recompute the yaw of every backed floor object from the wall it backs onto.

    A floor object declared ``against_wall:<wall_id>`` must present its back
    (local +Y in the asset frame) to the wall, so its front (local -Y) opens into
    the room. That yaw is a consequence of the wall's inward normal, not a design
    decision the planner is entitled to, so it is re-derived here from the same
    numbers the planner uses (``scene_layout.yaw_for_normal``). This is the durable
    guard against the 180 deg "reversed bookshelf" defect: no matter how stale or
    warm-started the layout seed is, the delivered yaw is always back-to-wall.

    Returns the list of object ids whose yaw actually changed.
    """
    changed = []
    for o in objects:
        rels = o.get("relations") or []
        wall_id = next((r.split(":", 1)[1] for r in rels
                        if r.startswith("against_wall:")), None)
        if not wall_id:
            continue
        wall = room["walls"].get(wall_id)
        if not wall:
            raise SystemExit(f"{o['id']} declares unknown wall {wall_id}")
        placement = o.setdefault("placement", {})
        derived = yaw_for_normal(wall["normal"])
        if abs(float(placement.get("yaw_deg", 0)) - derived) > 0.5:
            changed.append(o["id"])
        placement["yaw_deg"] = derived
        placement["z"] = 0.0
    return changed


def main() -> int:
    payload = json.loads(LAYOUT.read_text(encoding="utf-8"))
    room = json.loads(ROOM.read_text(encoding="utf-8"))
    measured = json.loads(ASSETS.read_text(encoding="utf-8"))["assets"]
    layout = payload["layout"]

    objects = list(layout["objects"])
    leaf_size = measured.get("entry_door", {}).get("size", [1.02, 0.12, 2.075])
    leaf = door_leaf(room, leaf_size)
    objects = [o for o in objects if o["id"] != "entry_door"] + [leaf]

    # Every object must carry a pose; a stage that "could not place" something
    # would otherwise disappear quietly and be re-invented downstream.
    missing = [o["id"] for o in objects if not o.get("placement")]
    if missing:
        raise SystemExit(f"layout has objects without a pose: {missing}")

    d = room["openings"]["door"]
    leaf_w = leaf_size[0]
    reveal = (d["width"] - leaf_w) / 2
    if not (0.0 <= reveal <= REVEAL_TOL):
        raise SystemExit(
            f"door leaf {leaf_w:.3f} m does not fit a {d['width']:.3f} m opening "
            f"({reveal*1000:+.0f} mm reveal); regenerate entry_door at the right width")

    # Re-derive the orientation of every backed floor object from its wall. This
    # is the guard that makes a reversed bookshelf impossible to ship, even from
    # a stale or warm-started layout seed. (tv_cabinet etc. keep their correct
    # yaw because yaw_for_normal reproduces the same value.)
    library = load_assets({o["id"]: o for o in OBJECT_LIBRARY})
    # The door leaf (and any other object the planner did not inventory) is not in
    # OBJECT_LIBRARY; give it a neutral, non-floor spec so validate_layout can run
    # without tripping on it. It is derived from the opening and exempt from the
    # floor constraints by construction.
    for o in objects:
        if o["id"] not in library:
            library[o["id"]] = {
                "mount": "opening",
                "size": (o.get("asset_item") or {}).get("dimensions") or [0.1, 0.1, 0.1],
                "local_z": [0.0, 0.1],
            }
    changed = derive_against_wall_yaw(objects, room)
    if changed:
        print("[finalize] re-derived back-to-wall yaw for:", changed)

    out = {
        "schema": "scenecode.layout.finalized.v1",
        "source_layout": str(LAYOUT),
        "room": room,
        "room_plan": payload.get("room_plan"),
        "validation": payload.get("validation"),
        "critic": payload.get("critic"),
        "objects": objects,
        "note": "against-wall floor yaw is re-derived here (back-to-wall); other "
                "pose components are authoritative and unchanged",
    }

    # Post-condition gate: re-run every constraint against the finished layout.
    # A reversed against-wall object is a HARD failure -- it should have just been
    # derived away -- so shipping one is impossible. Any other issue is reported
    # but tolerated, matching the existing pipeline's advisory treatment.
    report = validate_layout(out, library, room)
    if not report["passed"]:
        print("[finalize] CONSTRAINT ISSUES (non-blocking unless reversed wall):")
        for i in report["issues"]:
            print("   ", i)
        if any(i["id"] == "wall.facing" for i in report["issues"]):
            raise SystemExit("layout still has a reversed against-wall object "
                             "after re-derivation -- aborting")

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[finalize] objects=", len(objects),
          "door leaf reveal=", f"{reveal*1000:.0f} mm",
          "derived=", [o["id"] for o in objects if o.get("derived")])
    print("[finalize] wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
