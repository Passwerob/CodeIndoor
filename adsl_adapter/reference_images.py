"""Text-only input, image-mediated: one reference product image per object.

Why this module exists
----------------------
The furniture Agent used to author geometry from a prose brief alone. That is
where "plausible-looking nonsense" comes from: the model invents a design it
has never looked at. An image carries far more design information than a
brief, so the pipeline now goes

    brief (text) -> image model renders ONE clean catalogue shot -> VLM checks
    it and reads its construction -> the Agent writes the program FROM it

This is SceneCode's own design (router.py generates a reference image before
code_object runs); the local gateway had been answering that call with a
placeholder. Generation is the default source because it is controllable
(always one object, plain background, the requested view), reproducible from
the stored prompt, and free of third-party image rights. Two other sources
remain: photos dropped by hand into runtime/reference/<oid>/ (always tried
first), and open-licence web search (`web` mode -- in practice the open
archives rarely hold a usable product shot, which is why it is not default).

The user still supplies text only; the image is an intermediate artefact. The
same photo is later the appearance condition handed to TRELLIS (trellis_stage),
which is why the program has to be written from the image rather than beside
it: code and image must describe the same object or a code edit stops meaning
anything once appearance is generated.

Image API: any OpenAI-compatible /images/generations endpoint. Configure with
IMAGE_API_KEY, IMAGE_API_BASE and IMAGE_MODEL (and an optional second endpoint,
IMAGE_FALLBACK_API_KEY / _BASE / IMAGE_FALLBACK_MODEL, tried when the first
fails), as environment variables or as
lines in runtime/.env (gitignored). The key is never logged or stored.

Everything fetched or generated is cached under runtime/reference/<oid>/ with a manifest
(query, source URL, licence, sha256), so a run is reproducible and auditable
even though search results drift.

Pure Python, stdlib only. No bpy import.

    python adsl_adapter/reference_images.py --object bookshelf \
        --brief "Open timber bookshelf, four shelves"
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

# Default root is THIS repo's top level (derived from this file), so a fresh
# clone works out of the box and generated artefacts land here -- not in some
# other workspace. Override with CODEINDOOR_ROOT if you need another location.
DEFAULT_ROOT = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("CODEINDOOR_ROOT", DEFAULT_ROOT))
REFERENCE = ROOT / "runtime/reference"
GATEWAY = os.environ.get("CODEINDOOR_GATEWAY",
                         "http://127.0.0.1:8765/v1/chat/completions")
# A generic client name: image hosts ask automated clients to identify
# themselves, and nothing about the user or project belongs in a header.
USER_AGENT = "reference-image-fetch/0.1 (python-urllib)"

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 6
PROVIDERS = ("openverse", "commons")
ENV_FILE = ROOT / "runtime/.env"
IMAGE_API_BASE = "https://us.novaiapi.com/v1"
IMAGE_FALLBACK_API_BASE = "https://api.chimerahub.org/v1"
IMAGE_MODEL = "gpt-image-2.6"
IMAGE_TIMEOUT = 180                      # a hung relay must not stall the batch
MAX_GENERATED_BYTES = 24 * 1024 * 1024   # our own render, not a web thumbnail
GENERATE_TRIES = 2


def setting(name: str, default: str | None = None) -> str | None:
    """Environment first, then runtime/.env -- the same place the gateway
    reads the DeepSeek key from, so there is one secrets file, not two."""
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or default
    except OSError:
        pass
    return default


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def image_mime(data: bytes) -> str | None:
    """Sniff the type from magic bytes; a URL suffix is not evidence."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_part(path: Path) -> dict:
    """OpenAI-style image content part with the file's real mime type."""
    data = path.read_bytes()
    mime = image_mime(data) or "image/png"
    return {"type": "image_url",
            "image_url": {"url": f"data:{mime};base64,"
                                 + base64.b64encode(data).decode("ascii")}}


def _get(url: str, timeout: int = 30, limit: int = MAX_IMAGE_BYTES) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit + 1)


