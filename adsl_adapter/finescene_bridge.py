"""FineScene-backed geometry compiler (REPLACES the deprecated raw-bpy path).

Design borrowed verbatim from FineScene/aDSL:
  * The LLM emits ONLY a structured plan (kind + parameters). NEVER geometry code.
  * Geometry is compiled deterministically by FineScene/src/adsl_components.py
    FACTORIES / build_component into a real aDSL Asset.
  * Every part is rendered from 3 views and MUST pass FineScene's
    general_vision.review_images(scope='component') with accepted=True before
    it is allowed into the scene (hard gate, not a post-hoc score).
  * Accepted parts are registered as aDSL ProgramComponents with source
    provenance (file path + sha256), never code strings.

Run:
    python finescene_bridge.py            # demo: parametric chair through the gate
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"E:/3DUse/SceneCode_audit")
ADSL = Path(r"E:/3DUse/aDSL")
FINESCENE_SRC = Path(r"E:/3DUse/FineScene/src")
OUT = ROOT / "runtime/outputs/finescene_bridge"
BLENDER = r"E:/Blender/blender.exe"
WORKER = ADSL / "scripts/blender_worker.py"
PROFILE = Path(r"E:/3DUse/FineScene/configs/deepseek-flash.yaml")
GATEWAY = "http://127.0.0.1:8765/v1/chat/completions"

for path in (str(FINESCENE_SRC), str(ADSL / "adsl-core"),
             str(ADSL / "adsl-agents")):
    if path not in sys.path:
        sys.path.insert(0, path)

from adsl_components import FACTORIES, build_component  # noqa: E402
from core.serialize import asset_to_dict  # noqa: E402


def structured_plan_from_llm(brief: str) -> dict:
    """LLM outputs a PLAN only: which factory and what parameters. No bpy."""
    import urllib.request

    prompt = (
        "You are a scene planner. Convert the request into ONE structured part plan. "
        "Available factory kinds: " + ", ".join(sorted(FACTORIES)) +
        ". Return JSON with keys: kind, role, parameters (object), rationale. "
        "Do NOT output any Python/Blender code, only the plan.\nRequest: " + brief
    )
    payload = {
        "model": "gemini-3.1-pro-preview",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 600,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    req = urllib.request.Request(
        GATEWAY, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
    return json.loads(content)


def compile_part(plan: dict):
    """Deterministic aDSL geometry, compiled by FineScene's factory registry."""
    spec = {
        "component_id": plan.get("component_id", "part_01"),
        "kind": plan.get("kind", "chair"),
        "role": plan.get("role", ""),
        "parameters": plan.get("parameters") or {},
    }
    built = build_component(spec)
    return built.scene, built.manifest()


def export_and_render(asset, cid: str, outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    asset_json = outdir / f"{cid}.asset.json"
    asset_json.write_text(json.dumps(
        asset_to_dict(asset), ensure_ascii=False, indent=2,
        default=lambda x: x.tolist() if hasattr(x, "tolist") else str(x)),
        encoding="utf-8")
    render_out = outdir / f"{cid}_render"
    if render_out.exists():
        import shutil
        shutil.rmtree(render_out)
    subprocess.run(
        [BLENDER, "-b", "--python", str(WORKER), "--",
         "--input", str(asset_json), "--output", str(render_out),
         "--render", "--render-view-count", "3"],
        capture_output=True, timeout=300, check=True)
    return sorted((render_out / "render").glob("view_*.png"))


def visual_gate(views: list[Path], request: dict, cid: str) -> dict:
    """FineScene's per-component hard gate. accepted=False => part rejected."""
    from general_vision import review_images

    if len(views) < 3:
        return {"accepted": False, "status": "insufficient_views",
                "reason": "FineScene requires >=3 views for component scope"}
    report = review_images(
        views, request, str(PROFILE), OUT / f"{cid}_visual_qa.json",
        quality="realistic", scope="component", timeout_seconds=180)
    return {"accepted": bool(report.get("accepted")),
            "status": report.get("status"),
            "verdict": (report.get("decision") or {}).get("verdict"),
            "images": len(views)}


def register(plan: dict, asset, manifest: dict, cid: str) -> dict:
    """aDSL ProgramComponent: provenance only, never code strings."""
    from program_component import ProgramComponent, make_schema

    src = OUT / f"{cid}.asset.json"
    comp = ProgramComponent(
        component_id=cid,
        program="finescene_factory:" + str(manifest.get("factory")),
        params=make_schema("agent"),
        values=plan.get("parameters") or {},
        upstream_refs={"kind": plan.get("kind"), "role": plan.get("role"),
                       "generation": "adsl_parametric"},
        source_path=str(src),
        source_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
    )
    return {"component": comp.to_dict(), "manifest": manifest}


def build_one(brief: str, cid: str = "part_01", style: list | None = None) -> dict:
    plan = structured_plan_from_llm(brief)
    asset, manifest = compile_part({**plan, "component_id": cid})
    views = export_and_render(asset, cid, OUT)
    request = {
        "id": cid, "kind": plan.get("kind", "chair"),
        "original_user_request": brief,
        "style_requirements": style or [],
    }
    gate = visual_gate(views, request, cid)
    result = {"cid": cid, "plan": plan, "factory": manifest.get("factory"),
              "primitive_count": manifest.get("primitive_count"),
              "bounds": manifest.get("bounds"), "views": [str(v) for v in views],
              "gate": gate, "accepted": gate["accepted"]}
    if gate["accepted"]:
        result["registration"] = register(plan, asset, manifest, cid)
    (OUT / f"{cid}_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    brief = "A detailed medieval oak armchair with tufted oxblood leather, turned legs and brass studs."
    style = [
        {"id": "oak_frame", "feature": "smoked oak frame", "visible_evidence": "wood grain on posts and base"},
        {"id": "tufted_leather", "feature": "oxblood tufted leather", "visible_evidence": "diamond button tufting"},
        {"id": "brass_studs", "feature": "antique brass studs", "visible_evidence": "gold studs along rails"},
    ]
    result = build_one(brief, "parametric_chair_01", style)
    print("FINESCENE_BRIDGE_RESULT=" + json.dumps({
        "factory": result["factory"], "primitives": result["primitive_count"],
        "views": len(result["views"]), "gate": result["gate"],
        "accepted": result["accepted"],
    }, ensure_ascii=False))
    print("Wrote", OUT / "parametric_chair_01_result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
