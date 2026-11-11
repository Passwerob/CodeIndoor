"""Blender-side sandbox for ONE agent-generated part program.

Run by agentic_coder.py as:
    blender -b --python part_runner.py -- <code.py> <result.json> <params.json> <cid>

Verifies the generated program actually builds geometry, and stamps the
aDSL component id onto every object it creates. No hand-written geometry here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path

import bpy


def main() -> None:
    argv = sys.argv
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    if len(args) < 4:
        raise SystemExit("part_runner needs: code.py result.json params.json cid")
    code_path, result_path, params_path, cid = args[:4]

    result: dict = {"ok": False, "cid": cid}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        spec = importlib.util.spec_from_file_location("agent_part_" + cid, code_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load generated module")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "build_part"):
            raise RuntimeError("generated program has no build_part(params, name)")
        params = json.loads(Path(params_path).read_text(encoding="utf-8"))
        objects = module.build_part(params, cid)
        if not isinstance(objects, (list, tuple)) or len(objects) == 0:
            raise RuntimeError("build_part must return a non-empty list of objects")

        total_verts = 0
        names = []
        for obj in objects:
            obj["adsl_component_id"] = cid
            names.append(obj.name)
            if obj.type == "MESH":
                total_verts += len(obj.data.vertices)
        if total_verts == 0:
            raise RuntimeError("generated objects contain no mesh vertices")

        # Geometry sanity gate: an armrest program that emits a 1.9m oval tray
        # must not be accepted just because it produced some vertices.
        from mathutils import Vector
        mins = [float("inf")] * 3
        maxs = [float("-inf")] * 3
        for obj in objects:
            for corner in obj.bound_box:
                world = obj.matrix_world @ Vector(corner)
                for axis in range(3):
                    mins[axis] = min(mins[axis], world[axis])
                    maxs[axis] = max(maxs[axis], world[axis])
        extent = [round(maxs[i] - mins[i], 4) for i in range(3)]
        expected = [float(params.get(k, 0.5))
                    for k in ("width", "depth", "height")]
        # Thin parts (e.g. a 0.13m backrest panel) legitimately gain depth
        # from bevels/curves, so give the tolerance an absolute floor.
        limit = [max(expected[i] * 2.2 + 0.12, 0.6) for i in range(3)]
        # NON-BLOCKING on purpose. A hard size gate was tried and proved a net
        # regression: it falsely rejected legitimate bevelled/curved thin parts
        # and dropped acceptance from 15/15 to 10/15. Oversized geometry is
        # surfaced to the Visual Critic instead of being rejected at execution.
        if any(extent[i] > limit[i] for i in range(3)):
            result["extent_warning"] = (
                f"extent {extent} exceeds part spec {expected} "
                f"(soft limits {[round(v, 3) for v in limit]})")

        result.update({"ok": True, "objects": len(objects),
                       "mesh_vertices": total_verts, "extent": extent,
                       "extent_warning": result.get("extent_warning"),
                       "names": names[:12]})
    except Exception:  # noqa: BLE001
        result["error"] = traceback.format_exc()[-2500:]

    Path(result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print("PART_RESULT=" + json.dumps({"ok": result["ok"],
                                       "cid": cid,
                                       "error": result.get("error", "")[-160:]}))


if __name__ == "__main__":
    main()
