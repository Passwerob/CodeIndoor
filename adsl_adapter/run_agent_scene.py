"""Assemble the AGENT-GENERATED part programs into a scene (inside Blender).

All geometry here comes from files in runtime/outputs/agentic/generated/*.py,
each produced and repaired by the Constructor Agent. This script only:
  * executes those programs with their typed params,
  * stamps aDSL component ids / build tokens,
  * sets up stage lighting/camera/floor (not aDSL components),
  * renders multi-view before/after a localized re-execution,
  * saves .blend and a verification report.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
import uuid
from pathlib import Path

import bpy
from mathutils import Vector

RUNTIME = Path(r"E:/3DUse/New/CodeIndoor/runtime/outputs/agentic")
OUT = Path(r"E:/3DUse/New/CodeIndoor/runtime/outputs/agent_scene")
OUT.mkdir(parents=True, exist_ok=True)
SPECS = RUNTIME / "part_specs.json"
GEN = RUNTIME / "generated"


def load_program(path: Path, cid: str):
    spec = importlib.util.spec_from_file_location("agent_part_" + cid, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stamp(objects, cid: str, token: str) -> list[str]:
    names = []
    for obj in objects:
        obj["adsl_component_id"] = cid
        obj["adsl_build_token"] = token
        names.append(obj.name)
    return names


def remove_cid(cid: str) -> int:
    victims = [o for o in list(bpy.data.objects)
               if o.get("adsl_component_id") == cid]
    seen = set(victims)
    for obj in list(victims):
        node = obj.parent
        while node is not None and node not in seen:
            seen.add(node)
            node = node.parent
    for obj in seen:
        bpy.data.objects.remove(obj, do_unlink=True)
    return len(seen)


def snapshot() -> dict:
    return {o.name: {"cid": o.get("adsl_component_id"),
                     "token": o.get("adsl_build_token"),
                     "matrix": [round(float(v), 6) for row in o.matrix_world for v in row]}
            for o in bpy.data.objects}


def setup_stage():
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 0, 0))
    floor = bpy.context.object
    floor.name = "stage_floor"
    mat = bpy.data.materials.new("mat_stage_floor")
    mat.use_nodes = True
    mat.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (0.56, 0.56, 0.55, 1)
    floor.data.materials.append(mat)

    world = scene.world or bpy.data.worlds.new("StudioWorld")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.88, 0.90, 0.94, 1)
        bg.inputs["Strength"].default_value = 0.25
    for label, loc, energy in (("key", (2.4, -2.6, 3.2), 340),
                               ("fill", (-2.8, -1.2, 2.2), 150),
                               ("rim", (0.5, 3.0, 2.6), 240)):
        bpy.ops.object.light_add(type="AREA", location=loc)
        light = bpy.context.object
        light.name = "stage_light_" + label
        light.data.energy = energy
        light.data.shape = "DISK"
        light.data.size = 3.0
    bpy.ops.object.camera_add()
    cam = bpy.context.object
    cam.name = "stage_camera"
    cam.data.lens = 45
    scene.camera = cam
    scene.render.resolution_x = 960
    scene.render.resolution_y = 720
    scene.render.image_settings.file_format = "PNG"
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "Medium High Contrast"
    except (TypeError, ValueError):
        pass
    return cam


def render_views(cam, prefix: str) -> list[str]:
    scene = bpy.context.scene
    target = Vector((0, 0, 0.48))
    paths = []
    for label, loc in (("front_45", (2.7, -3.2, 1.75)),
                       ("side", (-3.3, -0.7, 1.35)),
                       ("top_3q", (1.9, 2.3, 2.7))):
        cam.location = loc
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        path = OUT / f"{prefix}_{label}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        paths.append(str(path))
    return paths


def main() -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    specs = json.loads(SPECS.read_text(encoding="utf-8"))
    built: dict[str, dict] = {}
    failures = {}

    for cid, entry in specs.items():
        path = GEN / f"{cid}.py"
        if not path.exists():
            failures[cid] = "no accepted program"
            continue
        try:
            module = load_program(path, cid)
            objects = module.build_part(entry["params"], cid)
            token = uuid.uuid4().hex
            names = stamp(objects, cid, token)
            built[cid] = {"objects": len(objects), "token": token,
                          "names": names, "source": path.name}
        except Exception:  # noqa: BLE001
            failures[cid] = traceback.format_exc()[-400:]

    bpy.context.view_layer.update()
    cam = setup_stage()
    before = render_views(cam, "before")
    snap0 = snapshot()

    # Localized re-execution of ONE agent-generated program with edited params.
    edit_report = {"skipped": "no suitable part"}
    target = next((c for c in ("back_cushion_2", "seat_cushion_2", "backrest_frame")
                   if c in built), None)
    if target:
        params = dict(specs[target]["params"])
        params["height"] = round(float(params.get("height", 0.4)) - 0.05, 4)
        removed = remove_cid(target)
        try:
            module = load_program(GEN / f"{target}.py", target + "_edited")
            objects = module.build_part(params, target)
            new_token = uuid.uuid4().hex
            stamp(objects, target, new_token)
            bpy.context.view_layer.update()
            snap1 = snapshot()
            violations = [
                n for n, rec in snap0.items()
                if rec["cid"] not in (target, None)
                and (n not in snap1 or snap1[n]["token"] != rec["token"])
            ]
            edit_report = {"target": target, "height_delta": -0.05,
                           "removed_objects": removed, "rebuilt_objects": len(objects),
                           "unaffected_violations": violations,
                           "pass": not violations}
        except Exception as exc:  # noqa: BLE001
            edit_report = {"target": target, "error": type(exc).__name__ + ": " + str(exc)[:200]}

    after = render_views(cam, "after")
    blend = OUT / "agent_scene.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    report = {
        "blender": bpy.app.version_string,
        "engine": bpy.context.scene.render.engine,
        "agent_generated_parts": len(built),
        "failed_parts": failures,
        "parts": built,
        "object_count": len(snap0),
        "localized_edit": edit_report,
        "before_renders": before,
        "after_renders": after,
        "blend_path": str(blend),
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print("AGENT_SCENE_RESULT=" + json.dumps({
        "parts": len(built), "failures": len(failures),
        "objects": len(snap0), "edit": edit_report,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    main()
