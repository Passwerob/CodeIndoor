"""Forge a single furniture item from an Agent-authored program (Blender side).

Why a factory call was not enough
---------------------------------
adsl_components.FACTORIES has exactly four kinds (bookshelf, shelf, desk, chair)
and compiles each into a handful of boxes. That is why the first living-room
assembly looked like stacked blocks. FineScene's own best furniture -- the
Baroque canape, the wing armchair, the chandelier -- is not built by those
factories either. It is built by `baroque_lib.Builder` plus its ornament
generators (volute, cabriole_leg, acanthus_leaf, torus, fluted_column) on top of
real 2k PBR maps.

So the Agent is given exactly that toolkit and writes the part list itself:

    def build(B, mats, P):
        B.box("seat", (P["width"]*0.9, 0.7, 0.16), (0, 0, 0.42), "linen_sand", bevel=0.03)
        ...
        return B.objects_created()

Nothing in this file decides furniture shape. It supplies the DSL, runs the
Agent's program, renders three views, and reports what was built.

Invoked by furniture_forge.py -- never run by hand.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

BAROQUE = Path(r"E:/3DUse/FineScene/deliveries/baroque_reception_pbr_20261104")
FINESCENE_SRC = Path(r"E:/3DUse/FineScene/src")
ASSETS = Path(r"E:/3DUse/assets/external/realistic_study_20261019")
ADAPTER = Path(__file__).resolve().parent

for p in (str(BAROQUE), str(FINESCENE_SRC), str(ADAPTER)):
    if p not in sys.path:
        sys.path.insert(0, p)

import baroque_lib as BL  # noqa: E402
from baroque_lib import (Builder, volute, cabriole_leg, acanthus_leaf,  # noqa: E402
                         torus, fluted_column, corinthian_capital, dentil_row)
from curve_dsl import (sweep, bezier_tube, piping_loop, lathe,  # noqa: E402
                       arc_tube)

# --------------------------------------------------------------------------- #
# material set for interior furniture (the Baroque palette is velvets + gilt;
# an interior living room needs neutral textiles and honest joinery)
# --------------------------------------------------------------------------- #
MODERN_CFG = {
    "linen_sand":     dict(tile=0.55, metallic=0.0, sheen=0.18, coat=0.0,
                           tint=(0.78, 0.73, 0.64), tint_amount=0.82),
    "boucle_grey":    dict(tile=0.48, metallic=0.0, sheen=0.22, coat=0.0,
                           tint=(0.62, 0.61, 0.58), tint_amount=0.80),
    "linen_charcoal": dict(tile=0.55, metallic=0.0, sheen=0.16, coat=0.0,
                           tint=(0.30, 0.31, 0.33), tint_amount=0.84),
    "oak_joinery":    dict(tile=0.95, metallic=0.0, sheen=0.0, coat=0.24),
    "walnut_joinery": dict(tile=0.90, metallic=0.0, sheen=0.0, coat=0.26),
    "brass_warm":     dict(tile=0.40, metallic=0.85, sheen=0.0, coat=0.0,
                           specular=0.5),
    "ceramic_matte":  dict(tile=0.60, metallic=0.0, sheen=0.0, coat=0.0),
    "lamp_shade":     dict(tile=0.60, metallic=0.0, sheen=0.30, coat=0.0,
                           tint=(0.90, 0.86, 0.76), tint_amount=0.75),
}

MODERN_FALLBACK = {
    "linen_sand": (0.74, 0.69, 0.60, 1),
    "boucle_grey": (0.58, 0.57, 0.54, 1),
    "linen_charcoal": (0.24, 0.25, 0.27, 1),
    "oak_joinery": (0.46, 0.33, 0.20, 1),
    "walnut_joinery": (0.28, 0.17, 0.10, 1),
    "brass_warm": (0.72, 0.55, 0.28, 1),
    "ceramic_matte": (0.86, 0.84, 0.80, 1),
    "lamp_shade": (0.88, 0.84, 0.74, 1),
}

_FABRIC = ASSETS / "fabric_pattern_07" / "textures"
_WOOD = ASSETS / "wood_floor" / "textures"
_POLYHAVEN = "https://polyhaven.com/a/"


def _fabric_set(asset: str) -> dict:
    return {"channels": {
        "diff": str(_FABRIC / "fabric_pattern_07_col_1_2k.jpg"),
        "nor": str(_FABRIC / "fabric_pattern_07_nor_gl_2k.jpg"),
        "rough": str(_FABRIC / "fabric_pattern_07_rough_2k.jpg")},
        "source_url": _POLYHAVEN + asset, "resolution": "2k"}


MODERN_MANIFEST = {"materials": {
    "linen_sand": _fabric_set("fabric_pattern_07"),
    "boucle_grey": _fabric_set("fabric_pattern_07"),
    "linen_charcoal": _fabric_set("fabric_pattern_07"),
    "oak_joinery": {"channels": {
        "diff": str(_WOOD / "wood_floor_diff_2k.jpg"),
        "nor": str(_WOOD / "wood_floor_nor_gl_2k.jpg"),
        "arm": str(_WOOD / "wood_floor_arm_2k.jpg")},
        "source_url": _POLYHAVEN + "wood_floor", "resolution": "2k"},
}}

# Baroque manifest supplies walnut / marble / brass texture sets by key.
_BAROQUE_MANIFEST = json.loads((BAROQUE / "texture_manifest.json").read_text(encoding="utf-8"))


def _principled(name: str):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.is_active_output = True
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat, nt, bsdf


def _noise(nt, scale, detail=3.0, stretch=None):
    tc = nt.nodes.new("ShaderNodeTexCoord")
    vec = tc.outputs["Generated"]
    if stretch:
        mul = nt.nodes.new("ShaderNodeVectorMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = stretch
        nt.links.new(vec, mul.inputs[0])
        vec = mul.outputs[0]
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = scale
    n.inputs["Detail"].default_value = detail
    nt.links.new(vec, n.inputs["Vector"])
    return n


def _ramp(nt, fac_socket, dark, light, p0=0.25, p1=0.80):
    r = nt.nodes.new("ShaderNodeValToRGB")
    r.color_ramp.elements[0].position = p0
    r.color_ramp.elements[0].color = (*dark, 1)
    r.color_ramp.elements[1].position = p1
    r.color_ramp.elements[1].color = (*light, 1)
    nt.links.new(fac_socket, r.inputs[0])
    return r


def _bump(nt, height_socket, bsdf, strength, distance):
    b = nt.nodes.new("ShaderNodeBump")
    b.inputs["Strength"].default_value = strength
    b.inputs["Distance"].default_value = distance
    nt.links.new(height_socket, b.inputs["Height"])
    nt.links.new(b.outputs["Normal"], bsdf.inputs["Normal"])
    return b


def fabric(name: str, base, dark, light, weave=210.0, roughness=0.80, sheen=0.30):
    """Woven textile: fine cross-hatch bump + tonal drift, exactly the recipe
    FineScene's present.py uses for upholstery (which is where the Oakwarden's
    leather read came from). No tiled colour map, so no accidental plaid."""
    mat, nt, bsdf = _principled(name)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Sheen Weight"].default_value = sheen
    bsdf.inputs["Sheen Roughness"].default_value = 0.6
    bsdf.inputs["Sheen Tint"].default_value = (1, 1, 1, 1)
    weave_noise = _noise(nt, weave, detail=2.0)
    slub = _noise(nt, 18.0, detail=4.0, stretch=(9.0, 9.0, 0.35))
    mix = nt.nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "OVERLAY"
    mix.inputs["Factor"].default_value = 0.55
    nt.links.new(weave_noise.outputs["Fac"], mix.inputs["Color1"])
    nt.links.new(slub.outputs["Fac"], mix.inputs["Color2"])
    nt.links.new(_ramp(nt, mix.outputs[0], dark, light).outputs[0],
                 bsdf.inputs["Base Color"])
    _bump(nt, weave_noise.outputs["Fac"], bsdf, 0.26, 0.0009)
    mat["adsl_material"] = name
    mat["pbr_source"] = "procedural (noise weave) following FineScene present.py"
    return mat


def wood(name: str, dark, light, grain=5.0, coat=0.25):
    mat, nt, bsdf = _principled(name)
    bsdf.inputs["Roughness"].default_value = 0.38
    bsdf.inputs["Coat Weight"].default_value = coat
    bsdf.inputs["Coat Roughness"].default_value = 0.16
    n = _noise(nt, grain, detail=5.0, stretch=(7.0, 8.0, 0.55))
    nt.links.new(_ramp(nt, n.outputs["Fac"], dark, light, 0.18, 0.84).outputs[0],
                 bsdf.inputs["Base Color"])
    _bump(nt, n.outputs["Fac"], bsdf, 0.20, 0.0016)
    mat["adsl_material"] = name
    mat["pbr_source"] = "procedural (noise grain)"
    return mat


def solid(name: str, rgb, metallic=0.0, roughness=0.5, emission=None):
    mat, nt, bsdf = _principled(name)
    bsdf.inputs["Base Color"].default_value = (*rgb, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission[:3], 1)
        bsdf.inputs["Emission Strength"].default_value = emission[3]
    if metallic > 0.5:
        n = _noise(nt, 40.0, detail=2.0)
        _bump(nt, n.outputs["Fac"], bsdf, 0.10, 0.0004)
    mat["adsl_material"] = name
    return mat


def build_materials() -> dict:
    """Neutral interior palette.

    The asset library's only fabric scan is a red check (fabric_pattern_07), and
    multiplying it toward beige left the plaid legible. Tiled colour maps also
    fight a parametric object because UVs are generated per part. So upholstery
    and timber are procedural here -- the same approach that produced the
    Oakwarden's leather and oak -- and the scanned maps stay for surfaces where
    a real photo is the point (floor, plaster, rug).
    """
    return {
        "linen_sand": fabric("Fabric | linen sand", (0.72, 0.67, 0.58),
                             (0.50, 0.46, 0.39), (0.80, 0.75, 0.66)),
        "boucle_grey": fabric("Fabric | boucle grey", (0.60, 0.59, 0.56),
                              (0.40, 0.40, 0.38), (0.72, 0.71, 0.68),
                              weave=260.0, sheen=0.38),
        "linen_charcoal": fabric("Fabric | charcoal", (0.20, 0.21, 0.23),
                                 (0.10, 0.10, 0.12), (0.30, 0.31, 0.34)),
        "oak_joinery": wood("Timber | oak", (0.18, 0.11, 0.055),
                            (0.46, 0.31, 0.17), grain=6.0, coat=0.22),
        "walnut_joinery": wood("Timber | walnut", (0.055, 0.028, 0.015),
                               (0.20, 0.105, 0.055), grain=4.5, coat=0.30),
        "brass_warm": solid("Metal | aged brass", (0.62, 0.44, 0.19),
                            metallic=0.92, roughness=0.30),
        "ceramic_matte": solid("Ceramic | chalk", (0.82, 0.80, 0.76),
                               metallic=0.0, roughness=0.55),
        "marble_cream": solid("Stone | marble cream", (0.78, 0.75, 0.69),
                              metallic=0.0, roughness=0.22),
        "lamp_shade": solid("Shade | warm linen", (0.86, 0.80, 0.68),
                            metallic=0.0, roughness=0.75,
                            emission=(1.0, 0.82, 0.55, 0.45)),
        "boiserie_walnut": wood("Timber | walnut dark", (0.045, 0.022, 0.012),
                                (0.16, 0.083, 0.043), grain=4.0, coat=0.32),
        "gilded_bronze": solid("Metal | gilt", (0.68, 0.44, 0.12),
                               metallic=0.88, roughness=0.34),
        # ---- electronics / hard goods ----
        # The palette was textiles + timber + brass only, so a television had
        # nothing to be built from: the Agent used linen_charcoal for the screen
        # and the gate saw a grey woven slab. A screen is a glossy near-black
        # dielectric, not fabric, so it gets its own key.
        "screen_black": solid("Display | black glass", (0.013, 0.015, 0.019),
                              metallic=0.0, roughness=0.075),
        "plastic_black": solid("Plastic | matte black", (0.017, 0.017, 0.018),
                               metallic=0.0, roughness=0.62),
        "steel_brushed": solid("Metal | brushed steel", (0.54, 0.55, 0.575),
                               metallic=0.94, roughness=0.27),
    }


# --------------------------------------------------------------------------- #
# lights / camera (studio setup for the component gate)
# --------------------------------------------------------------------------- #
def coplanar_tops(meshes: list, tol: float = 5e-4) -> list:
    """Find parts whose bounding faces lie on a shared plane and overlap.

    Two coincident surfaces are a real defect, not a taste issue: Cycles cannot
    resolve which one the camera hits, the two materials interleave, and the
    denoiser averages the noise into a solid dark plate. The reviewer read one
    of those plates as a "matte lacquered top" and passed it, so it has to be
    detected geometrically.

    The check started on upward faces only, which fixed the media console's
    top slab but missed the sofa's arms: there the arm's front face and the
    carcass's front face coincide on Y over a 0.19 x 0.135 m patch, and the
    render showed it as a black notch at the bottom of each arm. All six
    axis-aligned faces are covered now, because the defect is not specific to
    horizontal planes.
    """
    boxes = []
    for obj in meshes:
        lo = Vector((1e9, 1e9, 1e9))
        hi = Vector((-1e9, -1e9, -1e9))
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            lo = Vector((min(lo[i], w[i]) for i in range(3)))
            hi = Vector((max(hi[i], w[i]) for i in range(3)))
        boxes.append((obj.name, lo, hi))

    hits = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            n1, l1, h1 = boxes[i]
            n2, l2, h2 = boxes[j]
            for axis, ai in (("x", 0), ("y", 1), ("z", 2)):
                o1 = [k for k in range(3) if k != ai]
                for side in ("lo", "hi"):
                    v1 = (l1 if side == "lo" else h1)[ai]
                    v2 = (l2 if side == "lo" else h2)[ai]
                    if abs(v1 - v2) > tol:
                        continue
                    overlap = [min((h1, h2)[0][k], (h1, h2)[1][k])
                               - max((l1, l2)[0][k], (l1, l2)[1][k]) for k in o1]
                    if all(v > 0.02 for v in overlap):
                        hits.append({"a": n1, "b": n2, "axis": axis, "side": side,
                                     "plane": round(v1, 4),
                                     "overlap": [round(v, 3) for v in overlap],
                                     "z": round(v1, 4)})
                        break
    return hits


def fix_coplanar(meshes: list, hits: list, lift: float = 0.0007) -> list:
    """Nudge one part off a shared plane so the render stops going black.

    Coincident top faces are a stitching defect, not a design decision: the
    fix is to separate the two surfaces by a sub-millimetre amount, the same
    way you would shim a panel during assembly. Doing it here rather than
    asking the Agent to redo the arithmetic keeps the loop from oscillating
    (three rounds of "offset the slab" produced the same coincident plane).
    """
    by_name = {o.name: o for o in meshes}
    moved, seen = [], set()
    for hit in hits:
        obj = by_name.get(hit.get("b"))
        if obj is None or (obj.name, hit.get("axis"), hit.get("side")) in seen:
            continue
        axis = hit.get("axis") or "z"
        # Push the later part off the shared plane, away from the part it is
        # fighting with: shared max-faces move up, shared min-faces move down.
        delta = -lift if hit.get("side") == "lo" else lift
        obj.location[int("xyz".index(axis))] += delta
        seen.add((obj.name, axis, hit.get("side")))
        moved.append({"name": obj.name, "axis": axis, "offset_m": delta,
                      "plane": hit.get("plane"), "overlap": hit.get("overlap")})
    if moved:
        bpy.context.view_layer.update()
    return moved


def ensure_uvs(meshes: list) -> int:
    """Give every part a UV map.

    The PBR sets drive a tangent-space normal map. Without a UV layer the
    tangent basis degenerates, the shading normal comes out as zero and the
    surface renders black -- which is exactly what happened to every horizontal
    top face while vertical faces looked fine. A UV layer also makes the glTF
    export carry usable material coordinates.
    """
    made = 0
    for obj in meshes:
        if obj.data.uv_layers:
            continue
        try:
            for other in bpy.context.selected_objects:
                other.select_set(False)
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.smart_project(angle_limit=math.radians(66.0),
                                     island_margin=0.02)
            bpy.ops.object.mode_set(mode="OBJECT")
            made += 1
        except Exception:  # noqa: BLE001
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:  # noqa: BLE001
                pass
    for other in bpy.context.selected_objects:
        other.select_set(False)
    return made


def studio(scene, center=(0.0, 0.0, 0.0), with_ground=True):
    cx, cy, cz = center
    world = bpy.data.worlds.new("ForgeWorld")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        # 0.055 was dark enough that a horizontal lacquered top reflected the
        # world and rendered as a black slab while the vertical faces, which
        # see the area lights, stayed lit.
        bg.inputs["Color"].default_value = (0.16, 0.165, 0.175, 1.0)
        bg.inputs["Strength"].default_value = 1.0
    scene.world = world

    def area(name, loc, target, energy, size):
        d = bpy.data.lights.new(name, "AREA")
        d.energy = energy
        d.shape = "DISK"
        d.size = size
        o = bpy.data.objects.new(name, d)
        scene.collection.objects.link(o)
        o.location = loc
        o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        return o

    # Rig follows the object centre: a pendant hangs at z<0 and a rig aimed at
    # z=0.6 above it lights the ceiling side and leaves the shades black.
    area("KeyLight", (cx + 2.6, cy - 2.4, cz + 2.8), (cx, cy, cz), 340, 2.6)
    area("FillLight", (-2.8 + cx, cy - 1.6, cz + 1.9), (cx, cy, cz), 110, 3.0)
    area("RimLight", (cx - 0.8, cy + 3.0, cz + 2.4), (cx, cy, cz), 160, 2.0)
    # Overhead softbox: without it every horizontal surface reflects the dim
    # world and reads as black, which hid a perfectly good walnut top.
    area("TopLight", (cx, cy, cz + 3.6), (cx, cy, cz), 260, 3.4)

    if not with_ground:
        # A ceiling fixture has no floor under it. A ground plane at z=0 cut
        # through the fixture and the camera saw only a black disc.
        return None

    bpy.ops.mesh.primitive_plane_add(size=14, location=(0, 0, 0))
    plane = bpy.context.active_object
    plane.name = "ForgeGround"
    m = bpy.data.materials.new("ForgeGroundMat")
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    if b:
        b.inputs["Base Color"].default_value = (0.30, 0.29, 0.28, 1)
        b.inputs["Roughness"].default_value = 0.72
    plane.data.materials.append(m)
    return plane


def camera_for(scene, name, loc, target, lens=62):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    o = bpy.data.objects.new(name, cd)
    scene.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return o


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:]
    code_path = Path(argv[0])
    params_path = Path(argv[1])
    out_dir = Path(argv[2])
    oid = argv[3]
    out_dir.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    mats = build_materials()

    # Agent programs pass either a material key string or the material object
    # from mats[key]. Builder._register only understand keys and silently skips
    # anything else, which rendered an all-white sofa that still passed the
    # visual gate. Accept both forms.
    by_material_id = {id(m): k for k, m in mats.items()}
    original_register = Builder._register

    def register(self, ob, ptype, params, local, mat_key):
        if mat_key is not None and not isinstance(mat_key, str):
            mat_key = by_material_id.get(id(mat_key))
        return original_register(self, ob, ptype, params, local, mat_key)

    Builder._register = register

    # The curve helpers are module-level functions while the rest of the DSL is
    # B.method(). Agents mix the two forms, and a B.lathe() call used to die with
    # "'Builder' object has no attribute 'lathe'". Expose both spellings.
    Builder.sweep = lambda self, *a, **k: sweep(self, *a, **k)
    Builder.bezier_tube = lambda self, *a, **k: bezier_tube(self, *a, **k)
    Builder.piping_loop = lambda self, *a, **k: piping_loop(self, *a, **k)
    Builder.lathe = lambda self, *a, **k: lathe(self, *a, **k)
    Builder.arc_tube = lambda self, *a, **k: arc_tube(self, *a, **k)

    B = Builder(scene, mats)
    B.begin(oid, oid.replace("_", " ").title(), (0, 0, 0), 0.0, "furniture",
            "agent-authored via FineScene Builder DSL")

    P = json.loads(params_path.read_text(encoding="utf-8"))
    source = code_path.read_text(encoding="utf-8")

    namespace = {
        "B": B, "mats": mats, "P": P,
        "volute": volute, "cabriole_leg": cabriole_leg,
        "acanthus_leaf": acanthus_leaf, "torus": torus,
        "fluted_column": fluted_column, "corinthian_capital": corinthian_capital,
        "dentil_row": dentil_row,
        "sweep": sweep, "bezier_tube": bezier_tube, "piping_loop": piping_loop,
        "lathe": lathe, "arc_tube": arc_tube,
        "math": math,
    }
    result = {"ok": False, "objects": 0, "error": ""}
    try:
        exec(compile(source, str(code_path), "exec"), namespace)  # noqa: S102
        builder = namespace.get("build")
        if not callable(builder):
            raise RuntimeError("program does not define build(B, mats, P)")
        created = builder(B, mats, P)
        bpy.context.view_layer.update()
        meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.name != "ForgeGround"]
        verts = sum(len(o.data.vertices) for o in meshes)
        unshaded = [o.name for o in meshes if not o.data.materials]
        # Curved parts are the difference between furniture and a stack of
        # boxes. Count them so the orchestrator can refuse an all-box model
        # instead of relying on the visual gate to notice.
        curved = [o.name for o in meshes
                  if str(o.get("adsl_type", "")) == "sweep"
                  or '"lathe"' in str(o.get("adsl_params", ""))]
        uv_parts = ensure_uvs(meshes)
        coplanar = coplanar_tops(meshes)
        coplanar_fixed = fix_coplanar(meshes, coplanar)
        # Deterministic sanity the visual gate cannot be trusted to catch: a
        # material-less mesh renders paper white and still scored "passed".
        used = sorted({m.name for o in meshes for m in o.data.materials if m})
        lo = Vector((1e9, 1e9, 1e9))
        hi = Vector((-1e9, -1e9, -1e9))
        for o in meshes:
            for corner in o.bound_box:
                w = o.matrix_world @ Vector(corner)
                lo = Vector((min(lo[i], w[i]) for i in range(3)))
                hi = Vector((max(hi[i], w[i]) for i in range(3)))
        result = {"ok": True, "objects": len(meshes), "vertices": verts,
                  "unshaded_parts": unshaded[:12],
                  "unshaded_count": len(unshaded),
                  "materials_used": used,
                  "curve_parts": len(curved),
                  "curve_part_names": curved[:14],
                  "uv_parts": uv_parts,
                  "coplanar_tops": coplanar,
                  "coplanar_fixed": coplanar_fixed,
                  "bounds": {"min": [round(v, 4) for v in lo],
                             "max": [round(v, 4) for v in hi],
                             "size": [round(hi[i] - lo[i], 4) for i in range(3)]},
                  # The frame convention puts the origin on the bottom face.
                  # Not every program obeys it -- the accepted wall_tv centred
                  # its geometry on z = 0 -- so say so in the record instead of
                  # letting a downstream stage rediscover it as a floating
                  # object. Reported, not enforced: the pose caliper in the
                  # assembly resolves it from the measured bounds.
                  "origin_at_bottom": abs(lo.z) < 0.01,
                  "declared_return": len(created) if isinstance(created, list) else None}

        # The envelope is a hard contract, not a hint. `entry_door` was accepted
        # with 1.091 m of leaf for a 1.040 m clear opening because the sanity
        # check only ever looked at axis order; the overshoot then had to be
        # squashed by the assembly, which is a repair no stage should own. Measure
        # it here and refuse, so the failure stays in the loop that can fix it.
        size = [hi[i] - lo[i] for i in range(3)]
        want = [float(P["width"]), float(P["depth"]), float(P["height"])]
        overshoot = [round(size[i] - want[i], 4) for i in range(3)]
        tol = 0.012
        result["envelope"] = {"want": want, "got": [round(v, 4) for v in size],
                              "overshoot": overshoot, "tolerance": tol}
        result["envelope_ok"] = all(o <= tol for o in overshoot)
        if not result["envelope_ok"]:
            axes = "xyz"[max(range(3), key=lambda i: overshoot[i])]
            result["envelope_error"] = (
                f"built size {size[0]:.3f} x {size[1]:.3f} x {size[2]:.3f} m exceeds "
                f"the envelope {want[0]:.3f} x {want[1]:.3f} x {want[2]:.3f} m "
                f"(worst axis {axes}: {max(overshoot)*1000:+.0f} mm, tolerance "
                f"{tol*1000:.0f} mm). Scale the offending parts down; do not grow the model.")

    except Exception as exc:  # noqa: BLE001
        import traceback
        result = {"ok": False, "objects": 0,
                  "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc()[-2500:]}

    if not result["ok"]:
        (out_dir / "forge_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("FORGE_RESULT=" + json.dumps(result, ensure_ascii=False)[:600], flush=True)
        return 1

    # studio + renders
    lo = Vector(result["bounds"]["min"])
    hi = Vector(result["bounds"]["max"])
    center = (lo + hi) * 0.5
    radius = max((hi - lo).length * 0.5, 0.35)
    # A ceiling fixture lives entirely below z=0; a ground plane at z=0 would
    # slice through it and the camera would only see the underside of the plane.
    hanging = lo.z < -0.05
    studio(scene, center=tuple(center), with_ground=not hanging)
    # Frame the actual bounds, not an assumed floor-standing object: a pendant
    # hangs in negative Z and would otherwise fall outside every camera.
    target = tuple(center)
    cams = {
        "hero": (tuple(center + Vector((radius * 2.0, -radius * 2.0, radius * 1.5))), target, 55),
        "front": (tuple(center + Vector((0, -radius * 3.0, radius * 0.35))), target, 52),
        "three_quarter": (tuple(center + Vector((-radius * 2.1, -radius * 1.7, radius * 1.7))),
                          target, 55),
    }
    # A wall-mounted panel shows the room nothing but its own front, so the
    # three front cameras can never prove the mounting hardware exists -- the
    # gate asked for "visible steel bracket arms on the back" while every
    # image it was given pointed at the opposite side. Give it the back.
    # A door has the same problem in reverse: both faces are finished, and a
    # handle is only on one of them, so the rear camera is mandatory there too.
    if P.get("mount") in ("wall", "door"):
        cams["rear"] = (tuple(center + Vector((-radius * 1.5, radius * 2.6, radius * 1.2))),
                        target, 55)

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 48
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 900
    scene.render.resolution_y = 900
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = -0.2

    views = []
    for name, (loc, tgt, lens) in cams.items():
        cam = camera_for(scene, "Cam_" + name, loc, tgt, lens)
        scene.camera = cam
        path = out_dir / f"view_{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        views.append(str(path))

    bpy.ops.wm.save_as_mainfile(filepath=str(out_dir / f"{oid}.blend"))
    result["views"] = views
    result["blend"] = str(out_dir / f"{oid}.blend")
    result["materials_available"] = sorted(mats)
    (out_dir / "forge_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FORGE_RESULT=" + json.dumps({
        "ok": True, "objects": result["objects"], "vertices": result["vertices"],
        "size": result["bounds"]["size"], "views": len(views)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
