"""P1 — ProgramComponent: a structured, parametric, semantic aDSL component.

IMPORTANT: this does NOT stuff arbitrary Blender Python strings into a JSON
field. A ProgramComponent stores a *program reference* (a registered builder
name), a typed parameter schema, concrete values, a local frame, dependencies
and upstream provenance. The executable code lives in part_programs.py.

Pure-Python (no bpy import), so it can be unit-tested outside Blender.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str                 # "float" | "vec3" | "color" | "int"
    default: Any
    unit: str = "m"
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""

    def validate(self, value: Any) -> None:
        if self.kind in ("float", "int") and isinstance(value, (int, float)):
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.name}={value} < min {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{self.name}={value} > max {self.maximum}")


@dataclass
class ProgramComponent:
    component_id: str                      # = SceneCode PartPlan.name
    program: str                           # registered builder name, NOT code
    params: dict[str, ParamSpec] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    local_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    depends_on: tuple[str, ...] = ()
    upstream_refs: dict[str, Any] = field(default_factory=dict)
    revisions: list[dict] = field(default_factory=list)
    # Provenance of the AGENT-GENERATED program (a file reference, never code).
    source_path: str | None = None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        for key, spec in self.params.items():
            self.values.setdefault(key, spec.default)

    def edit(self, name: str, value: Any) -> dict:
        """A localized, typed parameter edit (Test A / Test C)."""
        if name not in self.params:
            raise KeyError(f"{self.component_id} has no parameter '{name}'")
        spec = self.params[name]
        spec.validate(value)
        old = self.values[name]
        self.values[name] = value
        revision = {"param": name, "from": old, "to": value}
        self.revisions.append(revision)
        return revision

    def to_dict(self) -> dict[str, Any]:
        """Structured AST node: schema + values + provenance, never raw code."""
        return {
            "component_id": self.component_id,
            "program": self.program,
            "params": {k: {"kind": s.kind, "default": s.default,
                           "unit": s.unit, "min": s.minimum, "max": s.maximum}
                       for k, s in self.params.items()},
            "values": dict(self.values),
            "local_origin": list(self.local_origin),
            "depends_on": list(self.depends_on),
            "upstream_refs": dict(self.upstream_refs),
            "revisions": list(self.revisions),
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
        }


# Registered part-wise builders. The actual implementations are in
# part_programs.py (Blender side); this registry is the stable contract.
BUILDER_NAMES = {
    "rounded_panel",   # frames, bases, armrests: bevel + weighted normals
    "upholstered_cushion",  # soft cushion: bulge + piping seam
    "tapered_leg",     # lathed / conical wooden leg
    "piping_seam",     # curve-based seam
}


def make_schema(kind: str) -> dict[str, ParamSpec]:
    common = [
        ParamSpec("width", "float", 0.5, minimum=0.01, maximum=3.0, description="X extent"),
        ParamSpec("depth", "float", 0.5, minimum=0.01, maximum=3.0, description="Y extent"),
        ParamSpec("height", "float", 0.2, minimum=0.01, maximum=2.0, description="Z extent"),
        ParamSpec("bevel", "float", 0.02, minimum=0.001, maximum=0.2, description="edge radius"),
    ]
    schemas = {s.name: s for s in common}
    if kind == "upholstered_cushion":
        schemas["bulge"] = ParamSpec("bulge", "float", 0.035, minimum=0.0, maximum=0.15,
                                     description=" cushion puffiness")
        schemas["seam"] = ParamSpec("seam", "float", 0.006, minimum=0.0, maximum=0.03,
                                    description="piping radius")
    if kind == "tapered_leg":
        schemas["radius_top"] = ParamSpec("radius_top", "float", 0.035, minimum=0.005, maximum=0.15)
        schemas["radius_bottom"] = ParamSpec("radius_bottom", "float", 0.022, minimum=0.003, maximum=0.15)
    schemas["color"] = ParamSpec("color", "color", (0.42, 0.46, 0.52), unit="rgb")
    schemas["roughness"] = ParamSpec("roughness", "float", 0.72, minimum=0.0, maximum=1.0)
    return schemas
