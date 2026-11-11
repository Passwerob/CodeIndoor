"""Assemble the SceneCode layout from REAL assets instead of editability fixtures.

Run inside Blender:
    E:/Blender/blender.exe -b --python adsl_adapter/build_asset_scene.py

What changed and why
--------------------
The previous assembly (assemble_scene.py) stamped SceneCode poses onto the
geometry in FineScene/deliveries/reception_lounge_adsl_20261104/build_reception_lounge.py.
That geometry is an aDSL *editability fixture*: one named primitive per part, so
attach_part and local-edit tests have something to reference. Its silhouettes and
single-colour materials were never a quality target, which is exactly why the
result looked like stacked blocks while FineScene's own deliveries did not.

This builder keeps the part of the pipeline that already worked -- SceneCode
decides WHAT goes WHERE (supply_plan.json carries the object list, envelopes,
and poses) -- and replaces the geometry/material layer with the two sources
FineScene actually draws quality from:

  * real CC0 scanned assets + their 2k PBR sets, via realistic_refiner's own
    load_asset / fitted_copy / surface (borrowed, never reimplemented);
  * procedural surface goods (rug) that are all texture and profile.

Nothing here authors furniture geometry by hand. Objects the asset library cannot
cover are marked `generated` in the supply plan and are left out until the aDSL
agent pipeline produces them.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

# Make sibling planner modules importable. scene_layout / layout_finalize are
# pure Python (no bpy import) and safe to reuse for the back-to-wall yaw
# re-derivation guard below.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_finalize import derive_against_wall_yaw  # noqa: E402

ROOT = Path(r"E:/3DUse/SceneCode_audit")
FINESCENE_SRC = Path(r"E:/3DUse/FineScene/src")
ASSET_ROOT = Path(r"E:/3DUse/assets/external/realistic_study_20261019")
SUPPLY_PATH = ROOT / "runtime/outputs/scene_layout/supply_plan.json"
# The clearance solver writes layout_relaxed.json. Reading the raw layout here
# silently discarded every clearance correction: the assembly used the poses
# that the solver had already rejected.
_LAYOUT_DIR = ROOT / "runtime/outputs/scene_layout"
# Only the finalized layout: poses are authoritative and nothing downstream may
# relocate them. Preferring a "relaxed" file here is what let a second designer
# (clearance_solver) overwrite the agent's decisions at assembly time.
LAYOUT_PATH = _LAYOUT_DIR / "layout_finalized.json"
ROOM_PATH = _LAYOUT_DIR / "room_measured.json"
BASE_BLEND = ROOT / "runtime/outputs/living_room/interior/living_room.blend"
OUT = ROOT / "runtime/outputs/living_room/assets"
FORGE_OUT = ROOT / "runtime/outputs/forge"
ROOM_HEIGHT = 2.90

# Centre of the relocated entrance opening on the south wall. 1.2 m wide, so
# 1.20-2.40: it clears a 1.8 m media unit centred on the seating axis at x=0
# with a 0.30 m pier between, and leaves the east 4.3 m of that wall solid.
DOOR_CENTER_X = 1.80

for p in (str(FINESCENE_SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from realistic_refiner import load_asset, fitted_copy, surface  # noqa: E402

REPORT: dict = {"opened": None, "shell_parts": 0, "removed_objects": [],
                "imported": [], "procedural": [], "skipped": [], "poses": [],
                "errors": []}


# --------------------------------------------------------------------------- #
# scene bookkeeping
# --------------------------------------------------------------------------- #
def object_groups() -> dict[str, list]:
    """Group scene objects by the aDSL object id stamped by build_blend.py."""
    groups: dict[str, list] = {}
    for obj in bpy.data.objects:
        oid = obj.get("adsl_object_id")
        if oid:
            groups.setdefault(str(oid), []).append(obj)
    return groups


def drop_object_group(oid: str, groups: dict) -> int:
    removed = 0
    for obj in groups.get(oid, []):
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
    col = bpy.data.collections.get(f"OBJ_{oid}")
    if col:
        for child in list(col.objects):
            bpy.data.objects.remove(child, do_unlink=True)
            removed += 1
        bpy.data.collections.remove(col)
    return removed


def attach_to_root(oid: str, label: str, position, yaw_deg: float, meshes: list):
    """Mirror FineScene's object-root convention so the tree stays editable."""
    col = bpy.data.collections.new(f"OBJ_{oid}")
    bpy.context.scene.collection.children["SCENE"].children.link(col) \
        if "SCENE" in bpy.context.scene.collection.children \
        else bpy.context.scene.collection.children.link(col)
    root = bpy.data.objects.new(oid, None)
    col.objects.link(root)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.22
    root.location = position
    root.rotation_euler.z = math.radians(yaw_deg)
    root["adsl_role"] = "object_root"
    root["adsl_object_id"] = oid
    root["adsl_label"] = label
    root["adsl_origin"] = list(position)
    root["adsl_yaw_deg"] = yaw_deg
    root["editable"] = "move/rotate this Empty to edit the whole object"
    for obj in meshes:
        obj["adsl_object_id"] = oid
        obj["adsl_part"] = obj.name.split("/")[-1]
        obj["adsl_provenance"] = "retrieved_cc0_asset"
    return root, col


# --------------------------------------------------------------------------- #
# surfaces
# --------------------------------------------------------------------------- #
def pbr(name: str, asset: str, scale: float = 1.0, tint=None):
    return surface(name, asset, tint=tint, scale=scale)


