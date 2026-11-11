"""P6 — one-way ingestion: SceneCode ObjectPlan JSON -> aDSL AST skeleton.

aDSL ADAPTS TO SceneCode here: component ids are taken verbatim from
PartPlan.name / sub-part names, and NO upstream file is modified.

Scope note (honest): this produces the STRUCTURAL aDSL tree (stable ids,
component hierarchy, upstream manifest). Geometry is NOT fabricated from
PartShape.base_shape, because SceneCode's real geometry lives in the
part-wise `code/<part>.py` programs. Those will be attached by P1's
ProgramComponent. Silently turning `custom`/`cone`/`torus` into a Cube would
violate the "do not degrade detailed furniture into a few primitives" rule.

Usage:
    python ingest_objectplan.py <objectplan.json> <output_a2.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, r"E:/3DUse/aDSL/adsl-core")

from core import Asset  # noqa: E402
from core.serialize import asset_from_dict, asset_to_dict  # noqa: E402

KNOWN_SHAPES = {"cube", "sphere", "cylinder"}


def ingest(plan: dict) -> tuple[Asset, dict]:
    name = plan.get("name") or plan.get("category") or "scenecode_object"
    root = Asset(label=name, component_id=name)
    manifest = {
        "source": "SceneCode ObjectPlan (unmodified upstream artifact)",
        "object_name": name,
        "category": plan.get("category"),
        "style": plan.get("style"),
        "total_dimensions": plan.get("total_dimensions"),
        "material_hints": plan.get("material_hints", {}),
        "parts": [],
    }
    unmapped: list[str] = []

    for part in plan.get("parts") or []:
        cid = str(part.get("name") or "unnamed_part")
        component = Asset(label=cid, component_id=cid)
        shape = part.get("shape") or {}
        base_shape = str(shape.get("base_shape", "custom")).lower()
        if base_shape not in KNOWN_SHAPES:
            unmapped.append(f"{cid}:{base_shape}")

        manifest["parts"].append({
            "component_id": cid,
            "part_type": part.get("part_type"),
            "upstream_base_shape": base_shape,
            "upstream_dimensions": shape.get("dimensions"),
            "upstream_modifiers": shape.get("modifiers", []),
            "upstream_position": part.get("position"),
            "upstream_rotation": part.get("rotation"),
            "is_symmetric": part.get("is_symmetric", False),
            "symmetric_axis": part.get("symmetric_axis"),
            "is_movable": part.get("is_movable", False),
            "material": part.get("material", {}),
            "geometry_source": "scenecode_program_pending_P1",
        })
        root.attach_part(cid, component)

        # SceneCode sub_parts are one level only (object_plan.py:212).
        for sub in part.get("sub_parts") or []:
            sid = str(sub.get("name") or "unnamed_sub_part")
            component.attach_part(sid, Asset(label=sid, component_id=sid))

    report = {
        "part_count": len(manifest["parts"]),
        "component_ids": sorted(root.component_ids().keys()),
        "unmapped_shapes": unmapped,
        "sub_parts_ingested": sum(len(p.get("sub_parts") or []) for p in plan.get("parts") or []),
        "note": "Geometric primitives intentionally NOT synthesized from PartShape; "
                "P1 ProgramComponent will attach the real part-wise programs.",
    }
    return root, {"manifest": manifest, "report": report}


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: ingest_objectplan.py <objectplan.json> <output_a2.json>")
        return 2
    src, dst = Path(args[0]), Path(args[1])
    plan = json.loads(src.read_text(encoding="utf-8"))
    root, bundle = ingest(plan)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(asset_to_dict(root), ensure_ascii=False, indent=2,
                              default=lambda x: x.tolist() if hasattr(x, "tolist") else str(x)),
                   encoding="utf-8")
    sidecar = dst.with_suffix(".upstream.json")
    sidecar.write_text(json.dumps(bundle["manifest"], ensure_ascii=False, indent=2),
                       encoding="utf-8")

    # Round-trip verification: stable ids must survive serialization.
    restored = asset_from_dict(json.loads(dst.read_text(encoding="utf-8")))
    bundle["report"]["roundtrip_ids_preserved"] = (
        sorted(restored.component_ids().keys()) == bundle["report"]["component_ids"]
    )
    report_path = dst.with_suffix(".ingest_report.json")
    report_path.write_text(json.dumps(bundle["report"], ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps(bundle["report"], ensure_ascii=False))
    print("Wrote:", dst, sidecar, report_path)
    return 0 if bundle["report"]["roundtrip_ids_preserved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
