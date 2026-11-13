"""DEPRECATED PATH — kept only as evidence of a real run, not as the system design.

Root cause (see ROOT_CAUSE_ANALYSIS.md): letting the LLM emit raw bpy code sits
at the WRONG abstraction layer. FineScene/aDSL produce high-quality single
assets by having the LLM emit a pydantic-validated structured PLAN and compiling
geometry with deterministic parametric factories + per-component visual QA.
The project even already recorded this failure in
aDSL/create_laptop_fixed.py ("fix DeepSeek-generated code to match aDSL API").

Do NOT extend this module. Rebuild on FineScene/src/adsl_components.py
FACTORIES + aDSL core instead.

Original description (superseded):
Agentic part-program generation with execution-guided repair.
Flow per part:

    part spec -> Constructor Agent (DeepSeek via local gateway)
              -> static contract check (AST)
              -> Blender sandbox execution
              -> on failure: error + code fed back to the Agent, retry
              -> on success: source file handed to aDSL as a program reference

No geometry is implemented in this module.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

ADAPTER = Path(r"E:/3DUse/New/CodeIndoor/adsl_adapter")
RUNTIME = Path(r"E:/3DUse/New/CodeIndoor/runtime/outputs/agentic")
GEN = RUNTIME / "generated"
RUNS = RUNTIME / "runs"
BLENDER = r"E:/Blender/blender.exe"
GATEWAY = "http://127.0.0.1:8765/v1/chat/completions"

for directory in (GEN, RUNS):
    directory.mkdir(parents=True, exist_ok=True)

ALLOWED_IMPORTS = {"bpy", "bmesh", "math", "mathutils", "random"}
FORBIDDEN = ("open(", "eval(", "exec(", "__import__", "importlib",
             "requests", "urllib", "subprocess", "os.", "socket",
             "read_factory_settings", "http://", "https://",
             "images.generate", "bpy.ops.wm.save_as_mainfile")

SYSTEM = """You are the Part Constructor Agent of an agentic indoor-scene coding system.
You write Blender Python programs that build ONE furniture part with high visual quality.

HARD CONTRACT:
1. Define exactly:  def build_part(params: dict, name: str) -> list:
2. Allowed imports ONLY: bpy, bmesh, math, mathutils, random.
3. FORBIDDEN: any file I/O, network, subprocess, eval/exec, importlib, os,
   saving .blend, clearing the scene, or calling any image/3D generation service.
4. params provides: width, depth, height, bevel, color (RGB tuple), roughness,
   origin (x,y,z); optionally bulge, seam, radius_top, radius_bottom.
5. Build REAL detailed geometry, not a bare cube: use BEVEL modifiers with
   several segments, smooth shading / weighted normals, subdivision, curves
   (bevel_depth), cones/lathes, or bmesh deformation. Give the object a
   Principled BSDF material using params["color"] and params["roughness"].
6. Place the part at params["origin"]. Name objects starting with `name`.
   If you apply a modifier, first set bpy.context.view_layer.objects.active = obj
   and obj.select_set(True), otherwise Blender raises "Context is incorrect".
