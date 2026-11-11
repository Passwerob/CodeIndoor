"""Export every placed object as a standalone, TRELLIS-ready asset package.

Why each object needs its own package
-------------------------------------
The room render proves the assembly works, but the geometry is trapped inside
one scene file at one pose. Downstream single-image/multi-view reconstruction
(TRELLIS) needs the opposite: one object, canonical pose, metres, origin at the
bottom centre, clean views on a neutral background, and a glTF that carries the
same material set the render used.

Output per object:

    runtime/outputs/library/<id>/
        <id>.glb            mesh + PBR materials, Z-up, metres, front = -Y
        <id>.blend          the isolated object, same convention
        views/front.png ... 4 orthographic-ish views on a neutral backdrop
        meta.json           dimensions, convention, source, provenance hashes

Run inside Blender:
    blender.exe -b --python adsl_adapter/export_asset_library.py
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(r"E:/3DUse/SceneCode_audit")
SRC_BLEND = ROOT / "runtime/outputs/living_room/assets/living_room_assets.blend"
SUPPLY = ROOT / "runtime/outputs/scene_layout/supply_plan.json"
FORGE = ROOT / "runtime/outputs/forge"
LIB = ROOT / "runtime/outputs/library"

VIEWS = [
    ("front", (0.0, -1.0)),
    ("three_quarter", (-0.85, -0.85)),
    ("side", (1.0, 0.0)),
    ("back", (0.0, 1.0)),
]


def purge_scene(exceptions=()) -> None:
    for obj in list(bpy.data.objects):
        if obj in exceptions:
            continue
        bpy.data.objects.remove(obj, do_unlink=True)


def keep_object_set(oid: str) -> list:
    """Meshes belonging to `oid` plus the empties that hold their hierarchy."""
    meshes = [o for o in bpy.data.objects
              if o.type == "MESH" and str(o.get("adsl_object_id", "")) == oid]
    roots = []
    for obj in meshes:
        parent = obj.parent
        while parent is not None:
            if parent not in roots:
                roots.append(parent)
            parent = parent.parent
    return meshes + roots


def world_bounds(objects: list) -> tuple[Vector, Vector]:
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    found = False
    for obj in objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            lo = Vector((min(lo[i], w[i]) for i in range(3)))
            hi = Vector((max(hi[i], w[i]) for i in range(3)))
            found = True
    if not found:
        raise RuntimeError("object set has no mesh geometry")
    return lo, hi


def normalise(objects: list) -> dict:
    """Move the set so its footprint centre is on Z and its base sits at z=0.

    Canonical pose is what makes the package usable: two assets can be compared,
    swapped, or fed to a reconstruction model only if the origin means the same
    thing for both.
    """
    lo, hi = world_bounds(objects)
    offset = Vector((-(lo.x + hi.x) * 0.5, -(lo.y + hi.y) * 0.5, -lo.z))
    for obj in objects:
        if obj.parent is None:
            obj.location = obj.location + offset
    bpy.context.view_layer.update()
    lo, hi = world_bounds(objects)
    return {"min": [round(v, 4) for v in lo], "max": [round(v, 4) for v in hi],
            "size": [round(hi[i] - lo[i], 4) for i in range(3)]}


def studio(size: Vector) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 40
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 640
    scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = 0.1

    world = bpy.data.worlds.new("AssetWorld")
    world.use_nodes = True
    # Blender 5.2 no longer guarantees a node literally named "Background".
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg is not None:
        bg.inputs[0].default_value = (0.82, 0.82, 0.82, 1.0)
        bg.inputs[1].default_value = 1.0
    scene.world = world

    biggest = max(size)
    span = max(biggest, 0.25)

    # ground: a large neutral plane so the object casts a contact shadow and
    # does not look like it floats in a void
    bpy.ops.mesh.primitive_plane_add(size=span * 12, location=(0, 0, -0.002))
    ground = bpy.context.active_object
    ground.name = "AssetGround"
    mat = bpy.data.materials.new("AssetGroundMat")
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.78, 0.78, 0.78, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.85
    ground.data.materials.append(mat)

    for name, loc, energy, area in (
        ("Key", (span * 1.8, -span * 2.0, span * 2.4), 900, span * 2.2),
        ("Fill", (-span * 2.2, -span * 1.4, span * 1.2), 320, span * 2.6),
        ("Rim", (span * 0.6, span * 2.4, span * 2.0), 420, span * 2.0),
    ):
        data = bpy.data.lights.new(name, "AREA")
        data.energy = energy * max(1.0, span)
        data.size = area
        light = bpy.data.objects.new(name, data)
        light.location = loc
        direction = Vector((0, 0, size.z * 0.45)) - Vector(loc)
        light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        scene.collection.objects.link(light)


def camera_for(name: str, direction: tuple, size: Vector):
    scene = bpy.context.scene
    data = bpy.data.cameras.new("Cam_" + name)
    data.lens = 70
    cam = bpy.data.objects.new("Cam_" + name, data)
    scene.collection.objects.link(cam)

    span = max(size)
    target = Vector((0, 0, size.z * 0.48))
    # Frame the object's diagonal, not just its height: a wide sofa seen from
    # the side would otherwise be cropped.
    radius = (size.length * 0.5) if size.length else 0.4
    dist = radius / math.tan(math.radians(14.0)) * 1.06
    loc = target + Vector((direction[0], direction[1], 0.34)).normalized() * dist
    loc.z = target.z + span * 0.42
    cam.location = loc
    cam.rotation_euler = (target - loc).to_track_quat("-Z", "Y").to_euler()
    return cam


def read_provenance(oid: str, entry: dict) -> dict:
    prov = {"mode": entry.get("mode"), "asset": entry.get("asset")}
    result = FORGE / f"{oid}_result.json"
    if result.is_file():
        try:
            payload = json.loads(result.read_text(encoding="utf-8"))
            last = [e for e in payload.get("log", []) if e.get("code_sha")]
            if last:
                tail = last[-1]
                prov.update({
                    "source": "agent-authored program over FineScene Builder DSL",
                    "program": f"runtime/outputs/forge/{oid}/program.py",
                    "code_sha": tail.get("code_sha"),
                    "llm_model": tail.get("llm_model"),
                    "llm_resp_sha": tail.get("llm_resp_sha"),
                    "llm_usage": tail.get("llm_usage"),
                    "attempt": tail.get("attempt"),
                })
        except Exception:  # noqa: BLE001
            pass
    if prov.get("mode") == "retrieved":
        prov["source"] = "CC0 scanned asset (Poly Haven / Poly Pizza) with fitted sizing"
    elif prov.get("mode") == "procedural":
        prov["source"] = "procedural geometry with generated PBR material"
    return prov


def export_one(oid: str, entry: dict) -> dict:
    bpy.ops.wm.open_mainfile(filepath=str(SRC_BLEND))
    keep = keep_object_set(oid)
    if not keep:
        raise RuntimeError(f"no objects for {oid} in the assembled scene")
    keep_set = set(keep)
    purge_scene(exceptions=keep_set)

    # Strip anything that is not geometry or an empty: lights and cameras from
    # the room scene would otherwise travel into the asset blend.
    for obj in list(bpy.data.objects):
        if obj.type not in {"MESH", "EMPTY"}:
            bpy.data.objects.remove(obj, do_unlink=True)

    bounds = normalise([o for o in bpy.data.objects])
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    size = Vector(bounds["size"])

    out = LIB / oid
    views_dir = out / "views"
    if out.exists():
        shutil.rmtree(out)
    views_dir.mkdir(parents=True, exist_ok=True)

    studio(size)
    scene = bpy.context.scene
    written = []
    for name, direction in VIEWS:
        cam = camera_for(name, direction, size)
        scene.camera = cam
        scene.render.filepath = str(views_dir / f"{name}.png")
        bpy.ops.render.render(write_still=True)
        written.append(f"views/{name}.png")

    for cam in [o for o in bpy.data.objects if o.type == "CAMERA"]:
        bpy.data.objects.remove(cam, do_unlink=True)
    for ground in [o for o in bpy.data.objects if o.name == "AssetGround"]:
        bpy.data.objects.remove(ground, do_unlink=True)

    blend_path = out / f"{oid}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    glb_path = out / f"{oid}.glb"
    for obj in bpy.data.objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0] if meshes else None
    try:
        bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB",
                                  use_selection=True, export_apply=True,
                                  export_yup=True)
    except TypeError:
        bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB",
                                  use_selection=True)

    meta = {
        "id": oid,
        "kind": entry.get("mode"),
        "label": entry.get("brief") or oid,
        "units": "metre",
        "up_axis": "Z in .blend, Y in .glb (glTF convention, exported with export_yup)",
        "forward_axis": "-Y in .blend",
        "origin": "bottom centre of the bounding box, object standing on z=0",
        "dimensions_m": {"width": bounds["size"][0], "depth": bounds["size"][1],
                         "height": bounds["size"][2]},
        "bounds_world": {"min": bounds["min"], "max": bounds["max"]},
        "mesh_count": len(meshes),
        "vertex_count": int(sum(len(o.data.vertices) for o in meshes)),
        "materials": sorted({m.name for o in meshes for m in o.data.materials if m}),
        "files": {"glb": f"{oid}.glb", "blend": f"{oid}.blend", "views": written},
        "provenance": read_provenance(oid, entry),
        "intended_use": "single-object asset package for scene assembly and "
                        "single-image / multi-view reconstruction (TRELLIS)",
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return {"id": oid, "glb_mb": round(glb_path.stat().st_size / 1e6, 2),
            "meshes": len(meshes), "size": bounds["size"]}


def main() -> int:
    supply = json.loads(SUPPLY.read_text(encoding="utf-8"))
    LIB.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in supply["objects"]:
        oid = entry.get("id")
        if not oid:
            continue
        if entry.get("mode") == "shell" or oid in {"room_shell"}:
            continue
        try:
            rows.append(export_one(oid, entry))
            print(f"[library] {oid:18s} ok  glb={rows[-1]['glb_mb']} MB", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[library] {oid:18s} FAILED {type(exc).__name__}: {exc}", flush=True)
            rows.append({"id": oid, "error": f"{type(exc).__name__}: {exc}"})
    (LIB / "index.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    ok = [r for r in rows if "error" not in r]
    print(f"LIBRARY_EXPORT={len(ok)}/{len(rows)} packages")
    return 0 if ok else 1


if __name__ == "__main__":
    main()