def box_project_normals(mat, scale: float = 2.5):
    """Put a PBR material's normal map on the same box projection as its albedo.

    `realistic_refiner.surface` samples the colour map with BOX projection in
    Object space but feeds the normal map from the UV map with FLAT projection.
    On a small part that is invisible. On a 4.35 x 2.9 m wall the box unwrap
    stretches the 2K normal map across the whole face, so a low-frequency tilt
    in the scan becomes a metre-wide gradient in the shading term rather than a
    surface texture. Reprojecting at a 0.4 m tile keeps the normal detail at
    plaster scale and consistent with the albedo.

    This is not what puts the horizontal band on the wall: an ambient-only
    render of the same scene is uniform, all three plaster channels are flat to
    within 3%, and the wall meshes are closed boxes with correct normals. The
    band is the lighting rig's falloff, so it is left as authored.
    """
    nt = mat.node_tree
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeVectorMath")
    mapping.operation = "SCALE"
    mapping.inputs[3].default_value = scale
    nt.links.new(coord.outputs["Object"], mapping.inputs[0])
    changed = 0
    for node in nt.nodes:
        if node.type != "TEX_IMAGE" or node.image is None:
            continue
        if "_nor_" not in node.image.name:
            continue
        node.projection = "BOX"
        node.projection_blend = 0.2
        nt.links.new(mapping.outputs[0], node.inputs["Vector"])
        changed += 1
    return changed


def woven(name: str, base, dark, light, weave=320.0, roughness=0.86):
    """Procedural woven textile.

    The library's only fabric scan (fabric_pattern_07) is a red-and-white check;
    tiled at rug scale it reads as a picnic blanket and fights a neutral room.
    Weave bump plus tonal drift gives real textile without importing that
    pattern.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.is_active_output = True
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Sheen Weight"].default_value = 0.22

    tc = nt.nodes.new("ShaderNodeTexCoord")
    fine = nt.nodes.new("ShaderNodeTexNoise")
    fine.inputs["Scale"].default_value = weave
    fine.inputs["Detail"].default_value = 2.0
    nt.links.new(tc.outputs["Object"], fine.inputs["Vector"])

    drift = nt.nodes.new("ShaderNodeTexNoise")
    drift.inputs["Scale"].default_value = 7.0
    drift.inputs["Detail"].default_value = 4.0
    nt.links.new(tc.outputs["Object"], drift.inputs["Vector"])

    mix = nt.nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "OVERLAY"
    mix.inputs["Factor"].default_value = 0.5
    nt.links.new(fine.outputs["Fac"], mix.inputs["Color1"])
    nt.links.new(drift.outputs["Fac"], mix.inputs["Color2"])

    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*dark, 1)
    ramp.color_ramp.elements[1].color = (*light, 1)
    nt.links.new(mix.outputs[0], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], bsdf.inputs["Base Color"])

    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    bump.inputs["Distance"].default_value = 0.0012
    nt.links.new(fine.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    mat["adsl_material"] = name
    mat["pbr_source"] = "procedural weave"
    return mat


def retarget_materials(meshes: list, oid: str):
    """Scanned assets arrive with their own PBR; only repair missing textures."""
    repaired, missing = 0, 0
    for obj in meshes:
        for slot in obj.data.materials:
            if not slot or not slot.use_nodes:
                missing += 1
                continue
            for node in slot.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    if node.image.source == "FILE" and not node.image.has_data:
                        try:
                            node.image.reload()
                            repaired += 1
                        except RuntimeError:
                            missing += 1
    return {"repaired": repaired, "missing_slots": missing}


# --------------------------------------------------------------------------- #
# per-mode builders
# --------------------------------------------------------------------------- #
def wall_inner_x(side: str) -> float | None:
    """Inner face of a named wall, read from the borrowed shell geometry."""
    key = f"wall_{side}"
    for obj in bpy.data.objects:
        if obj.get("adsl_object_id") != "room_shell" or obj.type != "MESH":
            continue
        if key not in str(obj.get("adsl_part", obj.name)).lower():
            continue
        xs = [(obj.matrix_world @ Vector(c)).x for c in obj.bound_box]
        return max(xs) if side == "west" else min(xs)
    return None


def wall_inner_y(side: str) -> float | None:
    """Same, for the walls whose normal runs along Y."""
    key = f"wall_{side}"
    for obj in bpy.data.objects:
        if obj.get("adsl_object_id") != "room_shell" or obj.type != "MESH":
            continue
        if key not in str(obj.get("adsl_part", obj.name)).lower():
            continue
        ys = [(obj.matrix_world @ Vector(c)).y for c in obj.bound_box]
        return max(ys) if side == "south" else min(ys)
    return None


def bbox(obj) -> tuple:
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for c in obj.bound_box:
        w = obj.matrix_world @ Vector(c)
        lo = Vector((min(lo[i], w[i]) for i in range(3)))
        hi = Vector((max(hi[i], w[i]) for i in range(3)))
    return lo, hi


def group_bounds_raw(oid: str, skip_books: bool = True) -> tuple:
    """World bounds of a group's own geometry, books excluded by default."""
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.get("adsl_object_id") != oid:
            continue
        if skip_books and "_books_" in obj.name:
            continue
        a, b = bbox(obj)
        lo = Vector((min(lo[i], a[i]) for i in range(3)))
        hi = Vector((max(hi[i], b[i]) for i in range(3)))
    return lo, hi


def measure_bays(oid: str, case_top: float) -> list:
    """Turn a bookcase's horizontal boards into (floor, ceiling) per bay.

    The generated unit has a plinth and an uneven top bay, so a formula pitch
    puts the top row into the underside of the top board. Reading the boards
    that actually exist keeps the books inside the opening whatever the agent
    modelled.
    """
    tops = {}
    runs = []
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.get("adsl_object_id") != oid:
            continue
        if "_books_" in obj.name:
            continue
        lo, hi = bbox(obj)
        if hi.z - lo.z > 0.06:                    # a vertical member
            continue
        if (hi.x - lo.x) * (hi.y - lo.y) < 0.05:  # a small fitting
            continue
        tops.setdefault(round(hi.z, 4), round(lo.z, 4))
        runs.append((lo.y, hi.y))

    ordered = sorted(tops.items())
    if not ordered or not runs:
        return []
    # The clear run is the boards' own span, not the case envelope: the side
    # panels are 22 mm thick, so insetting from the outer bound put the first
    # book 5 mm inside them.
    run_lo = max(r[0] for r in runs)
    run_hi = min(r[1] for r in runs)

    bays = []
    for i, (top, _bot) in enumerate(ordered):
        ceil = ordered[i + 1][1] if i + 1 < len(ordered) else case_top
        if ceil - top < 0.09:
            continue
        bays.append({"floor": top, "ceil": ceil, "run": (run_lo, run_hi)})
    return bays


