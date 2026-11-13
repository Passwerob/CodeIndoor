"""Offline tests for the retrieval-augmented forge and the TRELLIS hand-off.

No network, no Blender, no model: the model call, the Blender run and the
visual gate are replaced by stubs, so what is tested is the wiring -- that the
reference photo reaches the code-writing call, that a missing photo falls back
to the old behaviour, and that --use_trellis writes a hand-off without
pretending to have generated anything.
"""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="codeindoor_test_"))
os.environ["CODEINDOOR_ROOT"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adsl_adapter"))

import furniture_forge as ff  # noqa: E402
import reference_images as ri  # noqa: E402
import trellis_stage as ts  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
READING = {"choice": 0, "reason": "clean front view", "view": "front",
           "design_summary": "Open oak bookshelf with four shelves.",
           "parts": [{"name": "side_panel", "count": 2, "shape": "panel",
                      "note": "full height", "curved": False},
                     {"name": "shelf", "count": 4, "shape": "board",
                      "note": "evenly spaced", "curved": False}],
           "proportions": ["shelf spacing about 1/5 of the height"],
           "materials": ["carcass: oak, matte"], "symmetry": "left-right"}
CODE = "def build(B, mats, P):\n    return [B.box('shelf_1', (1, 1, 1), (0, 0, 0), 'oak_joinery')]\n"


def test_mime_and_query():
    assert ri.image_mime(PNG) == "image/png"
    assert ri.image_mime(JPEG) == "image/jpeg"
    assert ri.image_mime(b"<html>") is None
    assert ri.fallback_query(
        "Low walnut media console, 1.80 x 0.45 x 0.55 m: legs") == "low walnut media console"


def test_local_photo_is_selected_and_cached(monkeypatch=None):
    d = ri.REFERENCE / "bookshelf"
    d.mkdir(parents=True, exist_ok=True)
    (d / "photo.jpg").write_bytes(JPEG)
    calls = []

    def fake_chat(messages, max_tokens=0):
        calls.append(messages)
        return dict(READING)
    ri._chat = fake_chat
    ref = ri.find_reference_image("bookshelf", "Open timber bookshelf", [1.1, 0.4, 1.9],
                                  mode="local")
    assert ref and ref["usable"] and Path(ref["path"]).name == "photo.jpg"
    sent = calls[0][0]["content"]
    assert sent[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # second call is served from image_ref.json, the model is not asked again
    again = ri.find_reference_image("bookshelf", "Open timber bookshelf", [1.1, 0.4, 1.9],
                                    mode="local")
    assert again["path"] == ref["path"] and len(calls) == 1
    block = ri.reading_block(ref)
    assert "shelf x4" in block and "REFERENCE PHOTO" in block
    assert ri.find_reference_image("bookshelf", "x", [1, 1, 1], mode="off") is None


def test_no_usable_photo_returns_none():
    d = ri.REFERENCE / "lamp"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.png").write_bytes(PNG)
    ri._chat = lambda messages, max_tokens=0: {"choice": -1, "reason": "wrong type"}
    assert ri.find_reference_image("lamp", "Floor lamp", [0.4, 0.4, 1.6], mode="local") is None
    assert json.loads((d / "image_ref.json").read_text())["usable"] is False


def test_generate_mode_reads_and_retries():
    made = []

    def fake_generate(prompt, size, path):
        made.append((prompt, size))
        path = path.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG + bytes([len(made)]))
        return {"file": path.name, "provider": "generated", "model": "stub",
                "prompt": prompt, "size": size, "sha256": "s", "bytes": 1,
                "licence": "generated", "page": None, "full": None,
                "query": None, "title": "generated reference"}
    answers = [{"choice": -1, "reason": "room scene"}, dict(READING)]
    ri.generate_image = fake_generate
    ri._chat = lambda messages, max_tokens=0: answers.pop(0)
    ref = ri.find_reference_image("tall_shelf", "Open timber bookshelf",
                                  [1.1, 0.4, 1.9], mode="generate")
    assert ref and ref["source"]["provider"] == "generated"
    assert Path(ref["path"]).name == "gen_01.png" and len(made) == 2
    assert made[0][1] == "1024x1536" and "no books" in made[0][0]
    assert ri.image_size([2.2, 0.9, 0.88]) == "1536x1024"
    assert ri.image_size([0.86, 0.86, 0.82]) == "1024x1024"


def test_generate_without_key_falls_back():
    os.environ.pop("IMAGE_API_KEY", None)
    import importlib
    fresh = importlib.reload(ri)
    assert fresh.generate_image("p", "1024x1024", TMP / "x") is None
    assert fresh.find_reference_image("nokey", "Chair", [0.5, 0.5, 0.9],
                                      mode="generate") is None


def test_parametric_material_keys():
    import material_spec as ms
    white = ms.parse("paint:#FFFFFF")
    assert white["kind"] == "paint" and white["rgb"] == (1.0, 1.0, 1.0)
    assert white["roughness"] == ms.CLASSES["paint"]
    grey = ms.parse("fabric:#808080:0.9")
    assert grey["roughness"] == 0.9 and abs(grey["rgb"][0] - 0.21586) < 1e-4  # sRGB->linear
    for bad in ("oak_joinery", "paint:#FFF", "chrome:#FFFFFF", "paint:#FFFFFF:1.5", 3):
        assert ms.parse(bad) is None
    ok, _ = ff.check_contract(
        "def build(B, mats, P):\n    body = 'paint:#F3F1EC'\n"
        "    return [B.box('a', (1, 1, 1), (0, 0, 0), body)]\n")
    assert ok
    ok, why = ff.check_contract(
        "def build(B, mats, P):\n    return [B.box('a', (1, 1, 1), (0, 0, 0), 'chrome:#FFFFFF')]\n")
    assert not ok and "not a valid material" in why
    body = "def build(B, mats, P):\n    return [B.box('a', (1, 1, 1), (0, 0, math.pi), 'oak_joinery')]\n"
    assert ff.check_contract("import math\n" + body)[0]
    assert not ff.check_contract("import os\n" + body)[0]
    assert not ff.check_contract("from math import pi\n" + body)[0]


def test_image_endpoint_fallback():
    import importlib
    import urllib.error
    fresh = importlib.reload(ri)
    for k in ("IMAGE_API_KEY", "IMAGE_FALLBACK_API_KEY", "IMAGE_FALLBACK_API_BASE"):
        os.environ.pop(k, None)
    assert fresh.image_endpoints() == []
    os.environ["IMAGE_FALLBACK_API_KEY"] = "k2"
    os.environ["IMAGE_FALLBACK_API_BASE"] = "https://example.invalid"   # bare host
    only = fresh.image_endpoints()
    assert [e["label"] for e in only] == ["fallback"]
    assert only[0]["base"] == "https://example.invalid/v1"
    os.environ["IMAGE_API_KEY"] = "k1"
    assert [e["label"] for e in fresh.image_endpoints()] == ["primary", "fallback"]
    seen = []

    def once(endpoint, prompt, size):
        seen.append(endpoint["label"])
        if endpoint["label"] == "primary":
            raise urllib.error.HTTPError("u", 503, "Service Unavailable", None, None)
        return PNG
    fresh._generate_once = once
    made = fresh.generate_image("p", "1024x1024", TMP / "fb" / "gen_00")
    assert seen == ["primary", "fallback"] and made["endpoint_role"] == "fallback"
    assert len(made["failed_endpoints"]) == 1 and "503" in made["failed_endpoints"][0]
    assert "k1" not in json.dumps(made) and "k2" not in json.dumps(made)
    fresh._generate_once = lambda e, p, s: (_ for _ in ()).throw(RuntimeError("down"))
    assert fresh.generate_image("p", "1024x1024", TMP / "fb" / "gen_01") is None
    for k in ("IMAGE_API_KEY", "IMAGE_FALLBACK_API_KEY", "IMAGE_FALLBACK_API_BASE"):
        os.environ.pop(k, None)
    importlib.reload(ri)


def _stub_forge(captured):
    def call_agent(messages, max_tokens=0):
        captured.append(messages)
        return {"text": "```python\n" + CODE + "```", "raw": "{}",
                "meta": {"resp_sha": "x", "upstream_model": "stub", "usage": None}}

    def run_blender(code, params, oid, attempt):
        work = ff.OUT / f"{oid}_attempt{attempt}"
        work.mkdir(parents=True, exist_ok=True)
        (work / "program.py").write_text(code, encoding="utf-8")
        (work / f"{oid}.blend").write_bytes(b"BLENDER")
        (work / "view_front.png").write_bytes(PNG)
        return {"ok": True, "objects": 14, "vertices": 100, "curve_parts": 0,
                "views": [str(work / "view_front.png")], "work_dir": str(work),
                "bounds": {"size": [1.1, 0.4, 1.9]}, "envelope_ok": True}
    ff.call_agent, ff.run_blender = call_agent, run_blender
    gate_args = []
    ff.visual_gate = lambda views, brief, oid, summary=None: (
        gate_args.append(summary) or {"accepted": True, "verdict": "passed", "findings": []})
    return gate_args


def test_forge_sends_photo_and_writes_handoff():
    ri._chat = lambda messages, max_tokens=0: dict(READING)
    captured = []
    gate_args = _stub_forge(captured)
    entry = {"id": "bookshelf", "dimensions": [1.1, 0.4, 1.9],
             "brief": "Open timber bookshelf"}
    res = ff.forge(entry, max_attempts=1, reference_mode="local", use_trellis=True)
    assert res["ok"] and res["reference_image"]["provider"] == "local"
    first = captured[0][0]["content"]
    assert isinstance(first, list) and first[1]["type"] == "image_url"
    assert "REFERENCE PHOTO" in first[0]["text"] and "shelf x4" in first[0]["text"]
    assert "four shelves" in gate_args[0]
    acc = ff.OUT / "bookshelf"
    handoff = json.loads((acc / "trellis_handoff.json").read_text())
    assert handoff["schema"] == ts.SCHEMA
    assert handoff["condition"]["appearance"] == "reference_image"
    assert (acc / handoff["reference"]["image"]).is_file()
    # the stage must not claim to have generated anything
    assert res["trellis"]["generated"] is False
    assert res["trellis"]["status"] in ("trellis_unavailable", "not_implemented")


def test_forge_without_photo_matches_old_behaviour():
    captured = []
    _stub_forge(captured)
    entry = {"id": "side_table_x", "dimensions": [0.5, 0.5, 0.55], "brief": "Side table"}
    res = ff.forge(entry, max_attempts=1, reference_mode="off")
    assert res["ok"] and res["reference_image"] is None and "trellis" not in res
    assert isinstance(captured[0][0]["content"], str)
    assert not (ff.OUT / "side_table_x" / "trellis_handoff.json").exists()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ALL REFERENCE/FORGE TESTS PASSED")