7. Return the list of bpy objects created. Do not add lights or cameras.
8. Output ONLY Python source (a ```python fenced block is allowed). Nothing else.
9. Blender 5.2 API traps (past generated programs failed on these):
   - Mesh.use_auto_smooth was REMOVED; never use it. Use poly.use_smooth instead.
   - Do NOT hand-build box topology with bmesh.faces.new(): it easily raises
     "faces.new(...): face already exists". Prefer bpy.ops.mesh.primitive_*
     plus BEVEL / SUBSURF modifiers, or bmesh.ops.create_cube().
   - Keep topology construction simple and let modifiers do the detailing.
"""


def call_agent(messages: list, max_tokens: int = 3500) -> str:
    payload = {
        "model": "gemini-3.1-pro-preview",   # SceneCode constructor model name
        "messages": [{"role": "system", "content": SYSTEM}] + messages,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "temperature": 0.35,
    }
    req = urllib.request.Request(
        GATEWAY, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def strip_code(text: str) -> str:
    match = re.search(r"```(?:python)?\s*([\s\S]*?)\s*```", text)
    return (match.group(1) if match else text).strip()


def check_contract(code: str) -> tuple[bool, str]:
    lowered = code.lower()
    for bad in FORBIDDEN:
        if bad.lower() in lowered:
            return False, "forbidden token: " + bad
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, "syntax error: " + str(exc)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    return False, "import not allowed: " + alias.name
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] not in ALLOWED_IMPORTS:
                return False, "from-import not allowed: " + node.module
    has_builder = any(
        isinstance(n, ast.FunctionDef) and n.name == "build_part"
        and len(n.args.args) >= 2 for n in tree.body)
    if not has_builder:
        return False, "missing build_part(params, name)"
    if "bpy." not in code:
        return False, "no bpy usage"
    return True, "ok"


def execute_in_blender(code: str, params: dict, cid: str, attempt: int) -> dict:
    code_path = GEN / f"{cid}_attempt{attempt}.py"
    result_path = RUNS / f"{cid}_attempt{attempt}.json"
    params_path = RUNS / f"{cid}_params.json"
    code_path.write_text(code, encoding="utf-8")
    params_path.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
    cmd = [BLENDER, "-b", "--python", str(ADAPTER / "part_runner.py"), "--",
           str(code_path), str(result_path), str(params_path), cid]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Blender execution timed out (120s)"}
    if result_path.exists():
        return json.loads(result_path.read_text(encoding="utf-8"))
    tail = (proc.stderr or b"").decode("utf-8", "replace")[-1200:]
    return {"ok": False, "error": "no result file; blender stderr: " + tail}


def code_part(cid: str, params: dict, part_meta: dict | None = None,
              max_attempts: int = 3, feedback: str | None = None) -> dict:
    """Generate + execute + repair ONE part. Returns the accepted source path."""
    meta = part_meta or {}
    spec_text = (
        f"Part id: {cid}\n"
        f"Part type: {meta.get('part_type', 'furniture part')}\n"
        f"Upstream base shape: {meta.get('upstream_base_shape', 'custom')}\n"
        f"Params: {json.dumps(params, ensure_ascii=False)}\n"
        "Generate a visually rich, bevelled/smoothed part matching these params."
        + ("\n\nAdditional review feedback to address:\n" + feedback if feedback else "")
    )
    log: list[dict] = []
    prior_error: str | None = None
    prior_code: str | None = None

    for attempt in range(1, max_attempts + 1):
        started = time.time()
        if prior_error is None:
            messages = [{"role": "user", "content": spec_text}]
        else:
            messages = [
                {"role": "user", "content": spec_text},
                {"role": "assistant", "content": prior_code or ""},
                {"role": "user", "content":
                    "That program failed static validation or Blender execution.\n"
                    f"Error:\n{prior_error[-1800:]}\n\n"
                    "Return the COMPLETE corrected Python program, not a patch. "
                    "Keep the build_part contract."},
            ]
        # This host's proxy intermittently drops connections (RemoteDisconnected).
        # A transport failure is NOT an Agent verdict, so retry before giving up.
        raw = None
        for retry in range(3):
            try:
                raw = call_agent(messages)
                break
            except Exception as exc:  # noqa: BLE001
                if retry == 2:
                    log.append({"attempt": attempt, "stage": "llm",
                                "error": type(exc).__name__ + ": " + str(exc)[:150]})
                else:
                    time.sleep(1 + retry)
        if raw is None:
            break
        code = strip_code(raw)
        ok, why = check_contract(code)
        entry = {"attempt": attempt, "chars": len(code),
                 "contract_ok": ok, "contract_note": why,
                 "seconds": round(time.time() - started, 2)}
        if not ok:
            prior_error = "Static contract violation: " + why
            prior_code = code
            log.append(entry)
            continue
        result = execute_in_blender(code, params, cid, attempt)
        entry["blender_ok"] = bool(result.get("ok"))
        entry["objects"] = result.get("objects")
        entry["mesh_vertices"] = result.get("mesh_vertices")
        if not result.get("ok"):
            entry["error_preview"] = str(result.get("error", ""))[-300:]
            prior_error = str(result.get("error", "unknown Blender error"))
            prior_code = code
            log.append(entry)
            continue
        accepted = GEN / f"{cid}.py"
        accepted.write_text(code, encoding="utf-8")
        log.append(entry)
        return {"ok": True, "cid": cid, "source_path": str(accepted),
                "attempts": attempt, "log": log, "blender_result": result}
    return {"ok": False, "cid": cid, "attempts": len(log), "log": log}
