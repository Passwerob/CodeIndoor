"""Per-object bounds plus a full-traceback dry run of one export."""
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(r"E:/3DUse/New/CodeIndoor")
sys.path.insert(0, str(ROOT / "adsl_adapter"))
SRC = ROOT / "runtime/outputs/living_room/assets/living_room_assets.blend"

bpy.ops.wm.open_mainfile(filepath=str(SRC))


def bounds(oid):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    n = 0
    for obj in bpy.data.objects:
        if obj.type != "MESH" or str(obj.get("adsl_object_id", "")) != oid:
            continue
        n += 1
        for c in obj.bound_box:
            w = obj.matrix_world @ Vector(c)
            lo = Vector((min(lo[i], w[i]) for i in range(3)))
            hi = Vector((max(hi[i], w[i]) for i in range(3)))
    return lo, hi, n


for oid in ("plant_northwest", "wall_art_west", "tv_cabinet", "coffee_table"):
    lo, hi, n = bounds(oid)
    print(f"BOUNDS {oid:18s} n={n:3d} x[{lo.x:6.2f},{hi.x:6.2f}] "
          f"y[{lo.y:6.2f},{hi.y:6.2f}] z[{lo.z:5.2f},{hi.z:5.2f}]", flush=True)

import export_asset_library as lib  # noqa: E402

entry = {"id": "coffee_table", "mode": "retrieved"}
try:
    row = lib.export_one("coffee_table", entry)
    print("EXPORT_ONE_OK", row, flush=True)
except Exception:
    print("EXPORT_ONE_FAILED", flush=True)
    traceback.print_exc()