def _set_span_x(obj, lo_x: float, hi_x: float) -> None:
    lo, hi = bbox(obj)
    if hi.x - lo.x < 1e-6:
        return
    scale = (hi_x - lo_x) / (hi.x - lo.x)
    c_old, c_new = (lo.x + hi.x) / 2.0, (lo_x + hi_x) / 2.0
    obj.matrix_world = (
        Matrix.Translation(Vector((c_new, 0.0, 0.0)))
        @ Matrix.Scale(scale, 4, (1.0, 0.0, 0.0))
        @ Matrix.Translation(Vector((-c_old, 0.0, 0.0)))
        @ obj.matrix_world)


def relocate_door(new_center_x: float) -> dict | None:
    """Slide the entrance opening along its wall and refit the wall pieces.

    The borrowed shell puts a 1.2 m entrance dead centre on the south wall. That
    leaves no run of wall long enough for a media unit: put the console at the
    centre and it stands in the doorway, put it beside the door and the seating
    group no longer faces it -- which is exactly the state this scene was in
    (sofa axis x=0 aimed at the opening, console pushed to x=-1.65). Every
    alternative put the sofa across the entry path, so the opening moves to the
    east end of the same wall: structure untouched, 4.3 m of solid wall left.
    """
    shell = [o for o in bpy.data.objects
             if o.get("adsl_object_id") == "room_shell" and o.type == "MESH"]
    door = [o for o in shell if str(o.get("adsl_part", "")).startswith("door")]
    if not door:
        return None
    lo = min(bbox(o)[0].x for o in door)
    hi = max(bbox(o)[1].x for o in door)
    dx = new_center_x - (lo + hi) / 2.0
    if abs(dx) < 1e-4:
        return None

    for o in door:
        o.matrix_world = Matrix.Translation(Vector((dx, 0.0, 0.0))) @ o.matrix_world

    new_lo, new_hi = lo + dx, hi + dx
    touched = []
    for o in shell:
        part = str(o.get("adsl_part", "")).lower()
        if "wall_south" not in part and "skirting_south" not in part:
            continue
        a, b = bbox(o)
        if b.x <= lo + 0.01:
            _set_span_x(o, a.x, new_lo)
            touched.append({"part": part, "x": [round(a.x, 3), round(new_lo, 3)]})
        elif a.x >= hi - 0.01:
            _set_span_x(o, new_hi, b.x)
            touched.append({"part": part, "x": [round(new_hi, 3), round(b.x, 3)]})
    bpy.context.view_layer.update()
    return {"opening_x": [round(new_lo, 3), round(new_hi, 3)],
            "dx": round(dx, 3), "pieces": touched}


def populate_shelf(entry: dict) -> dict:
    """Fill a forged shelf with the retrieved book set, shelf by shelf.

    An empty bookcase reads as an unfinished placeholder, and the library has a
    87-mesh CC0 book run -- the same one FineScene uses. Copies are distributed
    per bay instead of one solid block.
    """
    oid = entry["id"]
    contents = entry.get("contents") or {}
    asset_id = (contents.get("fit") or {}).get("asset")
    if not asset_id:
        return {"id": oid, "populated": 0, "reason": "no book asset resolved"}
    gltf = ASSET_ROOT / contents["fit"]["file"]
    if not gltf.is_file():
        return {"id": oid, "populated": 0, "reason": f"missing {gltf}"}

    root = bpy.data.objects.get(f"ROOT_{oid}") or bpy.data.objects.get(oid)
    if root is None:
        return {"id": oid, "populated": 0, "reason": "no shelf root"}

    # Books are laid out one by one at native proportions. The earlier version
    # handed the whole run to fitted_copy(), which scales a group
    # non-uniformly to a target box: 30 volumes were squeezed into 0.97 m, so
    # sixteen neighbouring covers ended up 3-5 mm inside each other, and the top
    # row was pushed to exactly the case height and grew into the top board.
    case_lo, case_hi = group_bounds_raw(oid)
    bays = measure_bays(oid, case_hi.z)
    if not bays:
        return {"id": oid, "populated": 0, "reason": "no bays measured"}

    # Quarter-turned case: the run is its long horizontal axis, the depth the
    # short one, and the front is whichever face looks at the room centre.
    if (case_hi.y - case_lo.y) >= (case_hi.x - case_lo.x):
        right, fwd = Vector((0.0, 1.0, 0.0)), Vector((1.0, 0.0, 0.0))
    else:
        right, fwd = Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0))
    centre = Vector(((case_lo.x + case_hi.x) / 2.0, (case_lo.y + case_hi.y) / 2.0, 0.0))
    if fwd.dot(-centre) < 0.0:
        fwd = -fwd
    corners = [Vector((x, y, 0.0)) for x in (case_lo.x, case_hi.x)
               for y in (case_lo.y, case_hi.y)]
    front_coord = max(c.dot(fwd) for c in corners)

    yaw_deg = math.degrees(root.matrix_world.to_euler().z)
    gap, setback, per_bay = 0.002, 0.012, 40
    created, runs = 0, 0
    for i, bay in enumerate(bays):
        before = set(bpy.data.objects)
        load_asset(gltf)
        imported = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
        if not imported:
            continue

        def centre_x(o):
            pts = [(o.matrix_world @ Vector(c)).x for c in o.bound_box]
            return sum(pts) / len(pts)

        imported.sort(key=centre_x)
        # Each bay starts at a different point in the run, otherwise all four
        # shelves carry the identical sequence of spines, which reads as a
        # texture repeat rather than as a bookcase.
        start = (i * 11) % max(1, len(imported) - per_bay) if len(imported) > per_bay else 0
        subset = imported[start:start + per_bay]
        for obj in imported:
            if obj not in subset:
                bpy.data.objects.remove(obj, do_unlink=True)

        sizes = []
        for obj in subset:
            lo, hi = bbox(obj)
            sizes.append(Vector((hi.x - lo.x, hi.y - lo.y, hi.z - lo.z)))
        if not sizes:
            continue

        # One scale for the whole bay keeps a set of books in proportion; the
        # headroom under the board above is what the top row was missing.
        clear = bay["ceil"] - bay["floor"]
        target_h = min(clear - 0.020, 0.30)
        scale = min(1.0, target_h / max(v.z for v in sizes))

        cursor = bay["run"][0] + 0.004
        placed = []
        for obj, size in zip(subset, sizes):
            w, d = size.x * scale, size.y * scale
            if cursor + w > bay["run"][1] - 0.004:
                break
            target = (right * (cursor + w / 2.0)
                      + fwd * (front_coord - setback - d / 2.0)
                      + Vector((0.0, 0.0, bay["floor"] + 0.0015)))
            lo, hi = bbox(obj)
            obj.matrix_world = (
                Matrix.Translation(target)
                @ Matrix.Rotation(math.radians(yaw_deg), 4, "Z")
                @ Matrix.Scale(scale, 4)
                @ Matrix.Translation(Vector((-(lo.x + hi.x) / 2.0,
                                             -(lo.y + hi.y) / 2.0, -lo.z)))
                @ obj.matrix_world)
            cursor += w + gap
            obj.name = f"{oid}_books_{i:02d}/{obj.name.split('.')[0]}"
            obj["adsl_object_id"] = oid
            obj["adsl_part"] = obj.name
            obj["adsl_provenance"] = "retrieved_cc0_asset"
            placed.append(obj)
            created += 1
        # Whatever did not fit is still sitting at the imported run's own
        # position, which is out in the middle of the floor. Remove it instead
        # of leaving stray volumes lying in front of the sofa.
        for obj in subset:
            if obj not in placed:
                bpy.data.objects.remove(obj, do_unlink=True)
        runs += 1

    return {"id": oid, "populated": created, "bays": len(bays),
            "asset": asset_id, "runs": runs, "per_bay": per_bay}


