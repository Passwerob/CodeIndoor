"""Close-up render of a target point, for eyeballing a suspected defect.

Usage: blender -b --python _probe_zoom2.py -- <blend> <out.png> x,y,z dist lens
"""
import sys

import bpy
from mathutils import Vector


def main():
    argv = sys.argv
    a = argv[argv.index("--") + 1:]
    blend, out = a[0], a[1]
    target = Vector([float(v) for v in a[2].split(",")])
    dist = float(a[3])
    lens = float(a[4]) if len(a) > 4 else 50.0
    offset = Vector((float(v) for v in a[5].split(","))) if len(a) > 5 else Vector((1, -0.25, 0.12))

    bpy.ops.wm.open_mainfile(filepath=blend)
    sc = bpy.context.scene

    cam_obj = bpy.data.objects.get("ProbeCam")
    if cam_obj is None:
        cam_obj = bpy.data.objects.new("ProbeCam", bpy.data.cameras.new("ProbeCam"))
        sc.collection.objects.link(cam_obj)
    cam_obj.data.lens = lens
    loc = target + offset.normalized() * dist
    cam_obj.location = loc
    cam_obj.rotation_euler = (target - loc).to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam_obj

    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 48
    sc.cycles.use_denoising = True
    sc.render.resolution_x = 900
    sc.render.resolution_y = 900
    sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.exposure = -0.3
    sc.render.filepath = out
    bpy.ops.render.render(write_still=True)
    print("ZOOM_OK=" + out)


main()
