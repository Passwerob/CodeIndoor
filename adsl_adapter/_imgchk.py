import bpy, os, json
missing = 0; external = 0; packed = 0
for im in bpy.data.images:
    if im.packed_file: packed += 1; continue
    if im.filepath and not im.filepath.startswith('<'):
        p = bpy.path.abspath(im.filepath)
        external += 1
        if not os.path.exists(p): missing += 1
print("IMGCHK=" + json.dumps({"images": len(bpy.data.images), "packed": packed,
                              "external": external, "missing": missing,
                              "size_mb": round(os.path.getsize(bpy.data.filepath)/1e6, 1)}))
