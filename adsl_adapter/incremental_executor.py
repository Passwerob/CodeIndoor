"""P3 — incremental Blender executor for aDSL component trees.

Runs INSIDE Blender (bpy). Reuses the existing aDSL core:
  * core.serialize.asset_from_dict   (added in this work)
  * core.export.export_glb._build_shape

Unlike the stock executor (`clear_scene=True` + full `runpy.run_path`), this
one builds each component independently and can rebuild only dirty components.

The "build token" is a per-build UUID stamped on every object. A component that
was NOT rebuilt keeps its token, which gives an honest, verifiable answer to
"did non-target geometry get regenerated?" — stronger than comparing names.
"""

from __future__ import annotations

import importlib
import sys
import uuid

ADSL_CORE = r"E:/3DUse/aDSL/adsl-core"
if ADSL_CORE not in sys.path:
    sys.path.insert(0, ADSL_CORE)

import bpy  # noqa: E402

from core.serialize import asset_from_dict  # noqa: E402

geometry = importlib.import_module("core.export.export_glb")


def _prop(obj, key):
    try:
        return obj[key]
    except (KeyError, TypeError):
        return None


class IncrementalBlenderExecutor:
    def __init__(self, build_fn=None) -> None:
        self.components: dict[str, list[str]] = {}
        self.build_log: list[dict] = []
        # Optional P1 program builder. When provided, specs are ProgramComponent
        # objects instead of plain Assets, and geometry comes from part programs.
        self.build_fn = build_fn

    def _reset(self) -> None:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.components = {}
        self.build_log = []

    def build_initial(self, scene_dict: dict) -> list[str]:
        self._reset()
        root = asset_from_dict(scene_dict)
        for cid, child in root._children.items():
            self._build_component(cid, child)
        bpy.context.view_layer.update()
        self.build_log.append({"op": "initial_build", "components": self.component_ids()})
        return self.component_ids()

    def build_initial_from_specs(self, specs: dict) -> list[str]:
        """P1 path: build from structured ProgramComponents, not raw Assets."""
        self._reset()
        for cid, spec in specs.items():
            self._build_component(cid, spec)
        bpy.context.view_layer.update()
        self.build_log.append({"op": "initial_program_build", "components": self.component_ids()})
        return self.component_ids()

    def _build_component(self, cid: str, asset) -> list[str]:
        if self.build_fn is not None:
            objs = self.build_fn(cid, asset)
        else:
            objs = geometry._build_shape(asset, node_name=cid, path=cid)
        token = uuid.uuid4().hex
        roots = set()
        for obj in objs:
            obj["adsl_component_id"] = cid
            obj["adsl_build_token"] = token
            node = obj
            while node.parent is not None:
                node = node.parent
            roots.add(node)
        for root in roots:
            root["adsl_component_id"] = cid
            root["adsl_build_token"] = token
            root["adsl_component_root"] = cid
        names = sorted({o.name for o in objs} | {r.name for r in roots})
        self.components[cid] = names
        return names

    def _remove_component(self, cid: str) -> int:
        victims = []
        for obj in list(bpy.data.objects):
            node = obj
            while node is not None:
                if _prop(node, "adsl_component_root") == cid:
                    victims.append(obj)
                    break
                node = node.parent
        for obj in victims:
            bpy.data.objects.remove(obj, do_unlink=True)
        return len(victims)

    def apply_component_edit(self, cid: str, new_asset) -> dict:
        """Rebuild ONE component (Test A / Test C)."""
        removed = self._remove_component(cid)
        rebuilt = self._build_component(cid, new_asset)
        bpy.context.view_layer.update()
        entry = {"op": "component_edit", "cid": cid,
                 "removed_objects": removed, "rebuilt_objects": len(rebuilt)}
        self.build_log.append(entry)
        return entry

    def apply_relation_edit(self, cid: str, delta=(0.0, 0.0, 0.0)) -> list[str]:
        """Move an existing component WITHOUT rebuilding geometry (Test B)."""
        moved = []
        for obj in list(bpy.data.objects):
            node = obj
            while node is not None:
                if _prop(node, "adsl_component_root") == cid:
                    obj.location.x += delta[0]
                    obj.location.y += delta[1]
                    obj.location.z += delta[2]
                    moved.append(obj.name)
                    break
                node = node.parent
        bpy.context.view_layer.update()
        self.build_log.append({"op": "relation_edit", "cid": cid,
                               "delta": list(delta), "moved": moved})
        return moved

    def snapshot(self) -> dict:
        out = {}
        for obj in bpy.data.objects:
            out[obj.name] = {
                "cid": _prop(obj, "adsl_component_id"),
                "token": _prop(obj, "adsl_build_token"),
                "matrix": [round(float(v), 6) for row in obj.matrix_world for v in row],
                "verts": len(obj.data.vertices) if obj.type == "MESH" else 0,
            }
        return out

    def component_ids(self) -> list[str]:
        return sorted(self.components.keys())


def unaffected_violations(before: dict, after: dict, dirty_cids: set[str]) -> list[str]:
    """Every object not belonging to a dirty component must keep its build token."""
    problems = []
    for name, rec in before.items():
        if rec["cid"] in dirty_cids:
            continue
        new = after.get(name)
        if new is None:
            problems.append(f"{name}: missing after edit")
        elif new["token"] != rec["token"]:
            problems.append(f"{name}: build token changed (geometry was regenerated)")
    return problems
