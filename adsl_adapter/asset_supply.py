"""Single-object asset supply for the SceneCode -> aDSL scene pipeline.

Why this module exists
----------------------
The first living-room assembly borrowed geometry from
FineScene/deliveries/reception_lounge_adsl_20261104/build_reception_lounge.py.
That script is an *aDSL editability fixture*: every part is one named primitive
so attach_part / local-edit tests have something to bite on. It was never a
quality target, and using it threw away both sources of FineScene's real look:

  1. the 19 CC0 scanned assets (Poly Haven, 2k PBR) in assets/external/, driven
     by realistic_refiner.py::load_asset / fitted_copy / surface;
  2. the aDSL per-object generative pipeline (agent -> part program -> aDSL AST
     -> native Blender refinement), of which outputs/medieval_armchair_20261017
     is the reference result (200 primitives -> 156 meshes + 10 curves).

This module is the resolver: for each object the SceneCode layout asks for, it
decides where the geometry comes from, highest fidelity first.

  retrieved  a real scanned/photogrammetry asset, fitted to the AssetItem
             envelope. Always preferred: real silhouettes and real PBR.
  generated  no suitable asset exists -> the aDSL agent pipeline must author it
             (never hand-written by this file).
  procedural  flat goods whose value is entirely in the surface (rug, curtain):
             a profile + the fabric PBR set.

The table is explicit on purpose. Every entry records *why*, so a bad match is
auditable instead of hidden inside a similarity score.

Usage
-----
    python adsl_adapter/asset_supply.py            # writes supply_plan.json
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"E:/3DUse/SceneCode_audit")
ASSET_ROOT = Path(r"E:/3DUse/assets/external/realistic_study_20261019")
LAYOUT_DIR = ROOT / "runtime/outputs/scene_layout"
# The finalized layout is the only source: it already contains every pose the
# agent decided (plus the door leaf, which is derived from the opening). Supply
# resolves geometry, it does not adjust where anything stands.
LAYOUT_PATH = LAYOUT_DIR / "layout_finalized.json"
OUT_PATH = LAYOUT_DIR / "supply_plan.json"

# Measured from blender_asset_audit.json (metres, asset-native bbox).
ASSET_INDEX = {
    "ArmChair_01": {"meshes": 1, "size": [0.85, 0.77, 1.07],
                    "file": "ArmChair_01/ArmChair_01_2k.gltf"},
    "GreenChair_01": {"meshes": 1, "size": [0.67, 0.66, 1.06],
                      "file": "GreenChair_01/GreenChair_01_2k.gltf"},
    "mid_century_lounge_chair": {"meshes": 1, "size": [1.01, 1.19, 1.17],
                                 "file": "mid_century_lounge_chair/mid_century_lounge_chair_2k.gltf"},
    "painted_wooden_chair_02": {"meshes": 1, "size": [0.64, 0.66, 1.26],
                                "file": "painted_wooden_chair_02/painted_wooden_chair_02_2k.gltf"},
    "WoodenChair_01": {"meshes": 1, "size": [0.69, 0.66, 2.27],
                       "file": "WoodenChair_01/WoodenChair_01_2k.gltf"},
    "WoodenTable_01": {"meshes": 1, "size": [1.80, 0.66, 0.55],
                       "file": "WoodenTable_01/WoodenTable_01_2k.gltf"},
    "side_table_tall_01": {"meshes": 1, "size": [0.38, 0.38, 0.76],
                           "file": "side_table_tall_01/side_table_tall_01_2k.gltf"},
    "potted_plant_02": {"meshes": 3, "size": [0.70, 0.66, 0.84],
                        "file": "potted_plant_02/potted_plant_02_2k.gltf"},
    "fancy_picture_frame_01": {"meshes": 2, "size": [0.60, 0.02, 0.46],
                               "file": "fancy_picture_frame_01/fancy_picture_frame_01_2k.gltf"},
    "decorative_book_set_01": {"meshes": 87, "size": [2.51, 0.32, 0.29],
                               "file": "decorative_book_set_01/decorative_book_set_01_2k.blend"},
    "vintage_oil_lamp": {"meshes": 3, "size": [0.22, 0.22, 0.80],
                         "file": "vintage_oil_lamp/vintage_oil_lamp_2k.gltf"},
    "desk_lamp_arm_01": {"meshes": 1, "size": [0.20, 0.61, 0.89],
                         "file": "desk_lamp_arm_01/desk_lamp_arm_01_2k.gltf"},
}

# Explicit, auditable mapping. `fit` says how fitted_copy (or the equivalent
# import transform) should scale the asset onto the AssetItem envelope:
#   unit  scale to the requested width/depth/height (fitted_copy semantics)
#   thin  keep the asset's own ratio on x/z, pinch depth only (wall art)
#   none  keep native size, position only (never used for furniture)
SUPPLY_RULES = {
    "coffee_table": {
        "mode": "retrieved", "asset": "WoodenTable_01", "fit": "unit",
        "why": "Only real timber table in the library; fitted_copy from "
               "1.80x0.66x0.55 down to the 1.20x0.60x0.42 coffee-table envelope.",
    },
    "tv_cabinet": {
        "mode": "generated",
        "brief": "Low walnut media console, 1.80 x 0.45 x 0.55 m: plinth-less tapered "
                 "legs, one closed carcass, three inset doors with 2 mm shadow gaps, "
                 "a slim brass pull on each, two rear cable cutouts and an adjustable "
                 "shelf behind each door.",
        "why": "The library has no media cabinet; a table pinched to 0.45 m depth "
               "has no doors, no shadow gaps and no cable cutouts, which is why it "
               "read as a slab against the wall.",
    },
    "wall_tv": {
        "mode": "generated",
        "mount": "wall",
        "brief": "Wall-mounted 55-inch flat television, envelope 1.23 W x 0.060 D x "
                 "0.69 H m. Depth budget is exact and must not be exceeded: the "
                 "bezel's front face sits at y = -0.030, the rear housing's back "
                 "face at y = +0.006, and two brushed-steel wall-bracket arms fill "
                 "the remaining 24 mm from y = +0.006 to y = +0.030 -- their rear "
                 "edges are the deepest point of the object. Front (-Y) is a flat "
                 "near-black screen panel using the screen_black key, inset 2 mm "
                 "behind a continuous 20 mm matte-black bezel (plastic_black) that "
                 "wraps all four sides, so front and oblique views read as black "
                 "frame + black screen rather than one grey slab. Rear housing is a "
                 "shallow closed box in plastic_black with a recessed vent band; the "
                 "two bracket arms are steel_brushed vertical plates, symmetric about "
                 "x = 0. No fabric, no timber, no brass anywhere on this object.",
        "why": "The scene graph asks for a media console but no screen, so the wall "
               "reads as a sideboard on blank plaster. A 55-inch panel is a fixed "
               "product size, so it is specified rather than retrieved. The first "
               "three attempts were rejected for using the upholstery palette on a "
               "display part; screen_black / plastic_black / steel_brushed exist "
               "for exactly this case.",
    },
    "entry_door": {
        "mode": "generated",
        "mount": "door",
        "brief": "Interior entrance door leaf filling a 1.04 x 2.10 m opening, "
                 "closed. Total occupied envelope is 1.02 W x 0.120 D x 2.075 H m "
                 "and the 120 mm depth is the hard budget: it is a 40 mm slab "
                 "(y = -0.020 to +0.020) plus a lever handle and rose projecting "
                 "to y = +-0.058 on each face. Nothing may pass y = +-0.060. The "
                 "slab is oak_joinery: a flush leaf with a 4 mm vertical shadow "
                 "gap set 45 mm in from each vertical edge and a shallow 3 mm "
                 "recessed panel field on BOTH faces. Two barrel hinges sit on "
                 "the +X edge. A short lever handle in steel_brushed on both "
                 "faces, 60 mm in from the -X edge at 1.05 m, on a 55 mm round "
                 "rose, with a matching oval escutcheon 130 mm below it. Do not "
                 "add wall brackets, do not add a frame or architrave (the shell "
                 "already carries the jambs), do not open the leaf past its own "
                 "envelope, and do not wrap it in fabric. Both faces (-Y room "
                 "side and +Y corridor side) must read as painted joinery, not "
                 "as upholstery.",
        "why": "The borrowed shell ships jambs and a lintel but no leaf, so the "
               "south wall had a hole straight through to the void. A door is a "
               "fixed building component with a standard size, and it is the one "
               "surface a viewer looks straight at from the media corner, so it "
               "is modelled at real dimension rather than approximated.",
    },
    "armchair_west": {
        "mode": "retrieved", "asset": "ArmChair_01", "fit": "unit",
        "why": "0.85x0.77x1.07 armchair, closest envelope to 0.86x0.86x0.82.",
    },
    "armchair_east": {
        "mode": "retrieved", "asset": "ArmChair_01", "fit": "unit",
        "why": "Mirrored twin of armchair_west for the symmetric grouping.",
    },
    "side_table": {
        "mode": "retrieved", "asset": "side_table_tall_01", "fit": "unit",
        "why": "0.38x0.38x0.76 pedestal squeezed to the 0.50x0.50x0.55 side-table "
               "envelope; low distortion because the profile is round.",
    },
    "plant_northwest": {
        "mode": "retrieved", "asset": "potted_plant_02", "fit": "unit",
        "why": "3-mesh potted plant (pot + trunk + foliage), 0.84 native height "
               "stretched to 1.20 m reads as a taller floor plant.",
    },
    "wall_art_west": {
        "mode": "retrieved", "asset": "fancy_picture_frame_01", "fit": "thin",
        # SceneCode reports wall-hung items as (thickness, width, height) because
        # local +x is the wall normal, while floor furniture is (width, depth,
        # height). Declared per entry instead of guessed from the numbers.
        "axis_order": "twh",
        "why": "Real gilt frame; depth pinned to 0.05 m so it hangs on the wall "
               "instead of being scaled into a slab.",
    },
    "bookshelf": {
        "mode": "retrieved+generated",
        "shell": {"mode": "generated", "brief":
                  "Open timber bookshelf, 1.13 x 0.385 x 1.90 m, four shelves, "
                  "5 mm chamfered edges, visible side panels and back rail."},
        "contents": {"mode": "retrieved", "asset": "decorative_book_set_01",
                     "fit": "unit",
                     "why": "87 individual book meshes; scaled per shelf run so "
                            "the shelves read as filled, not as empty boxes."},
    },
    "area_rug": {
        "mode": "procedural",
        "brief": "Flat woven rug 3.60 x 2.60 m with 30 mm pile and a bound edge.",
        "surface": "fabric_pattern_07",
        "why": "A rug has no silhouette; all of its quality is the weave normal "
               "map and the bound edge, so it uses the fabric PBR set directly.",
    },
    "window_curtain": {
        "mode": "procedural",
        "brief": "Two curtain panels with vertical folds, 2.50 x 1.60 m, "
                 "hung from a timber rod with rings.",
        "surface": "fabric_pattern_07",
        "why": "Same fabric PBR set; folds come from a profile, not from geometry "
               "sitting in the asset library.",
    },
    "sofa_3seater": {
        "mode": "generated",
        "brief": "Two-seat sofa, 2.20 x 0.90 x 0.88 m. Deep seat cushions with "
                 "piping, loose back cushions, timber plinth, tapered legs, "
                 "3 mm chamfer on every cushion edge.",
        "why": "No sofa exists in the asset library and a chair stand-in would "
               "fail the layout contract. This is the object the aDSL agent "
               "pipeline must author (reference result: The Oakwarden, "
               "200 primitives -> 156 meshes + 10 curves).",
    },
    "floor_lamp": {
        "mode": "generated",
        "brief": "Floor lamp, 1.60 m tall: weighted disc base, 22 mm stem, "
                 "drum shade 0.40 m diameter, warm bulb inside.",
        "why": "Library has only desk and oil lamps; scaling a 0.89 m desk lamp "
               "to 1.60 m distorts the arm and shade proportions.",
    },
    "ceiling_light": {
        "mode": "generated",
        "mount": "ceiling",
        "brief": "Three-shade pendant light, 0.68 m across, matte enamel cones "
                 "on a brass ceiling plate.",
        "why": "No ceiling fixture in the library; must be authored.",
    },
    "room_shell": {
        "mode": "borrowed_shell",
        "source": "FineScene reception_lounge build_reception_lounge.py room_shell",
        "why": "Walls, skirting, window reveal and door are architectural context, "
               "not furniture; the FineScene shell is already correct and is "
               "reused verbatim.",
    },
}


def _fit_report(rule: dict, dims: list[float]) -> dict | None:
    asset = rule.get("asset")
    if not asset or asset not in ASSET_INDEX:
        return None
    native = ASSET_INDEX[asset]["size"]
    ratio = [round(dims[i] / native[i], 3) if native[i] else None for i in range(3)]
    worst = max(abs(r - 1.0) for r in ratio if r)
    return {"asset": asset, "native_size": native, "target_size": dims,
            "scale_ratio": ratio, "worst_deviation": round(worst, 3),
            "file": ASSET_INDEX[asset]["file"],
            "meshes": ASSET_INDEX[asset]["meshes"]}


def build_plan(layout: dict) -> dict:
    objects = layout.get("objects") or []
    plan, unknown = [], []

    for spec in objects:
        oid = spec.get("id")
        item = spec.get("asset_item") or {}
        dims = [float(v) for v in (item.get("dimensions") or [1, 1, 1])]
        rule = SUPPLY_RULES.get(oid)
        if rule is None:
            unknown.append(oid)
            plan.append({"id": oid, "mode": "unresolved", "dimensions": dims,
                         "why": "No supply rule; must be decided before assembly."})
            continue

        entry = {"id": oid, "mode": rule["mode"], "dimensions": dims,
                 "semantic": item.get("short_name") or item.get("semantic"),
                 "placement": spec.get("placement"),
                 "why": rule.get("why") or rule.get("brief")}
        fit = _fit_report(rule, dims)
        if fit:
            entry["fit"] = fit
            entry["fit_mode"] = rule.get("fit", "unit")
            if rule.get("axis_order"):
                entry["axis_order"] = rule["axis_order"]
        if rule["mode"] == "retrieved+generated":
            entry["shell"] = rule["shell"]
            entry["contents"] = {**rule["contents"],
                                 "fit": _fit_report(rule["contents"], dims)}
        if rule["mode"] == "procedural":
            entry["brief"] = rule["brief"]
            entry["surface"] = rule["surface"]
        if rule["mode"] == "generated":
            entry["brief"] = rule["brief"]
            if rule.get("mount"):
                entry["mount"] = rule["mount"]
        if rule["mode"] == "borrowed_shell":
            entry["source"] = rule["source"]
        plan.append(entry)

    def tally(mode: str) -> int:
        return sum(1 for p in plan if p["mode"] == mode)

    return {
        "schema": "scenecode.supply.v1",
        "asset_root": str(ASSET_ROOT),
        "policy": ["retrieved (CC0 scanned, 2k PBR) > generated (aDSL agent) > "
                   "procedural (profile + PBR). Parametric fixtures are not used "
                   "for furniture anymore."],
        "counts": {
            "retrieved": tally("retrieved"),
            "retrieved+generated": tally("retrieved+generated"),
            "procedural": tally("procedural"),
            "generated": tally("generated"),
            "borrowed_shell": tally("borrowed_shell"),
            "unresolved": tally("unresolved"),
        },
        "objects": plan,
        "needs_generation": [p["id"] for p in plan if p["mode"] == "generated"]
                             + [p["id"] for p in plan if p["mode"] == "retrieved+generated"],
        "unresolved": unknown,
    }


def main() -> int:
    payload = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    # layout_finalized.json IS the layout: `objects` sits at the top level, not
    # under a "layout" key the way layout.json nests it.
    plan = build_plan(payload.get("layout") or payload)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[supply] objects={len(plan['objects'])} counts={plan['counts']}")
    for p in plan["objects"]:
        fit = p.get("fit") or {}
        detail = (f"{fit.get('asset')} scale={fit.get('scale_ratio')}"
                  if fit else p["mode"])
        print(f"  {p['id']:20s} {p['mode']:20s} {detail}")
    if plan["unresolved"]:
        print(f"[supply] UNRESOLVED: {plan['unresolved']}")
    print(f"[supply] needs_generation={plan['needs_generation']}")
    print(f"[supply] wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
