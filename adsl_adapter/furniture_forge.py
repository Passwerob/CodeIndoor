"""Agent-authored single furniture, compiled through FineScene's Builder DSL.

The correction this module encodes
----------------------------------
The first living-room pass reported "single-object quality is bad", and the
diagnosis was not a material bug: the geometry source was wrong. It borrowed
build_reception_lounge.py, whose parts are deliberately minimal aDSL
editability fixtures, and the parametric factory path (adsl_components
FACTORIES) only offers four kinds compiled into a few boxes.

FineScene's own furniture is made differently. baroque_lib.Builder plus its
ornament generators (volute / cabriole_leg / acanthus_leaf / torus /
fluted_column) build the canape, the wing chair and the chandelier, over real
2k PBR maps. That toolkit is the capability worth reusing -- so the Agent is
given it, and writes the part list itself. This file contains no furniture
geometry.

Flow per object:
    brief + envelope
      -> (text-only input) reference_images has an image model render one
         catalogue shot of the piece and reads its construction; cached with
         the prompt that produced it
      -> Agent writes build(B, mats, P) using the DSL only, FROM that photo
      -> static contract check (no bpy, no I/O, no imports beyond math)
      -> Blender executes it, renders 3 studio views
      -> general_vision.review_images(scope='component') hard gate
      -> rejected: findings fed back, regenerate (max N attempts)

Run:
    python furniture_forge.py --object sofa_3seater
    python furniture_forge.py --all
    python furniture_forge.py --object bookshelf --reference-images off
    python furniture_forge.py --object bookshelf --use_trellis   # GPU box only

--reference-images generate|local|web|off   (default generate: photos dropped
    into runtime/reference/<id>/ first, then the image model -- needs
    IMAGE_API_KEY in the environment or runtime/.env; off = previous behaviour)
--use_trellis   off by default. Writes the hand-off for the appearance stage
    (trellis_stage.py); generation itself is not wired yet.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import material_spec  # noqa: E402  (sibling module, stdlib only)
import reference_images  # noqa: E402
import trellis_stage  # noqa: E402

ROOT = Path(os.environ.get("CODEINDOOR_ROOT", r"E:/3DUse/New/CodeIndoor"))
ADAPTER = ROOT / "adsl_adapter"
OUT = ROOT / "runtime/outputs/forge"
REFERENCE = ROOT / "runtime/reference"
SUPPLY = ROOT / "runtime/outputs/scene_layout/supply_plan.json"
BLENDER = r"E:/Blender/blender.exe"
GATEWAY = os.environ.get("CODEINDOOR_GATEWAY",
                         "http://127.0.0.1:8765/v1/chat/completions")
PROFILE = Path(r"E:/3DUse/FineScene/configs/deepseek-flash.yaml")
FINESCENE_SRC = Path(r"E:/3DUse/FineScene/src")
if str(FINESCENE_SRC) not in sys.path:
    sys.path.insert(0, str(FINESCENE_SRC))

ALLOWED_NAMES = {
    "B", "mats", "P", "math", "volute", "cabriole_leg", "acanthus_leaf",
    "torus", "fluted_column", "corinthian_capital", "dentil_row",
    "sweep", "bezier_tube", "piping_loop", "lathe", "arc_tube",
    "range", "len", "min", "max", "abs", "round", "float", "int", "enumerate",
    "zip", "list", "dict", "tuple", "sum", "sorted", "print", "True", "False",
    "None", "build",
}

SYSTEM = """You are the Furniture Constructor Agent inside a scene-generation
system. You write ONE Python program that builds ONE piece of furniture from a
parametric builder DSL. You never touch bpy directly: the DSL is the contract.

THE DSL (already imported for you):

  B.begin(...)                    already called; do not call it
  B.box(name, size, loc, mat, yaw=0.0, bevel=0.0)
        size=(sx,sy,sz) full extents in metres; loc=(x,y,z) is the CENTRE of the box
  B.cylinder(name, radius, depth, loc, mat, verts=32, rot=None)
  B.sphere(name, radius, loc, mat, segments=20, rings=12, scale=None)
  B.tube(name, points, radii, mat, bevel_res=3)
        points = list of (x,y,z); radii = list of floats, same length
  B.mesh(name, verts, faces, mat, params=None, loc=(0,0,0))

CURVE TOOLS (these produce real swept/lathed geometry; the difference between
furniture and stacked slabs is almost always here):
  bezier_tube(B, name, ctrl, radius, mat, samples=26, taper=1.0, start=0.0, end=1.0)
        ctrl = 4 control points (cubic Bezier) or >4 points (Catmull-Rom).
        taper<1 shrinks the end radius (tapered leg, stem, horn).
  piping_loop(B, name, center, half_w, half_h, radius, mat, corner=0.045,
              plane="XZ"|"XY"|"YZ", per_arc=6)
        a closed rounded-rectangle welt. XZ = vertical face (front of a back
        cushion); XY = horizontal (roll around a seat cushion top). This is the
        single most convincing detail on upholstery: put one on EVERY cushion.
        For a CIRCULAR ring (lamp shade rim, round table edge) pass corner equal
        to half_w and half_h, or use torus(); a small corner value on an equal
        half_w/half_h produces a square frame, which is never what you want.
  lathe(B, name, profile, center, mat, segments=48, axis="Z", closed_profile=False)
        profile = [(radius, height), ...] revolved around the axis. Turned feet,
        lamp shades, knobs, vases, table pedestals. A shade is a lathe, not a
        cone primitive stacked on a cylinder.
  arc_tube(B, name, center, radius, tube_r, mat, start_deg=0, end_deg=180, samples=20)
        arc in the XZ plane (arm rail, bent frame bar, swing arm).
  sweep(B, name, points, radii, mat, closed=False, bevel_res=4, smooth=True)
        raw swept tube when the shapes above do not fit.

  Every curve helper accepts both spellings: lathe(B, ...) or B.lathe(...).
  Both are the same function. Use whichever reads better; do not call any other
  method on B except box/cylinder/sphere/tube/mesh.

