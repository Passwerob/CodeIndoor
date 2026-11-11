#!/usr/bin/env python3
"""Prepare an ISOLATED SceneCode runtime on this Windows host.

Design constraints (see LOCAL_ENV_PROFILE.md):
  * Never touch system Python 3.13 / 3.10, never touch E:/3DUse/aDSL/venv.
  * Everything lives on E: (C: has only ~24 GB free).
  * No API key is ever written. The template only contains a placeholder.
  * This script does NOT install anything and does NOT modify SceneCode sources;
    it only creates directories, .gitignore, a .env template and an env script.

Usage:
    python E:/3DUse/SceneCode_audit/env/prepare_local.py [--check]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"E:/3DUse/SceneCode_audit/runtime")
VENV = ROOT / ".venv-scenecode"
BLENDER_DIR = ROOT / "blender-4.4.3-windows"
CACHE = ROOT / "cache"
OUTPUTS = ROOT / "outputs"
BRIDGE_PORT = 8765

ENV_LINES = [
    "# Copy to .env and fill in DEEPSEEK_API_KEY at runtime only.",
    "# NEVER commit .env. NEVER paste a real key into code, logs or reports.",
    "# DeepSeek key is read from the parent process environment at launch time.",
    'OPENAI_API_KEY=${DEEPSEEK_API_KEY}',
    f'OPENAI_API_BASE=http://127.0.0.1:{BRIDGE_PORT}/v1',
    'SCENECODE_TEXT_MODEL=deepseek-flash',
    'SCENECODE_VISION_MODEL=deepseek-flash',
    'OPENAI_TRACING_KEY=',
    '',
    '# All caches and outputs stay on E:',
    f'UV_CACHE_DIR={CACHE.as_posix()}/uv',
    f'UV_PYTHON_INSTALL_DIR={ROOT.as_posix()}/python',
    f'PIP_CACHE_DIR={CACHE.as_posix()}/pip',
    f'HF_HOME={CACHE.as_posix()}/hf',
    f'SCENECODE_OUTPUT_DIR={OUTPUTS.as_posix()}',
    f'SCENECODE_MATERIALS_DIR={OUTPUTS.as_posix()}/materials',
    f'SCENECODE_MATERIAL_EMBEDDINGS_DIR={OUTPUTS.as_posix()}/materials/embeddings',
    f'SCENECODE_BLENDER_GLOBAL_LOCK={ROOT.as_posix()}/blender_requests.lock',
    '',
    '# Disabled generative / retrieval backends (16 GB VRAM budget):',
    'SAM3_CHECKPOINT=',
    'SAM3D_PIPELINE_CONFIG=',
    'FLUX_PYTHON=',
    'FLUX_MODEL_PATH=',
    'GOOGLE_API_KEY=',
]

GITIGNORE_LINES = [
    ".env",
    ".venv-scenecode/",
    "python/",
    "cache/",
    "outputs/",
    "blender-4.4.3-windows/",
    "*.lock",
]

SHELL_LINES = [
    "# source this from Git Bash before running SceneCode",
    *[f'export {line}' for line in ENV_LINES
      if line and not line.startswith("#") and "${" not in line],
    f'export PATH="{BLENDER_DIR.as_posix()}:$PATH"',
    f'export PATH="{VENV.as_posix()}/Scripts:$PATH"',
    '# The key must be supplied by the caller, never stored here:',
    '#   export DEEPSEEK_API_KEY=...   (interactive shell only)',
]


def check() -> int:
    print("=== Local environment check ===")
    py = shutil.which("python")
    print(f"system python       : {py} -> {sys.version.split()[0]} (must stay untouched)")
    print(f"aDSL venv           : {Path(r'E:/3DUse/aDSL/venv/Scripts/python.exe').exists()}")
    print(f"uv on PATH          : {shutil.which('uv') or 'NOT FOUND'}")
    blender = Path(r"E:/Blender/blender.exe")
    print(f"existing Blender    : {blender} exists={blender.exists()} (aDSL, 5.2.2 LTS)")
    print(f"isolated Blender    : {BLENDER_DIR} exists={BLENDER_DIR.exists()}")
    print(f"isolated venv       : {VENV} exists={VENV.exists()}")
    drive = shutil.disk_usage("E:/")
    print(f"E: free             : {drive.free / (1024**3):.1f} GB")

    wsl = shutil.which("wsl")
    print(f"wsl.exe on PATH     : {wsl or 'NOT FOUND'}")
    print("WSL feature         : Disabled (verified) + wsl.exe blocked by security policy")
    print("-> Windows-native deployment only. Do not attempt to bypass the blacklist.")
    return 0


def prepare() -> int:
    for d in (ROOT, CACHE, OUTPUTS):
        d.mkdir(parents=True, exist_ok=True)
    (ROOT / ".gitignore").write_text("\n".join(GITIGNORE_LINES) + "\n", encoding="utf-8")
    (ROOT / ".env.template").write_text("\n".join(ENV_LINES) + "\n", encoding="utf-8")
    (ROOT / "env.sh").write_text("\n".join(SHELL_LINES) + "\n", encoding="utf-8")
    print("Created (no installs, no source edits, no secrets written):")
    for name in (".gitignore", ".env.template", "env.sh"):
        print(f"  {ROOT / name}")
    print(f"  dirs: {CACHE}, {OUTPUTS}")
    print()
    print("Next manual steps (NOT executed by this script):")
    print("  1. Obtain uv (isolated binary under runtime/uv/)")
    print("  2. uv python install 3.11   -> runtime/python/")
    print("  3. uv venv --python 3.11 runtime/.venv-scenecode")
    print("  4. Download Blender 4.4.3 Windows portable zip into runtime/blender-4.4.3-windows/")
    print("  5. Copy .env.template -> .env and inject DEEPSEEK_API_KEY in the shell only")
    print("  6. Start bridge/ local gateway on port %d, then run SceneCode" % BRIDGE_PORT)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="read-only environment report")
    args = ap.parse_args()
    if args.check:
        raise SystemExit(check())
    check()
    print()
    raise SystemExit(prepare())