def build_retrieved(entry: dict) -> dict:
    oid = entry["id"]
    fit = entry.get("fit") or {}
    asset_id = fit.get("asset")
    gltf = ASSET_ROOT / fit["file"]
    if not gltf.is_file():
        raise FileNotFoundError(str(gltf))

    dims = entry["dimensions"]
    order = entry.get("axis_order", "wdh")
    if order == "twh":                      # wall-hung: (thickness, width, height)
        size = (dims[1], dims[0], dims[2])
    else:                                   # floor furniture: (width, depth, height)
        size = (dims[0], dims[1], dims[2])

    pl = entry["placement"] or {}
    yaw = float(pl.get("yaw_deg", 0.0))
    z = float(pl.get("z", 0.0))
    x = float(pl.get("x", 0.0))
    y = float(pl.get("y", 0.0))
    if order == "twh":                      # wall items carry their root pose
        # The wall face is no longer re-derived here. The planner resolves a hung
        # object onto its wall from the measured room (position, yaw and height),
        # so re-anchoring in assembly would override a decision with a guess --
        # and hide the case where the two disagree. Mismatches are reported by
        # snap_group_to_pose instead, which is a measurement, not a repair.
        pass
    position = (x, y, z)

    if entry.get("fit_mode") == "shallow":
        size = (size[0], min(size[1], 0.45), size[2])

    before = set(bpy.data.objects)
    load_asset(gltf)
    imported = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    if not imported:
        raise RuntimeError(f"import produced no mesh for {asset_id}")

    copies = fitted_copy(imported, oid, size, position, rotation=yaw)
    for obj in imported:
        bpy.data.objects.remove(obj, do_unlink=True)
    for obj in copies:
        obj["adsl_asset_id"] = asset_id
        obj["finescene_component_id"] = oid

    mats = retarget_materials(copies, oid)
    # fitted_copy already placed these in world space. Parenting must not move
    # them again -- and assigning matrix_world after obj.parent does exactly
    # that: Blender converts the value using the parent's *depsgraph* matrix,
    # which is still identity for an Empty created in the same tick, so the
    # child's local location was left holding a world coordinate and the root
    # translation got applied a second time. The plant ended up at (5.03, 3.94)
    # -- twice its intended (2.55, 1.95) -- and the wall art through the west
    # wall. Compute the local matrix directly instead of relying on the update.
    world_before = {obj.name: obj.matrix_world.copy() for obj in copies}
    root, col = attach_to_root(oid, asset_id, position, yaw, copies)
    # Build the parent matrix from the pose we just wrote rather than reading
    # root.matrix_world: a freshly created Empty has no evaluated matrix until
    # the depsgraph runs, so reading it yields identity and the correction
    # silently becomes a no-op (that is why the plant stayed at 2x its target
    # even after the first two attempts at this fix).
    parent_world = (Matrix.Translation(Vector(position))
                    @ Matrix.Rotation(math.radians(yaw), 4, "Z"))
    root_inv = parent_world.inverted()
    for obj in copies:
        obj.parent = root
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_basis = root_inv @ world_before[obj.name]
    bpy.context.view_layer.update()

    return {"id": oid, "asset": asset_id, "file": str(gltf),
            "meshes": len(copies), "size": [round(v, 3) for v in size],
            "position": [round(v, 3) for v in position], "yaw": yaw,
            "materials": mats}


def half_projection(dims, yaw_deg: float) -> tuple:
    """Half extents of a rotated footprint on the world X and Y axes."""
    w, d = float(dims[0]) / 2.0, float(dims[1]) / 2.0
    a = math.radians(yaw_deg)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return w * c + d * s, w * s + d * c


def door_span(axis: str):
    """Opening span of the door in the borrowed shell, measured from geometry."""
    lo, hi = 1e9, -1e9
    for obj in bpy.data.objects:
        if obj.get("adsl_object_id") != "room_shell" or obj.type != "MESH":
            continue
        if "door" not in str(obj.get("adsl_part", obj.name)).lower():
            continue
        idx = 0 if axis == "x" else 1
        vals = [(obj.matrix_world @ Vector(c))[idx] for c in obj.bound_box]
        lo, hi = min(lo, min(vals)), max(hi, max(vals))
    return (lo, hi) if hi > lo else None


# NOTE: assembly performs no geometric corrections. It used to slide pieces off
# the door opening and nudge groups onto their target centre; both moved the scene
# after it had been planned, so the gate was judging a room that no file
# described. Poses come from layout_finalized.json and are applied verbatim.



