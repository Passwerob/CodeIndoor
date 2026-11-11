"""Parametric material keys: `<class>:<#hex>[:<roughness>]`.

Why this exists
---------------
The forge offered a fixed palette of fourteen named materials. A reference
image of a white-lacquered shelf or a sage-green sofa had nothing to map to,
so the Agent picked the nearest name and the result was the right shape in
the wrong finish. Most finishes do not need a texture file at all: a class
(how the surface responds to light) plus a colour is the whole description,
and Blender's native Principled BSDF with procedural grain/weave renders it.

So a material is now also a *parameter*: "paint:#F3F1EC", "wood:#B58A5A",
"fabric:#8FA08A:0.9". Changing a finish is editing a string in the program,
which is the same kind of edit as changing a height.

This module is the grammar only -- pure Python, no bpy -- so the orchestrator
can reject a malformed key before Blender starts, and tests can run anywhere.
forge_runner.py turns a parsed spec into a node tree.
"""
from __future__ import annotations

import re

# class -> default roughness. The class decides the shading model; the colour
# and (optionally) roughness come from the key.
CLASSES = {
    "paint": 0.35,     # lacquer, painted timber / MDF, enamel
    "plastic": 0.50,
    "metal": 0.30,     # colour is the metal's tint: brass, steel, black iron
    "glass": 0.03,     # clear or tinted, transmissive
    "ceramic": 0.30,
    "stone": 0.25,     # marble, terrazzo, concrete (raise the roughness)
    "wood": 0.38,      # procedural grain around the given colour
    "fabric": 0.80,    # procedural weave around the given colour
    "velvet": 0.85,    # fabric with strong sheen
    "leather": 0.45,
    "shade": 0.75,     # lamp shade: the colour also glows faintly
}

_KEY = re.compile(r"^([a-z]+):#([0-9a-fA-F]{6})(?::([0-9]*\.?[0-9]+))?$")


def looks_parametric(key: str) -> bool:
    """True for anything shaped like `word:#...`, valid or not -- used to tell
    a mistyped parametric key from an ordinary string in the program."""
    return isinstance(key, str) and re.match(r"^[A-Za-z_]+:#", key) is not None


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def parse(key: str) -> dict | None:
    """Parse a parametric key. Returns None when `key` is not one.

    The hex colour is what a person would sample from a photo (sRGB); Blender
    wants linear values, so the conversion happens here, once.
    """
    if not isinstance(key, str):
        return None
    m = _KEY.match(key.strip())
    if not m or m.group(1) not in CLASSES:
        return None
    kind, hexa, rough = m.group(1), m.group(2).upper(), m.group(3)
    srgb = tuple(int(hexa[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    roughness = CLASSES[kind] if rough is None else float(rough)
    if not 0.0 <= roughness <= 1.0:
        return None
    return {"key": key.strip(), "kind": kind, "hex": "#" + hexa,
            "rgb": tuple(round(srgb_to_linear(c), 5) for c in srgb),
            "roughness": roughness,
            "name": f"{kind.title()} | #{hexa}"
                    + ("" if rough is None else f" r{roughness:g}")}


def explain(key: str) -> str:
    """Why a parametric-looking key was rejected, as Agent feedback."""
    return (f"'{key}' is not a valid material. Use a named key, or the form "
            f"<class>:#RRGGBB[:roughness 0-1] with class one of "
            f"{', '.join(sorted(CLASSES))} -- e.g. 'paint:#F3F1EC', "
            f"'wood:#B58A5A', 'fabric:#8FA08A:0.9'.")


def shades(rgb: tuple, dark: float, light: float) -> tuple[tuple, tuple]:
    """Dark and light variants of a colour, for a grain or weave ramp."""
    return (tuple(max(0.0, c * dark) for c in rgb),
            tuple(min(1.0, c * light) for c in rgb))
