"""Print the exact boxes behind the book/side-panel overlap."""
import sys

import bpy
from mathutils import Vector


def bb(o):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        lo = Vector((min(lo[i], w[i]) for i in range(3)))
        hi = Vector((max(hi[i], w[i]) for i in range(3)))
    return lo, hi


path = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.open_mainfile(filepath=path)

want = ["side_-1", "side_1", "top_panel", "bottom_panel"]
rows = []
for o in bpy.data.objects:
    if o.type != "MESH" or o.get("adsl_object_id") != "bookshelf":
        continue
    if "books_" in o.name:
        continue
    lo, hi = bb(o)
    rows.append((o.name, lo, hi, (hi.y - lo.y), (hi.z - lo.z)))
for name, lo, hi, lw, lh in sorted(rows, key=lambda r: r[3]):
    print(f"PANEL {name:22s} y=[{lo.y:.4f},{hi.y:.4f}] x=[{lo.x:.4f},{hi.x:.4f}] "
          f"z=[{lo.z:.4f},{hi.z:.4f}] len={lw:.3f} thick={lh:.3f}")

first = []
for o in bpy.data.objects:
    if o.type == "MESH" and "bookshelf_books_00" in o.name:
        lo, hi = bb(o)
        first.append((lo.y, o.name, lo, hi))
first.sort()
for y, name, lo, hi in first[:3]:
    print(f"BOOK  {name.split('/')[-1][:34]:34s} y=[{lo.y:.4f},{hi.y:.4f}] "
          f"x=[{lo.x:.4f},{hi.x:.4f}] z=[{lo.z:.4f},{hi.z:.4f}]")
