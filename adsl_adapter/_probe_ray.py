"""Raycast into the notch and report exactly which surface is visible there."""
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.open_mainfile(filepath=str(Path(args[0])))
deps = bpy.context.evaluated_depsgraph_get()
scene = bpy.context.scene

spots = [
    ("notch_centrei", Vector((-1.00, -2.0, 0.240))),
    ("notch_left", Vector((-1.06, -2.0, 0.240))),
    ("above_white", Vector((-1.00, -2.0, 0.360))),
    ("carcass_grey", Vector((-0.60, -2.0, 0.240))),
]
for label, origin in spots:
    hit, loc, normal, index, obj, matrix = scene.ray_cast(deps, origin, Vector((0, 1, 0)))
    if not hit:
        print(f"RAY {label:14s} MISS", flush=True)
        continue
    mat = obj.material_slots[0].material if obj.material_slots else None
    print(f"RAY {label:14s} obj={obj.name!r} mat={mat.name if mat else 'NONE'} "
          f"hit_y={loc.y:.4f} normal={tuple(round(v, 2) for v in normal)}", flush=True)

# Who owns faces around there at all?
for label, origin in spots:
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        ys = [v.y for v in bb]
        xs = [v.x for v in bb]
        zs = [v.z for v in bb]
        if min(xs) <= origin.x <= max(xs) and min(zs) <= origin.z <= max(zs):
            print(f"  CAND {label:12s} {obj.name:24s} y[{min(ys):.3f},{max(ys):.3f}] "
                  f"x[{min(xs):.3f},{max(xs):.3f}] z[{min(zs):.3f},{max(zs):.3f}]", flush=True)
