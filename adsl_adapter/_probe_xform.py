"""Dissect the transform chain of one object group inside a saved blend."""
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
BLEND, OID = Path(args[0]), args[1]
bpy.ops.wm.open_mainfile(filepath=str(BLEND))

for obj in bpy.data.objects:
    if str(obj.get("adsl_object_id", "")) != OID:
        continue
    bb = [Vector(c) for c in obj.bound_box]
    lo = Vector((min(v[i] for v in bb) for i in range(3)))
    hi = Vector((max(v[i] for v in bb) for i in range(3)))
    mw = obj.matrix_world
    par = obj.parent
    print(f"OBJ {obj.name!r} type={obj.type} parent={par.name if par else None}", flush=True)
    print(f"    loc={tuple(round(v,3) for v in obj.location)} "
          f"scale={tuple(round(v,3) for v in obj.scale)} "
          f"parent_inverse_t={tuple(round(v,3) for v in obj.matrix_parent_inverse.translation)}",
          flush=True)
    if par:
        print(f"    parent_loc={tuple(round(v,3) for v in par.location)} "
              f"parent_parent={par.parent.name if par.parent else None}", flush=True)
    print(f"    world_t={tuple(round(v,3) for v in mw.translation)} "
          f"localbbox=[{lo.x:.3f},{hi.x:.3f}]", flush=True)
    wl = mw @ lo
    wh = mw @ hi
    print(f"    world_bbox=[{wl.x:.3f},{wh.x:.3f}] [{wl.y:.3f},{wh.y:.3f}]", flush=True)
