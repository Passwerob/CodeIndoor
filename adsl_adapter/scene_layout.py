"""SceneCode-side scene organisation & placement planner.

Contract source (read from the real SceneCode repository, not invented):
  scenecode/prompts/data/floor_plan_agent/designer_agent.yaml  -> room specs,
      wall openings, house description analysis.
  scenecode/prompts/data/furniture_agent/stateful_planner_agent.yaml
      -> Planner orchestrates Designer(placement) + Critic(evaluation) + iterate;
         "Designer can only control x, y position and yaw rotation".
  scenecode/prompts/data/asset_router/request_analysis_furniture.yaml
      -> AssetItem: description / short_name / dimensions / object_type / strategies.
  scenecode/experiments/indoor_scene_generation.py
      -> PIPELINE_STAGES = floor_plan, furniture, wall_mounted, ceiling_mounted, manipuland.

Three rules this file now obeys, after the previous version broke them:

1. THE ROOM IS MEASURED, NOT ASSERTED. Every dimension, wall plane and opening
   comes from `room_measured.json` (written by room_spec.py from the assembled
   shell). The old prompt hard-coded "door at x = 0" while the shell had it at
   x 1.20..2.40, so the Designer was planning in a room that no longer existed.

2. THE AGENT DECIDES, THE ADAPTER DERIVES. The Designer gives x/y/yaw plus the
   intents it is allowed to have (which wall, facing what, how high). Anything
   that follows mechanically from those -- the z of a wall-hung panel above a
   cabinet, the yaw of a picture hung on a known wall, a door leaf filling an
   opening -- is computed here from the same numbers. No human-authored pose.

3. A CONSTRAINT IS A TEST, NOT A REPAIR. `expect()` returns typed violations
   (`[id] message`). The same list is used as Designer feedback, as Critic
   evidence, and as the post-condition that must be empty before the loop is
   allowed to finish. Nothing downstream is permitted to move an object.
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"E:/3DUse/SceneCode_audit")
OUT = ROOT / "runtime/outputs/scene_layout"
ROOM_PATH = OUT / "room_measured.json"
LAYOUT_PATH = OUT / "layout.json"
GATEWAY = "http://127.0.0.1:8765/v1/chat/completions"

BRIEF = (
    "Create a realistic modern living room with a detailed two-seat sofa, a coffee "
    "table, a TV cabinet, a large area rug, a floor lamp, a side table, indoor "
    "plants, wall decorations, and appropriate ceiling and ambient lighting. "
    "The room should have coherent furniture arrangements, realistic proportions, "
    "functional circulation paths, and a visually balanced composition."
)

# Sizes are the ACTUAL bounds of the built objects. `local_z` records where the
# geometry sits inside its own origin, which is an asset fact -- it lets the
# adapter derive a pose instead of asking the model to memorise an offset.
# `mount` states what the object is supported by, which decides which
# constraints apply and which pose components are derived rather than chosen.
OBJECT_LIBRARY = [
    {"id": "room_shell", "label": "ArchitecturalShell", "size": [6.0, 5.0, 2.9],
     "stage": "floor_plan", "mount": "fixed", "fixed": True},
    {"id": "sofa_3seater", "label": "Sofa", "size": [2.2, 0.9, 0.88], "stage": "furniture",
     "mount": "floor", "local_z": [0.0, 0.88],
     "note": "faces -Y at yaw 0; backrest must not block the window"},
    {"id": "area_rug", "label": "AreaRug", "size": [3.6, 2.6, 0.02], "stage": "furniture",
     "mount": "floor", "local_z": [0.0, 0.02], "note": "flat; exempt from overlap"},
    {"id": "coffee_table", "label": "CoffeeTable", "size": [1.2, 0.6, 0.42],
     "stage": "furniture", "mount": "floor", "local_z": [0.0, 0.42],
     "note": "serves the sofa; 0.35-0.90 m in front of it"},
    {"id": "tv_cabinet", "label": "TVCabinet", "size": [1.8, 0.45, 0.55],
     "stage": "furniture", "mount": "floor", "local_z": [0.0, 0.55],
     "note": "low media cabinet; must stand against a wall and face the sofa"},
    {"id": "armchair_west", "label": "ArmchairWest", "size": [0.86, 0.86, 0.82],
     "stage": "furniture", "mount": "floor", "local_z": [0.0, 0.82]},
    {"id": "armchair_east", "label": "ArmchairEast", "size": [0.86, 0.86, 0.82],
     "stage": "furniture", "mount": "floor", "local_z": [0.0, 0.82]},
    {"id": "side_table", "label": "SideTable", "size": [0.5, 0.5, 0.55],
     "stage": "furniture", "mount": "floor", "local_z": [0.0, 0.55]},
    {"id": "floor_lamp", "label": "FloorLamp", "size": [0.42, 0.42, 1.57],
     "stage": "furniture", "mount": "floor", "local_z": [0.0, 1.57]},
    {"id": "plant_northwest", "label": "PottedPlant", "size": [0.6, 0.6, 1.2],
     "stage": "furniture", "mount": "floor", "local_z": [0.0, 1.2]},
    {"id": "bookshelf", "label": "Bookshelf", "size": [1.13, 0.385, 1.9],
     "stage": "furniture", "mount": "floor", "local_z": [0.0, 1.9],
     "note": "carcass: must stand back-to-wall, never free-standing"},
    {"id": "wall_art_west", "label": "WallArt", "size": [0.05, 1.06, 0.70],
     "stage": "wall_mounted", "mount": "wall", "local_z": [0.0, 0.70],
     "declares": "mount_height", "note": "hung at eye level on a wall with free span"},
    {"id": "window_curtain", "label": "Curtain", "size": [2.78, 0.07, 2.28],
     "stage": "wall_mounted", "mount": "wall", "local_z": [1.62, 2.44],
     "derived_from": "window", "note": "dresses the window opening"},
    {"id": "wall_tv", "label": "WallTV", "size": [1.23, 0.06, 0.69],
     "stage": "wall_mounted", "mount": "wall", "local_z": [0.0, 0.69],
     "derived_from": "above:tv_cabinet",
     "note": "screen hung above the media cabinet on the same wall, on its centre line"},
    {"id": "ceiling_light", "label": "CeilingLight", "size": [0.68, 0.68, 0.60],
     "stage": "ceiling_mounted", "mount": "ceiling", "local_z": [2.30, 2.90],
     "note": "pendant, hangs from the ceiling above the seating group"},
]

# Nominal order, used as the tie-break for everything with no declared
# dependency. Wall- and ceiling-mounted pieces come first because their pose is
# derived from the room, not packed around other furniture -- leaving them until
# last made the sofa wait on the curtain and pushed the whole seating group to
# the end of the queue, where nothing good was left to fit into.
PLACE_ORDER = [
    "room_shell", "window_curtain", "wall_art_west", "ceiling_light",
    "sofa_3seater", "tv_cabinet", "wall_tv", "area_rug", "coffee_table",
    "armchair_west", "armchair_east", "side_table", "floor_lamp",
    "plant_northwest", "bookshelf",
]

# Threshold constants used by the tests. These are physical/ergonomic rules, not
# design choices: a 6 cm gap to plaster reads as "against the wall", a 25 degree
# tolerance is "facing", and a 1.0 m deep corridor is a door's clear approach.
BACK_GAP = 0.06
FACE_TOL_DEG = 25.0

# The relation kinds `expect()` actually measures. Anything outside this set is
# reported as `relation.unsupported` instead of being dropped, so the inventory
# stage cannot ask for something no rule will ever check.
ENFORCED_RELATIONS = {"faces", "centered_with", "above", "clearance",
                      "against_wall", "on_wall", "in_front_of"}
CORRIDOR_DEPTH = 1.0


# --------------------------------------------------------------------------
# room
# --------------------------------------------------------------------------
def load_room() -> dict:
    if not ROOM_PATH.is_file():
        raise SystemExit("room_measured.json missing -- run: blender -b --python "
                         "adsl_adapter/room_spec.py")
    return json.loads(ROOM_PATH.read_text(encoding="utf-8"))


def load_assets(library: dict) -> dict:
    """Replace the hand-typed size table with measured local bounds.

    The typed table mixed three conventions (local, already-rotated, envelope),
    which is how a 0.775 m chair came to be tested as 0.86 m and a 1.06 m wide
    picture as 0.05 m wide. Measurement is the only source that cannot drift.
    """
    path = OUT / "assets_measured.json"
    if not path.is_file():
        raise SystemExit("assets_measured.json missing -- run: blender -b --python "
                         "adsl_adapter/measure_assets.py")
    measured = json.loads(path.read_text(encoding="utf-8"))["assets"]
    updated = {}
    for oid, spec in library.items():
        m = measured.get(oid)
        if not m:
            updated[oid] = spec
            continue
        spec = dict(spec)
        spec["size"] = m["size"]
        spec["local_z"] = m["local_z"]
        spec["measured_parts"] = m["parts"]
        updated[oid] = spec
    return updated


def room_text(room: dict) -> str:
    i, d, w = room["interior"], room["openings"]["door"], room["openings"]["window"]
    return (
        f"Room interior: {i['width']:.2f} m wide (x {i['x'][0]:+.2f}..{i['x'][1]:+.2f}), "
        f"{i['depth']:.2f} m deep (y {i['y'][0]:+.2f}..{i['y'][1]:+.2f}), "
        f"{i['height']:.2f} m high (z {i['z'][0]:.2f}..{i['z'][1]:.2f}).\n"
        f"Walls (id / inner plane / extent): front y={room['walls']['front']['plane']:+.2f} "
        f"x {room['walls']['front']['span'][0]:+.2f}..{room['walls']['front']['span'][1]:+.2f}; "
        f"back y={room['walls']['back']['plane']:+.2f}; west x={room['walls']['west']['plane']:+.2f} "
        f"y {room['walls']['west']['span'][0]:+.2f}..{room['walls']['west']['span'][1]:+.2f}; "
        f"east x={room['walls']['east']['plane']:+.2f}.\n"
        f"Existing openings: door on {d['wall']} wall, {d['width']:.2f} m wide, "
        f"{d['height']:.2f} m high, x {d['x'][0]:+.2f}..{d['x'][1]:+.2f}; "
        f"window on {w['wall']} wall, {w['width']:.2f} x {w['height']:.2f} m, "
        f"x {w['x'][0]:+.2f}..{w['x'][1]:+.2f}, z {w['z'][0]:.2f}..{w['z'][1]:.2f}."
    )


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------
INVENTORY_PROMPT = """You are the SceneCode request-analysis + floor-plan stage.
You do NOT create geometry and you do NOT choose furniture coordinates yet.

