"""Dump every part intersecting the region where the black notch appears."""
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
BLEND = Path(args[0])
bpy.ops.wm.open_mainfile(filepath=str(BLEND))

rows = []
for obj in bpy.data.objects:
    if obj.type != "MESH":
        continue
    bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(v[i] for v in bb) for i in range(3)))
    hi = Vector((max(v[i] for v in bb) for i in range(3)))
    # front-bottom of the arms: outboard, low, near the front
    if hi.z < 0.30 and abs((lo.x + hi.x) / 2) > 0.80 and lo.y < 0.0:
        mat = obj.material_slots[0].material if obj.material_slots else None
        rows.append((obj.name, lo, hi, mat.name if mat else "NONE"))

for name, lo, hi, mat in sorted(rows, key=lambda r: -r[2].z):
    print(f"NOTCH {name:28s} x[{lo.x:6.3f},{hi.x:6.3f}] y[{lo.y:6.3f},{hi.y:6.3f}] "
          f"z[{lo.z:6.3f},{hi.z:6.3f}] mat={mat}", flush=True)
print(f"TOTAL {len(rows)}", flush=True)