ORNAMENT GENERATORS (already imported; these are what make furniture read as
furniture instead of boxes -- use them):
  volute(B, name, center, r0, r1, tube_r, mat, plane="XZ", turns=1.55, sign=1)
        a scroll/curl; r0 outer radius, r1 inner radius, tube_r thickness
  cabriole_leg(B, name, base, height, mat, foot_r=0.055, sway=0.075)
        tapered S-curved leg, base=(x,y,z) at the foot
  acanthus_leaf(B, name, loc, height, width, mat, tilt=0.0, thickness=0.022, yaw=0.0)
  torus(B, name, center, major, minor, mat, segments=64, sides=10)
  fluted_column(B, name, base, height, radius, mat, flutes=14, groove=0.011)
  dentil_row(B, name, start, end, z, mat, count=26, size=(0.055,0.075,0.065))

MATERIAL KEYS (use these strings as `mat`):
  linen_sand, boucle_grey, linen_charcoal   woven upholstery, real normal+roughness
  oak_joinery, walnut_joinery               timber with grain PBR
  brass_warm                                polished brass (metallic)
  ceramic_matte                             matte ceramic
  marble_cream                              marble
  lamp_shade                                translucent warm shade
  screen_black                              glossy near-black display glass
  plastic_black                             matte black polymer (bezels, casings)
  steel_brushed                             brushed steel (brackets, hardware)

PARAMETRIC MATERIALS (when no key above is the right finish or colour):
  "<class>:#RRGGBB"  or  "<class>:#RRGGBB:<roughness 0-1>"
  class is one of:
    paint     lacquer, painted timber or MDF, enamel      "paint:#F3F1EC"
    wood      timber with grain, any species/stain         "wood:#B58A5A"
    fabric    woven upholstery, any colour                 "fabric:#8FA08A"
    velvet    pile fabric with sheen                       "velvet:#2F4A3C"
    leather   smooth leather                               "leather:#5A3A22"
    metal     the hex is the metal's tint                  "metal:#1A1A1A:0.5"
    plastic   moulded polymer                              "plastic:#E8E8E8"
    glass     clear or tinted, see-through                 "glass:#FFFFFF"
    ceramic   glazed ceramic                               "ceramic:#DCD6CC"
    stone     marble / terrazzo / concrete                 "stone:#BEB9B0:0.6"
    shade     lamp shade that glows                        "shade:#F2E3C4"
  The hex is the surface colour as you would sample it from a photo. Use these
  freely: the named keys are a convenience, not a limit. Define each finish
  once as a variable at the top of build() (e.g.  body = "paint:#F3F1EC") and
  pass the variable, so the finish can be changed in one place.
  Never put an upholstery or timber key on a screen, a glass pane or a metal
  bracket: the weave/grain texture is the tell, and the gate rejects it.

FRAME CONVENTION (must be obeyed exactly):
  * Origin (0,0,0) is the BOTTOM CENTRE of the object, on the floor.
  * -Y is the FRONT (the side people sit facing). +Y is the BACK.
  * +Z is up. Build in metres.
  * EXCEPTION when P["mount"] == "ceiling": the origin is the point where the
    fixture attaches to the ceiling, and the fixture hangs DOWNWARD, so every
    part sits at negative Z (shades at z = -0.4, ceiling plate at z = 0).
  * EXCEPTION when P["mount"] == "wall": the origin is the BOTTOM CENTRE of the
    panel, and -Y is still the FRONT (the screen faces the room). +Y is the
    wall side, where the mounting hardware lives. Nothing may pass y = +depth/2:
    the bracket arms must end on that plane, not cross it.
  * EXCEPTION when P["mount"] == "door": the origin is the BOTTOM CENTRE of the
    leaf, which hangs in a finished opening. -Y is the room-side face and +Y
    the corridor-side face; BOTH faces are designed and visible, so neither may
    be left as an untextured back. The leaf must stay inside the envelope
    exactly (no architrave, no frame -- the building already has jambs), and the
    handle, rose, escutcheon and hinges are the only things allowed to stand
    proud, by at most 55 mm.

HARD RULES:
1. Define exactly one function:   def build(B, mats, P):
2. Return the list of created objects, or None. Do not call B.begin.
3. No imports. math is already available as `math`. P is a dict of dimensions.
4. No bpy, no files, no network, no eval/exec, no getattr tricks.
5. Quality bar: this object will be compared against a photogrammetry-scanned
   armchair and a hand-modelled Baroque canape. A stack of three boxes will be
   rejected by the visual gate. Build real furniture:
   - cushions with bevel >= 0.025 and a piping_loop welt around the visible seam
   - timber frame members as separate boxes with small bevels, not one slab
   - legs: bezier_tube with taper<1, or cabriole_leg, or lathe. Never a bare box
   - curved members (arm rails, lamp stems, pulls) via arc_tube / bezier_tube /
     lathe; straights are fine only for carcass panels and shelf boards
   - at least 12 distinct parts, and every surface must get a material
