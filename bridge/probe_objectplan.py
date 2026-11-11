#!/usr/bin/env python3
"""Answer the open risk: does a procedural (non-photographic) reference image
make SceneCode's Planner produce a degenerate ObjectPlan?

Uses the real SceneCode planner prompt contract (prompts/planner.md) and calls
deepseek-flash through the local gateway, comparing:
  A) text-only input
  B) procedural reference image + the same text

This is a capability probe, NOT a full SceneCode pipeline reproduction: the
real PlannerAgent also does checker/repair rounds. Report that distinction.

No API key, prompt body or image bytes are written to the result.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765/v1"
OUT = Path(r"E:/3DUse/SceneCode_audit/OBJECTPLAN_PROBE_RESULT.json")

BRIEF = (
    "Create a detailed two-seat modern sofa with a high backrest, two armrests, "
    "three seat cushions, three back cushions, a wooden plinth base, four tapered "
    "legs, and visible piping seams. Realistic proportions, suitable for a living room."
)

SYSTEM = """You are a professional 3D furniture analysis expert. Analyze the input
furniture image or text description, identify the object category, break down ALL
functional parts, and generate a detailed construction plan.

Output a JSON object with this exact structure:
{
  "category": "chair|table|sofa|...",
  "name": "object_name",
  "description": "overall description",
  "parts": [
    {
      "name": "unique part name, e.g. seat, leg_01, backrest",
      "part_type": "seat|leg|backrest|armrest|cushion|base|...",
      "shape": {"base_shape": "cube|cylinder|sphere|cone|torus|custom",
                "dimensions": {"width":0.5,"depth":0.5,"height":0.05},
                "modifiers": ["bevel","smooth",...], "description": "..."},
      "position": {"x":0,"y":0,"z":0}, "rotation": {"x":0,"y":0,"z":0},
      "is_symmetric": false, "symmetric_axis": "x", "is_movable": false,
      "material": {"type":"fabric","base_color":[0.8,0.5,0.2]},
      "sub_parts": []
    }
  ],
  "total_dimensions": {"width":2.0,"depth":0.9,"height":0.9},
  "style": "modern",
  "material_hints": {}
}

Rules: model EVERY functional part separately (backrest, armrests, each cushion,
plinth, each leg, piping/seams where relevant). Do not collapse the object into a
single cube. Use varied base_shape values and modifiers where appropriate.
Output ONLY the JSON object."""


def post(path: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_procedural_image() -> str:
    resp = post("/images/generations", {"model": "gpt-image-1.5",
                                        "prompt": BRIEF, "size": "512x512", "n": 1})
    return resp["data"][0]["b64_json"]


def parse_plan(content: str) -> dict:
    cleaned = content.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if match:
        cleaned = match.group(1)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def stats(plan: dict) -> dict:
    parts = plan.get("parts") or []
    shapes = [str((p.get("shape") or {}).get("base_shape", "")) for p in parts]
    types = [str(p.get("part_type", "")) for p in parts]
    names = [str(p.get("name", "")) for p in parts]
    joined = " ".join(names).lower()
    keywords = ("backrest", "armrest", "cushion", "leg", "base", "plinth", "seam", "piping")
    found = [k for k in keywords if k in joined]
    return {
        "part_count": len(parts),
        "unique_part_types": sorted(set(t for t in types if t)),
        "shape_distribution": {s: shapes.count(s) for s in sorted(set(shapes)) if s},
        "functional_keywords_found": found,
        "sub_parts_total": sum(len(p.get("sub_parts") or []) for p in parts),
        "modifiers_total": sum(len((p.get("shape") or {}).get("modifiers") or []) for p in parts),
        "parts_with_material": sum(bool(p.get("material")) for p in parts),
        "part_names": names,
        "degenerate": len(parts) < 5 or len(set(shapes)) <= 1,
    }


def run_case(label: str, messages: list) -> dict:
    payload = {
        "model": "gemini-3.1-pro-preview",  # SceneCode code_object planner model
        "messages": [{"role": "system", "content": SYSTEM}] + messages,
        "max_tokens": 6000,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    started = time.time()
    resp = post("/chat/completions", payload)
    elapsed = round(time.time() - started, 2)
    choice = (resp.get("choices") or [{}])[0]
    finish = choice.get("finish_reason")
    content = (choice.get("message") or {}).get("content") or ""
    try:
        plan = parse_plan(content)
        result = stats(plan)
        result["parse_error"] = None
        sample = OUT.parent / f"OBJECTPLAN_SAMPLE_{label[0]}.json"
        sample.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        # A malformed / truncated JSON must NOT be silently reported as success.
        # Fall back to a regex census so we still get a truthful richness signal.
        names = re.findall(r'"name"\s*:\s*"([^"]+)"', content)
        types = re.findall(r'"part_type"\s*:\s*"([^"]+)"', content)
        shapes = re.findall(r'"base_shape"\s*:\s*"([^"]+)"', content)
        result = {
            "parse_error": type(exc).__name__ + ": " + str(exc)[:120],
            "fallback": {
                "name_occurrences": len(names),
                "part_type_occurrences": len(types),
                "shape_distribution": {s: shapes.count(s) for s in sorted(set(shapes))},
                "sample_names": names[:14],
                "content_tail": content[-160:],
            },
            "degenerate": len(types) < 5 or len(set(shapes)) <= 1,
        }
    result.update({"case": label, "seconds": elapsed,
                   "usage": resp.get("usage", {}),
                   "finish_reason": finish, "content_chars": len(content)})
    print(f"\n=== {label} ({elapsed}s) ===")
    for key in ("part_count", "unique_part_types", "shape_distribution",
                "functional_keywords_found", "sub_parts_total", "modifiers_total",
                "parts_with_material", "degenerate", "parse_error", "fallback",
                "finish_reason", "content_chars"):
        if key in result:
            print(f"  {key}: {result[key]}")
    return result


def main() -> int:
    text_only = run_case("A: text only", [{"role": "user", "content": BRIEF}])

    b64 = get_procedural_image()
    with_image = run_case("B: procedural image + text", [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            {"type": "text", "text": "Reference silhouette for the object. " + BRIEF},
        ],
    }])

    def count(case: dict) -> int:
        return (case.get("part_count")
                or (case.get("fallback") or {}).get("part_type_occurrences") or 0)

    verdict = {
        "text_only_part_count": count(text_only),
        "image_part_count": count(with_image),
        "text_only_degenerate": text_only.get("degenerate"),
        "image_degenerate": with_image.get("degenerate"),
        "part_count_delta": count(with_image) - count(text_only),
        "json_strictly_parsed": [text_only.get("parse_error") is None,
                                 with_image.get("parse_error") is None],
    }
    OUT.write_text(json.dumps({
        "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": "deepseek-flash via local gateway",
        "brief_chars": len(BRIEF),
        "cases": [text_only, with_image],
        "verdict": verdict,
        "caveat": "Capability probe only. Real PlannerAgent adds checker/repair rounds "
                  "and uses the full 20KB prompt; not a full pipeline reproduction.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== verdict ===")
    print(json.dumps(verdict, ensure_ascii=False))
    print("Wrote ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
