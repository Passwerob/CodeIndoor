"""Measure every placed asset's LOCAL bounding box and publish it as a manifest.

The hand-typed size table in the planner had three incompatible conventions in
one list: `bookshelf` was local (w, d), `wall_art` was already rotated, and
`armchair` was an envelope 8 cm larger than the mesh. Overlap and wall-contact
tests silently inherited those errors -- the delivered scene had the west
armchair's corner 11 x 23 cm inside the sofa and no rule could see it, because
both boxes were inflated/deflated in different directions.

This measures, per object root, the bounds in the ROOT's own frame: that is what
a pose transform actually rotates, so footprint maths becomes exact.

    blender -b --python adsl_adapter/measure_assets.py
"""
from __future__ import annotations

import json
from pathlib import Path

import bpy

ROOT = Path(r"E:/3DUse/SceneCode_audit")
BLEND = ROOT / "runtime/outputs/living_room/assets/living_room_assets.blend"
OUT = ROOT / "runtime/outputs/scene_layout/assets_measured.json"


def measure() -> dict:
    bpy.ops.wm.open_mainfile(filepath=str(BLEND))
    roots = {o.name: o for o in bpy.data.objects
             if o.type == "EMPTY" and o.get("adsl_object_id")}
    assets: dict[str, dict] = {}
    for name, root in roots.items():
        oid = str(root.get("adsl_object_id"))
        inv = root.matrix_world.inverted()
        lo = [1e9] * 3
        hi = [-1e9] * 3
        parts = 0
        for obj in bpy.data.objects:
            if obj.type != "MESH" or obj.parent is not root:
                continue
            parts += 1
            for v in obj.data.vertices:
                local = inv @ (obj.matrix_world @ v.co)
                for i in range(3):
                    lo[i] = min(lo[i], local[i])
                    hi[i] = max(hi[i], local[i])
        if not parts:
            continue
        assets[oid] = {
            "root": name,
            "root_world": [round(v, 4) for v in root.matrix_world.translation],
            "parts": parts,
            "local_min": [round(v, 4) for v in lo],
            "local_max": [round(v, 4) for v in hi],
            "size": [round(hi[i] - lo[i], 4) for i in range(3)],
            "local_z": [round(lo[2], 4), round(hi[2], 4)],
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"schema": "scenecode.assets.v1",
                               "source": str(BLEND), "assets": assets},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    for oid, a in sorted(assets.items()):
        print(f"ASSET {oid:16s} size={a['size']} local_z={a['local_z']} parts={a['parts']}")
    print("ASSETS=" + json.dumps({"count": len(assets), "out": str(OUT)}))
    return assets


if __name__ == "__main__":
    measure()
