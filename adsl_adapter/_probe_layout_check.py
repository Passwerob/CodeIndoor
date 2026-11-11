"""Measure the two things the user asked about, from geometry.

1. Where the door actually is on the south wall, where the sofa faces, and
   where the media console sits -- i.e. is the seating group aligned with the
   screen or with the doorway?
2. Is the bookshelf self-intersecting: do books overlap the carcass panels,
   and does anything poke outside the cabinet envelope?

Read-only. Prints DOOR=, AXIS=, SHELF= lines.
"""
import itertools
import json
import sys

import bpy
from mathutils import Vector


def world_bounds(obj):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for c in obj.bound_box:
        w = obj.matrix_world @ Vector(c)
        lo = Vector((min(lo[i], w[i]) for i in range(3)))
        hi = Vector((max(hi[i], w[i]) for i in range(3)))
    return lo, hi


def group(oid):
    out = []
    for o in bpy.data.objects:
        if o.type == "MESH" and o.get("adsl_object_id") == oid:
            lo, hi = world_bounds(o)
            out.append((o, lo, hi))
    return out


def union(items):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for _, a, b in items:
        lo = Vector((min(lo[i], a[i]) for i in range(3)))
        hi = Vector((max(hi[i], b[i]) for i in range(3)))
    return lo, hi


def main():
    argv = sys.argv
    path = argv[argv.index("--") + 1] if "--" in argv else None
    if path:
        bpy.ops.wm.open_mainfile(filepath=path)

    # ---- door / window / walls -------------------------------------------
    door = []
    window = []
    for o in bpy.data.objects:
        if o.get("adsl_object_id") != "room_shell":
            continue
        part = str(o.get("adsl_part", "")).lower()
        lo, hi = world_bounds(o)
        if part.startswith("door"):
            door.append({"part": part, "x": [round(lo.x, 3), round(hi.x, 3)],
                         "y": [round(lo.y, 3), round(hi.y, 3)],
                         "z": [round(lo.z, 3), round(hi.z, 3)]})
        elif part.startswith("window"):
            window.append({"part": part, "x": [round(lo.x, 3), round(hi.x, 3)],
                           "y": [round(lo.y, 3), round(hi.y, 3)],
                           "z": [round(lo.z, 3), round(hi.z, 3)]})
    print("DOOR=" + json.dumps(door, ensure_ascii=False))
    print("WINDOW=" + json.dumps(window, ensure_ascii=False))

    # ---- seating axis vs console axis ------------------------------------
    rows = {}
    for oid in ("sofa_3seater", "tv_cabinet", "coffee_table", "area_rug",
                "armchair_west", "armchair_east", "bookshelf", "floor_lamp",
                "wall_art_west", "plant_northwest", "side_table", "wall_tv",
                "ceiling_light", "window_curtain", "entry_door"):
        items = group(oid)
        if not items:
            continue
        lo, hi = union(items)
        rows[oid] = {"x": [round(lo.x, 3), round(hi.x, 3)],
                     "y": [round(lo.y, 3), round(hi.y, 3)],
                     "z": [round(lo.z, 3), round(hi.z, 3)],
                     "cx": round((lo.x + hi.x) / 2, 3),
                     "cy": round((lo.y + hi.y) / 2, 3)}
    print("AXIS=" + json.dumps(rows, ensure_ascii=False))

    # ---- bookshelf self-intersection -------------------------------------
    items = group("bookshelf")
    lo, hi = union(items)
    env = {"x": (lo.x, hi.x), "y": (lo.y, hi.y), "z": (lo.z, hi.z)}

    outside = []
    eps = 0.002
    for o, a, b in items:
        over = []
        for i, ax in enumerate("xyz"):
            if a[i] < env[ax][0] - eps:
                over.append(f"-{ax}{env[ax][0]-a[i]:.3f}")
            if b[i] > env[ax][1] + eps:
                over.append(f"+{ax}{b[i]-env[ax][1]:.3f}")
        if over:
            outside.append({"name": o.name, "over": over})

    # pair overlap volume, books only against non-books and vice versa
    def ov(a1, b1, a2, b2):
        d = [min(b1[i], b2[i]) - max(a1[i], a2[i]) for i in range(3)]
        if min(d) <= 0:
            return 0.0
        return d[0] * d[1] * d[2]

    hits = []
    for (o1, a1, b1), (o2, a2, b2) in itertools.combinations(items, 2):
        v = ov(a1, b1, a2, b2)
        if v > 1e-6:
            hits.append({"a": o1.name, "b": o2.name, "m3": round(v, 7),
                         "cm3": round(v * 1e6, 2)})
    hits.sort(key=lambda h: -h["m3"])

    names = [o.name for o, _, _ in items]
    books = [n for n in names if "book" in n.lower() or "magazine" in n.lower()
             or "catalogue" in n.lower()]

    print("SHELF=" + json.dumps({
        "parts": len(items),
        "bookish": len(books),
        "envelope": {k: [round(v[0], 3), round(v[1], 3)] for k, v in env.items()},
        "outside": outside[:20],
        "outside_count": len(outside),
        "overlaps": hits[:25],
        "overlap_count": len(hits),
        "worst_cm3": hits[0]["cm3"] if hits else 0,
    }, ensure_ascii=False))


main()
