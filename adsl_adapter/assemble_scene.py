"""Bind SceneCode's placement plan onto FineScene's existing aDSL object library.

Division of labour (the user's requirement):
  * SceneCode -> scene organisation: which objects, five stages, x/y/yaw,
    relations, circulation, critic. Produced by scene_layout.py as layout.json.
  * aDSL / FineScene -> single-object geometry: reuses the ALREADY BUILT
    parametric aDSL assets in
    FineScene/deliveries/reception_lounge_adsl_20261104/build_reception_lounge.py
    (13 independently editable objects, every part a named primitive).

This module writes NO geometry. It only stamps an object-level 4x4 transform
onto each existing aDSL object (exactly FineScene's own apply_pose/mat4), then
serialises the scene, renders it, and runs FineScene's scene-scope visual audit
(which includes a layout audit of the SceneCode placement decisions).
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"E:/3DUse/SceneCode_audit")
ADSL = Path(r"E:/3DUse/aDSL")
FINESCENE_SRC = Path(r"E:/3DUse/FineScene/src")
LOUNGE = Path(r"E:/3DUse/FineScene/deliveries/reception_lounge_adsl_20261104")
LOUNGE_BUILD = LOUNGE / "build_reception_lounge.py"
LOUNGE_BLEND_BUILD = LOUNGE / "build_blend.py"
LAYOUT_PATH = ROOT / "runtime/outputs/scene_layout/layout.json"
OUT = ROOT / "runtime/outputs/living_room"
BLENDER = r"E:/Blender/blender.exe"
WORKER = ADSL / "scripts/blender_worker.py"
PROFILE = Path(r"E:/3DUse/FineScene/configs/deepseek-flash.yaml")

for p in (str(ADSL / "adsl-core"), str(ADSL / "adsl-agents"), str(FINESCENE_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)


def load_lounge_objects():
    """Execute ONLY the geometry-definition part of FineScene's build script.

    The exports section (which writes into FineScene's own delivery directory)
    is deliberately excluded. Geometry code is borrowed verbatim, never rewritten.
    """
    source = LOUNGE_BUILD.read_text(encoding="utf-8")
    lines = source.splitlines()
    cut = next((i for i, line in enumerate(lines)
                if line.startswith("# ===") and "exports" in line.lower()), len(lines))
    head = "\n".join(lines[:cut])
    # Redirect its OUT constant so absolutely nothing is written to FineScene.
    head = head.replace(
        'OUT = Path(r"E:\\3DUse\\FineScene\\deliveries\\reception_lounge_adsl_20261104")',
        f'OUT = Path(r"{OUT.as_posix()}")')
    namespace = {"__name__": "_lounge_geometry_only", "__file__": str(LOUNGE_BUILD)}
    exec(compile(head, str(LOUNGE_BUILD), "exec"), namespace)  # noqa: S102
    return namespace


def apply_layout(ns: dict, layout: dict) -> dict:
    """Stamp SceneCode poses onto the borrowed aDSL assets; return edit report."""
    scene = ns["scene"]
    apply_pose, mat4 = ns["apply_pose"], ns["mat4"]
    objects = layout.get("objects") or []
    report = {"applied": [], "missing": [], "cloned": []}

    for spec in objects:
        oid = spec.get("id")
        if oid == "room_shell":
            report["applied"].append({"id": oid, "pose": [0, 0, 0, 0], "origin": True})
            continue
        pl = spec.get("placement") or {}
        pos = (float(pl.get("x", 0)), float(pl.get("y", 0)), float(pl.get("z", 0)))
        yaw = float(pl.get("yaw_deg", 0))
        matrix = mat4(pos, yaw)

        child = scene._children.get(oid)
        if child is None and oid == "tv_cabinet":
            # The aDSL library has no TV cabinet. Use FineScene's OWN parametric
            # desk factory (adsl_components.py::build_desk) driven by the
            # AssetItem dimensions from the SceneCode layout. No geometry is
            # written here either: LLM -> dimensions -> deterministic factory.
            from adsl_components import build_component
            from core.serialize import asset_from_dict, asset_to_dict

            dims = (spec.get("asset_item") or {}).get("dimensions") or [1.8, 0.45, 0.55]
            built = build_component({
                "component_id": oid, "kind": "desk",
                "parameters": {"width": float(dims[0]), "depth": float(dims[1]),
                               "height": float(dims[2])},
            })
            # FineScene factories return adsl.core.asset.Asset while the lounge
            # tree uses core.asset.Asset (two parallel copies of the core).
            # Round-trip through aDSL's own serializer instead of hand-converting.
            child = asset_from_dict(asset_to_dict(built.scene))
            object.__setattr__(child, "label", "TVCabinet")
            scene.attach_part(oid, child)
            report["cloned"].append({"from": "finescene_factory:build_desk", "to": oid,
                                     "parameters": {"width": dims[0], "depth": dims[1],
                                                    "height": dims[2]}})
        if child is None:
            report["missing"].append(oid)
            continue
        apply_pose(child, matrix)
        report["applied"].append({"id": oid, "pose": [pos[0], pos[1], pos[2], yaw],
                                  "parts": len(child._children) + len(child._primitives)})
    return report


def render_scene(ns: dict) -> dict:
    adsl_json = OUT / "living_room.adsl.json"
    adsl_json.write_text(json.dumps(
        ns["normalize"](ns["asset_to_dict"](ns["scene"])),
        ensure_ascii=False, indent=2), encoding="utf-8")

    render_out = OUT / "blender"
    if render_out.exists():
        shutil.rmtree(render_out)
    proc = subprocess.run(
        [BLENDER, "-b", "--python", str(WORKER), "--",
         "--input", str(adsl_json), "--output", str(render_out),
         "--render", "--render-view-count", "4", "--resolution", "960"],
        capture_output=True, text=True, timeout=900)
    (OUT / "blender_stdout.txt").write_text(
        (proc.stdout or "")[-8000:] + "\n--- STDERR ---\n" + (proc.stderr or "")[-4000:],
        encoding="utf-8")
    views = sorted((render_out / "render").glob("view_*.png"))
    return {"adsl_json": str(adsl_json), "blend": str(render_out / "scene.blend"),
            "views": [str(v) for v in views], "returncode": proc.returncode,
            "blend_exists": (render_out / "scene.blend").exists()}


def render_interior(adsl_json: Path | None = None, subdir: str = "interior",
                    blend_name: str = "living_room.blend") -> dict:
    """Reuse FineScene's own build_blend.py: editable object hierarchy, lights,
    cutaway/interior cameras AND its built-in local-edit validation tests.

    Only the output paths and the render loop are redirected; geometry import,
    materials, lighting and cameras are borrowed verbatim.
    """
    adsl_json = adsl_json or (OUT / "living_room.adsl.json")
    interior = OUT / subdir
    if interior.exists():
        shutil.rmtree(interior)
    interior.mkdir(parents=True, exist_ok=True)
    (interior / "object_manifest.json").write_text(json.dumps({
        "provenance": "SceneCode layout (scene organisation) + FineScene/aDSL "
                      "existing parametric assets (single-object geometry)",
        "scene_label": "LivingRoom", "units": "m",
        "coordinate_frame": {"up": "+Z", "front": "-Y", "right": "+X"},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    source = LOUNGE_BLEND_BUILD.read_text(encoding="utf-8")
    source = source.replace(
        'OUT = Path(r"E:\\3DUse\\FineScene\\deliveries\\reception_lounge_adsl_20261104")',
        f'OUT = Path(r"{interior.as_posix()}")')
    source = source.replace(
        'ADSL_JSON = OUT / "reception_lounge.adsl.json"',
        f'ADSL_JSON = Path(r"{adsl_json.as_posix()}")')

    start = source.index("# cutaway: hide the near walls")
    end = source.index('(OUT / "validation_report.json").write_text(')
    render_block = '''# SceneCode layout: four interior/cutaway views of the assembled room.
hide_keys = ("south", "wall_east", "wall_west", "skirting_south",
             "skirting_west", "door_jamb", "door_lintel", "ceiling")
hidden = [o for o in part_objects
          if (o["adsl_object_id"] == "room_shell" and any(k in o["adsl_part"] for k in hide_keys))
          or o["adsl_object_id"] == "wall_art_west"]  # wall removed by cutaway -> no floating frame
cam_w = camera("Camera_WestCutaway", (-5.9, -6.6, 4.3), (0.0, 0.4, 1.0), 30)
cam_back = camera("Camera_BackInterior", (0.2, 2.15, 1.90), (0.0, 0.25, 0.90), 20)

for o in hidden:
    o.hide_render = True
sc.camera = cam_cut
sc.render.filepath = str(OUT / "view_000.png")
bpy.ops.render.render(write_still=True)

for o in hidden:
    o.hide_render = False
sc.camera = cam_in
sc.render.filepath = str(OUT / "view_001.png")
bpy.ops.render.render(write_still=True)

for o in hidden:
    o.hide_render = True
sc.camera = cam_w
sc.render.filepath = str(OUT / "view_002.png")
bpy.ops.render.render(write_still=True)
for o in hidden:
    o.hide_render = False
sc.camera = cam_back
sc.render.filepath = str(OUT / "view_003.png")
bpy.ops.render.render(write_still=True)

for o in hidden:
    o.hide_render = True
sc.camera = cam_cut
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "__BLEND_NAME__"))

'''
    render_block = render_block.replace("__BLEND_NAME__", blend_name)
    source = source[:start] + render_block + source[end:]
    generated = interior / "_build_living_room.py"
    generated.write_text(source, encoding="utf-8")

    proc = subprocess.run([BLENDER, "-b", "--python", str(generated)],
                          capture_output=True, text=True, timeout=1200)
    (interior / "render_stdout.txt").write_text(
        (proc.stdout or "")[-12000:] + "\n--- STDERR ---\n" + (proc.stderr or "")[-5000:],
        encoding="utf-8")
    views = sorted(interior.glob("view_*.png"))
    return {"views": [str(v) for v in views], "returncode": proc.returncode,
            "blend": str(interior / blend_name),
            "blend_exists": (interior / blend_name).exists(),
            "build_script": str(generated), "adsl_source": str(adsl_json)}


def scene_visual_audit(views: list[Path], layout: dict, qa_path: Path | None = None) -> dict:
    """FineScene's scene-scope hard gate, including layout audit of placement."""
    from general_vision import review_images

    if len(views) < 3:
        return {"accepted": False, "status": "insufficient_views", "views": len(views)}
    style = [
        {"id": "coherent_arrangement",
         "feature": "furniture arranged as a coherent conversation grouping",
         "visible_evidence": "sofa, coffee table and chairs form a readable grouping"},
        {"id": "circulation",
         "feature": "clear walking path from the door into the room",
         "visible_evidence": "unobstructed floor between the door and room centre"},
        {"id": "proportions",
         "feature": "realistic furniture proportions and clearances",
         "visible_evidence": "no oversized or intersecting furniture"},
    ]
    request = {
        "id": "living_room_01",
        "original_user_request": layout.get("_brief") or (
            "A realistic modern living room with a two-seat sofa, coffee table, "
            "TV cabinet, area rug, floor lamp, side table, plants, wall decorations "
            "and ceiling lighting; coherent arrangement, realistic proportions, "
            "functional circulation paths, balanced composition."),
        "style_requirements": style,
        "components": [
            {"id": o["id"], "semantic": o.get("asset_item", {}).get("short_name", o["id"]),
             "required_features": [], "materials": []}
            for o in (layout.get("objects") or [])
        ],
        "layout_audit_required": True,
    }
    report = review_images(views, request, str(PROFILE), qa_path or OUT / "scene_visual_qa.json",
                           quality="realistic", scope="scene", timeout_seconds=300)
    decision = report.get("decision") or {}
    return {"accepted": bool(report.get("accepted")), "status": report.get("status"),
            "verdict": decision.get("verdict"),
            # reviewer returns evidence + correction; reading only `note` made
            # every finding an empty string and the repair loop blind.
            "findings": [{"severity": f.get("severity"),
                          "note": (str(f.get("note") or f.get("message")
                                       or f.get("evidence") or "")[:200]
                                   + (" -> FIX: " + str(f.get("correction"))[:160]
                                      if f.get("correction") else ""))}
                         for f in decision.get("findings", [])][:10],
            "views": len(views)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    layout = payload["layout"]
    layout["_brief"] = payload.get("brief")

    print("[assemble] loading FineScene aDSL object library ...", flush=True)
    ns = load_lounge_objects()
    print(f"[assemble] library objects = {len(ns['scene']._children)}", flush=True)

    edit = apply_layout(ns, layout)
    print(f"[assemble] poses applied = {len(edit['applied'])}, "
          f"missing = {edit['missing']}", flush=True)

    render = render_scene(ns)
    print(f"[assemble] external view rc={render['returncode']}, views={len(render['views'])}, "
          f"blend={render['blend_exists']}", flush=True)

    print("[assemble] rendering FineScene interior/cutaway views ...", flush=True)
    interior = render_interior()
    print(f"[assemble] interior rc={interior['returncode']}, "
          f"views={len(interior['views'])}, blend={interior['blend_exists']}", flush=True)

    audit = scene_visual_audit([Path(v) for v in interior["views"]], layout)
    print(f"[assemble] scene gate accepted={audit['accepted']} "
          f"status={audit['status']} verdict={audit['verdict']}", flush=True)

    report = {
        "schema": "scenecode.layout.v1 + finescene.scene.v1",
        "division_of_labour": {
            "scene_organisation": "SceneCode five-stage planner / designer / critic contract",
            "single_object_geometry": "FineScene existing parametric aDSL assets (borrowed verbatim)",
            "bridge": "object-level 4x4 xform stamp only; no geometry written here",
        },
        "layout_source": str(LAYOUT_PATH),
        "geometry_source": str(LOUNGE_BUILD),
        "validator": payload.get("validation"),
        "critic": payload.get("critic"),
        "edit": edit,
        "render_external": render,
        "render_interior": interior,
        "scene_visual_audit": audit,
        "stage_isolation": "generation: SceneCode layout is SoT; editing: aDSL AST is SoT",
    }
    (OUT / "SCENE_ASSEMBLY_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ASSEMBLY_RESULT=" + json.dumps({
        "objects": len(edit["applied"]), "missing": edit["missing"],
        "interior_views": len(interior["views"]), "blend": interior["blend_exists"],
        "gate": audit["accepted"], "verdict": audit["verdict"],
    }, ensure_ascii=False))
    return 0 if interior["blend_exists"] and not edit["missing"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
