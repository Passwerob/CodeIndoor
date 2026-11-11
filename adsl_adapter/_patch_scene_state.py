"""Bring the delivered .blend in line with the images it ships with.

Opens the assembled scene, adds the two interior cameras that only existed at
render time, applies the Cycles/output preset that produced the PNGs, clears the
cutaway hiding, and saves. No geometry is touched.
"""
import sys
import pathlib

import bpy

ADAPTER = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ADAPTER))

import build_asset_scene as bas  # noqa: E402


def main():
    argv = sys.argv
    path = argv[argv.index("--") + 1] if "--" in argv else None
    if path:
        bpy.ops.wm.open_mainfile(filepath=path)

    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 64
    sc.cycles.use_denoising = True
    sc.render.resolution_x = 1280
    sc.render.resolution_y = 800
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.exposure = -0.30

    # Same rig as build_asset_scene.render_views, so the file is reproducible.
    bas.ensure_camera("Camera_Cutaway", (5.9, -6.9, 4.5), (0.0, 0.35, 1.00), 30)
    bas.ensure_camera("Camera_Interior", (0.0, -2.35, 1.68), (0.0, 0.85, 0.95), 18)
    bas.ensure_camera("Camera_WestCutaway", (-5.9, -6.6, 4.3), (0.0, 0.4, 1.0), 30)
    bas.ensure_camera("Camera_Corner", (2.15, -1.95, 1.66), (-0.55, 1.15, 0.92), 22)
    bas.ensure_camera("Camera_Media", (2.15, 1.85, 1.60), (-0.30, -2.45, 0.80), 24)

    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.hide_render = False
            obj.hide_set(False)
    sc.camera = bpy.data.objects.get("Camera_Corner")

    out = bpy.data.filepath
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print("PATCHED=" + out)


main()