Contract: AssetItem(description, short_name, dimensions[w,d,h] in metres,
object_type, strategies). Five stages: floor_plan, furniture, wall_mounted,
ceiling_mounted, manipuland. PlacementInfo records the support surface.

{room}

+Z up, +X right, -Y front. Floor top z = {floor_z:.2f}.

Available objects (use EXACTLY these ids, one each):
{library}

User brief: {brief}

Floor-plan decisions you must make (this is your stage, not the furniture stage):
* where the door opening sits on the front wall: give its centre x and width.
  The room must keep a clear approach path from that door into the room, and the
  seating group must not face a blank wall. The window on the back wall is fixed.
* which wall carries the media group (cabinet + screen), and which wall is free
  for a single hung picture.

Return ONLY JSON:
{{
  "schema": "scenecode.layout.v1",
  "room_plan": {{"door": {{"wall": "front", "center_x": 0.0, "width": 1.2}},
                 "media_wall": "front|back|west|east",
                 "art_wall": "front|back|west|east"}},
  "placement_style": "natural",
  "stages": ["floor_plan", "furniture", "wall_mounted", "ceiling_mounted", "manipuland"],
  "inventory": [
    {{"id": "<exact id>", "stage": "<stage>",
      "asset_item": {{"description": "...", "short_name": "...",
                      "dimensions": [w,d,h], "object_type": "FURNITURE",
                      "strategies": ["code_generated"]}},
      "mount": {{"surface": "floor|wall|ceiling", "wall": "<wall id or null>",
                 "mount_height": "<centre height in m, wall items only>"}},
      "relations": ["against_wall:front", "faces:sofa_3seater"],
      "intent": "<one sentence: where this object belongs and why>"}}
  ],
  "circulation_paths": [{{"from": "door", "to": "room_centre", "min_width_m": 0.8}}],
  "layout_rationale": "<2-3 sentences on balance, sightlines, circulation>"
}}
The ONLY relations that are measured are these seven kinds. Anything else is
rejected and you will be asked again, so do not invent vocabulary:
  against_wall:<wall>            floor object must touch that wall
  on_wall:<wall>                 hung object must hang on that wall
  faces:<id>                     this object's front must point at <id>
  centered_with:<id>             aligned on the axis perpendicular to facing
  above:<id>                     stacked on top of <id>
  in_front_of:<id>:<metres>      keep that much open floor between this object
                                 and <id>'s front face
  clearance:<id>:<metres>        keep that much gap from <id>, any direction
Every relation is measured against the finished room, and an unsatisfiable one
blocks the whole layout, so declare only what you actually need:
* a rug runs under the seating and the table -- give it no clearance relation;
* `in_front_of` is for objects that stand in the open in front of something
  (a coffee table before a sofa). Never use it for rugs, corner plants, or
  anything hung on a wall;
* two objects must not each face the other unless both really do.
Do NOT include x/y/yaw coordinates. Do NOT output code or markdown.
"""

PLACE_PROMPT = """You are the SceneCode furniture Designer placing ONE object.
You control x, y and yaw only ({freedom}). +Z up, +X right, -Y front.

{room}

Brief: {brief}

Object: {current}

Already placed (id, x, y, half-extents, yaw, free space in front):
{occupied}

Violations of your previous attempt (empty on the first try): {feedback}

Return ONLY JSON:
{{"id": "<same id>", "placement": {{"x": 0.0, "y": 0.0, "z": 0.0, "yaw_deg": 0.0}},
  "reason": "<one sentence>"}}

Hard constraints for THIS object:
{rules}
"""

CRITIC_PROMPT = """You are the SceneCode layout Critic (furniture_agent/stateful_critic_agent.yaml).
Judge ONLY the arrangement: circulation, clearances, sightlines, alignment,
balance, and whether the brief is satisfied. Geometry and materials come from an
accepted asset library and are out of scope.

{room}

Brief: {brief}

Proposed layout:
{layout}

Deterministic violations still open (empty means the measurable rules pass):
{violations}

Return ONLY JSON:
{{"score": 0-10, "verdict": "passed|needs_revision",
  "issues": ["<what is wrong, in terms of the arrangement>"],
  "suggested_changes": ["<imperative correction, naming the object id>"]}}