5d. CURVES ARE MANDATORY WHERE PHYSICS HAS THEM. A model with too few swept or
   lathed parts will be rejected without rendering. Minimum curved parts:
     upholstered furniture (sofa, armchair, bench) >= 4
     lighting (pendant, floor lamp, table lamp)      >= 3
     case goods (shelf, cabinet, table)              optional
   For case goods a smooth panel edge still helps: a sweep along a shelf front
   reads as 2 mm edge banding, a bezier_tube leg reads as a turned taper.
   Curves carry real information, not decoration: a piping welt closes a seam,
   a lathe profile is how a turned leg is actually made, a bent stem is how a
   lamp arm clears its shade.
5b. NO HOLES. Every surface a camera can see must be closed geometry.
   Specifically:
   - the seat deck must be fully covered by cushions or by a solid panel; a
     sight line from above must never reach an unlit cavity
   - cushions must butt against each other and against the back and arms, or
     overlap by at least 5 mm; never leave a visible slot between them
   - the back must be a solid panel behind the back cushions
   A black gap between cushions is the single most common rejection reason.
   Build the carcass first (a closed box), then sit the cushions on top of it.
5c. SYMMETRY. For a symmetric piece, compute one side and mirror the coordinate
   with a loop over  sx in (-1, 1); never write two independent left/right code
   paths. A stray block on one side is the second most common rejection reason.
   Also make sure every part you create is actually attached to the piece --
   nothing may float outside the envelope. The visible back of the object must
   be as finished as the front: it will be seen from the room.
5e. WELTS LIE ON THE SURFACE, NEVER OUTSIDE THE SILHOUETTE. A piping_loop or
   arc_tube is a raised seam: centre it on the face it decorates and keep the
   tube radius at 6-12 mm. An arc whose radius is a large fraction of the part
   it decorates escapes the outline and reads as a bent bar bolted to the
   outside -- an automatic rejection. For a vertical seam on the front of an
   arm, sweep a short bezier_tube 8 mm proud of that face; do not arc around
   the whole arm.
5f. UPHOLSTERED PANELS REACH THE BASE. An arm or apron that stops short of the
   floor rail leaves a rectangular black notch looking into an unlit cavity.
   Extend the panel to meet the rail, or close the gap with a solid sub-panel.
6. Proportions must match P exactly: total width ~P["width"], depth ~P["depth"],
   height ~P["height"]. Fit inside that envelope; do not exceed it.
