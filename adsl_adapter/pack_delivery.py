"""Pack every external dependency into a self-contained delivery .blend.

The working file keeps its textures as external references into
`E:/3DUse/assets/external/...` -- that is fast to rebuild and keeps diffs
small, but it is useless the moment it leaves this machine: 474 of 495 images
would render as pink placeholders. This step is the handoff: it opens the
built scene, packs every file-backed datablock, and writes a sibling file.

Run inside Blender:

    blender -b --python adsl_adapter/pack_delivery.py
    blender -b --python adsl_adapter/pack_delivery.py -- --verify <packed.blend>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy

ROOT = Path(r"E:/3DUse/SceneCode_audit")
ASSETS = ROOT / "runtime/outputs/living_room/assets"
SRC = ASSETS / "living_room_assets.blend"
DST = ASSETS / "living_room_assets_packed.blend"
REPORT = ROOT / "runtime/outputs/living_room/assets/PACK_REPORT.json"


def _argv() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def scan() -> dict:
    """Inventory the file-backed datablocks and how many are already packed."""
    ext, packed, missing = [], 0, []
    external_bytes = 0
    for img in bpy.data.images:
        if img.source != "FILE":
            continue
        if img.packed_file is not None:
            packed += 1
            continue
        p = Path(bpy.path.abspath(img.filepath))
        ext.append(str(p))
        if p.is_file():
            external_bytes += p.stat().st_size
        else:
            missing.append(str(p))
    libs = [lib.filepath for lib in bpy.data.libraries if lib.filepath]
    sounds = [s.name for s in bpy.data.sounds if s.filepath]
    fonts = [f.name for f in bpy.data.fonts if f.filepath and f.packed_file is None]
    return {
        "images_external": len(ext),
        "images_packed": packed,
        "external_bytes": external_bytes,
        "missing": missing,
        "linked_libraries": libs,
        "external_sounds": sounds,
        "external_fonts": fonts,
    }


def purge_orphans() -> dict:
    """Drop datablocks nothing uses before packing.

    The working file accumulates orphans every time the assemble step appends a
    library item: 437 materials exist but only 167 are on a slot, and the 270
    dead ones still hold image users, so pack_all was embedding 217 duplicate
    texture datablocks nobody can see (783 MB of source files for a scene whose
    visible materials need 350 MB). Purging first is what makes the delivery
    file shippable rather than merely self-contained.
    """
    count = lambda: (len(bpy.data.materials), len(bpy.data.images))
    mats, imgs = count()
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True,
                                   do_recursive=True)
    mats2, imgs2 = count()
    print(f"[pack] purge orphans: materials {mats} -> {mats2}, images {imgs} -> {imgs2}",
          flush=True)
    return {"materials_before": mats, "materials_after": mats2,
            "images_before": imgs, "images_after": imgs2}


def dedupe_images() -> dict:
    """Collapse image datablocks that read the same file with the same settings.

    Append-based assembly re-imports the shared PBR sets once per object, so the
    257 surviving images still point at only 99 distinct files (158 redundant
    slots). Every duplicate is embedded separately by pack_all, which is where
    most of the delivery size came from -- 148 MB of unique pixels cost 346 MB.

    Merge only on an exact (path, colour space, alpha mode) match: two datablocks
    of the same file with a different colour space are a normal-map/base-colour
    pair and must stay apart.
    """
    groups: dict[tuple, list] = {}
    for img in bpy.data.images:
        if img.source != "FILE":
            continue
        key = (bpy.path.abspath(img.filepath), img.colorspace_settings.name,
               img.alpha_mode, img.file_format)
        groups.setdefault(key, []).append(img)

    merged = 0
    for key, imgs in groups.items():
        if len(imgs) < 2:
            continue
        # Keep the one with the most users; it is normally the first import.
        imgs.sort(key=lambda i: -i.users)
        keep = imgs[0]
        for dup in imgs[1:]:
            dup.user_remap(keep)
            merged += 1
            bpy.data.images.remove(dup)

    print(f"[pack] dedupe images: {sum(len(v) for v in groups.values())} -> "
          f"{len(bpy.data.images)} datablocks ({merged} merged)", flush=True)
    return {"merged": merged,
            "groups_before": len(groups),
            "datablocks_before": sum(len(v) for v in groups.values())}


def pack() -> dict:
    bpy.ops.wm.open_mainfile(filepath=str(SRC))
    purged = purge_orphans()
    deduped = dedupe_images()
    before = scan()
    print(f"[pack] before: {before['images_external']} external / "
          f"{before['images_packed']} packed / "
          f"{before['external_bytes'] / 1048576:.1f} MB on disk", flush=True)
    if before["missing"]:
        # Packing cannot invent bytes. Fail loudly rather than ship a file that
        # renders pink in one corner.
        for m in before["missing"][:10]:
            print(f"[pack] MISSING {m}", flush=True)
        raise SystemExit("pack_delivery: missing source files, refusing to pack")

    # Pack all: images, fonts, sounds and any linked libraries.
    bpy.ops.file.pack_all()
    if bpy.data.libraries:
        bpy.ops.file.pack_libraries()

    after = scan()
    print(f"[pack] after: {after['images_external']} external / "
          f"{after['images_packed']} packed", flush=True)
    if after["images_external"] or after["linked_libraries"]:
        raise SystemExit("pack_delivery: something stayed external after pack_all")

    bpy.ops.wm.save_as_mainfile(filepath=str(DST), compress=True)
    mb = DST.stat().st_size / 1048576
    print(f"[pack] wrote {DST.name} ({mb:.1f} MB, {after['images_packed']} images embedded)",
          flush=True)

    report = {
        "source": str(SRC),
        "delivery": str(DST),
        "delivery_mb": round(mb, 1),
        "images_total": after["images_packed"],
        "images_were_external": before["images_external"],
        "external_source_mb": round(before["external_bytes"] / 1048576, 1),
        "orphans": purged,
        "dedupe": deduped,
        "missing": before["missing"],
        "linked_libraries": after["linked_libraries"],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PACK=" + json.dumps(report, ensure_ascii=False), flush=True)
    return report


def verify(path: Path) -> dict:
    """Reopen the delivery file and prove nothing points outside it."""
    bpy.ops.wm.open_mainfile(filepath=str(path))
    state = scan()
    ok = not state["images_external"] and not state["linked_libraries"] and not state["missing"]
    print(f"[verify] images packed={state['images_packed']} external={state['images_external']} "
          f"missing={len(state['missing'])} libs={len(state['linked_libraries'])}", flush=True)
    # A packed image still reports a filepath; what matters is that reload reads
    # the packed bytes. Force a reload and make sure no image failed to load.
    failed = [i.name for i in bpy.data.images
              if i.source == "FILE" and i.packed_file is not None and i.size[0] == 0]
    if failed:
        ok = False
        print(f"[verify] failed to load: {failed[:6]}", flush=True)
    print("VERIFY=" + json.dumps({"ok": ok, **{k: v for k, v in state.items()
                                               if k != "missing"}}, ensure_ascii=False), flush=True)
    return {"ok": ok, **state}


def main() -> None:
    args = _argv()
    if args and args[0] == "--verify":
        verify(Path(args[1]) if len(args) > 1 else DST)
    else:
        pack()


if __name__ == "__main__":
    main()