"""


# --------------------------------------------------------------------------
# llm plumbing
# --------------------------------------------------------------------------
def call_llm(prompt: str, max_tokens: int = 3000, retries: int = 4) -> str:
    payload = {
        "model": "gpt-5.4",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                GATEWAY, data=raw, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if not content:
                raise RuntimeError("empty LLM content")
            return content
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"[layout] LLM attempt {attempt+1} failed: {type(exc).__name__}", flush=True)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM unavailable after {retries} attempts: {last}")


def parse_json(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def half_extents(size, yaw_deg: float) -> tuple[float, float]:
    w, d = size[0] / 2, size[1] / 2
    a = math.radians(float(yaw_deg))
    return (abs(math.cos(a)) * w + abs(math.sin(a)) * d,
            abs(math.sin(a)) * w + abs(math.cos(a)) * d)


def forward(yaw_deg: float) -> tuple[float, float]:
    """World direction the object faces. Asset convention: front is local -Y."""
    a = math.radians(float(yaw_deg))
    return (math.sin(a), -math.cos(a))


def yaw_for_normal(normal: list[float]) -> float:
    """Yaw that makes an asset's front (+ -Y) point along a wall's inward normal."""
    return round(math.degrees(math.atan2(normal[0], -normal[1])), 1)


def footprint(pose: dict, size) -> list[tuple[float, float]]:
    hx, hy = size[0] / 2, size[1] / 2
    pts = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]
    a = math.radians(float(pose.get("yaw_deg", 0)))
    c, s = math.cos(a), math.sin(a)
    return [(pose["x"] + c * x - s * y, pose["y"] + s * x + c * y) for x, y in pts]


def front_gap(pose: dict, size, target: dict) -> tuple[float, str, int, float]:
    """Clearance between `pose` and the face `target` is looking at.

    Returns (gap, world axis, direction sign, combined half-depth on that axis).
    A sofa facing -y invites a table on its -y side; a table 0.8 m to its side is
    not "in front" even though the centre distance is large. Measuring through
    the target's own facing direction is what makes this different from
    `clearance`, which only looks at the axis between the two centres.
    """
    fx, fy = forward(target["yaw_deg"])
    along = "y" if abs(fy) >= abs(fx) else "x"
    sign = 1 if (fy if along == "y" else fx) > 0 else -1
    thx, thy = half_extents(target["size"], target["yaw_deg"])
    ohx, ohy = half_extents(size, pose["yaw_deg"])
    t_half = thy if along == "y" else thx
    o_half = ohy if along == "y" else ohx
    ahead = ((pose["y"] - target["y"]) if along == "y"
             else (pose["x"] - target["x"])) * sign
    return ahead - t_half - o_half, along, sign, t_half + o_half


def wall_gap(pose: dict, size, wall: dict) -> float:
    """Signed distance from the object's nearest face to the wall plane.

    Measured along the wall's inward normal: 0 means flush, positive means
    standing off, negative buried. The plane coordinate has to be compared on the
    axis the normal runs along -- projecting the whole point onto the normal and
    subtracting `plane` flips sign on a wall whose normal points in -x/-y.
    """
    nx, ny = wall["normal"]
    plane = wall["plane"]
    if abs(nx) > abs(ny):
        return round(min((px - plane) * nx for px, _ in footprint(pose, size)), 5)
    return round(min((py - plane) * ny for _, py in footprint(pose, size)), 5)


def resolve_pose(oid: str, raw: dict, placed: dict, spec: dict, room: dict) -> dict:
    """Fill in every pose component that already follows from room or intents.

    The Designer supplies a position along the support surface plus the intents
    it is entitled to (which wall, what it faces, how high). Everything else is
    derived: a pendant hangs from the ceiling plane, a curtain hangs off the
    opening it dresses, a screen sits above its cabinet on the same wall, and a
    hung object's yaw is simply the wall's inward normal.
    """
    pose = {k: float(raw.get(k, 0.0) or 0.0) for k in ("x", "y", "z", "yaw_deg")}
    mount = spec.get("mount")
    local_z = spec.get("local_z") or [0.0, spec["size"][2]]

    if mount == "floor":
        pose["z"] = 0.0
        # A back-to-wall floor object must open INTO the room: its back (+Y in
        # the asset frame) sits on the wall, so its front (-Y) points along the
        # wall's inward normal -- the exact convention wall-mounted objects use.
        # Leaving this to the Designer produced a 180 deg yaw error on the
        # bookshelf: the solid back faced the room and the shelves faced the
        # plaster. Derive it so the orientation can never be a taste call.
        for rel in raw.get("relations") or []:
            if rel.startswith("against_wall:"):
                wall = room["walls"].get(rel.split(":", 1)[1])
                if wall:
                    pose["yaw_deg"] = yaw_for_normal(wall["normal"])
                break
    elif mount == "ceiling":
        pose["z"] = round(room["interior"]["z"][1] - local_z[1], 4)
        pose["yaw_deg"] = 0.0
    elif mount == "wall":
        wall_id = _wall_for(oid, spec, placed, room, raw.get("relations") or [])
        derived = spec.get("derived_from") or ""
        wall = room["walls"].get(wall_id) if wall_id else None
        if wall:
            nx, ny = wall["normal"]
            # The asset's local depth (size[1]) is what faces the wall, so the
            # back face lands on the plane however the object is rotated.
            off = spec["size"][1] / 2 + 0.005
            if nx:
                pose["x"] = round(wall["plane"] + nx * off, 4)
            else:
                pose["y"] = round(wall["plane"] + ny * off, 4)
            pose["yaw_deg"] = yaw_for_normal(wall["normal"])

        if derived == "window":
            win = room["openings"]["window"]
            wall = room["walls"][win["wall"]]
            along = "x" if wall["normal"][1] else "y"
            pose[along] = win["center_x"]
            # A floor-length drape: solve the root height from its own geometry so
            # the hem lands on the floor regardless of where the asset's origin is.
            pose["z"] = round(room["interior"]["z"][0] - local_z[0], 4)
        elif derived.startswith("above:"):
            host = placed.get(derived.split(":", 1)[1])
            if host and wall:
                along = "x" if wall["normal"][1] else "y"
                pose[along] = round(host[along], 4)
                # Root height = wanted bottom edge minus the asset's own bottom
                # offset. wall_tv is built centred on its origin (local_z spans
                # -0.345..0.345), so ignoring local_z[0] drops the panel half a
                # metre -- exactly the bug that had to be patched in assembly.
                pose["z"] = round(host["z"] + host["size"][2] + 0.10 - local_z[0], 4)
        else:
            height = float(raw.get("mount_height") or 0.0)
            if height:
                pose["z"] = round(height - (local_z[0] + local_z[1]) / 2, 4)
    return {k: round(v, 4) for k, v in pose.items()}


def _wall_from_relations(relations: list[str], spec: dict) -> str | None:
    for rel in relations:
        for key in ("against_wall:", "on_wall:"):
            if rel.startswith(key):
                return rel.split(":", 1)[1]
    return spec.get("wall")


def _wall_for(oid: str, spec: dict, placed: dict, room: dict,
              relations: list[str] | None = None) -> str | None:
    """Which wall an object belongs to, in order of authority.

    Declared relation first, then whatever the object's own derivation implies
    (a drape belongs to the window's wall; a screen belongs to its host's wall).
    """
    declared = _wall_from_relations(relations or [], spec)
    if declared:
        return declared
    derived = spec.get("derived_from") or ""
    if derived == "window":
        return room["openings"]["window"]["wall"]
    if derived.startswith("above:"):
        host = placed.get(derived.split(":", 1)[1]) or {}
        return host.get("_wall")
    return spec.get("wall")


# --------------------------------------------------------------------------
# constraints: typed, testable, no repair
# --------------------------------------------------------------------------
def expect(oid: str, pose: dict, placed: dict, spec: dict, room: dict,
           relations: list[str] | None = None, plan: dict | None = None) -> list[dict]:
    issues: list[dict] = []
    add = lambda cid, msg: issues.append({"id": cid, "msg": msg})  # noqa: E731
    size, mount = spec["size"], spec.get("mount")
    inter = room["interior"]
    x, y, yaw = pose["x"], pose["y"], pose["yaw_deg"]

    if oid == "room_shell":
        return [] if abs(x) < 1e-6 and abs(y) < 1e-6 else [{"id": "shell.origin", "msg": "shell at origin"}]

    if mount == "floor":
        hx, hy = half_extents(size, yaw)
        if x - hx < inter["x"][0] - 1e-3 or x + hx > inter["x"][1] + 1e-3 or \
                y - hy < inter["y"][0] - 1e-3 or y + hy > inter["y"][1] + 1e-3:
            add("bounds", f"{oid} leaves the room (x={x:.2f},y={y:.2f})")
        if oid != "area_rug":
            for pid, p in placed.items():
                if p.get("_exempt") or pid == oid:
                    continue
                if (abs(x - p["x"]) < hx + p["hx"] - 0.02 and
                        abs(y - p["y"]) < hy + p["hy"] - 0.02):
                    add(f"overlap.{pid}", f"{oid} overlaps {pid} in plan view")
            door = room["openings"]["door"]
            py0 = room["walls"][door["wall"]]["plane"]
            if (x + hx > door["x"][0] + 0.02 and x - hx < door["x"][1] - 0.02
                    and y - hy < py0 + CORRIDOR_DEPTH and y + hy > py0):
                add("corridor", f"{oid} blocks the {door['wall']} door approach "
                                f"(x {door['x'][0]:+.2f}..{door['x'][1]:+.2f}, "
                                f"{CORRIDOR_DEPTH:.1f} m deep)")

            # Nothing tall may stand in front of the window: a sofa backed onto
            # the glass is the classic version of this, and it is derivable from
            # the opening rather than a taste call.
            win = room["openings"]["window"]
            wall = room["walls"][win["wall"]]
            along = "x" if wall["normal"][1] else "y"
            across = "y" if along == "x" else "x"
            if pose["z"] + size[2] > win["z"][0]:
                near = abs(pose[across] - wall["plane"]) - hy if across == "y" else \
                    abs(pose[across] - wall["plane"]) - hx
                if near < 0.35 and (pose[along] + (hx if along == "x" else hy) > win["x"][0]
                                    and pose[along] - (hx if along == "x" else hy) < win["x"][1]):
                    add("opening.window",
                        f"{oid} stands in front of the {win['wall']} window "
                        f"({near:.2f} m from the wall); move it clear of the opening")

    # wall contact: declared against_wall must actually touch that wall
    for rel in relations or []:
        if rel.startswith("against_wall:"):
            wid = rel.split(":", 1)[1]
            wall = room["walls"].get(wid)
            if not wall:
                add("wall.unknown", f"{oid} declares unknown wall {wid}")
                continue
            g = wall_gap(pose, size, wall)
            if g < -0.005:
                add("wall.buried", f"{oid} is {abs(g)*1000:.0f} mm inside the {wid} wall")
            elif g > BACK_GAP:
                add("wall.gap", f"{oid} stands {g*100:.0f} cm off the {wid} wall "
                                f"(max {BACK_GAP*100:.0f} cm)")
            # The object must open INTO the room, not toward the wall. The asset
            # front is local -Y, so front_world = (sin yaw, -cos yaw); a 180 deg
            # error makes this point at the wall. This is the test that catches
            # the bookshelf-reversed defect regardless of where the pose came
            # from (Designer call or warm-start seed).
            facing = math.sin(math.radians(yaw)) * wall["normal"][0] \
                - math.cos(math.radians(yaw)) * wall["normal"][1]
            if facing < 0.5:
                add("wall.facing",
                    f"{oid} opens toward the {wid} wall instead of the room "
                    f"(back-to-wall reversed); yaw should be "
                    f"{yaw_for_normal(wall['normal']):.0f}, got {yaw:.0f}")

    if mount == "wall":
        wid = _wall_for(oid, spec, placed, room, relations)
        wall = room["walls"].get(wid) if wid else None
        if wall is None:
            add("wall.unassigned", f"{oid} needs a wall id")
        else:
            g = wall_gap(pose, size, wall)
            if g < -0.02 or g > 0.12:
                add("wall.hang", f"{oid} must hang on the {wid} wall "
                                 f"(plane {wall['plane']:+.2f}), currently {g*100:+.0f} cm off")
            want = yaw_for_normal(wall["normal"])
            if abs(((yaw - want) + 180) % 360 - 180) > 5:
                add("wall.yaw", f"{oid} must face into the room: yaw {want:.0f}, got {yaw:.0f}")
            span = wall["span"]
            hx, _ = half_extents(size, yaw)
            centre = x if wall["normal"][1] else y
            if centre - hx < min(span) + 0.05 or centre + hx > max(span) - 0.05:
                add("wall.span", f"{oid} runs past the end of the {wid} wall")
            if oid == "wall_art_west":
                lz = spec.get("local_z") or [0.0, size[2]]
                zc = pose["z"] + (lz[0] + lz[1]) / 2
                if not (1.10 <= zc <= 1.70):
                    add("art.height", f"picture centre at {zc:.2f} m, want 1.10-1.70 m")

    if mount == "ceiling":
        if oid == "ceiling_light":
            top = pose["z"] + (spec["local_z"][1] if spec.get("local_z") else size[2])
            if abs(top - inter["z"][1]) > 0.02:
                add("ceiling.contact", f"pendant canopy at z={top:.2f}, ceiling is "
                                       f"{inter['z'][1]:.2f}")

    # relations that couple two objects
    for rel in relations or []:
        kind, _, arg = rel.partition(":")
        if kind == "faces":
            target = placed.get(arg)
            if not target:
                continue
            fx, fy = forward(yaw)
            dx, dy = target["x"] - x, target["y"] - y
            norm = math.hypot(dx, dy) or 1e-6
            cos = (fx * dx + fy * dy) / norm
            ang = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
            if ang > FACE_TOL_DEG:
                add(f"faces.{arg}", f"{oid} faces {ang:.0f} deg away from {arg} "
                                    f"(max {FACE_TOL_DEG:.0f})")
        elif kind == "centered_with":
            target = placed.get(arg)
            if target and spec.get("mount") == "floor":
                if abs(x - target["x"]) > 0.15:
                    add(f"centred.{arg}", f"{oid} is {abs(x-target['x']):.2f} m off "
                                          f"{arg}'s centre line")
        elif kind == "above":
            target = placed.get(arg)
            if target:
                # "Above" is measured along the wall the host stands on: a screen
                # over a cabinet shares that wall's horizontal axis, whichever
                # wall the agent picked.
                axis = _wall_axis(target.get("_wall"), room)
                if abs(pose[axis] - target[axis]) > 0.05:
                    add("above.axis", f"{oid} centre is "
                                      f"{abs(pose[axis]-target[axis])*100:.0f} cm off "
                                      f"{arg}'s centre line")
                if pose["z"] < target["z"] + target["size"][2]:
                    add("above.inside", f"{oid} overlaps the top of {arg}")
        elif kind == "clearance":
            name, _, dist = arg.partition(":")
            target = placed.get(name)
            if target and dist:
                want_min = float(dist)
                # Axis-aligned gap between the two footprints. Using the max
                # half-extent as a radius (the previous arithmetic) subtracted
                # the longest side of the sofa from a side-by-side distance and
                # reported a 0.4 m gap as -0.56 m.
                # Half-extents are taken AFTER rotation: the unrotated size
                # let a sofa turned 90 degrees pass a 0.5 m clearance with 7 cm
                # of real gap.
                ohx, ohy = half_extents(spec["size"], yaw)
                thx, thy = half_extents(target["size"], target.get("yaw_deg", 0.0))
                gx = abs(x - target["x"]) - (ohx + thx)
                gy = abs(y - target["y"]) - (ohy + thy)
                gap = max(gx, gy)
                if gap < want_min - 1e-3:
                    # The requirement travels in the id so a repair hint can be
                    # derived from the violation alone.
                    add(f"clearance.{name}:{want_min:.2f}",
                        f"{oid} leaves {gap:.2f} m to {name}, needs {want_min:.2f}")
        elif kind == "in_front_of":
            name, _, dist = arg.partition(":")
            target = placed.get(name)
            if target and dist:
                want_min = float(dist)
                gap, _, _, _ = front_gap(pose, spec["size"], target)
                if gap < want_min - 1e-3:
                    add(f"front.{name}:{want_min:.2f}",
                        f"{oid} leaves {gap:.2f} m of floor in front of {name}, "
                        f"needs {want_min:.2f}")
        elif kind not in ENFORCED_RELATIONS:
            # The inventory asks for relations in prose, and unsupported kinds
            # used to disappear without a trace: `in_front_of` was requested on
            # the coffee table and nothing measured it, so the table sat 0.25 m
            # off the sofa's front edge while every check reported zero issues.
            # An unenforceable declaration is now itself a violation.
            add("relation.unsupported",
                f"{oid} declares '{kind}', which no rule enforces; use one of "
                f"{sorted(ENFORCED_RELATIONS)}")

    # viewing geometry: the media pair has to work as a pair, on whichever wall
    # the cabinet was told to stand against
    sofa, cab = placed.get("sofa_3seater"), placed.get("tv_cabinet")
    if oid in {"sofa_3seater", "tv_cabinet"} and sofa and cab:
        axis = _wall_axis(cab.get("_wall"), room)
        cross = "y" if axis == "x" else "x"
        lateral = abs(sofa[axis] - cab[axis])
        depth = abs(sofa[cross] - cab[cross])
        if lateral > 0.25:
            add("view.axis", f"seating axis broken: sofa {axis}={sofa[axis]:.2f} vs "
                             f"cabinet {axis}={cab[axis]:.2f}")
        if not (2.0 <= depth <= 4.8):
            add("view.distance", f"screen distance {depth:.2f} m is outside 2.0-4.8 m")
    return issues


def _wall_axis(wall_id: str | None, room: dict) -> str:
    """The horizontal axis that runs *along* a wall: 'x' for the north/south walls."""
    wall = room["walls"].get(wall_id or "")
    if wall is None:
        return "x"
    return "x" if wall["normal"][1] else "y"


def rules_text(oid: str, spec: dict, relations: list[str], room: dict) -> str:
    """Render the constraints as prose for the Designer. All values are derived."""
    i = room["interior"]
    lines = [f"- Stay inside the room: x {i['x'][0]:+.2f}..{i['x'][1]:+.2f}, "
             f"y {i['y'][0]:+.2f}..{i['y'][1]:+.2f}."]
    if spec.get("mount") == "floor":
        lines.append("- Do not overlap any already placed floor object (rug exempt).")
        door = room["openings"]["door"]
        lines.append(f"- Keep the {door['wall']} door approach clear: "
                     f"x {door['x'][0]:+.2f}..{door['x'][1]:+.2f} for {CORRIDOR_DEPTH:.1f} m "
                     f"in front of y {room['walls'][door['wall']]['plane']:+.2f}.")
    for rel in relations:
        kind, _, arg = rel.partition(":")
        if kind == "against_wall":
            wall = room["walls"].get(arg)
            if wall:
                lines.append(f"- Stand against the {arg} wall: nearest face within "
                             f"{BACK_GAP*100:.0f} cm of plane {wall['plane']:+.2f}, "
                             f"not inside it.")
        elif kind == "faces":
            lines.append(f"- Front must point at {arg} (within {FACE_TOL_DEG:.0f} deg). "
                         f"Remember the asset faces -Y at yaw 0.")
        elif kind == "centered_with":
            lines.append(f"- Share {arg}'s centre line (within 0.15 m).")
        elif kind == "clearance":
            name, _, dist = arg.partition(":")
            lines.append(f"- Keep at least {dist} m clear of {name}.")
        elif kind == "on_wall":
            wall = room["walls"].get(arg)
            if wall:
                lines.append(f"- Hang on the {arg} wall (plane {wall['plane']:+.2f}); "
                             f"the wall sets your yaw, you choose the position along it.")
    if oid == "sofa_3seater":
        lines.append("- Seating group: the sofa and the media cabinet must share the "
                     "same x axis within 0.25 m, and stand 2.0-4.8 m apart.")
    if oid == "tv_cabinet":
        lines.append("- Seating group: the cabinet and the sofa must share the same x "
                     "axis within 0.25 m, and stand 2.0-4.8 m apart.")
    if oid == "wall_tv":
        lines.append("- The screen's pose is DERIVED from the cabinet and the wall; "
                     "declare only which wall it hangs on.")
    if spec.get("derived_from") == "window":
        lines.append("- Pose is derived from the window opening; declare the wall only.")
    return "\n".join(lines)


def freedom_text(spec: dict) -> str:
    """What the Designer is actually allowed to decide for this object."""
    if spec.get("mount") == "floor":
        return "free on the floor plan"
    if spec.get("mount") == "ceiling":
        return "where it hangs under the ceiling; z is derived"
    if spec.get("derived_from"):
        return "the position along the wall; plane, yaw and height are derived"
    return "the position along the wall and a mount_height; yaw is derived"


# --------------------------------------------------------------------------
# placement
# --------------------------------------------------------------------------
def store(placed: dict, oid: str, pose: dict, spec: dict, relations: list,
          reason: str = "") -> None:
    """Commit one accepted pose, together with the bookkeeping the next
    constraint check needs (footprint, exemption, wall, the relations that were
    actually in force)."""
    hx, hy = half_extents(spec["size"], pose["yaw_deg"])
    placed[oid] = {**pose, "hx": hx, "hy": hy, "size": spec["size"],
                   "_exempt": oid == "area_rug" or spec.get("mount") != "floor",
                   "_mount": spec.get("mount"),
                   "_wall": _wall_from_relations(relations, spec),
                   "_relations": list(relations),
                   "reason": reason}


def occupied_text(placed: dict) -> str:
    return ("\n".join(
        f"  - {pid}: ({p['x']:.2f}, {p['y']:.2f}) "
        f"hx={p['hx']:.2f} hy={p['hy']:.2f} yaw={p['yaw_deg']:.0f}"
        for pid, p in placed.items() if not p.get("_exempt")) or "  (none yet)")


def hint_text(oid: str, spec: dict, pose: dict, problems: list[dict],
              placed: dict, room: dict, relations: list) -> str:
    """Turn each violation into the value that would satisfy it.

    A constraint the model keeps failing usually has one right answer. Deriving
    it is arithmetic on the target, not a design decision, and it saves the
    model from guessing at conventions it demonstrably does not share (it wrote
    yaw 0 four times for a cabinet that had to face +Y).
    """
    hints = []
    for p in problems:
        if p["id"].startswith("faces."):
            target = placed.get(p["id"].split(".", 1)[1])
            if target:
                dx, dy = target["x"] - pose["x"], target["y"] - pose["y"]
                hints.append(f"{p['id']} -> yaw_deg={math.degrees(math.atan2(dx, -dy)):.0f}")
        elif p["id"] == "wall.yaw":
            wid = _wall_for(oid, spec, placed, room, relations)
            if wid:
                hints.append(f"wall.yaw -> yaw_deg="
                             f"{yaw_for_normal(room['walls'][wid]['normal']):.0f}")
        elif p["id"].startswith("clearance."):
            ref, _, need = p["id"].split(".", 1)[1].partition(":")
            target = placed.get(ref)
            if target:
                want = float(need or 0.0)
                axis = ("y" if abs(pose["y"] - target["y"]) >= abs(pose["x"] - target["x"])
                        else "x")
                step = 1 if pose[axis] >= target[axis] else -1
                half = (spec["size"][1 if axis == "y" else 0] / 2
                        + target["size"][1 if axis == "y" else 0] / 2)
                gap = abs(pose[axis] - target[axis]) - half
                # Give the coordinate, not the delta. "move y -1 by 0.30 m" was
                # read four times as "move a bit" and the table never cleared
                # the sofa; an absolute bound is unambiguous and does not depend
                # on the model doing arithmetic on its own previous output.
                edge = target[axis] + step * (want + half)
                hints.append(f"{p['id']} -> {axis} {'>=' if step > 0 else '<='} "
                             f"{edge:.2f} (gap now {gap:.2f}, need {want:.2f})")
        elif p["id"].startswith("front."):
            ref, _, need = p["id"].split(".", 1)[1].partition(":")
            target = placed.get(ref)
            if target:
                want = float(need or 0.0)
                gap, along, sign, half = front_gap(pose, spec["size"], target)
                edge = target[along] + sign * (want + half)
                hints.append(f"{p['id']} -> {along} "
                             f"{'>=' if sign > 0 else '<='} {edge:.2f} "
                             f"(gap now {gap:.2f}, need {want:.2f})")
        elif p["id"] == "opening.window":
            win = room["openings"]["window"]
            wall = room["walls"][win["wall"]]
            hints.append(f"opening.window -> keep 0.35 m clear of "
                         f"{win['wall']} (y {wall['plane']:+.2f}) or move outside "
                         f"x {win['x'][0]:+.2f}..{win['x'][1]:+.2f}")
    return "; ".join(sorted(set(hints)))


def design_pose(oid: str, spec: dict, item: dict, relations: list, placed: dict,
                room: dict, attempts: int = 4,
                history: list | None = None) -> tuple[dict | None, str, int, list]:
    """Ask the Designer for one object until its own constraints pass.

    Returns (pose or None, reason, attempts used, last violations). The object
    is checked against `placed` as it stands, so callers decide the context: the
    sequential pass passes the partial room, the repair pass passes the whole
    room.
    """
    feedback = "none"
    problems: list[dict] = []
    for attempt in range(1, attempts + 1):
        prompt = PLACE_PROMPT.format(
            freedom=freedom_text(spec), room=room_text(room), brief=BRIEF,
            current=json.dumps({
                "id": oid, "size": spec["size"], "stage": spec["stage"],
                "mount": item.get("mount") or {"surface": spec.get("mount")},
                "relations": relations,
                "intent": item.get("intent", ""),
            }, ensure_ascii=False),
            occupied=occupied_text(placed), feedback=feedback,
            rules=rules_text(oid, spec, relations, room))
        result = parse_json(call_llm(prompt, max_tokens=500))
        if not result or not isinstance(result.get("placement"), dict):
            feedback = "not valid JSON with a placement object"
            continue
        raw = dict(result["placement"])
        raw["relations"] = relations
        raw.setdefault("mount_height", (item.get("mount") or {}).get("mount_height"))
        pose = resolve_pose(oid, raw, placed, spec, room)
        problems = expect(oid, pose, placed, spec, room, relations)
        if history is not None:
            history.append({"id": oid, "attempt": attempt, "pose": pose,
                            "violations": problems})
        if not problems:
            return pose, result.get("reason", ""), attempt, problems
        feedback = json.dumps([f"[{p['id']}] {p['msg']}" for p in problems])
        hints = hint_text(oid, spec, pose, problems, placed, room, relations)
        if hints:
            feedback += " | values that satisfy them: " + hints
        print(f"[layout] {oid} attempt {attempt} rejected: {feedback}", flush=True)
    return None, "", attempts, problems


def unusable_relations(inventory: dict, room: dict) -> list[str]:
    """Relations the room has no rule for, listed so the planner can rewrite them.

    Returns human-readable lines and strips nothing: the caller decides whether
    to replace the inventory or to proceed, and a silent drop would hide the
    fact that the agent asked for something nobody measured.
    """
    ids = {i["id"] for i in inventory.get("inventory") or []}
    walls = set(room["walls"])
    bad = []
    for item in inventory.get("inventory") or []:
        for rel in item.get("relations") or []:
            kind, _, arg = rel.partition(":")
            if kind not in ENFORCED_RELATIONS:
                bad.append(f"{item['id']}: '{rel}' (unknown kind '{kind}')")
                continue
            if kind in ("against_wall", "on_wall"):
                if arg not in walls:
                    bad.append(f"{item['id']}: '{rel}' (no such wall)")
                continue
            target = arg.partition(":")[0]
            if target not in ids:
                bad.append(f"{item['id']}: '{rel}' (no such object id '{target}')")
    return bad


def dependency_order(inventory: dict, library: dict) -> list[str]:
    """Place what others point at before the things that point at it.

    The fixed PLACE_ORDER put the sofa first, so its `faces:tv_cabinet` had no
    target to measure against and was accepted unverified; the TV arrived four
    calls later and nothing went back. A declared relation is a dependency, and
    a dependency the Designer ignores is a constraint the room never sees.

    Cycles are real -- the sofa faces the cabinet and the cabinet faces the sofa
    -- and are broken in favour of the object whose position is already pinned:
    an `against_wall` object has fewer degrees of freedom left, so it goes first
    and the free-standing one has to comply.
    """
    inv = {i["id"]: i for i in inventory.get("inventory") or []}
    base = {oid: n for n, oid in enumerate(PLACE_ORDER)}
    ids = [oid for oid in PLACE_ORDER if oid in inv]
    deps: dict[str, set[str]] = {}
    for oid in ids:
        spec = library[oid]
        if spec.get("fixed"):
            deps[oid] = set()
            continue
        # A wall or ceiling object takes its orientation from the wall, so what
        # it "faces" is not a dependency -- but `above:host` still is: the TV
        # cannot be hung until the cabinet it sits over has a pose, because its
        # height is derived from the cabinet's top.
        wall_bound = spec.get("mount") in ("wall", "ceiling") or spec.get("derived_from")
        need = set()
        for rel in inv[oid].get("relations") or []:
            kind, _, arg = rel.partition(":")
            if kind in ("faces", "in_front_of"):
                if wall_bound:
                    continue
            elif kind not in ("above", "centered_with"):
                continue
            target = arg.partition(":")[0]
            if target in inv and target != oid:
                need.add(target)
        deps[oid] = need

    def pinned(oid: str) -> bool:
        return any(str(r).startswith(("against_wall:", "on_wall:"))
                   for r in inv[oid].get("relations") or [])

    # Break mutual dependencies up front, not when the queue stalls: a cycle
    # discovered mid-loop releases its member after every independent object has
    # already been placed, which pushes the media cabinet to the very end and
    # leaves the sofa nowhere to sit.
    for a in ids:
        for b in list(deps.get(a, ())):
            if a in deps.get(b, ()):
                loser = b if pinned(a) and not pinned(b) else a
                deps[loser].discard(a if loser is b else b)
                print(f"[layout] mutual dependency {a} <-> {b}; "
                      f"{loser} no longer waits", flush=True)

    order: list[str] = []
    remaining = list(ids)
    while remaining:
        ready = [o for o in remaining if deps[o] <= set(order)]
        if not ready:
            # Cycle: release the most constrained object and drop its unmet
            # dependencies rather than deadlocking the whole room.
            remaining.sort(key=lambda o: (not pinned(o), len(deps[o]), base.get(o, 99)))
            freed = remaining[0]
            print(f"[layout] dependency cycle around {freed}; "
                  f"placing it first and dropping {sorted(deps[freed] - set(order))}",
                  flush=True)
            deps[freed] = set()
            ready = [freed]
        ready.sort(key=lambda o: base.get(o, 99))
        # One at a time, lowest nominal rank first: releasing the whole ready
        # set in one go lets late objects that happen to have no dependencies
        # jump ahead of early ones that are merely waiting on a single, already
        # placeable neighbour.
        order.append(ready[0])
        remaining.remove(ready[0])
    return order


def place_sequential(inventory: dict, library: dict, room: dict,
                     seed: dict | None = None) -> tuple[dict, list, dict]:
    """Place one object at a time, repairing only what is broken.

    `seed` carries the previous round's poses. A pose that still satisfies every
    constraint is kept without asking the model again -- re-rolling the whole
    room each round made things worse, because a fresh Design call would discard
    eleven correct decisions to fix one.
    """
    placed: dict[str, dict] = {}
    history: list[dict] = []
    relations_used: dict[str, list[str]] = {}
    seed = seed or {}
    inv = {i["id"]: i for i in inventory.get("inventory") or []}
    order = dependency_order(inventory, library)
    print(f"[layout] place order: {', '.join(order)}", flush=True)
    for oid in order:
        if oid not in inv:
            continue
        spec = library[oid]
        relations = list(inv[oid].get("relations") or [])
        wall = (inv[oid].get("mount") or {}).get("wall")
        if wall and not _wall_from_relations(relations, spec):
            relations = relations + [f"against_wall:{wall}" if spec.get("mount") == "floor"
                                     else f"on_wall:{wall}"]

        if oid in seed and not spec.get("fixed"):
            prev = dict(seed[oid])
            prev["relations"] = relations
            problem = expect(oid, prev, placed, spec, room, relations)
            if not problem:
                store(placed, oid, {k: prev[k] for k in ("x", "y", "z", "yaw_deg")},
                      spec, relations, reason="kept from previous round")
                relations_used[oid] = relations
                history.append({"id": oid, "attempt": 0, "pose": prev, "kept": True})
                print(f"[layout] kept {oid} at ({prev['x']:.2f},{prev['y']:.2f})",
                      flush=True)
                continue
        if spec.get("fixed"):
            placed[oid] = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw_deg": 0.0,
                           "hx": 0.0, "hy": 0.0, "size": spec["size"],
                           "_exempt": True, "_mount": "fixed"}
            continue

        pose, reason, attempt, _ = design_pose(
            oid, spec, inv[oid], relations, placed, room, history=history)
        if pose:
            store(placed, oid, pose, spec, relations, reason)
            relations_used[oid] = relations
            print(f"[layout] placed {oid} at ({pose['x']:.2f},{pose['y']:.2f},"
                  f"{pose['z']:.2f}) yaw={pose['yaw_deg']} attempt={attempt}",
                  flush=True)
        else:
            print(f"[layout] WARNING: {oid} unplaced after {attempt} attempts",
                  flush=True)
    return placed, history, relations_used


def repair_pass(placed: dict, library: dict, room: dict, inv: dict,
                history: list | None = None, sweeps: int = 3) -> bool:
    """Re-check every pose against the finished room and send failures back.

    Placement runs in a fixed order, so an object can be accepted before the
    thing it has to face exists. The sofa went in first -- no TV yet, so the
    `faces` rule had nothing to compare against -- and the TV arrived several
    calls later. No stage ever went back to the sofa, which is exactly why a
    round could end with the seating facing the window while the TV faced it.
    This sweep is that missing edge: full context in, violations out, Designer
    re-invoked for the objects that actually break.
    """
    history = history if history is not None else []
    for sweep in range(1, sweeps + 1):
        bad = []
        for oid in PLACE_ORDER:
            if oid not in placed or oid not in inv:
                continue
            spec = library[oid]
            if spec.get("fixed"):
                continue
            rel = placed[oid].get("_relations") or inv[oid].get("relations") or []
            probs = expect(oid, placed[oid], placed, spec, room, rel)
            if probs:
                bad.append((oid, probs))
        if not bad:
            print(f"[layout] repair sweep {sweep}: full-context audit clean", flush=True)
            return True
        print(f"[layout] repair sweep {sweep}: {len(bad)} object(s) only fail once "
              f"the whole room exists -> {[b[0] for b in bad]}", flush=True)
        changed = False
        for oid, probs in bad:
            spec = library[oid]
            rel = placed[oid].get("_relations") or inv[oid].get("relations") or []
            others = {k: v for k, v in placed.items() if k != oid}
            item = dict(inv[oid])
            item["intent"] = (item.get("intent", "") + " | FIX: "
                              + "; ".join(f"[{p['id']}] {p['msg']}" for p in probs))
            pose, reason, attempt, _ = design_pose(
                oid, spec, item, rel, others, room, attempts=3, history=history)
            if pose:
                store(placed, oid, pose, spec, rel, reason)
                changed = True
                print(f"[layout] repaired {oid} -> ({pose['x']:.2f},{pose['y']:.2f}) "
                      f"yaw={pose['yaw_deg']} after {attempt} attempt(s)", flush=True)
        if not changed:
            print("[layout] repair sweep made no progress; stopping", flush=True)
            return False
    return False


def build_layout(inventory: dict, placed: dict, room: dict, room_plan: dict,
                 relations_map: dict | None = None) -> dict:
    inv = {i["id"]: i for i in inventory.get("inventory") or []}
    relations_map = relations_map or {}
    objects = []
    for oid in PLACE_ORDER:
        if oid not in placed or oid not in inv:
            continue
        p = placed[oid]
        objects.append({
            "id": oid,
            "stage": inv[oid].get("stage"),
            "placement": {k: round(p[k], 4) for k in ("x", "y", "z", "yaw_deg")},
            "asset_item": inv[oid].get("asset_item") or {"dimensions": p["size"]},
            "relations": relations_map.get(oid) or inv[oid].get("relations") or [],
            "intent": inv[oid].get("intent", ""),
            "reason": p.get("reason", ""),
            "mount": inv[oid].get("mount") or {"surface": p.get("_mount")},
        })
    return {"schema": "scenecode.layout.v1", "room": room, "room_plan": room_plan,
            "objects": objects}


def validate_layout(layout: dict, library: dict, room: dict) -> dict:
    """Re-run every constraint against the finished layout.

    This is the post-condition of the whole loop: no downstream stage is allowed
    to relocate anything, so whatever fails here has to be repaired by the agent.
    """
    placed = {}
    for o in layout["objects"]:
        spec = library[o["id"]]
        p = dict(o["placement"])
        p["size"] = spec["size"]
        p["hx"], p["hy"] = half_extents(spec["size"], p["yaw_deg"])
        p["_exempt"] = o["id"] == "area_rug" or spec.get("mount") != "floor"
        p["_wall"] = _wall_from_relations(o.get("relations") or [], spec)
        placed[o["id"]] = p

    issues = []
    for o in layout["objects"]:
        for i in expect(o["id"], placed[o["id"]], placed, library[o["id"]], room,
                        o.get("relations") or []):
            issues.append({"object": o["id"], **i})
        # A relation naming something that is not in the room reads like a
        # constraint and behaves like a comment: `faces:seating_group` was
        # accepted, skipped, and counted as satisfied. Only checkable once every
        # object exists, so it lives here rather than in the per-object call.
        for rel in o.get("relations") or []:
            kind, _, arg = rel.partition(":")
            if kind not in ("faces", "centered_with", "above", "clearance",
                            "in_front_of"):
                continue
            target = arg.partition(":")[0]
            if target and target != o["id"] and target not in placed:
                issues.append({"object": o["id"], "id": "relation.unknown_target",
                               "msg": f"{o['id']} relates to '{target}', which is "
                                      f"not in the room"})
    return {"passed": not issues, "issues": issues, "objects": len(layout["objects"])}


def ask_inventory(room: dict, library_txt: str,
                  complaint: list[str] | None = None) -> dict:
    """Ask the floor-plan stage for the object programme and its relations.

    `complaint` carries lines the room rejected last time. Rewriting the whole
    inventory -- rather than patching single fields -- keeps the answer coherent:
    dropping a clearance can change which wall carries the group.
    """
    prompt = INVENTORY_PROMPT.format(
        room=room_text(room), floor_z=room["interior"]["z"][0],
        library=library_txt, brief=BRIEF)
    if complaint:
        prompt += ("\n\nYour previous inventory cannot be realised in this room:\n"
                   + "\n".join(f"  - {c}" for c in complaint)
                   + "\nRewrite the WHOLE inventory. Keep everything that worked; "
                     "for the lines above, drop or relax the relation that makes "
                     "the layout impossible.")
    return parse_json(call_llm(prompt, max_tokens=6000)) or {}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    room = load_room()
    library = load_assets({o["id"]: o for o in OBJECT_LIBRARY})
    library_txt = "\n".join(
        f"  - {o['id']} | size {o['size']} | stage {o['stage']} | mount {o.get('mount')}"
        + (f" | {o['note']}" if o.get("note") else "")
        for o in OBJECT_LIBRARY)

    print(f"[layout] room measured: {room['interior']['width']:.2f} x "
          f"{room['interior']['depth']:.2f} x {room['interior']['height']:.2f}, "
          f"door x {room['openings']['door']['x']}", flush=True)

    inventory = ask_inventory(room, library_txt)
    if not inventory:
        raise RuntimeError("inventory planning produced no parseable JSON")
    # A relation the room cannot enforce is worse than no relation: it reads as
    # a design decision and behaves like a comment. Send the inventory back to
    # the floor-plan stage with the offending lines quoted.
    bad = unusable_relations(inventory, room)
    if bad:
        print(f"[layout] inventory used {len(bad)} unusable relation(s) -> "
              f"re-asking floor_plan", flush=True)
        retry = ask_inventory(room, library_txt, complaint=bad)
        if retry and len(unusable_relations(retry, room)) < len(bad):
            inventory = retry
    inventory_notes: list[str] = []
    room_plan = inventory.get("room_plan") or {}
    base_intents = {i["id"]: i.get("intent", "") for i in inventory["inventory"]}
    print(f"[layout] inventory={len(inventory['inventory'])} "
          f"media_wall={room_plan.get('media_wall')} "
          f"door={json.dumps(room_plan.get('door'), ensure_ascii=False)}", flush=True)

    best = None
    rounds = 0
    seed: dict = {}
    for rnd in range(1, 4):
        rounds = rnd
        kept_note = f" (warm start from {len(seed)} prior poses)" if seed else ""
        print(f"[layout] round {rnd}: sequential Designer placement{kept_note}",
              flush=True)
        try:
            placed, history, relations_used = place_sequential(
                inventory, library, room, seed=seed)
        except RuntimeError as exc:
            # A transient upstream outage must not throw away the rounds that
            # already succeeded. Keep the best layout and stop climbing.
            print(f"[layout] round {rnd} aborted: {exc}", flush=True)
            if best is None:
                raise
            break
        # The sequential pass validates each object against a partial room; this
        # sweep validates all of them against the finished one and repairs only
        # what the partial pass could not have seen.
        repair_pass(placed, library, room,
                    {i["id"]: i for i in inventory.get("inventory") or []},
                    history=history)
        layout = build_layout(inventory, placed, room, room_plan, relations_used)
        validation = validate_layout(layout, library, room)
        # An object that never got placed is a failure of the layout, not a
        # neutral omission. Round 1 reported "PASS (0 issues)" while the coffee
        # table was missing entirely, so the loop declared success on a room it
        # had not actually furnished.
        missing = [o["id"] for o in inventory["inventory"]
                   if o["id"] not in placed
                   and (library.get(o["id"]) or {}).get("mount") != "fixed"]
        for oid in missing:
            validation["issues"].append(
                {"object": oid, "id": "missing",
                 "msg": f"{oid} is in the inventory but has no pose"})
        validation["passed"] = not validation["issues"]
        validation["missing"] = missing
        print(f"[layout] validator: {'PASS' if validation['passed'] else 'FAIL'} "
              f"({len(validation['issues'])} issues, missing={missing})", flush=True)

        # An object the Designer could not place at all is not a furniture
        # problem: it means the programme asked for something the room cannot
        # hold. Send that back to the stage that wrote the programme instead of
        # letting the round fail quietly with one object missing.
        if missing and not inventory_notes:
            current = {i["id"]: i for i in inventory["inventory"]}
            complaint = [f"{oid}: relations {current.get(oid, {}).get('relations')} "
                         f"are impossible to place in this room" for oid in missing]
            print(f"[layout] {len(missing)} object(s) unplaceable -> re-asking "
                  f"floor_plan to relax the programme", flush=True)
            revised = ask_inventory(room, library_txt, complaint=complaint)
            if revised and revised.get("inventory"):
                new_ids = {i["id"] for i in revised["inventory"]}
                if set(current) <= new_ids or len(new_ids) >= len(current):
                    inventory_notes = complaint
                    inventory = revised
                    base_intents = {i["id"]: i.get("intent", "")
                                    for i in inventory["inventory"]}
                    print("[layout] floor_plan revised; next round uses the new "
                          "programme", flush=True)

        try:
            critic = parse_json(call_llm(CRITIC_PROMPT.format(
                room=room_text(room), brief=BRIEF,
                layout=json.dumps(layout, ensure_ascii=False),
                violations=json.dumps(validation["issues"], ensure_ascii=False),
            ), max_tokens=2000)) or {"score": 0, "verdict": "unavailable", "issues": []}
        except RuntimeError as exc:
            critic = {"score": 0, "verdict": "unavailable", "issues": [],
                      "error": str(exc)}
        score = critic.get("score")
        score = float(score) if isinstance(score, (int, float)) else 0.0
        print(f"[layout] critic: {critic.get('verdict')} score={score} "
              f"issues={len(critic.get('issues') or [])}", flush=True)

        entry = {"layout": layout, "validation": validation, "critic": critic,
                 "score": score, "history": history, "round": rnd,
                 "placed": placed}
        rank = (validation["passed"], -len(validation["issues"]),
                len(placed), score)
        if best is None or rank > best["rank"]:
            entry["rank"] = rank
            best = entry
        # Repair from the best round so far, not from the round that just ran.
        # Round 3 is usually the worst one, and seeding from it would throw away
        # poses that round 1 had already got right.
        seed = best["placed"]
        if validation["passed"] and critic.get("verdict") == "passed" and score >= 8:
            break
        # Feed the objective failures and the critic's corrections into the next
        # round's per-object intents. This is SceneCode's Critic -> Designer edge.
        corrections = json.dumps({
            "violations": [f"{i['object']}: [{i['id']}] {i['msg']}"
                           for i in validation["issues"]],
            "critic_issues": critic.get("issues") or [],
            "suggested_changes": critic.get("suggested_changes") or [],
        }, ensure_ascii=False)
        # Rebuild each intent from its original sentence, not from last round's
        # intent. Appending made the field grow a new CORRECTIONS block per
        # round, and by round 3 the object's brief was buried under stale
        # evidence -- the critic's score fell 4 -> 3 while the room improved.
        for item in inventory["inventory"]:
            item["intent"] = base_intents.get(item["id"], "") + " | CORRECTIONS: " + corrections
        print("[layout] corrections fed back into Designer intents", flush=True)

    if best is None:
        raise RuntimeError("no layout produced")
    result = {
        "schema": "scenecode.layout.v1",
        "brief": BRIEF,
        "room_source": str(ROOM_PATH),
        "room": room,
        "room_plan": room_plan,
        "layout": best["layout"]["layout"] if "layout" in best["layout"] else best["layout"],
        "validation": best["validation"],
        "critic": best["critic"],
        "critic_rounds": rounds,
        "selected_round": best["round"],
        "authority": ("validator_passed is the gate: every rule below is measured "
                      "against the finished room. The critic is advisory; its "
                      "score is not a pass condition."),
        "enforced_relations": sorted(ENFORCED_RELATIONS),
        "placement_history": best["history"],
        "object_library": OBJECT_LIBRARY,
        "contract_sources": [
            "scenecode/prompts/data/floor_plan_agent/designer_agent.yaml",
            "scenecode/prompts/data/furniture_agent/stateful_planner_agent.yaml",
            "scenecode/prompts/data/asset_router/request_analysis_furniture.yaml",
            "scenecode/agent_utils/room.py (SceneObject / PlacementInfo / add_object)",
            "scenecode/experiments/indoor_scene_generation.py (PIPELINE_STAGES)",
        ],
    }
    (OUT / "layout.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print("LAYOUT_RESULT=" + json.dumps({
        "validator_passed": best["validation"]["passed"],
        "violations": len(best["validation"]["issues"]),
        "critic_score": best["critic"].get("score"),
        "critic_verdict": best["critic"].get("verdict"),
        "rounds": rounds, "objects": len(best["layout"]["objects"]),
        "unplaced": [o["id"] for o in OBJECT_LIBRARY
                     if o["id"] not in {x["id"] for x in best["layout"]["objects"]}],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
