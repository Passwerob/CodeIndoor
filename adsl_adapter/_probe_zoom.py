"""Close-up render of the left arm's bottom-front corner."""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
BLEND, OUT = Path(args[0]), Path(args[1])
bpy.ops.wm.open_mainfile(filepath=str(BLEND))

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 64
scene.cycles.device = "CPU"
scene.render.resolution_x = 760
scene.render.resolution_y = 760

world = bpy.data.worlds.new("W")
scene.world = world
world.use_nodes = True
bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
if bg:
    bg.inputs[0].default_value = (0.5, 0.5, 0.5, 1.0)
    bg.inputs[1].default_value = 1.0

target = Vector((-1.00, -0.45, 0.26))
cam_data = bpy.data.cameras.new("Zoom")
cam_data.lens = 85
cam = bpy.data.objects.new("Zoom", cam_data)
scene.collection.objects.link(cam)
cam.location = target + Vector((-0.55, -0.95, 0.28))
direction = target - cam.location
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
scene.camera = cam

lamp = bpy.data.lights.new("Key", "AREA")
lamp.energy = 120
lamp.size = 1.2
lo = bpy.data.objects.new("Key", lamp)
scene.collection.objects.link(lo)
lo.location = target + Vector((-0.8, -1.0, 1.2))
lo.rotation_euler = (target - lo.location).to_track_quat("-Z", "Y").to_euler()

scene.render.filepath = str(OUT)
bpy.ops.render.render(write_still=True)
print("ZOOM_OK", OUT, flush=True)