def group_bounds(oid: str):
    meshes = [o for o in bpy.data.objects
              if o.type == "MESH" and o.get("adsl_object_id") == oid]
    if not meshes:
        return None
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for obj in meshes:
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            lo = Vector((min(lo[i], w[i]) for i in range(3)))
            hi = Vector((max(hi[i], w[i]) for i in range(3)))
    return lo, hi, len(meshes)


def snap_group_to_pose(entry: dict, tol: float = 0.02) -> dict | None:
    """Assert that a group actually landed where the layout asked it to.

    A parenting bug put the plant at (5.1, 3.9) -- exactly twice its intended
    (2.55, 1.95) -- and the wall art outside the room, while every per-asset
    function reported success. So the assembly measures the built geometry
    instead of trusting the transform chain, and records the measurement even
    when nothing needs moving: a pose log with every object in it is the only
    way to tell "correctly placed" from "never measured".
    """
    oid = entry.get("id")
    pl = entry.get("placement") or {}
    if not oid or not pl:
        return None
    got = group_bounds(oid)
    if got is None:
        return None
    lo, hi, count = got

    # Assert that the ROOT landed on the pose the layout asked for. The previous
    # version compared the bounding-box centre and then translated by the error,
    # i.e. it quietly re-placed anything whose centre differed from its origin --
    # a repair hiding behind a measurement. A pose is authoritative now, so this
    # only reports drift.
    roots = set()
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.get("adsl_object_id") != oid:
            continue
        top = obj
        while top.parent is not None:
            top = top.parent
        roots.add(top)
    pose = [float(pl.get(k, 0.0)) for k in ("x", "y", "z")]
    row = {"id": oid, "meshes": count, "moved": False,
           "pose": [round(v, 3) for v in pose],
           "roots": sorted(r.name for r in roots),
           "span": [[round(v, 3) for v in (lo.x, hi.x)],
                    [round(v, 3) for v in (lo.y, hi.y)],
                    [round(v, 3) for v in (lo.z, hi.z)]]}
    if len(roots) == 1:
        root = next(iter(roots))
        world = [round(v, 3) for v in root.matrix_world.translation]
        drift = [round(world[i] - pose[i], 3) for i in range(3)]
        row["root_world"] = world
        row["drift"] = drift
        if any(abs(d) > 0.005 for d in drift):
            row["mismatch"] = True
    elif len(roots) > 1:
        row["mismatch"] = True
        row["reason"] = f"{len(roots)} independent roots for one object"
    return row


def build_curtain(entry: dict) -> dict:
    """Two gathered panels either side of the window, plus a timber rod.

    Curtains have no retrieved asset and no interesting silhouette beyond their
    folds, so this is profile geometry with a woven PBR - the same category as
    the rug, not a stand-in for furniture the agent should have modelled.
    """
    oid = entry["id"]
    pl = entry["placement"] or {}
    w, d, h = (float(v) for v in entry["dimensions"])
    panel_w = w * 0.16
    panel_h = 2.22
    z0 = 0.12
    rod_z = z0 + panel_h + 0.04
    y0 = float(pl.get("y", 0.0))

    mat = woven("Curtain | linen", (0.52, 0.49, 0.44), (0.42, 0.40, 0.36),
                (0.62, 0.59, 0.53), weave=200.0, roughness=0.90)

    meshes = []
    for side, cx in (("west", -w * 0.5 + panel_w * 0.35),
                     ("east", w * 0.5 - panel_w * 0.35)):
        nx, nz = 16, 10
        verts, faces = [], []
        folds = 3.5
        amp = 0.035
        for iz in range(nz + 1):
            z = z0 + panel_h * iz / nz
            # Panels taper slightly at the bottom as real gathered fabric does.
            spread = 1.0 + 0.10 * (1.0 - iz / nz)
            for ix in range(nx + 1):
                t = ix / nx
                x = cx + (t - 0.5) * panel_w * spread
                y = y0 + amp * math.sin(2 * math.pi * folds * t)
                verts.append((x, y, z))
        for iz in range(nz):
            for ix in range(nx):
                a = iz * (nx + 1) + ix
                faces.append((a, a + 1, a + nx + 2, a + nx + 1))
        me = bpy.data.meshes.new(f"curtain_{side}_mesh")
        me.from_pydata(verts, [], faces)
        me.update()
        ob = bpy.data.objects.new(f"curtain_{side}", me)
        ob.data.materials.append(mat)
        for poly in ob.data.polygons:
            poly.use_smooth = True
        bpy.context.scene.collection.objects.link(ob)
        meshes.append(ob)

    # rod
    # A cylinder is born along +Z; rotate about Y to lay it along X.
    bpy.ops.mesh.primitive_cylinder_add(radius=0.016, depth=w + 0.28,
                                        location=(0, y0 - 0.02, rod_z),
                                        rotation=(0, math.radians(90), 0))
    rod = bpy.context.active_object
    rod.name = "curtain_rod"
    rodm = bpy.data.materials.new("Rod | walnut")
    rodm.use_nodes = True
    b = rodm.node_tree.nodes.get("Principled BSDF")
    if b:
        b.inputs["Base Color"].default_value = (0.14, 0.085, 0.05, 1)
        b.inputs["Roughness"].default_value = 0.42
    rod.data.materials.append(rodm)
    meshes.append(rod)

    root, col = attach_to_root(oid, "window curtain",
                               (0, 0, 0), float(pl.get("yaw_deg", 0)), meshes)
    for obj in meshes:
        obj.parent = root
        obj.matrix_parent_inverse = root.matrix_world.inverted()
    return {"id": oid, "mode": "procedural", "panels": 2,
            "size": [round(w, 3), round(d, 3), round(panel_h, 3)],
            "material": "procedural linen weave"}


