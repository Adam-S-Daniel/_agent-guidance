"""yaml_parses: every file matching `paths` parses as YAML."""
from __future__ import annotations

from pathlib import Path

import yaml

from .common import ScorerError, expand


def score(check: dict, workspace: Path, **_) -> tuple[bool, str]:
    files = expand(workspace, check["paths"])
    bad = []
    for f in files:
        try:
            list(yaml.safe_load_all(f.read_text(encoding="utf-8")))
        except (yaml.YAMLError, UnicodeDecodeError):
            bad.append(str(f.relative_to(workspace.resolve())))
        except OSError as exc:
            raise ScorerError(f"cannot read a file: {type(exc).__name__}") from None
    return (not bad, f"{len(files)} files, {len(bad)} unparsable")
