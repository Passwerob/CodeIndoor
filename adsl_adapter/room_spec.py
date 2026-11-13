"""Measure the real shell and publish it as the single room fact source.

Every stage used to carry its own idea of the room: PLACE_PROMPT said the door
was at x = 0, check_placement kept the entry corridor at x in [-0.65, 0.65], and
the shell on disk had the opening at x 1.20..2.40 after it was relocated. The
Designer was therefore planning inside a room that no longer existed, and the
difference was papered over downstream by hand-written pose rules.

This module removes the duplication: it reads the assembled shell, derives the
inner volume and every opening from the meshes, and writes `room_measured.json`.
Planners, validators and the shell builder all read that file. Nobody retypes a
room dimension again.

    blender -b --python adsl_adapter/room_spec.py
"""
from __future__ import annotations

import json
from pathlib import Path

import bpy

ROOT = Path(r"E:/3DUse/New/CodeIndoor")
BLEND = ROOT / "runtime/outputs/living_room/assets/living_room_assets.blend"
OUT = ROOT / "runtime/outputs/scene_layout/room_measured.json"


def _bounds(obj) -> tuple[list[float], list[float]]:
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    return [round(v, 4) for v in lo], [round(v, 4) for v in hi]


def measure() -> dict:
    bpy.ops.wm.open_mainfile(filepath=str(BLEND))
    parts: dict[str, tuple[list[float], list[float]]] = {}
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.get("adsl_object_id") != "room_shell":
            continue
        parts[str(obj.get("adsl_part", obj.name))] = _bounds(obj)

    # The room-side face of each wall is the face that bounds the interior: the
    # west wall's inner face is its high x, the east wall's is its low x, and the
    # same inversion holds for front (high y) and back (low y). Deriving it from
    # the box rather than from a wall thickness constant keeps this correct if
    # the shell is ever re-cut.
    x_lo = parts["wall_west"][1][0]
    x_hi = parts["wall_east"][0][0]
    y_lo = parts["wall_south_left"][1][1]
    y_hi = parts["wall_north_left"][0][1]
    z_lo = parts["floor"][1][2]
    z_hi = parts["ceiling"][0][2]

    door_lo, door_hi = parts["door_jamb_left"], parts["door_jamb_right"]
    lintel = parts["door_lintel"]
    door = {
        "wall": "front",
        # Rough opening: jamb outer faces, i.e. the hole cut in the wall.
        "x": [round(door_lo[0][0], 4), round(door_hi[1][0], 4)],
        # Clear opening: between the jamb inner faces. A leaf has to fit THIS,
        # not the rough hole -- 8 cm of jamb on each side is not negotiable.
        "clear_x": [round(door_lo[1][0], 4), round(door_hi[0][0], 4)],
        "height": round(lintel[0][2], 4),
        "plane_y": round(lintel[1][1], 4),
    }
    door["width"] = round(door["x"][1] - door["x"][0], 4)
    door["clear_width"] = round(door["clear_x"][1] - door["clear_x"][0], 4)
    door["center_x"] = round((door["x"][0] + door["x"][1]) / 2, 4)

    wl, wr = parts["window_frame_left"], parts["window_frame_right"]
    wb, wt = parts["window_frame_bottom"], parts["window_frame_top"]
    window = {
        "wall": "back",
        "x": [round(wl[0][0], 4), round(wr[1][0], 4)],
        "z": [round(wb[0][2], 4), round(wt[1][2], 4)],
    }
    window["width"] = round(window["x"][1] - window["x"][0], 4)
    window["height"] = round(window["z"][1] - window["z"][0], 4)
    window["center_x"] = round((window["x"][0] + window["x"][1]) / 2, 4)

    spec = {
        "schema": "scenecode.room.v1",
        "source": str(BLEND),
        "interior": {
            "width": round(x_hi - x_lo, 4),
            "depth": round(y_hi - y_lo, 4),
            "height": round(z_hi - z_lo, 4),
            "x": [round(x_lo, 4), round(x_hi, 4)],
            "y": [round(y_lo, 4), round(y_hi, 4)],
            "z": [round(z_lo, 4), round(z_hi, 4)],
        },
        # A wall's "plane" is its room-side coordinate; its "normal" points into
        # the room. Wall-mounted yaw is therefore derived, never invented.
        "walls": {
            "front": {"plane": round(y_lo, 4), "normal": [0.0, 1.0], "span": [round(x_lo, 4), round(x_hi, 4)]},
            "back": {"plane": round(y_hi, 4), "normal": [0.0, -1.0], "span": [round(x_lo, 4), round(x_hi, 4)]},
            "west": {"plane": round(x_lo, 4), "normal": [1.0, 0.0], "span": [round(y_lo, 4), round(y_hi, 4)]},
            "east": {"plane": round(x_hi, 4), "normal": [-1.0, 0.0], "span": [round(y_lo, 4), round(y_hi, 4)]},
        },
        "openings": {"door": door, "window": window},
        "skirting": {
            "height": round(parts["skirting_west"][1][2] - parts["skirting_west"][0][2], 4),
            "thickness": round(parts["skirting_west"][1][0] - parts["skirting_west"][0][0], 4),
        },
        "wall_thickness": round(parts["wall_west"][1][0] - parts["wall_west"][0][0], 4),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print("ROOM=" + json.dumps({
        "interior": [spec["interior"]["width"], spec["interior"]["depth"], spec["interior"]["height"]],
        "door_x": door["x"], "door_h": door["height"], "door_plane_y": door["plane_y"],
        "window_x": window["x"], "window_z": window["z"],
        "out": str(OUT),
    }, ensure_ascii=False))
    return spec


if __name__ == "__main__":
    measure()