def build_rug(entry: dict) -> dict:
    """A rug has no silhouette worth modelling; its quality is weave + bound edge."""
    oid = entry["id"]
    dims = entry["dimensions"]
    pl = entry["placement"] or {}
    w, d, h = dims[0], dims[1], max(dims[2], 0.028)

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(pl.get("x", 0), pl.get("y", 0), h / 2))
    obj = bpy.context.active_object
    obj.name = f"{oid}/pile"
    obj.scale = (w, d, h)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = obj.modifiers.new("bound_edge", "BEVEL")
    bevel.width = 0.012
    bevel.segments = 3
    bpy.ops.object.modifier_apply(modifier="bound_edge")

    mat = woven("Rug | wool flatweave", (0.34, 0.31, 0.27), (0.30, 0.28, 0.25),
                (0.46, 0.43, 0.38), weave=380.0)
    obj.data.materials.append(mat)

    root, col = attach_to_root(oid, "area rug", (pl.get("x", 0), pl.get("y", 0), 0),
                               float(pl.get("yaw_deg", 0)), [obj])
    obj.parent = root
    obj.matrix_parent_inverse = root.matrix_world.inverted()
    return {"id": oid, "mode": "procedural", "size": [w, d, h],
            "material": "fabric_pattern_07"}


def build_generated(entry: dict) -> dict:
    """Load an Agent-forged object: the Builder hierarchy is already correct, so
    the only work is re-rooting it at the SceneCode pose."""
    oid = entry["id"]
    blend = FORGE_OUT / oid / f"{oid}.blend"
    if not blend.is_file():
        raise FileNotFoundError(str(blend))

    with bpy.data.libraries.load(str(blend), link=False) as (src, dst):
        dst.objects = list(src.objects)
    loaded = [o for o in dst.objects if o is not None]

    root = next((o for o in loaded if o.name.startswith("ROOT_")), None)
    if root is None:
        root = next((o for o in loaded if o.parent is None and o.type == "EMPTY"), None)
    if root is None:
        raise RuntimeError(f"{blend.name} has no root empty")

    def descendants(node):
        out = []
        for child in node.children:
            out.append(child)
            out.extend(descendants(child))
        return out

    keep = [root] + descendants(root)
    keep = [o for o in keep if o.type == "MESH"]
    root_objs = [o for o in loaded if o.name.startswith("ROOT_")]

    # Forge renders with its own studio rig; drop everything that is not part
    # of the object itself.
    drop = [o for o in loaded if o not in keep and o not in root_objs]
    for obj in drop:
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except RuntimeError:
            pass

    col = bpy.data.collections.new(f"OBJ_{oid}")
    scene_col = (bpy.context.scene.collection.children.get("SCENE")
                 or bpy.context.scene.collection)
    scene_col.children.link(col)
    for obj in [root] + keep:
        col.objects.link(obj)

    pl = entry["placement"] or {}
    yaw = float(pl.get("yaw_deg", 0.0))
    # Pose z is the ROOT height now, for every mount: the planner derives it from
    # the measured local bounds (a centred panel's origin is not its bottom edge)
    # and from the ceiling plane for pendants.
    z = float(pl.get("z", 0.0))
    if entry.get("mount") == "ceiling" and abs(z) < 1e-6:
        # A layout that reports 0 for a pendant predates the derivation; say so
        # rather than silently hoisting it and pretending the pose was honoured.
        print(f"[assets] {oid:18s} <- ceiling pose missing, using room height "
              f"(stale layout?)", flush=True)
        z = ROOM_HEIGHT
    pos = (float(pl.get("x", 0.0)), float(pl.get("y", 0.0)), z)
    root.location = pos
    root.rotation_euler.z = math.radians(yaw)
    root["adsl_object_id"] = oid
    root["adsl_label"] = oid
    root["adsl_origin"] = list(root.location)
    root["adsl_yaw_deg"] = yaw
    root["adsl_provenance"] = "agent_forged_finescene_builder"
    for obj in keep:
        obj["adsl_object_id"] = oid
        obj["adsl_part"] = obj.name
        obj["adsl_provenance"] = "agent_forged_finescene_builder"
    # matrix_world is stale until the depsgraph runs; callers read it to place
    # children (the book runs) and would otherwise get the identity matrix.
    bpy.context.view_layer.update()

    # Pose z is the root height, derived by the planner from the measured local
    # bounds. The old drop-and-squash corrections lived here; both were repairs
    # of an upstream contract violation (a centred origin, an over-wide leaf) and
    # moved the failure to a stage that could not fix the program. The forge now
    # rejects an over-envelope build and the planner derives z, so anything left
    # to correct here is a bug worth seeing, not smoothing over.
    got = group_bounds(oid)
    if got is not None:
        lo, hi, _ = got
        drift = round(lo.z - float(pl.get("z", 0.0)), 3)
        if abs(drift) > 0.02:
            root["adsl_pose_drift"] = drift
            print(f"[assets] {oid:18s} <- bottom edge is {drift:+.3f} m off the pose "
                  f"(planner and asset disagree on local_z)", flush=True)

    return {"id": oid, "mode": "generated", "source_blend": str(blend),
            "meshes": len(keep), "position": [round(v, 3) for v in root.location],
            "yaw": yaw}


def rebuild_shell_surfaces() -> dict:
    """Give the borrowed architectural shell real floor and plaster PBR."""
    groups = object_groups()
    touched = {"floor": 0, "wall": 0}
    floor_mat = pbr("Floor | oak plank", "wood_floor", scale=1.0)
    wall_mat = pbr("Wall | lime plaster", "plastered_wall_04", scale=1.0)
    touched["normal_fixed"] = box_project_normals(wall_mat, scale=2.5)
    for obj in groups.get("room_shell", []):
        if obj.type != "MESH":
            continue
        part = str(obj.get("adsl_part", obj.name)).lower()
        if "floor" in part:
            obj.data.materials.clear()
            obj.data.materials.append(floor_mat)
            touched["floor"] += 1
        # door_lintel is the 0.80 m of wall above the opening, not joinery: it
        # kept the shell's cream placeholder and read as a painted panel stuck
        # over the plaster. The jambs stay light joinery, which is correct.
        elif ("wall" in part and "wall_art" not in part) or "lintel" in part:
            obj.data.materials.clear()
            obj.data.materials.append(wall_mat)
            touched["wall"] += 1
    return touched


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def ensure_camera(name: str, loc, target, lens: float):
    cam = bpy.data.objects.get(name)
    if cam is None:
        cd = bpy.data.cameras.new(name)
        cam = bpy.data.objects.new(name, cd)
        bpy.context.scene.collection.objects.link(cam)
    cam.data.lens = lens
    cam.location = loc
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return cam


