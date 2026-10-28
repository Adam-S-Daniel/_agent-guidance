"""fixture.yaml schema (unknown keys refused), loading and the fixture digest."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import jsonschema
import yaml

MODEL_ID = re.compile(r"^claude-[a-z0-9][a-z0-9.-]{0,62}$")
ID = {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,62}\\Z"}
PATHS = {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}

CHECK = {
    "type": "object",
    "required": ["id", "type"],
    "properties": {
        "id": ID,
        "type": {"enum": ["yaml_parses", "command_succeeds", "file_unchanged"]},
        "paths": PATHS,
        "command": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
    "allOf": [
        {"if": {"properties": {"type": {"const": "command_succeeds"}}},
         "then": {"required": ["command"], "not": {"required": ["paths"]}}},
        {"if": {"properties": {"type": {"enum": ["yaml_parses", "file_unchanged"]}}},
         "then": {"required": ["paths"], "not": {"required": ["command"]}}},
    ],
}

FIXTURE_SCHEMA = {
    "type": "object",
    "required": ["skill", "registry", "prompt", "arms", "judge", "objective_checks"],
    "additionalProperties": False,
    "properties": {
        "skill": ID,
        "registry": {"type": "string", "pattern": r"^(<you>|[A-Za-z0-9][A-Za-z0-9-]*)/[A-Za-z0-9._-]+\Z"},
        # A prompt that starts with "-" could be read as a CLI flag.
        "prompt": {"type": "string", "pattern": r"^[^-\s][\s\S]*\Z", "maxLength": 4000},
        "arms": {
            "type": "object",
            "required": ["with_skill", "without_skill"],
            "additionalProperties": False,
            "properties": {
                "with_skill": {"type": "object", "required": ["install"],
                               "additionalProperties": False,
                               "properties": {"install": {"const": "copy"}}},
                "without_skill": {"type": "object", "required": ["install"],
                                  "additionalProperties": False,
                                  "properties": {"install": {"const": "none"}}},
            },
        },
        "judge": {
            "type": "object", "required": ["weights"], "additionalProperties": False,
            "properties": {"weights": {
                "type": "object", "minProperties": 1,
                "propertyNames": {"pattern": "^[a-z][a-z0-9_]{0,31}\\Z"},
                "additionalProperties": {"type": "number", "exclusiveMinimum": 0, "maximum": 1}}},
        },
        "objective_checks": {"type": "array", "minItems": 1, "items": CHECK},
        "limits": {
            "type": "object", "additionalProperties": False,
            "properties": {"max_turns": {"type": "integer", "minimum": 1, "maximum": 200},
                           "timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 3600}},
        },
        # A fixture that pins a model must say why, right beside the pin.
        "model": {"type": "string", "pattern": MODEL_ID.pattern[:-1] + r"\Z"},
        "model_reason": {"type": "string", "minLength": 5},
    },
    "dependentRequired": {"model": ["model_reason"], "model_reason": ["model"]},
}


class FixtureError(Exception):
    pass


def validate_fixture(doc: object) -> dict:
    if not isinstance(doc, dict):
        raise FixtureError("fixture.yaml must be a mapping")
    try:
        jsonschema.validate(doc, FIXTURE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise FixtureError(f"fixture invalid at {list(exc.absolute_path)}: {exc.validator}") from None
    ids = [c["id"] for c in doc["objective_checks"]]
    if len(set(ids)) != len(ids):
        raise FixtureError("duplicate check id")
    total = sum(doc["judge"]["weights"].values())
    if abs(total - 1.0) > 1e-6:
        raise FixtureError("judge weights must sum to 1")
    return doc


def load_fixture(fixture_dir: Path) -> dict:
    try:
        doc = yaml.safe_load((fixture_dir / "fixture.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FixtureError(f"cannot read fixture.yaml: {type(exc).__name__}") from None
    if not (fixture_dir / "seed").is_dir():
        raise FixtureError("fixture has no seed/ directory")
    return validate_fixture(doc)


def fixture_digest(fixture_dir: Path) -> str:
    """sha256 over fixture.yaml, seed/ and checks/: sorted relative paths and
    file bytes. Symlinks are hashed by target, never followed."""
    h = hashlib.sha256()
    for part in ("fixture.yaml", "seed", "checks"):
        base = fixture_dir / part
        if not base.exists() and not base.is_symlink():
            continue
        files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if not p.is_dir())
        for p in files:
            rel = p.relative_to(fixture_dir).as_posix()
            h.update(rel.encode() + b"\0")
            h.update(("L:" + str(p.readlink())).encode() if p.is_symlink() else p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()
