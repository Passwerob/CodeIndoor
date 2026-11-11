"""Run the scene-scope hard gate against the current assembled room."""
import json
import sys
from pathlib import Path

ROOT = Path(r"E:/3DUse/SceneCode_audit")
sys.path.insert(0, str(ROOT / "adsl_adapter"))
sys.path.insert(0, str(ROOT.parent / "FineScene" / "src"))

import assemble_scene as asm  # noqa: E402

ASSETS = ROOT / "runtime/outputs/living_room/assets"
layout = json.loads((ROOT / "runtime/outputs/scene_layout/layout_finalized.json")
                    .read_text(encoding="utf-8"))["layout"]
views = sorted(ASSETS.glob("v2_view_*.png"))
audit = asm.scene_visual_audit(views, layout, ASSETS / "scene_visual_qa.json")
print("SCENE_GATE=" + json.dumps(audit, ensure_ascii=False, indent=2))