7. Output ONLY Python source in one ```python block.
8. WHEN A REFERENCE PHOTO IS ATTACHED, you are reproducing that product, not
   designing a new one. The photo decides the DESIGN: which parts exist and how
   many, their relative proportions, where they join, which are curved, and the
   materials. The envelope P and any explicit number in the brief decide the
   SIZE; where photo and numbers disagree, the numbers win. Concretely:
   - count what you see (shelves, doors, legs, cushions) and build that count
   - keep the photo's ratios (shelf spacing, leg height vs carcass, arm vs seat)
     and express them as fractions of P["width"/"depth"/"height"], never as
     absolute constants, so the piece still looks right if P changes
   - name every part after what it is in the photo (shelf_1, door_left,
     leg_front_right); one named part per visible component
   - give each visible material its real finish and colour: a named key if
     one matches, otherwise a PARAMETRIC MATERIAL with the hex read from the
     photo (a white lacquered shelf is "paint:#F3F1EC", not oak_joinery)
   - parts the photo cannot show (the back, the underside) are still built,
     in the plainest form consistent with what is visible
   The other hard rules still apply: a photo of a simple piece does not excuse
   holes, floating parts or missing materials.

Known Blender 5.2 traps you cannot hit through this DSL, but respect the spirit:
keep parts as separate named objects, let B.box(bevel=...) do the easing, and
never build zero-thickness geometry.
"""


def call_agent(messages: list, max_tokens: int = 4200) -> dict:
    """Return the upstream text PLUS a provenance record.

    The point of the record is that the geometry program can be traced back to
    a specific upstream response: the gateway logs resp_sha for every call, and
    this function stores the same hash next to the code it produced. If the two
    hashes match, the program demonstrably came off the wire from the model and
    was not authored locally.
    """
    payload = {
        "model": "gemini-3.1-pro-preview",
        "messages": [{"role": "system", "content": SYSTEM}] + messages,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "temperature": 0.4,
    }
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        GATEWAY, data=encoded, method="POST",
        headers={"Content-Type": "application/json"})
    started = time.time()
    with urllib.request.urlopen(req, timeout=300) as resp:
        blob = resp.read()
        http_status = resp.status
    body = json.loads(blob.decode("utf-8"))
    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return {
        "text": content,
        "raw": blob.decode("utf-8", "replace"),
        "meta": {
            "gateway": GATEWAY,
            "requested_model": payload["model"],
            "upstream_model": body.get("model"),
            "http_status": http_status,
            "resp_sha": hashlib.sha256(blob).hexdigest()[:16],
            "request_sha": hashlib.sha256(encoded).hexdigest()[:16],
            "usage": body.get("usage"),
            "seconds": round(time.time() - started, 2),
            "chars": len(content),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


def strip_code(text: str) -> str:
    import re
    m = re.search(r"```(?:python)?\s*([\s\S]*?)\s*```", text)
    return (m.group(1) if m else text).strip()


def code_hash(text: str) -> str:
    """Hash that survives Windows newline translation.

    Hashing the raw bytes of program.py on Windows folds in CR characters the
    model never emitted, so the digest never matched the digest logged at
    generation time and every program looked forged. Normalise first.
    """
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def curve_minimum(brief: str) -> int:
    """How many swept/lathed parts this class of furniture physically needs.

    Not a decoration quota. Upholstery cannot exist without a welt closing the
    seam, and a shade is a lathe, so those classes have a hard floor. Case goods
    (a plank bookshelf, a boxy console) are legitimately rectilinear, so a
    model made of panels is not a defect there and must not be retried forever.
    """
    text = (brief or "").lower()
    if any(k in text for k in ("sofa", "couch", "armchair", "arm chair", "chair",
                               "bench", "lounge", "settee")):
        return 4
    if any(k in text for k in ("lamp", "pendant", "light", "chandelier", "sconce")):
        return 3
    return 0


def check_contract(code: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"syntax error: {exc}"
    # `import math` is harmless (math is already in scope) and the model writes
    # it out of habit: rejecting it cost a whole attempt on two of three live
    # runs. It is tolerated rather than stripped, so the executed file is still
    # byte-identical to what the model sent and the provenance hash holds.
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) or (
                isinstance(n, ast.Import)
                and any(a.name != "math" or a.asname not in (None, "math")
                        for a in n.names)):
            return False, "imports are not allowed (math is already available)"

    # Locals, loop targets and parameters are legitimately free names; only
    # names that resolve to *nothing* in the sandbox are a contract violation.
    defined = set(ALLOWED_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, ast.FunctionDef):
            defined.add(node.name)
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                defined.add(arg.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in defined:
                return False, f"unknown name '{node.id}' (only the DSL is available)"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False, "dunder access is not allowed"
    # A malformed parametric material would only surface inside Blender; the
    # grammar is pure Python, so reject it here with the valid forms spelled out.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and material_spec.looks_parametric(node.value) \
                and material_spec.parse(node.value) is None:
            return False, material_spec.explain(node.value)
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "build"), None)
    if fn is None:
        return False, "missing def build(B, mats, P)"
    if len(fn.args.args) != 3:
        return False, "build must take exactly (B, mats, P)"
    if "B." not in code:
        return False, "program never calls the builder"
    return True, "ok"


def run_blender(code: str, params: dict, oid: str, attempt: int) -> dict:
    work = OUT / f"{oid}_attempt{attempt}"
    work.mkdir(parents=True, exist_ok=True)
    code_path = work / "program.py"
    params_path = work / "params.json"
    code_path.write_text(code, encoding="utf-8")
    params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    proc = subprocess.run(
        [BLENDER, "-b", "--python", str(ADAPTER / "forge_runner.py"), "--",
         str(code_path), str(params_path), str(work), oid],
        capture_output=True, text=True, timeout=900)
    (work / "blender_stdout.txt").write_text(
        (proc.stdout or "")[-12000:] + "\n--- STDERR ---\n" + (proc.stderr or "")[-4000:],
        encoding="utf-8")
    res_path = work / "forge_result.json"
    if not res_path.exists():
        return {"ok": False, "error": "no forge_result.json; see blender_stdout.txt"}
    res = json.loads(res_path.read_text(encoding="utf-8"))
    res["work_dir"] = str(work)
    return res


def visual_gate(views: list[Path], brief: str, oid: str,
                reference_summary: str | None = None) -> dict:
    from general_vision import review_images
    if len(views) < 3:
        return {"accepted": False, "status": "insufficient_views"}
    style = [
        {"id": "furniture_reads_as_object",
         "feature": "the object is recognisable as the requested furniture type",
         "visible_evidence": "legs, seat, back and arms in plausible relation"},
        {"id": "no_visible_voids",
         "feature": "no black gaps, slots or see-through cavities anywhere",
         "visible_evidence": "seat and back are continuous surfaces; no dark slot "
                             "between cushions or between cushion and frame"},
        # A sofa once passed this gate with a rectangular black notch at the
        # front-bottom of each arm, where the upholstered panel stopped short of
        # the rail and exposed an unlit interior. The void requirement only
        # looked at cushion seams, so the notch slipped through.
        {"id": "no_dark_notch",
         "feature": "no dark rectangular notch, hole or window on any face",
         "visible_evidence": "outer arm faces, apron and plinth run continuously "
                             "to the floor rail with no black cut-out"},
        # And a welt swept with too large a radius escaped the arm silhouette
        # and hung outside the piece like a bent bar. Nothing may float.
        {"id": "no_stray_members",
         "feature": "no member protrudes outside the object's silhouette or "
                    "stands detached from the surface it decorates",
         "visible_evidence": "piping and welts hug the panel they sit on; every "
                             "leg, rail and tube is attached at both ends"},
        {"id": "material_quality",
         "feature": "upholstery and timber show real texture, not flat colour",
         "visible_evidence": "visible weave grain, seams, or wood grain"},
        {"id": "edge_quality",
         "feature": "cushion and frame edges are eased, not razor sharp",
         "visible_evidence": "rounded cushion edges and piping"},
    ]
    if reference_summary:
        # The reviewer only sees our renders, so the photo reaches it as the
        # description read from it. Without this the gate would happily accept
        # a generic piece that ignores the reference it was written from.
        style.append(
            {"id": "matches_reference",
             "feature": "the object reproduces this reference design: "
                        + reference_summary[:600],
             "visible_evidence": "same part count and arrangement, similar "
                                 "proportions and materials as described"})
    request = {"id": oid, "original_user_request": brief,
               "style_requirements": style, "kind": "furniture"}
    report = review_images(views, request, str(PROFILE),
                           OUT / f"{oid}_visual_qa.json",
                           quality="realistic", scope="component", timeout_seconds=240)
    decision = report.get("decision") or {}
    findings = []
    for f in decision.get("findings", [])[:8]:
        # The reviewer returns `evidence` + `correction`; older calls returned
        # `note`. Dropping to an empty note made the retry loop blind: three
        # attempts produced the same code hash because the feedback string was
        # "unspecified insufficiency".
        text = str(f.get("note") or f.get("message") or f.get("evidence") or "").strip()
        fix = str(f.get("correction") or "").strip()
        if fix:
            text = f"{text} -> FIX: {fix}" if text else f"FIX: {fix}"
        if not text:
            text = str(f.get("requirement_ids") or "") or "unspecified"
        findings.append({"severity": f.get("severity"), "note": text[:420]})
    return {"accepted": bool(report.get("accepted")), "status": report.get("status"),
            "verdict": decision.get("verdict"), "findings": findings,
            "views": len(views)}


def _image_part(path: Path) -> dict:
    # Renders are PNG, retrieved photos are usually JPEG: sniff, do not assume.
    return reference_images.image_part(path)


def load_reference(oid: str) -> dict | None:
    """Real-product construction data gathered from the web, when it exists.

    The asset library has no sofa, lamp, case goods or pendant, so geometry for
    those has to be authored. Authoring without a reference produces plausible-
    looking nonsense: a sofa with a 30 cm seat depth, a shade smaller than its
    base. These files carry the measured anatomy and proportion rules that
    actual products use, with the source URL on every claim.
    """
    path = REFERENCE / f"{oid}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def reference_block(ref: dict) -> str:
    lines = ["REFERENCE DATA (measured from real products; follow it, do not invent "
             "dimensions):"]
    dims = ref.get("dimensions_m") or {}
    if dims:
        lines.append("  dimensions in metres:")
        for key, val in dims.items():
            if isinstance(val, (list, tuple)) and len(val) == 2:
                lines.append(f"    {key}: {val[0]} - {val[1]}")
            else:
                lines.append(f"    {key}: {val}")
    anatomy = ref.get("anatomy") or []
    if anatomy:
        lines.append("  how the real thing is built, part by part:")
        for item in anatomy:
            curve = item.get("curve")
            tail = f"   [curve: {curve}]" if curve else ""
            lines.append(f"    - {item['part']}: {item['note']}{tail}")
    rules = ref.get("proportion_rules") or []
    if rules:
        lines.append("  proportion rules:")
        lines.extend(f"    - {r}" for r in rules)
    errors = ref.get("common_errors") or []
    if errors:
        lines.append("  failure modes to avoid:")
        lines.extend(f"    - {r}" for r in errors)
    return "\n".join(lines)


def replay(oid: str) -> dict:
    """Re-run an accepted program through the current runner, with no LLM call.

    Deterministic post-processing (coplanar stitching, UVs, curve accounting)
    changes between runs and can only be applied by executing the program
    again. Replaying the identical source keeps code_sha stable, so the
    provenance chain still binds it to the response that produced it -- unlike
    a regeneration, which would replace a verified program with a new,
    unrelated one just to pick up an assembly fix.
    """
    supply = json.loads(SUPPLY.read_text(encoding="utf-8"))
    entry = next((o for o in supply["objects"] if o["id"] == oid), None)
    if entry is None:
        raise SystemExit(f"{oid} is not in the supply plan")
    program = OUT / oid / "program.py"
    if not program.is_file():
        raise SystemExit(f"no accepted program at {program}")
    result_path = OUT / f"{oid}_result.json"
    previous = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    code = program.read_text(encoding="utf-8").replace("\r\n", "\n")
    prior = next((e for e in reversed(previous.get("log", []))
                  if e.get("code_sha") == code_hash(code)), {})

    dims = entry["dimensions"]
    params = {"width": round(float(dims[0]), 3), "depth": round(float(dims[1]), 3),
              "height": round(float(dims[2]), 3), "units": "m",
              "mount": entry.get("mount", "floor")}
    res = run_blender(code, params, oid, 1)
    if not res.get("ok"):
        raise SystemExit(f"replay failed: {res.get('error')}")

    accepted_dir = OUT / oid
    work = Path(res["work_dir"])
    import shutil
    for name in (f"{oid}.blend", "view_front.png", "view_hero.png",
                 "view_three_quarter.png", "forge_result.json", "blender_stdout.txt"):
        src = work / name
        if src.is_file():
            shutil.copy2(src, accepted_dir / name)
    with open(accepted_dir / "program.py", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(code)

    entry_log = dict(prior)
    entry_log.update({
        "attempt": prior.get("attempt", 1), "code_sha": code_hash(code),
        "blender_ok": True, "objects": res.get("objects"),
        "vertices": res.get("vertices"), "replayed": True,
        "coplanar_stitched": res.get("coplanar_fixed"),
        "curve_parts": res.get("curve_parts"), "curve_part_names": res.get("curve_part_names"),
        "note": "replayed through the current runner; source unchanged",
    })
    result = {"ok": True, "id": oid, "attempts": previous.get("attempts", 1),
              "blend": str(accepted_dir / f"{oid}.blend"),
              "views": [str(accepted_dir / f"view_{v}.png")
                        for v in ("front", "hero", "three_quarter")],
              "objects": res.get("objects"), "vertices": res.get("vertices"),
              "bounds": res.get("bounds"), "gate": previous.get("gate"),
              "log": list(previous.get("log", [])) + [entry_log]}
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return {"id": oid, "ok": True, "replayed": True,
            "stitched": len(res.get("coplanar_fixed") or [])}


def reference_summary(image_ref: dict | None) -> str | None:
    """One paragraph describing the photo, for the visual gate."""
    if not image_ref:
        return None
    r = image_ref.get("reading") or {}
    parts = ", ".join(f"{p.get('count', 1)} x {p.get('name')}"
                      for p in (r.get("parts") or [])[:14])
    text = " ".join(x for x in (str(r.get("design_summary") or "").strip(),
                                f"Parts: {parts}." if parts else "") if x)
    return text or None


def forge(entry: dict, max_attempts: int = 3,
          resume_code: str | None = None,
          initial_feedback: str | None = None,
          reference_mode: str = "generate",
          refresh_reference: bool = False,
          use_trellis: bool = False) -> dict:
    oid = entry["id"]
    dims = entry["dimensions"]
    params = {"width": round(float(dims[0]), 3), "depth": round(float(dims[1]), 3),
              "height": round(float(dims[2]), 3), "units": "m",
              "mount": entry.get("mount", "floor")}
    brief = entry.get("brief") or oid
    reference = load_reference(oid)
    # Retrieval never blocks the forge: no photo (offline, nothing usable) just
    # means the Agent authors from the brief, exactly as before.
    try:
        image_ref = reference_images.find_reference_image(
            oid, brief, [params["width"], params["depth"], params["height"]],
            mode=reference_mode, refresh=refresh_reference,
            mount=params["mount"])
    except Exception as exc:  # noqa: BLE001
        print(f"[forge] reference retrieval failed: {type(exc).__name__}: {exc}",
              flush=True)
        image_ref = None
    ref_image = Path(image_ref["path"]) if image_ref else None
    ref_summary = reference_summary(image_ref)
    ref_record = None
    if image_ref:
        src = image_ref.get("source") or {}
        ref_record = {"image": image_ref["path"], "provider": src.get("provider"),
                      "page": src.get("page"), "licence": src.get("licence"),
                      "sha256": src.get("sha256"), "model": src.get("model"),
                      "prompt": src.get("prompt")}
        print(f"[forge] {oid}: reference photo from {src.get('provider')} "
              f"({src.get('page') or src.get('file')})", flush=True)
    else:
        print(f"[forge] {oid}: no reference photo (mode={reference_mode}); "
              f"authoring from the brief", flush=True)
    log: list[dict] = []
    feedback: str | None = initial_feedback
    prior_code: str | None = None
    prior_view: Path | None = None
    if resume_code:
        cand = Path(resume_code)
        if cand.is_file():
            prior_code = cand.read_text(encoding="utf-8")
            sibling = cand.parent / "view_three_quarter.png"
            prior_view = sibling if sibling.is_file() else None

    for attempt in range(1, max_attempts + 1):
        mount = params["mount"]
        if mount == "ceiling":
            mount_note = ("  -> the origin is the ceiling attachment point and the fixture "
                          "hangs downward in negative Z.\n")
        elif mount == "wall":
            mount_note = ("  -> the origin is the bottom centre of the panel; the screen "
                          "faces -Y and the wall-side hardware faces +Y, ending exactly on "
                          f"y = +{params['depth'] / 2:.3f}.\n")
        elif mount == "door":
            mount_note = ("  -> the origin is the bottom centre of the closed leaf; -Y is the "
                          "room side and +Y the corridor side, both finished. Depth is the "
                          "HARD budget and already includes the hardware: the slab is ~40 mm "
                          "centred on y = 0, and handle, rose, escutcheon and hinges may "
                          f"project only as far as y = +-{params['depth'] / 2:.3f}.\n")
        else:
            mount_note = "\n"
        spec = (
            f"Object id: {oid}\n"
            f"Requested furniture: {brief}\n"
            f"Envelope P (metres): width={params['width']}, depth={params['depth']}, "
            f"height={params['height']}\n"
            f"Mount: {mount}" + mount_note
            + f"Style context: contemporary living room; neutral textiles, warm timber, "
              f"brass hardware. Photoreal, not stylised.\n"
            + ((reference_block(reference) + "\n") if reference else "")
            + ((reference_images.reading_block(image_ref) + "\n") if image_ref else "")
            + "Write the build(B, mats, P) program now."
        )
        if feedback:
            spec += "\n\nThe previous attempt was rejected. Fix exactly this:\n" + feedback

        # The photo travels with the spec on every attempt, so a repair round
        # is still judged against the product and not against the last render.
        first: str | list = spec
        if ref_image is not None:
            first = [{"type": "text", "text": spec}, _image_part(ref_image),
                     {"type": "text", "text": "Above: the reference photo."}]
        if not prior_code:
            messages = [{"role": "user", "content": first}]
        else:
            # Show the Agent its own render: text findings alone led it to fix
            # the wrong thing, while the black slot between cushions was obvious
            # in the image.
            content: list = [{"type": "text", "text": spec}]
            if prior_view and prior_view.is_file():
                content.append(_image_part(prior_view))
                content.append({"type": "text", "text":
                                "This is your previous result. Look at it and fix the "
                                "defects listed above. Return the COMPLETE corrected "
                                "program, not a patch."})
            else:
                content.append({"type": "text", "text":
                                "That version was rejected by the visual gate. Return "
                                "the COMPLETE corrected program, not a patch."})
            if ref_image is not None:
                content.append({"type": "text", "text":
                                "Compare your result with the reference photo in "
                                "the first message: where part count, proportions "
                                "or materials differ, the photo is right."})
            messages = [{"role": "user", "content": first},
                        {"role": "assistant", "content": "```python\n" + prior_code + "\n```"},
                        {"role": "user", "content": content}]

        raw = None
        llm_meta = None
        for retry in range(3):
            try:
                reply = call_agent(messages)
                raw, llm_meta = reply["text"], reply["meta"]
                break
            except Exception as exc:  # noqa: BLE001
                if retry == 2:
                    log.append({"attempt": attempt, "stage": "llm",
                                "error": f"{type(exc).__name__}: {exc}"})
                else:
                    time.sleep(2 + retry * 2)
        if raw is None:
            continue

        # Provenance: the raw upstream body and its hash sit beside the program
        # they produced, so the code can be tied to the gateway log line.
        #
        # It must live OUTSIDE OUT/<oid>, because acceptance rmtree's that
        # directory to clear stale attempt output -- which silently deleted
        # every stored response body and left the chain unfalsifiable. The
        # archive below is append-only for the whole run.
        prov_dir = OUT / f"{oid}_prov"
        prov_dir.mkdir(parents=True, exist_ok=True)
        (prov_dir / f"llm_attempt{attempt}.json").write_text(
            json.dumps({"meta": llm_meta, "raw_response": reply["raw"]},
                       ensure_ascii=False, indent=2), encoding="utf-8")

        code = strip_code(raw)
        ok, why = check_contract(code)
        if not ok:
            log.append({"attempt": attempt, "stage": "contract", "note": why})
            feedback = "Static contract violation: " + why
            prior_code = code
            continue

        started = time.time()
        res = run_blender(code, params, oid, attempt)
        # Hash the file Blender actually executed, not the string in memory:
        # if the two ever diverge, the hash must describe what was rendered.
        executed = Path(res.get("work_dir") or "") / "program.py"
        try:
            code = executed.read_text(encoding="utf-8").replace("\r\n", "\n")
        except OSError:
            pass
        entry_log = {"attempt": attempt, "chars": len(code), "blender_ok": res.get("ok"),
                     "objects": res.get("objects"), "vertices": res.get("vertices"),
                     "code_sha": code_hash(code),
                     "llm_model": (llm_meta or {}).get("upstream_model"),
                     "llm_resp_sha": (llm_meta or {}).get("resp_sha"),
                     "llm_usage": (llm_meta or {}).get("usage"),
                     "seconds": round(time.time() - started, 1)}
        if not res.get("ok"):
            entry_log["error"] = str(res.get("error"))[:400]
            log.append(entry_log)
            feedback = "Blender execution failed:\n" + str(res.get("error"))[:1200]
            prior_code = code
            prior_view = None
            continue

        views = [Path(v) for v in res.get("views") or []]
        # Deterministic checks first: these are facts, not opinions, and the
        # visual gate has already passed an all-white unshaded model once.
        sanity = []
        if res.get("unshaded_count"):
            sanity.append(f"{res['unshaded_count']} parts carry no material and render "
                          f"paper-white: {', '.join(res.get('unshaded_parts') or [])}")
        # The envelope test used to allow 10% overshoot, which on a 1.02 m door
        # meant 1.12 m passed -- and the leaf was then squeezed by the assembly,
        # i.e. the failure was moved downstream instead of being repaired here.
        # The runner now measures against the envelope with an absolute tolerance.
        env = res.get("envelope") or {}
        if res.get("envelope_ok") is False:
            got, want = env.get("got") or [0, 0, 0], env.get("want") or [0, 0, 0]
            sanity.append(
                res.get("envelope_error")
                or f"built {got} exceeds envelope {want}")
        if res.get("objects", 0) < 12:
            sanity.append(f"only {res.get('objects')} parts; a believable piece needs "
                          f"separate frame members, cushions, legs and hardware")
        # Curves are the difference between furniture and a stack of slabs --
        # where the class physically has curves. A plank shelf does not.
        need = curve_minimum(brief)
        if need and res.get("curve_parts", 0) < need:
            names = ", ".join(res.get("curve_part_names") or []) or "none"
            sanity.append(
                f"only {res.get('curve_parts', 0)} swept/lathed part(s), this piece "
                f"needs at least {need}: {names}. Use piping_loop on every cushion "
                f"seam, bezier_tube for tapered legs and bent stems, lathe for turned "
                f"feet and shades, arc_tube for arm rails.")
        collisions = res.get("coplanar_tops") or []
        if collisions:
            # Stitched automatically by the runner: separating coincident
            # surfaces is assembly, not design. Recorded, not rejected.
            entry_log["coplanar_stitched"] = res.get("coplanar_fixed")
        if sanity:
            entry_log["sanity"] = sanity
            log.append(entry_log)
            prior_code = code
            prior_view = views[0] if views else None
            feedback = ("Deterministic checks failed (not a matter of taste):\n- "
                        + "\n- ".join(sanity)
                        + "\nEvery object must be created with a real material key from "
                          "mats, and the whole piece must stay inside the envelope.")
            continue

        gate = (visual_gate(views, brief, oid, ref_summary) if views
                else {"accepted": False})
        entry_log["gate"] = gate
        log.append(entry_log)

        if not gate.get("accepted"):
            prior_code = code
            prior_view = views[0] if views else None
            notes = "; ".join(f"[{f['severity']}] {f['note']}" for f in gate.get("findings", []))
            feedback = ("The render was rejected by the component visual gate: "
                        + (notes or "unspecified insufficiency")
                        + ". Increase real furniture detail: eased cushion edges, "
                          "piping, tapered legs, separate frame members, brass hardware. "
                          "Close every visible gap so no cavity is visible.")
            continue

        accepted_dir = OUT / oid
        if accepted_dir.exists():
            import shutil
            shutil.rmtree(accepted_dir)
        import shutil
        shutil.copytree(res["work_dir"], accepted_dir)
        # newline="\n" keeps the file byte-identical to the string that was
        # hashed, so anyone re-hashing program.py gets the logged code_sha.
        with open(accepted_dir / "program.py", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(code)
        # Keep the upstream body beside the program it produced. The archive in
        # <oid>_prov is the durable copy; this one makes a delivered asset
        # self-documenting when it is handed to TRELLIS on its own.
        body = prov_dir / f"llm_attempt{attempt}.json"
        if body.is_file():
            shutil.copy2(body, accepted_dir / "llm_response.json")
        result = {"ok": True, "id": oid, "attempts": attempt,
                  "blend": str(accepted_dir / f"{oid}.blend"),
                  "views": [str(accepted_dir / Path(v).name) for v in views],
                  "objects": res.get("objects"), "vertices": res.get("vertices"),
                  "bounds": res.get("bounds"), "gate": gate, "log": log,
                  "reference_image": ref_record}
        if image_ref:
            # Keep the photo and its reading beside the program written from it.
            (accepted_dir / "reference_image.json").write_text(
                json.dumps({k: v for k, v in image_ref.items() if k != "candidates"},
                           ensure_ascii=False, indent=2), encoding="utf-8")
        if use_trellis:
            handoff = trellis_stage.write_handoff(
                oid, accepted_dir, brief, params, result, image_ref)
            result["trellis"] = trellis_stage.run(handoff)
            print(f"[forge] {oid}: trellis -> {result['trellis']['status']} "
                  f"({result['trellis']['note']})", flush=True)
        (OUT / f"{oid}_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    result = {"ok": False, "id": oid, "attempts": len(log), "log": log,
              "reference_image": ref_record}
    (OUT / f"{oid}_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--object")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--resume-code", help="previous program.py to repair instead "
                                             "of starting from scratch")
    parser.add_argument("--initial-feedback", help="specific defects to fix on resume")
    parser.add_argument("--replay", action="store_true",
                        help="re-run the accepted program through the current "
                             "runner without calling the model; keeps code_sha")
    parser.add_argument("--reference-images", default="generate",
                        choices=("generate", "local", "web", "off"),
                        help="generate: photos in runtime/reference/<id>/ first, "
                             "then the image model; local: only those photos; "
                             "web: open-licence image search; off: author from "
                             "the brief alone (previous behaviour)")
    parser.add_argument("--refresh-reference", action="store_true",
                        help="ignore the cached photo choice and search again")
    parser.add_argument("--use_trellis", "--use-trellis", dest="use_trellis",
                        action="store_true",
                        help="after acceptance, write the TRELLIS hand-off and "
                             "call the appearance stage (off by default; the "
                             "generation itself is not wired yet)")
    args = parser.parse_args()

    supply = json.loads(SUPPLY.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    targets = [o for o in supply["objects"]
               if o["mode"] in ("generated", "retrieved+generated")]
    if args.object:
        targets = [o for o in targets if o["id"] == args.object]
    if not targets:
        print("no matching generated objects")
        return 1

    if args.replay:
        only = args.object or None
        if only is None:
            print("--replay needs --object")
            return 2
        done = replay(only)
        print("REPLAY=" + json.dumps(done, ensure_ascii=False))
        return 0

    summary = []
    for entry in targets:
        if entry["mode"] == "retrieved+generated":
            target = {"id": entry["id"], "dimensions": entry["dimensions"],
                      "brief": entry["shell"]["brief"]}
        else:
            target = entry
        print(f"[forge] {target['id']} ...", flush=True)
        res = forge(target, max_attempts=args.attempts,
                    resume_code=args.resume_code,
                    initial_feedback=args.initial_feedback,
                    reference_mode=args.reference_images,
                    refresh_reference=args.refresh_reference,
                    use_trellis=args.use_trellis)
        summary.append({"id": res["id"], "ok": res["ok"],
                        "reference": bool(res.get("reference_image")),
                        "trellis": (res.get("trellis") or {}).get("status"),
                        "attempts": res["attempts"],
                        "objects": res.get("objects"),
                        "gate": (res.get("gate") or {}).get("verdict")})
        print(f"[forge] {res['id']}: ok={res['ok']} attempts={res['attempts']} "
              f"objects={res.get('objects')}", flush=True)

    print("FORGE_SUMMARY=" + json.dumps(summary, ensure_ascii=False))
    return 0 if all(s["ok"] for s in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
