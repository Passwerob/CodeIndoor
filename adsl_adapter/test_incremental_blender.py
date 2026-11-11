"""Run INSIDE Blender 5.2.2 to verify P3 localized editing end-to-end.

    E:/Blender/blender.exe -b --python E:/3DUse/SceneCode_audit/adsl_adapter/test_incremental_blender.py

Test A: lower the backrest -> rebuild ONLY that component.
Test B: relation edit -> move a component WITHOUT rebuilding its geometry.
Test C: widen both armrests symmetrically -> rebuild exactly two components.

Verification uses per-build UUID tokens: any untouched geometry that was
silently regenerated is detected. This is a mechanism test on a chair, not a
full living-room PHASE 5 reproduction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, r"E:/3DUse/aDSL/adsl-core")
sys.path.insert(0, r"E:/3DUse/SceneCode_audit/adsl_adapter")

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

from core import Asset, Cube, Cylinder  # noqa: E402
from incremental_executor import (  # noqa: E402
    IncrementalBlenderExecutor,
    unaffected_violations,
)

OUT = Path(r"E:/3DUse/SceneCode_audit/runtime/outputs/incremental_test")


def make_chair(back_h: float = 0.60, arm_w: float = 0.08) -> Asset:
    root = Asset(label="chair_001", component_id="chair_001")
    root.attach_part("seat", Cube(scale=(0.50, 0.50, 0.08), center=(0, 0, 0.42)))
    root.attach_part("backrest", Cube(scale=(0.50, 0.08, back_h),
                                     center=(0, 0.22, 0.42 + back_h / 2)))
    root.attach_part("armrest_left", Cube(scale=(arm_w, 0.45, 0.10), center=(-0.24, 0, 0.58)))
    root.attach_part("armrest_right", Cube(scale=(arm_w, 0.45, 0.10), center=(0.24, 0, 0.58)))
    legs = Asset(label="legs")
    for i, (x, y) in enumerate([(-0.20, -0.20), (0.20, -0.20),
                                (-0.20, 0.20), (0.20, 0.20)]):
        legs.attach_part(f"leg_{i:02d}", Cylinder(radius=0.025, height=0.42,
                                                  center=(x, y, 0.21)))
    root.attach_part("legs", legs)
    return root


def tokens(snap: dict, cid: str) -> list[str]:
    return [r["token"] for r in snap.values() if r["cid"] == cid]


def render_verification() -> str | None:
    try:
        bpy.ops.object.camera_add(location=(1.5, -1.5, 1.15))
        cam = bpy.context.object
        cam.rotation_euler = (Vector((0, 0, 0.45)) - cam.location).to_track_quat("-Z", "Y").to_euler()
        bpy.context.scene.camera = cam
        scene = bpy.context.scene
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.render.resolution_x = 640
        scene.render.resolution_y = 480
        scene.render.image_settings.file_format = "PNG"
        path = OUT / "chair_after_edits.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        return str(path)
    except Exception as exc:  # noqa: BLE001
        return "render_failed: " + type(exc).__name__ + ": " + str(exc)[:100]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ex = IncrementalBlenderExecutor()
    scene_dict = make_chair().to_dict()
    ids0 = ex.build_initial(scene_dict)
    snap0 = ex.snapshot()

    # ---- Test A: component-level parameter edit ----
    new_backrest = Cube(scale=(0.50, 0.08, 0.55), center=(0, 0.22, 0.42 + 0.55 / 2))
    edit_a = ex.apply_component_edit("backrest", new_backrest)
    snap_a = ex.snapshot()
    violations_a = unaffected_violations(snap0, snap_a, {"backrest"})
    a_rebuilt = set(tokens(snap0, "backrest")) != set(tokens(snap_a, "backrest"))

    # ---- Test B: relation-level edit (move, no geometry rebuild) ----
    moved_b = ex.apply_relation_edit("seat", (0.05, 0.0, 0.0))
    snap_b = ex.snapshot()
    violations_b = unaffected_violations(snap_a, snap_b, {"seat"})
    b_token_kept = tokens(snap_a, "seat") == tokens(snap_b, "seat")
    b_moved = any(
        snap_b.get(n, {}).get("matrix") != rec["matrix"]
        for n, rec in snap_a.items() if rec["cid"] == "seat"
    )

    # ---- Test C: symmetric program-level edit (two dirty components) ----
    for cid, x in (("armrest_left", -0.24), ("armrest_right", 0.24)):
        ex.apply_component_edit(cid, Cube(scale=(0.11, 0.45, 0.10), center=(x, 0, 0.58)))
    snap_c = ex.snapshot()
    dirty_c = {"armrest_left", "armrest_right"}
    violations_c = unaffected_violations(snap_b, snap_c, dirty_c)
    c_rebuilt = all(
        set(tokens(snap_b, cid)) != set(tokens(snap_c, cid)) for cid in dirty_c
    )

    ids_final = ex.component_ids()
    blend_path = OUT / "chair_incremental.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    render_path = render_verification()

    report = {
        "blender": bpy.app.version_string,
        "component_ids_initial": ids0,
        "component_ids_final": ids_final,
        "ids_preserved": ids0 == ids_final,
        "object_count_initial": len(snap0),
        "object_count_final": len(snap_c),
        "test_a": {"edit": edit_a, "unaffected_violations": violations_a,
                   "target_was_rebuilt": a_rebuilt,
                   "pass": not violations_a and a_rebuilt},
        "test_b": {"moved_objects": moved_b, "unaffected_violations": violations_b,
                   "target_token_kept": b_token_kept, "target_matrix_changed": b_moved,
                   "pass": not violations_b and b_token_kept and b_moved},
        "test_c": {"dirty_components": sorted(dirty_c),
                   "unaffected_violations": violations_c,
                   "both_targets_rebuilt": c_rebuilt,
                   "pass": not violations_c and c_rebuilt},
        "blend_path": str(blend_path),
        "render_path": render_path,
        "build_log": ex.build_log,
        "caveat": "Mechanism verification on a single chair. Full PHASE 5 living-room "
                  "tests still require SceneCode ingestion + the aDSL relation solver.",
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print("ADSL_INCREMENTAL_RESULT=" + json.dumps({
        k: report[k] for k in ("blender", "ids_preserved", "test_a", "test_b", "test_c",
                               "object_count_initial", "object_count_final")
    }, ensure_ascii=False))
    return 0 if all(report[k]["pass"] for k in ("test_a", "test_b", "test_c")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