def relight() -> dict:
    """Replace the borrowed DIMMER-style rig with broad soft sources.

    The shell ships four lights shaped DISK with size 3.0 / 2.2 / 2.6 m, parked
    0.4-0.7 m off the plaster. A DISK's `size` is its DIAMETER, and `size_y` is
    silently ignored, so each of those was a 3 m glowing disc -- and the edge of
    a glowing disc half a metre from a wall is a hard line in the shading. That
    is the horizontal band the review kept calling "water staining": killing one
    light at a time never removed it because two others drew their own edges a
    few centimetres away, on the same wall.

    Keep the intent (overhead key, window fill, side fill, low sun) and give
    each source a wide rectangular aperture aimed into the room, well clear of
    the walls. Under the same four lights the plaster now shades evenly.
    """
    want = {
        # name: (size_x, size_y, location, rotation, energy)
        "Key_South":   (2.60, 2.60, (0.0, -0.25, 2.80), (0.0, 0.0, 0.0), 195.0),
        "Window_Fill": (1.90, 1.30, (0.0, 3.05, 1.55), (math.pi / 2, 0.0, math.pi), 145.0),
        "Side_Fill":   (2.20, 1.60, (-2.55, 0.30, 2.20), (1.10, 0.0, -math.pi / 2), 70.0),
    }
    done = []
    for name, (sx, sy, loc, rot, watts) in want.items():
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "LIGHT":
            continue
        before = {"shape": obj.data.shape, "size": round(obj.data.size, 3),
                  "energy": round(obj.data.energy, 1)}
        obj.data.shape = "RECTANGLE"
        obj.data.size, obj.data.size_y = sx, sy
        obj.location = loc
        obj.rotation_euler = rot
        obj.data.energy = watts
        done.append({"light": name, "from": before,
                     "to": {"shape": "RECTANGLE", "size": [sx, sy],
                            "energy": watts,
                            "location": [round(v, 3) for v in loc]}})
    bpy.context.view_layer.update()
    return {"relit": done,
            "why": "DISK sizes were read as radii; each source was a 3 m disc "
                   "0.5 m off the wall and its edge drew a hard band on the "
                   "plaster. Wide rectangular apertures aim the same four "
                   "sources into the room instead."}


