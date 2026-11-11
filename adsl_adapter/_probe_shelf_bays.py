"""Per-bay geometry of the book run: intervals along the shelf length and
depth, so interpenetration can be read as numbers rather than guessed.

The shelf is rotated 90 deg, so world Y runs along the shelves and world X is
the depth into the case.
"""
import json
import sys

import bpy
from mathutils import Vector


def bounds(o):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        lo = Vector((min(lo[i], w[i]) for i in range(3)))
        hi = Vector((max(hi[i], w[i]) for i in range(3)))
    return lo, hi


def main():
    argv = sys.argv
    path = argv[argv.index("--") + 1] if "--" in argv else None
    if path:
        bpy.ops.wm.open_mainfile(filepath=path)

    books = []
    for o in bpy.data.objects:
        if o.type == "MESH" and o.get("adsl_object_id") == "bookshelf" \
                and "books_" in o.name:
            lo, hi = bounds(o)
            bay = o.name.split("books_")[1][:2]
            books.append({"name": o.name.split("/")[-1], "bay": bay,
                          "y": [round(lo.y, 4), round(hi.y, 4)],
                          "x": [round(lo.x, 4), round(hi.x, 4)],
                          "z": [round(lo.z, 4), round(hi.z, 4)],
                          "w": round(hi.y - lo.y, 4),
                          "d": round(hi.x - lo.x, 4),
                          "h": round(hi.z - lo.z, 4)})

    bays = {}
    for b in books:
        bays.setdefault(b["bay"], []).append(b)

    report = {}
    for bay, items in sorted(bays.items()):
        items.sort(key=lambda b: b["y"][0])
        overlaps = []
        for a, b in zip(items, items[1:]):
            ov = a["y"][1] - b["y"][0]
            if ov > 0.002:
                overlaps.append({"a": a["name"], "b": b["name"],
                                 "overlap_mm": round(ov * 1000, 1)})
        span = [items[0]["y"][0], max(i["y"][1] for i in items)]
        depths = sorted({round(i["d"], 3) for i in items})
        report[bay] = {
            "count": len(items),
            "span_y": [round(span[0], 3), round(span[1], 3)],
            "run_len": round(span[1] - span[0], 3),
            "widths": [i["w"] for i in items[:8]],
            "depths": depths[:8],
            "z_range": [min(i["z"][0] for i in items), max(i["z"][1] for i in items)],
            "overlaps": overlaps[:8],
            "overlap_count": len(overlaps),
            "worst_mm": max([o["overlap_mm"] for o in overlaps], default=0),
        }
    print("BAYS=" + json.dumps(report, ensure_ascii=False))


main()
