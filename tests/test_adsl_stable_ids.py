"""aDSL stable component-id / bidirectional serialization regression tests.

Run with:
    E:/3DUse/aDSL/venv/Scripts/python.exe E:/3DUse/SceneCode_audit/tests/test_adsl_stable_ids.py

These tests live OUTSIDE the aDSL repo on purpose: the adapter work must not
pollute the existing project tree.
"""

from __future__ import annotations

import copy
import json
import sys
import unittest

sys.path.insert(0, r"E:/3DUse/aDSL/adsl-core")

from core import Asset, Cube, asset_from_dict, asset_to_dict  # noqa: E402
from core.primitives import Cylinder  # noqa: E402


def make_chair() -> Asset:
    """Mirror a SceneCode ObjectPlan: named parts, one nested sub-assembly."""
    chair = Asset(label="chair_001", component_id="chair_001")
    chair.attach_part("seat", Cube(scale=(0.50, 0.50, 0.08)))
    chair.attach_part("backrest", Cube(scale=(0.50, 0.08, 0.60)))
    chair.attach_part("armrest_left", Cube(scale=(0.08, 0.45, 0.10)))
    chair.attach_part("armrest_right", Cube(scale=(0.08, 0.45, 0.10)))

    legs = Asset(label="legs_assembly")
    for i in range(4):
        legs.attach_part(f"leg_{i:02d}", Cylinder(radius=0.025, height=0.45))
    chair.attach_part("legs", legs)
    return chair


class StableIdTests(unittest.TestCase):
    def test_part_key_becomes_component_id(self):
        """aDSL adopts SceneCode's PartPlan.name, not a new naming scheme."""
        chair = make_chair()
        self.assertEqual(chair.seat.component_id, "seat")
        self.assertEqual(chair.backrest.component_id, "backrest")
        self.assertEqual(chair.legs.leg_02.component_id, "leg_02")

    def test_explicit_upstream_id_is_not_overwritten(self):
        root = Asset(label="root")
        part = Asset(label="whatever")
        part.component_id = "sofa_backrest"  # id coming from upstream
        root.attach_part("backrest", part)
        self.assertEqual(part.component_id, "sofa_backrest")

    def test_component_ids_cover_nested_subtree(self):
        ids = set(make_chair().component_ids())
        expected = {
            "chair_001", "seat", "backrest", "armrest_left", "armrest_right",
            "legs", "leg_00", "leg_01", "leg_02", "leg_03",
        }
        # 'legs' keeps its own label-derived id only if no key binding happened;
        # attach_part binds it to the key, so check the SceneCode keys instead.
        self.assertTrue(expected - {"legs"} <= ids, ids)
        self.assertIn("legs", ids)

    def test_find_component_and_path(self):
        chair = make_chair()
        self.assertIs(chair.find_component("backrest"), chair.backrest)
        self.assertEqual(
            chair.component_path("leg_02"),
            ("chair_001", "legs", "leg_02"),
        )
        self.assertIsNone(chair.find_component("does_not_exist"))

    def test_roundtrip_preserves_ids_and_geometry(self):
        chair = make_chair()
        before = set(chair.component_ids())
        payload = json.loads(json.dumps(chair.to_dict(), default=lambda x: x.tolist()
                                       if hasattr(x, "tolist") else str(x)))
        restored = Asset.from_dict(payload)
        self.assertEqual(set(restored.component_ids()), before)
        self.assertEqual(len(list(restored.iter_primitives())),
                         len(list(chair.iter_primitives())))
        self.assertEqual(restored.find_component("leg_02").component_id, "leg_02")

    def test_core_asset_from_dict_matches_asset_classmethod(self):
        chair = make_chair()
        d = chair.to_dict()
        self.assertEqual(set(asset_from_dict(d).component_ids()),
                         set(Asset.from_dict(d).component_ids()))
        self.assertEqual(set(asset_to_dict(chair).keys()),
                         {"label", "component_id", "primitives", "children", "joints"})

    def test_copy_preserves_ids(self):
        chair = make_chair()
        self.assertEqual(set(chair.copy().component_ids()), set(chair.component_ids()))

    def test_legacy_json_without_component_id_still_restores(self):
        """Existing outputs/*.json snapshots must remain loadable."""
        chair = make_chair()
        legacy = chair.to_dict()
        stack = [legacy]
        while stack:
            node = stack.pop()
            node.pop("component_id", None)
            stack.extend(node.get("children", {}).values())
            stack.extend(e["child"] for e in node.get("joints", {}).values())
        restored = Asset.from_dict(legacy)
        self.assertIn("seat", restored.component_ids())
        self.assertIn("leg_02", restored.component_ids())
        self.assertEqual(restored.seat.component_id, "seat")

    def test_duplicate_ids_are_rejected(self):
        root = Asset(label="root")
        a, b = Asset(label="A"), Asset(label="B")
        a.component_id = b.component_id = "dup"
        root.attach_part("a", a)
        root.attach_part("b", b)
        with self.assertRaises(ValueError):
            root.component_ids()

    def test_existing_positional_constructor_unchanged(self):
        """kw_only field appended last must not shift existing parameters."""
        legacy = Asset("LegacyShape", (1.0, 0.0, 0.0))
        self.assertEqual(legacy._link_color, (1.0, 0.0, 0.0))
        self.assertIsNone(legacy.component_id)
        self.assertEqual(legacy.effective_component_id, "LegacyShape")


if __name__ == "__main__":
    unittest.main(verbosity=2)
