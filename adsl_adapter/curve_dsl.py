"""Curve primitives handed to the furniture Agent (Blender side).

Why this module exists
----------------------
The first Agent-authored sofa was 129 lines of `B.box` and `B.cylinder`. Every
edge was a chamfer, every cushion a bevelled slab, so the render read as a
stack of blocks -- and it still passed the visual gate. Furniture that reads as
real needs *swept* geometry: a piping roll around a cushion, a bent steel stem
on a lamp, a turned (lathed) foot, a curved arm rail.

baroque_lib already had `Builder.tube`, but it takes a POLY spline, so four
control points produce four straight segments. These helpers interpolate a
curve, then evaluate it into a real swept tube with `bevel_depth`, and finally
bake the result to a mesh so bounds, material audit and glTF export all see it.

Nothing here decides furniture shape. It only supplies the curve toolkit; the
part list still comes from the Agent-authored program.
"""

from __future__ import annotations

import math

import bpy


# --------------------------------------------------------------------------- #
# curve evaluation
# --------------------------------------------------------------------------- #
def _bezier(p0, p1, p2, p3, t):
    u = 1.0 - t
    return tuple(u * u * u * p0[i] + 3 * u * u * t * p1[i]
                 + 3 * u * t * t * p2[i] + t * t * t * p3[i] for i in range(3))


def _catmull(points, samples):
    """Centripetal-ish Catmull-Rom through >4 control points."""
    pts = [tuple(p) for p in points]
    if len(pts) == 2:
        return [tuple(pts[0] + (pts[1][i] - pts[0][i]) * (k / (samples - 1))
                      for i in range(3)) for k in range(samples)]
    ext = [pts[0]] + pts + [pts[-1]]
    out = []
    segs = len(pts) - 1
    per = max(2, samples // segs)
    for s in range(segs):
        p0, p1, p2, p3 = ext[s], ext[s + 1], ext[s + 2], ext[s + 3]
        for k in range(per):
            t = k / per
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * ((2 * p1[i]) + (-p0[i] + p2[i]) * t
                       + (2 * p0[i] - 5 * p1[i] + 4 * p2[i] - p3[i]) * t2
                       + (-p0[i] + 3 * p1[i] - 3 * p2[i] + p3[i]) * t3)
                for i in range(3)))
    out.append(tuple(pts[-1]))
    return out


# --------------------------------------------------------------------------- #
# swept tube
# --------------------------------------------------------------------------- #
def sweep(B, name, points, radii, mat, closed=False, bevel_res=4,
          smooth=True, cap=True):
    """Sweep a circular profile along `points`; per-point radius supported.

    A real CURVE object with bevel_depth does the work, then it is evaluated and
    baked to a mesh: a CURVE object would otherwise be invisible to the bounds
    and material audits and would leave the glTF export to guess.
    """
    pts = [tuple(map(float, p)) for p in points]
    if len(pts) < 2:
        raise ValueError("sweep needs at least 2 points")
    if not isinstance(radii, (list, tuple)):
        radii = [float(radii)] * len(pts)
    radii = [float(r) for r in radii]
    if len(radii) != len(pts):
        raise ValueError("radii length must match points length")

    cu = bpy.data.curves.new(name + "_cu", "CURVE")
    cu.dimensions = "3D"
    cu.resolution_u = 14
    cu.bevel_depth = 1.0
    cu.bevel_resolution = bevel_res
    cu.use_fill_caps = bool(cap and not closed)

    use_nurbs = smooth and len(pts) >= 4 and not closed
    sp = cu.splines.new("NURBS" if use_nurbs else "POLY")
    sp.points.add(len(pts) - 1)
    for point, co, r in zip(sp.points, pts, radii):
        point.co = (co[0], co[1], co[2], 1.0)
        point.radius = r
    if use_nurbs:
        sp.order_u = min(4, len(pts))
        sp.use_endpoint_u = True
    sp.use_cyclic_u = bool(closed)

    tmp = bpy.data.objects.new(name + "_tmp", cu)
    B.coll.objects.link(tmp)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    me.name = name + "_mesh"
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.data.curves.remove(cu, do_unlink=True)

    ob = bpy.data.objects.new(name, me)
    B.coll.objects.link(ob)
    return B._register(ob, "sweep", {
        "points": len(pts), "closed": bool(closed),
        "radius_start": round(radii[0], 4), "radius_end": round(radii[-1], 4),
        "verts": len(me.vertices),
    }, (0, 0, 0), mat)


