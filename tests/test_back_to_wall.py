"""Regression tests for the reversed-bookshelf guard.

These pin the behaviour that prevents a backed floor object (bookshelf, cabinet)
from being placed 180 deg out -- solid back facing the room. Three things must
hold:

  1. resolve_pose() derives a back-to-wall yaw for `against_wall` floor objects,
     overriding whatever the planner proposed (bookshelf vs east wall -> -90).
  2. expect() flags a reversed orientation as `wall.facing`, and stays silent
     once the yaw is correct.
  3. layout_finalize.derive_against_wall_yaw() repairs a stale layout seed that
     still carries the reversed yaw, while leaving already-correct objects alone.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adsl_adapter"))

from scene_layout import resolve_pose, expect  # noqa: E402
from layout_finalize import derive_against_wall_yaw  # noqa: E402

ROOM = {
    "walls": {
        "east": {"normal": [-1.0, 0.0], "plane": 3.0, "span": [0.0, 1.5]},
        "front": {"normal": [0.0, 1.0], "plane": -2.5, "span": [-3.0, 3.0]},
        "west": {"normal": [1.0, 0.0], "plane": -3.0, "span": [-2.5, 2.5]},
    },
    "interior": {"x": [-3.0, 3.0], "y": [-2.5, 2.5], "z": [0.0, 2.9]},
    "openings": {
        "door": {"wall": "front", "x": [1.2, 2.4], "width": 1.2, "z": [0, 2.1]},
        "window": {"wall": "west", "x": [-1, 1], "width": 2, "z": [0.9, 2.1]},
    },
    "wall_thickness": 0.15,
}

SPECS = {
    "bookshelf": {"mount": "floor", "size": [0.35, 2.0, 2.0], "local_z": [0.0, 2.0]},
    "tv_cabinet": {"mount": "floor", "size": [0.4, 1.8, 0.55], "local_z": [0.0, 0.55]},
}


def test_resolve_pose_back_to_wall():
    raw = {"x": 2.8, "y": 0.75, "yaw_deg": 90.0, "relations": ["against_wall:east"]}
    pose = resolve_pose("bookshelf", raw, {}, SPECS["bookshelf"], ROOM)
    assert abs(pose["yaw_deg"] - (-90)) < 1e-6, pose
    raw2 = {"x": 0.0, "y": -2.3, "yaw_deg": 180.0, "relations": ["against_wall:front"]}
    pose2 = resolve_pose("tv_cabinet", raw2, {}, SPECS["tv_cabinet"], ROOM)
    assert abs(pose2["yaw_deg"] - 180) < 1e-6, pose2


def test_expect_catches_reversed():
    rev = {"x": 2.8, "y": 0.75, "z": 0.0, "yaw_deg": 90.0, "size": SPECS["bookshelf"]["size"]}
    issues = expect("bookshelf", rev, {"bookshelf": dict(rev)},
                    SPECS["bookshelf"], ROOM, ["against_wall:east"])
    assert any(i["id"] == "wall.facing" for i in issues), issues
    ok = {"x": 2.8, "y": 0.75, "z": 0.0, "yaw_deg": -90.0, "size": SPECS["bookshelf"]["size"]}
    issues2 = expect("bookshelf", ok, {"bookshelf": dict(ok)},
                     SPECS["bookshelf"], ROOM, ["against_wall:east"])
    assert not any(i["id"] == "wall.facing" for i in issues2), issues2


def test_derive_fixes_reversed_seed():
    objects = [
        {"id": "bookshelf", "relations": ["against_wall:east"],
         "placement": {"x": 2.8, "y": 0.75, "yaw_deg": 90.0}},
        {"id": "tv_cabinet", "relations": ["against_wall:front"],
         "placement": {"x": 0.0, "y": -2.3, "yaw_deg": 180.0}},
    ]
    changed = derive_against_wall_yaw(objects, ROOM)
    assert "bookshelf" in changed and "tv_cabinet" not in changed, changed
    assert abs(objects[0]["placement"]["yaw_deg"] - (-90)) < 1e-6, objects[0]
    assert abs(objects[1]["placement"]["yaw_deg"] - 180) < 1e-6, objects[1]


if __name__ == "__main__":
    test_resolve_pose_back_to_wall()
    test_expect_catches_reversed()
    test_derive_fixes_reversed_seed()
    print("ALL BACK-TO-WALL TESTS PASSED")
