"""Appearance stage (TRELLIS) -- interface and hand-off only, generation NOT wired.

Where this sits
---------------
furniture_forge produces, per object, an accepted parametric program, the
.blend it built, studio renders and (when retrieval found one) the reference
photo the program was written from. That is the *editable* half. This stage is
the *appearance* half: the program's parts become the structure condition and
the reference photo the appearance condition of a TRELLIS generation, so the
result looks generated but stays part-aligned with the code.

It is behind `furniture_forge.py --use_trellis` and is off by default, so the
Windows runs that test the editable half are unaffected.

What exists today
-----------------
  * `write_handoff()` -- real. Collects everything the generation needs into
    one self-describing `trellis_handoff.json` next to the accepted program.
    This file is the contract between the two machines: the forge runs on the
    Windows box, TRELLIS on the Linux GPU box.
  * `run()` -- a stub. It checks whether a TRELLIS runtime is importable and
    reports honestly; it does NOT generate anything yet. The three steps it
    must implement are listed in PENDING so nobody mistakes the flag for a
    working feature.

Pure Python, stdlib only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

SCHEMA = "codeindoor.trellis_handoff.v1"

PENDING = [
    "voxelise each named part of the .blend into TRELLIS's sparse-structure "
    "grid, keeping a part id per voxel",
    "run TRELLIS with that structure fixed and the reference photo as the "
    "appearance condition (falls back to the text brief when there is no photo)",
    "on a program edit, regenerate only voxels whose part changed and reuse "
    "the stored latents everywhere else",
]


def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def write_handoff(oid: str, accepted_dir: Path, brief: str, params: dict,
                  result: dict, reference: dict | None) -> Path:
    """Write trellis_handoff.json into the accepted asset directory.

    Paths are stored relative to that directory, so the folder can be copied
    to another machine as-is.
    """
    accepted_dir = Path(accepted_dir)
    program = accepted_dir / "program.py"
    blend = accepted_dir / f"{oid}.blend"
    ref_block = None
    if reference and reference.get("path"):
        src = Path(reference["path"])
        if src.is_file():
            # Copy the photo in: the handoff folder must be self-contained.
            dst = accepted_dir / ("reference" + src.suffix.lower())
            dst.write_bytes(src.read_bytes())
            source = reference.get("source") or {}
            ref_block = {"image": dst.name, "sha256": _sha(dst),
                         "page": source.get("page"), "licence": source.get("licence"),
                         "provider": source.get("provider"),
                         "model": source.get("model"), "prompt": source.get("prompt"),
                         "reading": reference.get("reading")}
    handoff = {
        "schema": SCHEMA,
        "id": oid,
        "brief": brief,
        "envelope_m": {k: params.get(k) for k in ("width", "depth", "height")},
        "mount": params.get("mount"),
        "program": {"file": program.name, "sha256": _sha(program)},
        "blend": {"file": blend.name, "sha256": _sha(blend)},
        "views": [Path(v).name for v in result.get("views") or []],
        "bounds": result.get("bounds"),
        "parts": result.get("objects"),
        "condition": {"structure": "program parts (from blend)",
                      "appearance": "reference_image" if ref_block else "text_brief"},
        "reference": ref_block,
        "status": "handoff_written",
        "pending": PENDING,
    }
    path = accepted_dir / "trellis_handoff.json"
    path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def runtime_available() -> bool:
    return importlib.util.find_spec("trellis") is not None


def run(handoff_path: Path) -> dict:
    """Generation entry point. Currently reports state; generates nothing."""
    handoff_path = Path(handoff_path)
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if not runtime_available():
        status, note = "trellis_unavailable", (
            "no `trellis` package on this machine; hand-off written, nothing generated")
    else:
        status, note = "not_implemented", (
            "TRELLIS is importable but structure-conditioned generation is not "
            "written yet; hand-off written, nothing generated")
    handoff["status"] = status
    handoff_path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    return {"id": handoff["id"], "status": status, "note": note,
            "generated": False, "handoff": str(handoff_path)}