def bezier_tube(B, name, ctrl, radius, mat, samples=26, taper=1.0,
                start=0.0, end=1.0, smooth=True):
    """Tube along a cubic Bezier (4 control points) or Catmull-Rom (>4).

    `taper` scales the final radius: 0.4 gives a tapered leg or lamp stem.
    `start`/`end` trim the parameter range, so one spline can serve several
    parts (e.g. only the curved top of a lamp stem).
    """
    ctrl = [tuple(map(float, p)) for p in ctrl]
    samples = max(4, int(samples))
    if len(ctrl) == 4:
        ts = [start + (end - start) * i / (samples - 1) for i in range(samples)]
        pts = [_bezier(*ctrl, t=t) for t in ts]
    else:
        pts = _catmull(ctrl, samples)
    radii = [radius * (1.0 + (taper - 1.0) * i / (samples - 1))
             for i in range(samples)]
    return sweep(B, name, pts, radii, mat, smooth=smooth)


# --------------------------------------------------------------------------- #
# rounded rectangle loop (cushion piping / frame inlay)
# --------------------------------------------------------------------------- #
def _rounded_rect_points(hw, hh, cr, per_arc=6):
    cr = max(1e-4, min(cr, hw * 0.98, hh * 0.98))
    cx, cy = hw - cr, hh - cr
    quads = [(cx, -cy, -90.0), (cx, cy, 0.0), (-cx, cy, 90.0), (-cx, -cy, 180.0)]
    pts = []
    for qx, qy, a0 in quads:
        for k in range(per_arc):
            a = math.radians(a0 + 90.0 * k / max(1, per_arc - 1))
            pts.append((qx + cr * math.cos(a), qy + cr * math.sin(a)))
    return pts


def piping_loop(B, name, center, half_w, half_h, radius, mat,
                corner=0.045, plane="XZ", per_arc=6, smooth=False):
    """Closed rounded-rectangle tube: the welt that closes a cushion seam.

    plane "XZ" faces the camera (front of a back cushion), "XY" is horizontal
    (the roll around the top face of a seat cushion).
    """
    cx, cy, cz = (float(v) for v in center)
    loop = _rounded_rect_points(float(half_w), float(half_h), float(corner), per_arc)
    if plane == "XZ":
        pts = [(cx + x, cy, cz + y) for x, y in loop]
    elif plane == "XY":
        pts = [(cx + x, cy + y, cz) for x, y in loop]
    elif plane == "YZ":
        pts = [(cx, cy + x, cz + y) for x, y in loop]
    else:
        raise ValueError("plane must be XZ, XY or YZ")
    pts.append(pts[0])
    radii = [radius] * len(pts)
    return sweep(B, name, pts, radii, mat, closed=True, smooth=smooth)


# --------------------------------------------------------------------------- #
# lathe (turned foot, lamp shade, vase, knob)
# --------------------------------------------------------------------------- #
def lathe(B, name, profile, center, mat, segments=48, axis="Z",
          closed_profile=False):
    """Revolve a 2D profile [(radius, height), ...] around an axis.

    Turned furniture parts are lathed, not boxed: a foot, a knob, a shade cone,
    a knob on a drawer front. `closed_profile` joins the last point back to the
    first, which is how you close a solid (e.g. a shade that returns inward).
    """
    cx, cy, cz = (float(v) for v in center)
    prof = [(float(r), float(h)) for r, h in profile]
    n = len(prof)
    segments = max(6, int(segments))

    verts, faces = [], []
    for i in range(segments):
        a = 2.0 * math.pi * i / segments
        ca, sa = math.cos(a), math.sin(a)
        for r, h in prof:
            if axis == "Z":
                verts.append((cx + r * ca, cy + r * sa, cz + h))
            elif axis == "X":
                verts.append((cx + h, cy + r * ca, cz + r * sa))
            else:
                verts.append((cx + r * ca, cy + h, cz + r * sa))
    span = n if closed_profile else n - 1
    for i in range(segments):
        i2 = (i + 1) % segments
        for j in range(span):
            j2 = (j + 1) % n
            faces.append((i * n + j, i * n + j2, i2 * n + j2, i2 * n + j))

    return B.mesh(name, verts, faces, mat, {
        "primitive": "lathe", "segments": segments, "profile_pts": n,
        "axis": axis,
    })


# --------------------------------------------------------------------------- #
# convenience: arc rail (chair arm, lamp arm, frame bar with bent ends)
# --------------------------------------------------------------------------- #
def arc_tube(B, name, center, radius, tube_r, mat, start_deg=0.0,
             end_deg=180.0, samples=20, z_scale=1.0):
    """Arc in the XZ plane: an arm rail, a bent frame bar, a swing arm.

    Angles are degrees measured from +X toward +Z, so 0..180 draws the top half
    of a circle whose centre is `center`.
    """
    cx, cy, cz = (float(v) for v in center)
    pts = []
    for i in range(samples):
        a = math.radians(start_deg + (end_deg - start_deg) * i / (samples - 1))
        pts.append((cx + radius * math.cos(a), cy,
                    cz + radius * z_scale * math.sin(a)))
    radii = [tube_r] * samples
    return sweep(B, name, pts, radii, mat)
