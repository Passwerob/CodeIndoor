"""List every part of a forged asset with its material's base luminance."""
import sys
from pathlib import Path

import bpy

BLEND = Path(sys.argv[sys.argv.index("--") + 1])
bpy.ops.wm.open_mainfile(filepath=str(BLEND))

for obj in sorted([o for o in bpy.data.objects if o.type == "MESH"],
                  key=lambda o: o.name):
    cols = []
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or not mat.use_nodes:
            cols.append("NO-MAT")
            continue
        bsdf = next((n for n in mat.node_tree.nodes
                     if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            cols.append(f"{mat.name}:NO-BSDF")
            continue
        c = bsdf.inputs["Base Color"].default_value
        lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        flag = " DARK" if lum < 0.06 else ""
        cols.append(f"{mat.name}:{lum:.3f}{flag}")
    print(f"PART {obj.name:26s} {', '.join(cols)}", flush=True)