def _chat(messages: list, max_tokens: int = 1200) -> dict | None:
    """One JSON-mode call through the local gateway. None on any failure:
    retrieval is an enrichment, so it must never take the forge down."""
    payload = {"model": "gemini-3.1-pro-preview", "messages": messages,
               "max_tokens": max_tokens, "temperature": 0.0,
               "response_format": {"type": "json_object"},
               "thinking": {"type": "disabled"}}
    try:
        req = urllib.request.Request(
            GATEWAY, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[reference] model call failed: {type(exc).__name__}: {exc}", flush=True)
        return None


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------
def fallback_query(brief: str) -> str:
    """The noun phrase a brief opens with, minus dimensions.

    "Low walnut media console, 1.80 x 0.45 x 0.55 m: plinth-less ..." ->
    "low walnut media console". Used when the model is unreachable.
    """
    head = re.split(r"[,:.;(]", brief or "", maxsplit=1)[0]
    head = re.sub(r"\b\d+(\.\d+)?\s*(m|mm|cm|inch)?\b", " ", head, flags=re.I)
    return re.sub(r"\s+", " ", head).strip().lower()


QUERY_PROMPT = """Turn this furniture brief into image-search queries that would
find a clean PRODUCT PHOTO of such a piece (catalogue style: one object, plain
background). Photo archives match on few words, so long queries return
nothing: drop all dimensions, counts and construction detail. Return a json object
{{"queries": [...]}} with exactly 3 queries, each 2 to 3 words:
  1. material + furniture type        (e.g. "oak bookshelf")
  2. style or synonym + furniture type (e.g. "open bookcase")
  3. the bare furniture type           (e.g. "bookshelf")

Brief: {brief}"""


def search_queries(brief: str) -> list[str]:
    queries: list[str] = []
    answer = _chat([{"role": "user", "content": QUERY_PROMPT.format(brief=brief)}],
                   max_tokens=200)
    for q in (answer or {}).get("queries") or []:
        q = str(q).strip()
        if q and q.lower() not in [x.lower() for x in queries]:
            queries.append(q)
    fb = fallback_query(brief)
    if fb and fb not in [x.lower() for x in queries]:
        queries.append(fb)
    return queries[:4]


# --------------------------------------------------------------------------
# providers -- each returns [{thumb, full, page, licence, title, provider}]
# --------------------------------------------------------------------------
def search_openverse(query: str, limit: int) -> list[dict]:
    url = ("https://api.openverse.org/v1/images/?"
           + urllib.parse.urlencode({"q": query, "page_size": limit,
                                     "mature": "false"}))
    data = json.loads(_get(url).decode("utf-8"))
    return [{"thumb": r.get("thumbnail") or r.get("url"), "full": r.get("url"),
             "page": r.get("foreign_landing_url"),
             "licence": f"{r.get('license')} {r.get('license_version') or ''}".strip(),
             "title": r.get("title") or "", "provider": "openverse"}
            for r in data.get("results") or [] if r.get("url")]


def search_commons(query: str, limit: int) -> list[dict]:
    url = ("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6,
        "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime", "iiurlwidth": 768}))
    data = json.loads(_get(url).decode("utf-8"))
    out = []
    for page in ((data.get("query") or {}).get("pages") or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            continue
        meta = info.get("extmetadata") or {}
        out.append({"thumb": info.get("thumburl") or info.get("url"),
                    "full": info.get("url"), "page": info.get("descriptionurl"),
                    "licence": (meta.get("LicenseShortName") or {}).get("value", ""),
                    "title": page.get("title", ""), "provider": "commons"})
    return out


SEARCHERS = {"openverse": search_openverse, "commons": search_commons}


def gather_candidates(queries: list[str], directory: Path,
                      providers: tuple[str, ...] = PROVIDERS) -> list[dict]:
    """Download up to MAX_CANDIDATES distinct thumbnails into `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    found: list[dict] = []
    seen: set[str] = set()
    for query in queries:
        for name in providers:
            if len(found) >= MAX_CANDIDATES:
                return found
            try:
                hits = SEARCHERS[name](query, MAX_CANDIDATES)
            except Exception as exc:  # noqa: BLE001
                print(f"[reference] {name} search failed for '{query}': "
                      f"{type(exc).__name__}", flush=True)
                continue
            for hit in hits:
                if len(found) >= MAX_CANDIDATES:
                    break
                try:
                    data = _get(hit["thumb"])
                except Exception:  # noqa: BLE001
                    continue
                mime = image_mime(data)
                digest = hashlib.sha256(data).hexdigest()
                if not mime or len(data) > MAX_IMAGE_BYTES or digest in seen:
                    continue
                seen.add(digest)
                path = directory / f"cand_{len(found):02d}.{mime.split('/')[1]}"
                path.write_bytes(data)
                found.append({**hit, "query": query, "file": path.name,
                              "sha256": digest, "bytes": len(data)})
    return found


def local_candidates(directory: Path) -> list[dict]:
    """Photos the user dropped into runtime/reference/<oid>/ by hand."""
    out = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            continue
        data = path.read_bytes()
        if not image_mime(data) or len(data) > MAX_IMAGE_BYTES:
            continue
        out.append({"file": path.name, "provider": "local", "title": path.stem,
                    "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                    "licence": "user-supplied", "page": None, "full": None,
                    "query": None})
    return out[:MAX_CANDIDATES]


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
def image_size(dims: list[float]) -> str:
    """Canvas that matches the object's silhouette, so a tall shelf is not
    shrunk into the middle of a square and a long sofa is not cropped."""
    across, up = max(dims[0], dims[1]), dims[2]
    if up > 1.3 * across:
        return "1024x1536"
    if across > 1.3 * up:
        return "1536x1024"
    return "1024x1024"


def generation_prompt(brief: str, dims: list[float], mount: str = "floor") -> str:
    """Catalogue-shot prompt. Everything after the brief is fixed: the image is
    read by a VLM and later conditions TRELLIS, so it must always be one whole
    object, unoccluded, on a plain ground, from a view that shows its depth."""
    view = {"wall": "straight-on front view, very slightly from the side so "
                    "the thickness is visible",
            "ceiling": "three-quarter view from slightly below, the fixture "
                       "hanging from a plain white ceiling",
            "door": "straight-on front view of the closed door leaf alone"
            }.get(mount, "three-quarter front view from slightly above eye level")
    return (
        f"Studio product photograph of exactly one piece of furniture: {brief} "
        f"Overall proportions about {dims[0]:.2f} m wide, {dims[1]:.2f} m deep, "
        f"{dims[2]:.2f} m tall. {view}. The whole object is inside the frame "
        f"with margin on every side, nothing cropped. Plain seamless white "
        f"background, soft even studio lighting, faint contact shadow only. "
        f"The piece is empty and unstyled: no books, no plants, no cushions "
        f"that are not part of it, no props, no people, no room, no text, no "
        f"dimension lines, no watermark. Photorealistic, sharp, true materials.")


def image_endpoints() -> list[dict]:
    """Image endpoints in the order they are tried: primary, then fallback.

    Relay providers lose their upstream channel without warning (the primary
    answered 503 "no available channel" for every image model for a whole
    evening), so one endpoint is not enough to run a batch on. An endpoint
    with no key is simply absent; nothing here is required.
    """
    out = []
    for label, prefix, base_default in (
            ("primary", "IMAGE_API", IMAGE_API_BASE),
            ("fallback", "IMAGE_FALLBACK_API", IMAGE_FALLBACK_API_BASE)):
        key = setting(prefix + "_KEY")
        if not key:
            continue
        base = (setting(prefix + "_BASE", base_default) or base_default).rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"       # providers publish the bare host as often as not
        model_name = "IMAGE_MODEL" if label == "primary" else "IMAGE_FALLBACK_MODEL"
        out.append({"label": label, "key": key, "base": base,
                    "model": setting(model_name, IMAGE_MODEL) or IMAGE_MODEL})
    return out


def _generate_once(endpoint: dict, prompt: str, size: str) -> bytes:
    """One call to one OpenAI-compatible images endpoint; raises on failure."""
    payload = {"model": endpoint["model"], "prompt": prompt, "n": 1, "size": size}
    req = urllib.request.Request(
        endpoint["base"] + "/images/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + endpoint["key"],
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=IMAGE_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    item = (body.get("data") or [{}])[0]
    if item.get("b64_json"):            # some relays answer inline ...
        data = base64.b64decode(item["b64_json"])
    elif item.get("url"):               # ... others with a short-lived URL
        data = _get(item["url"], timeout=120, limit=MAX_GENERATED_BYTES)
    else:
        raise RuntimeError("response carries neither b64_json nor url")
    if not image_mime(data):
        raise RuntimeError("endpoint returned something that is not an image")
    return data


def generate_image(prompt: str, size: str, path: Path) -> dict | None:
    """Render one image, trying each configured endpoint in turn.

    None when no endpoint is configured or all of them fail -- the caller then
    authors from the brief alone.
    """
    endpoints = image_endpoints()
    if not endpoints:
        print("[reference] IMAGE_API_KEY not set (env or runtime/.env); "
              "cannot generate a reference image", flush=True)
        return None
    data, used, failures = None, None, []
    for endpoint in endpoints:
        try:
            data, used = _generate_once(endpoint, prompt, size), endpoint
            break
        except Exception as exc:  # noqa: BLE001
            # HTTPError's str() is the status line only: no key, no request body.
            note = f"{endpoint['label']} ({endpoint['base']}): {type(exc).__name__}: {exc}"
            failures.append(note)
            print(f"[reference] image generation failed on {note}", flush=True)
    if data is None:
        return None
    if failures:
        print(f"[reference] image served by the {used['label']} endpoint", flush=True)
    mime = image_mime(data)
    path = path.with_suffix("." + mime.split("/")[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"file": path.name, "provider": "generated", "model": used["model"],
            "endpoint": used["base"], "endpoint_role": used["label"],
            "failed_endpoints": failures, "prompt": prompt, "size": size,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "licence": "generated", "page": None, "full": None, "query": None,
            "title": "generated reference"}


# --------------------------------------------------------------------------
# selection + reading
# --------------------------------------------------------------------------
SELECT_PROMPT = """You are choosing ONE reference photo for a furniture
modeller, then reading how the piece in it is built.

Requested piece: {brief}
Envelope it must fit (metres): width {w}, depth {d}, height {h}

The {n} images that follow are numbered 0..{last} in order.

A usable reference shows exactly one piece of the requested TYPE, whole and
unoccluded, from the front or a three-quarter view, sharp enough to count its
parts. Reject drawings, catalogue page scans, room scenes where the piece is
small, close-up details, and the wrong furniture type. If none is usable, say
so -- a wrong reference is worse than none.

Return a json object:
{{
  "choice": <index, or -1 if none is usable>,
  "reason": "<one sentence>",
  "view": "front" | "three_quarter" | "side" | "other",
  "design_summary": "<two sentences: what this specific piece looks like>",
  "parts": [
    {{"name": "<snake_case>", "count": <int>, "shape": "<panel|board|leg|rail|cushion|door|drawer|shade|stem|base|other>",
      "note": "<where it sits, relative size, edge treatment>", "curved": <true|false>}}
  ],
  "proportions": ["<measurable ratio, e.g. 'shelf spacing is even, about 1/5 of the height'>"],
  "materials": ["<part group>: <class> <#RRGGBB> <finish>, class one of paint|wood|fabric|velvet|leather|metal|plastic|glass|ceramic|stone|shade, hex sampled from the lit surface in the image"],
  "symmetry": "<left-right | none | radial>"
}}
List every part you can actually see; do not invent hidden ones."""


def select_and_read(candidates: list[dict], directory: Path, brief: str,
                    dims: list[float]) -> dict | None:
    if not candidates:
        return None
    content: list = [{"type": "text", "text": SELECT_PROMPT.format(
        brief=brief, w=dims[0], d=dims[1], h=dims[2],
        n=len(candidates), last=len(candidates) - 1)}]
    for cand in candidates:
        content.append(image_part(directory / cand["file"]))
    answer = _chat([{"role": "user", "content": content}], max_tokens=1600)
    if not answer:
        return None
    try:
        choice = int(answer.get("choice", -1))
    except (TypeError, ValueError):
        choice = -1
    if not 0 <= choice < len(candidates):
        print(f"[reference] no usable photo: {answer.get('reason', '')}", flush=True)
        return {"usable": False, "reason": answer.get("reason", "")}
    return {"usable": True, "choice": choice, "reading": answer}


# --------------------------------------------------------------------------
# public entry
# --------------------------------------------------------------------------
def find_reference_image(oid: str, brief: str, dims: list[float],
                         mode: str = "generate", refresh: bool = False,
                         mount: str = "floor") -> dict | None:
    """Resolve one reference image for `oid`, or None.

    mode: "off"      never look;
          "local"    only photos already in runtime/reference/<oid>/;
          "generate" local photos first, then the image model (default);
          "web"      local photos first, then open-licence web search.

    The result is cached in runtime/reference/<oid>/image_ref.json and reused
    on later runs (the model is not asked twice about the same photo), unless
    `refresh` is set. A cached "nothing usable" is also honoured, so a run
    without network does not re-search every time.
    """
    if mode == "off":
        return None
    directory = REFERENCE / oid
    manifest = directory / "image_ref.json"
    if manifest.is_file() and not refresh:
        try:
            cached = json.loads(manifest.read_text(encoding="utf-8"))
            if cached.get("brief") == brief:
                if not cached.get("usable"):
                    return None
                if (directory / cached["image"]).is_file():
                    cached["path"] = str(directory / cached["image"])
                    return cached
        except Exception:  # noqa: BLE001
            pass

    candidates, base, queries = local_candidates(directory), directory, []
    picked = select_and_read(candidates, base, brief, dims) if candidates else None
    if not (picked and picked.get("usable")) and mode == "web":
        queries = search_queries(brief)
        base = directory / "candidates"
        candidates = gather_candidates(queries, base)
        picked = select_and_read(candidates, base, brief, dims)
    elif not (picked and picked.get("usable")) and mode == "generate":
        # The reader is also the quality check: a render that came out as a
        # room scene or the wrong type is rejected and drawn once more.
        base = directory / "generated"
        candidates, picked = [], None
        prompt, size = generation_prompt(brief, dims, mount), image_size(dims)
        for attempt in range(GENERATE_TRIES):
            made = generate_image(prompt, size, base / f"gen_{attempt:02d}")
            if not made:
                break
            candidates.append(made)
            one = select_and_read([made], base, brief, dims)
            if one and one.get("usable"):
                picked = {**one, "choice": len(candidates) - 1}
                break
            picked = one

    record: dict = {"schema": "codeindoor.reference_image.v1", "id": oid,
                    "brief": brief, "queries": queries,
                    "candidates": candidates, "usable": False}
    if picked and picked.get("usable"):
        cand = candidates[picked["choice"]]
        rel = (base / cand["file"]).relative_to(directory).as_posix()
        record.update({"usable": True, "image": rel, "source": cand,
                       "reading": picked["reading"]})
    elif picked:
        record["reason"] = picked.get("reason", "")
    directory.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    if not record["usable"]:
        return None
    record["path"] = str(directory / record["image"])
    return record


def reading_block(ref: dict) -> str:
    """The VLM's reading of the photo, as text the code-writing call can follow."""
    r = ref.get("reading") or {}
    lines = ["REFERENCE PHOTO (attached). This is a product of the requested "
             "type. Rebuild THIS design -- its part list, proportions and "
             "materials -- scaled to the envelope P. Do not fall back to a "
             "generic version of the furniture type."]
    if r.get("design_summary"):
        lines.append("  what it is: " + str(r["design_summary"]))
    parts = r.get("parts") or []
    if parts:
        lines.append("  visible parts (each must exist in your program, same count):")
        for p in parts:
            curved = "  [curved: use a curve tool]" if p.get("curved") else ""
            lines.append(f"    - {p.get('name')} x{p.get('count', 1)} "
                         f"({p.get('shape')}): {p.get('note', '')}{curved}")
    for key, title in (("proportions", "proportions to reproduce"),
                       ("materials", "materials (a named key if one matches, else "
                                     "'<class>:#RRGGBB' with this colour)")):
        items = r.get(key) or []
        if items:
            lines.append(f"  {title}:")
            lines.extend(f"    - {i}" for i in items)
    if r.get("symmetry"):
        lines.append(f"  symmetry: {r['symmetry']}")
    lines.append("  Name each part after the list above (B.box('shelf_2', ...)), "
                 "so every part of the program maps to a part of the photo.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--object", required=True)
    parser.add_argument("--brief", required=True)
    parser.add_argument("--dims", default="1.0,0.4,1.0")
    parser.add_argument("--mode", default="generate",
                        choices=("generate", "local", "web"))
    parser.add_argument("--mount", default="floor")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    dims = [float(v) for v in args.dims.split(",")]
    ref = find_reference_image(args.object, args.brief, dims, args.mode, args.refresh,
                               args.mount)
    print("REFERENCE_IMAGE=" + json.dumps(
        {"id": args.object, "usable": bool(ref),
         "image": (ref or {}).get("path"),
         "source": ((ref or {}).get("source") or {}).get("page")},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