def render_views(tag: str = "v2") -> dict:
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

    cam_cut = bpy.data.objects.get("Camera_Cutaway") or ensure_camera(
        "Camera_Cutaway", (5.9, -6.9, 4.5), (0.0, 0.35, 1.00), 30)
    cam_in = bpy.data.objects.get("Camera_Interior") or ensure_camera(
        "Camera_Interior", (0.0, -2.35, 1.68), (0.0, 0.85, 0.95), 18)
    cam_west = ensure_camera("Camera_WestCutaway", (-5.9, -6.6, 4.3), (0.0, 0.4, 1.0), 30)
    # Inside the room: a camera parked outside the south wall only ever sees the
    # untrimmed back of that wall.
    cam_corner = ensure_camera("Camera_Corner", (2.15, -1.95, 1.66), (-0.55, 1.15, 0.92), 22)
    # The media wall is the south wall, which every other interior shot has
    # behind the camera -- and the cutaway views remove that wall entirely, so
    # the console was invisible in all four. Pulled back into the room corner
    # and widened to 18 mm so the sofa, the console and the screen all land in
    # one frame: a reviewer who only sees the console can't tell whether the
    # seating actually faces it.
    cam_media = ensure_camera("Camera_Media", (2.35, 2.25, 1.72), (-0.85, -2.30, 0.92), 18)

    hide_keys = ("south", "wall_east", "wall_west", "skirting_south",
                 "skirting_west", "door_jamb", "door_lintel", "ceiling")
    hidden = [o for o in bpy.data.objects
              if o.get("adsl_object_id") == "room_shell"
              and any(k in str(o.get("adsl_part", "")).lower() for k in hide_keys)]
    # Anything mounted on a wall that the cutaway removes has to go with it,
    # otherwise the frame / cabinet hangs in mid-air. FineScene's own cutaway
    # hides wall_art_west for exactly this reason.
    mounted = [o for o in bpy.data.objects
               if o.get("adsl_object_id") in {"wall_art_west", "tv_cabinet",
                                              "wall_tv", "entry_door"}]
    hidden.extend(o for o in mounted if o not in hidden)

    shots = [("view_000", cam_cut, True), ("view_001", cam_in, False),
             ("view_002", cam_west, True), ("view_003", cam_corner, False),
             ("view_004", cam_media, False)]
    written = []
    for name, cam, cutaway in shots:
        for obj in hidden:
            obj.hide_render = cutaway
        sc.camera = cam
        sc.render.filepath = str(OUT / f"{tag}_{name}.png")
        bpy.ops.render.render(write_still=True)
        written.append(str(OUT / f"{tag}_{name}.png"))
    for obj in hidden:
        obj.hide_render = True
    return {"views": written, "hidden_parts": len(hidden)}


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    supply = json.loads(SUPPLY_PATH.read_text(encoding="utf-8"))
    payload = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    # layout_finalized.json keeps `objects` at the top level.
    layout = payload.get("layout") or payload
    layout_by_id = {o["id"]: o for o in layout.get("objects", [])}

    # The build must consume the AUTHORITATIVE pose from layout_finalized.json,
    # not the stale pose the supply plan copied. The planner + finalize step
    # derive the back-to-wall yaw (see layout_finalize.derive_against_wall_yaw);
    # the supply plan only carries inventory + mode. Merging the finalized
    # placement (and its relations) into each entry guarantees the assembly uses
    # the derived-correct orientation instead of a possibly reversed seed.
    for entry in supply["objects"]:
        lp = layout_by_id.get(entry["id"])
        if lp:
            entry["placement"] = dict(lp.get("placement") or entry.get("placement") or {})
            if lp.get("relations"):
                entry["relations"] = lp["relations"]
    # Geometric guard: re-derive every backed floor object's yaw from the actual
    # wall normal so a reversed "bookshelf facing the wall" can never ship, even
    # if a pose file is somehow stale. This is derivation, not a relocation.
    room = json.loads(ROOM_PATH.read_text(encoding="utf-8"))
    changed = derive_against_wall_yaw(supply["objects"], room)
    if changed:
        print("[assets] re-derived back-to-wall yaw for:", changed, flush=True)

    bpy.ops.wm.open_mainfile(filepath=str(BASE_BLEND))
    REPORT["opened"] = str(BASE_BLEND)
    # The opening is the floor-plan stage's decision, so the cut follows it
    # rather than a constant in this file. Cutting the hole is derivation; the
    # *where* was decided upstream and is read from the layout.
    door_x = ((layout.get("room_plan") or {}).get("door") or {}).get("center_x")
    REPORT["door"] = relocate_door(float(door_x if door_x is not None else DOOR_CENTER_X))
    if REPORT["door"]:
        print(f"[assets] door opening moved to x={REPORT['door']['opening_x']}", flush=True)
    groups = object_groups()
    REPORT["shell_parts"] = len([o for o in groups.get("room_shell", []) if o.type == "MESH"])
    print(f"[assets] opened base blend, shell parts={REPORT['shell_parts']}", flush=True)

    # clear every furniture group: their geometry is the editability fixture
    for entry in supply["objects"]:
        oid = entry["id"]
        if oid == "room_shell":
            continue
        n = drop_object_group(oid, groups)
        if n:
            REPORT["removed_objects"].append({"id": oid, "objects": n})

    REPORT["shell_surfaces"] = rebuild_shell_surfaces()

    for entry in supply["objects"]:
        oid = entry["id"]
        mode = entry["mode"]
        if oid == "room_shell":
            continue
        placed = False
        if mode == "retrieved":
            try:
                REPORT["imported"].append(build_retrieved(entry))
                print(f"[assets] {oid:18s} <- {REPORT['imported'][-1]['asset']}", flush=True)
                placed = True
            except Exception as exc:  # noqa: BLE001
                REPORT["errors"].append({"id": oid, "error": f"{type(exc).__name__}: {exc}"})
                print(f"[assets] {oid} FAILED {exc}", flush=True)
        elif mode in ("generated", "retrieved+generated"):
            try:
                built = build_generated(entry)
                REPORT["imported"].append(built)
                placed = True
                print(f"[assets] {oid:18s} <- forged blend ({built['meshes']} meshes)",
                      flush=True)
                if mode == "retrieved+generated":
                    books = populate_shelf(entry)
                    REPORT.setdefault("populated", []).append(books)
                    print(f"[assets] {oid:18s} <- +{books['populated']} book runs",
                          flush=True)
            except Exception as exc:  # noqa: BLE001
                REPORT["errors"].append({"id": oid, "error": f"{type(exc).__name__}: {exc}"})
                print(f"[assets] {oid} FAILED {exc}", flush=True)
        elif mode == "procedural" and oid == "area_rug":
            try:
                REPORT["procedural"].append(build_rug(entry))
                print(f"[assets] {oid:18s} <- procedural fabric", flush=True)
                placed = True
            except Exception as exc:  # noqa: BLE001
                REPORT["errors"].append({"id": oid, "error": f"{type(exc).__name__}: {exc}"})
        elif mode == "procedural" and oid == "window_curtain":
            try:
                REPORT["procedural"].append(build_curtain(entry))
                print(f"[assets] {oid:18s} <- procedural linen panels", flush=True)
                placed = True
            except Exception as exc:  # noqa: BLE001
                REPORT["errors"].append({"id": oid, "error": f"{type(exc).__name__}: {exc}"})
                print(f"[assets] {oid} FAILED {exc}", flush=True)
        else:
            REPORT["skipped"].append({"id": oid, "mode": mode,
                                      "reason": "awaiting aDSL agent generation"})

        # Caliper check: measure what was actually built against the pose the
        # clearance solver asked for. Every per-object function can report
        # success while the transform chain quietly applied the root twice, so
        # the assembly trusts the geometry, not the functions.
        if placed:
            fix = snap_group_to_pose(entry)
            if fix:
                REPORT.setdefault("poses", []).append(fix)
                if fix.get("moved"):
                    print(f"[assets] {oid:18s} <- position corrected by "
                          f"({fix['dx']:+.3f}, {fix['dy']:+.3f}) m, residual "
                          f"{fix['residual']}", flush=True)
                else:
                    drift = fix.get("drift") or [0.0, 0.0, 0.0]
                    flag = " DRIFT" if fix.get("mismatch") else ""
                    print(f"[assets] {oid:18s} <- pose ok at "
                          f"({fix['pose'][0]}, {fix['pose'][1]}, {fix['pose'][2]}) "
                          f"drift=({drift[0]:+.3f}, {drift[1]:+.3f}, {drift[2]:+.3f})"
                          f"{flag}", flush=True)

    REPORT["lighting"] = relight()
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "living_room_assets.blend"))
    render = render_views()
    REPORT["render"] = render

    # The render pass is what adds Camera_Corner / Camera_Media and switches the
    # scene to the 64-sample 1280x800 preset. Saving only before it left the
    # delivered .blend on the borrowed 16-sample preset with two of the six
    # cameras missing, so the file did not describe the images it shipped with.
    # Restore a neutral viewing state (no cutaway hiding) and save once more.
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.hide_render = False
            obj.hide_set(False)
    scene = bpy.context.scene
    scene.camera = bpy.data.objects.get("Camera_Corner") or scene.camera
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "living_room_assets.blend"))

    (OUT / "ASSET_SCENE_REPORT.json").write_text(
        json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ASSET_SCENE=" + json.dumps({
        "imported": len(REPORT["imported"]),
        "procedural": len(REPORT["procedural"]),
        "skipped": [s["id"] for s in REPORT["skipped"]],
        "moved": [p_["id"] for p_ in REPORT["poses"] if p_.get("moved")],
        "errors": REPORT["errors"],
        "views": len(render["views"]),
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
