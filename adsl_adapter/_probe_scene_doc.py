"""Read-only probe: dump everything a scene write-up needs to quote.

Opens the assembled living room and reports collections, object inventory,
polycount, materials, lights, cameras, world and render settings. No writes.
"""
import json
import sys
from collections import Counter, defaultdict

import bpy


def main():
    argv = sys.argv
    path = argv[argv.index("--") + 1] if "--" in argv else None
    if path:
        bpy.ops.wm.open_mainfile(filepath=path)

    sc = bpy.context.scene
    out = {}

    out["file"] = bpy.data.filepath
    out["scene_name"] = sc.name
    out["unit"] = {
        "system": sc.unit_settings.system,
        "scale_length": sc.unit_settings.scale_length,
        "length_unit": sc.unit_settings.length_unit,
    }

    # ---- collections -------------------------------------------------------
    def walk(coll, depth=0):
        rows = [{
            "name": coll.name,
            "depth": depth,
            "objects": len(coll.objects),
            "children": len(coll.children),
        }]
        for child in coll.children:
            rows.extend(walk(child, depth + 1))
        return rows

    out["collections"] = walk(sc.collection)

    # ---- objects -----------------------------------------------------------
    kinds = Counter()
    per_coll = defaultdict(Counter)
    tri_total = 0
    vert_total = 0
    per_object = []
    for obj in bpy.data.objects:
        kinds[obj.type] += 1
        cov = [c for c in obj.users_collection]
        cname = cov[0].name if cov else "?"
        per_coll[cname][obj.type] += 1
        if obj.type == "MESH":
            me = obj.data
            me.calc_loop_triangles()
            tri_total += len(me.loop_triangles)
            vert_total += len(me.vertices)
            per_object.append({
                "name": obj.name,
                "collection": cname,
                "object_id": obj.get("adsl_object_id"),
                "part": obj.get("adsl_part"),
                "verts": len(me.vertices),
                "tris": len(me.loop_triangles),
                "materials": [m.name for m in me.materials if m],
                "dim": [round(d, 3) for d in obj.dimensions],
            })
    out["object_types"] = dict(kinds)
    out["per_collection"] = {k: dict(v) for k, v in per_coll.items()}
    out["mesh_count"] = kinds.get("MESH", 0)
    out["verts_total"] = vert_total
    out["tris_total"] = tri_total
    out["objects"] = sorted(per_object, key=lambda r: -r["tris"])

    # per adsl object id roll-up
    by_group = defaultdict(lambda: {"meshes": 0, "tris": 0, "materials": set()})
    for r in per_object:
        g = r["object_id"] or "(none)"
        by_group[g]["meshes"] += 1
        by_group[g]["tris"] += r["tris"]
        by_group[g]["materials"].update(r["materials"])
    out["groups"] = {k: {"meshes": v["meshes"], "tris": v["tris"],
                         "materials": sorted(v["materials"])}
                     for k, v in by_group.items()}

    # ---- materials ---------------------------------------------------------
    mats = []
    for mat in bpy.data.materials:
        if not mat.users:
            continue
        row = {"name": mat.name, "users": mat.users, "blend_method": None,
               "nodes": len(mat.node_tree.nodes) if mat.use_nodes else 0}
        if mat.use_nodes:
            for n in mat.node_tree.nodes:
                if n.type == "BSDF_PRINCIPLED":
                    row["base_color"] = [round(c, 3) for c in n.inputs["Base Color"].default_value]
                    row["roughness"] = round(n.inputs["Roughness"].default_value, 3)
                    row["metallic"] = round(n.inputs["Metallic"].default_value, 3)
                    break
            row["has_image"] = any(n.type == "TEX_IMAGE" for n in mat.node_tree.nodes)
        mats.append(row)
    out["materials"] = mats

    # ---- lights ------------------------------------------------------------
    lights = []
    for o in bpy.data.objects:
        if o.type != "LIGHT":
            continue
        d = o.data
        lights.append({
            "name": o.name,
            "type": d.type,
            "energy": round(d.energy, 1),
            "size": round(getattr(d, "size", 0.0), 3),
            "size_y": round(getattr(d, "size_y", 0.0), 3),
            "color": [round(c, 3) for c in d.color],
            "location": [round(v, 3) for v in o.location],
        })
    out["lights"] = lights

    # ---- cameras -----------------------------------------------------------
    cams = []
    for o in bpy.data.objects:
        if o.type != "CAMERA":
            continue
        cams.append({
            "name": o.name,
            "lens_mm": round(o.data.lens, 1),
            "sensor_mm": round(o.data.sensor_width, 1),
            "location": [round(v, 3) for v in o.location],
            "active": o is sc.camera,
        })
    out["cameras"] = cams

    # ---- world -------------------------------------------------------------
    w = sc.world
    if w:
        bg = next((n for n in w.node_tree.nodes if n.type == "BACKGROUND"), None) if w.use_nodes else None
        out["world"] = {
            "name": w.name,
            "color": [round(c, 3) for c in bg.inputs[0].default_value] if bg else None,
            "strength": round(bg.inputs[1].default_value, 2) if bg else None,
        }

    # ---- render ------------------------------------------------------------
    ee = sc.eevee if hasattr(sc, "eevee") else None
    out["render"] = {
        "engine": sc.render.engine,
        "resolution": [sc.render.resolution_x, sc.render.resolution_y],
        "resolution_percent": sc.render.resolution_percentage,
        "samples": getattr(sc, "cycles").samples if sc.render.engine == "CYCLES" else (
            getattr(ee, "taa_render_samples", None) if ee else None),
        "film_transparent": sc.render.film_transparent,
        "file_format": sc.render.image_settings.file_format,
        "look": sc.view_settings.look if hasattr(sc.view_settings, "look") else None,
        "view_transform": sc.view_settings.view_transform,
    }

    # ---- scene bounds ------------------------------------------------------
    from mathutils import Vector
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for c in obj.bound_box:
            wc = obj.matrix_world @ Vector(c)
            lo = Vector((min(lo[i], wc[i]) for i in range(3)))
            hi = Vector((max(hi[i], wc[i]) for i in range(3)))
    out["bounds"] = {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi],
                     "size": [round(hi[i] - lo[i], 3) for i in range(3)]}

    out["textures"] = {
        "images": len(bpy.data.images),
        "packed": sum(1 for i in bpy.data.images if i.packed_file),
        "files": sorted({i.name for i in bpy.data.images})[:20],
    }

    print("SCENE_DOC=" + json.dumps(out, ensure_ascii=False))


main()
