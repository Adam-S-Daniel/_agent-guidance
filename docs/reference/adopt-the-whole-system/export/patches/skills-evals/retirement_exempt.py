"""Content tiers (2026-10-29): the automatic retirement never removes a core item.

The owner's core items (bootstrap/content-catalog.json, tier `core`) are
what the rest of the system depends on, so the loop that retires a skill or
a guidance section with no measured benefit must skip them. The smallest
change that does it, on the substituted tree:

- evals/retirement-exempt.yml, written here from the catalog: the core
  skills and the core guidance sections (by eval-coverage.yml row id);
- compute_gates.py's finalize() turns a core item's `retire` verdict into
  `retire-exempt` (its raw_verdict is kept) BEFORE it picks the one
  retirement per evaluation, so a core item never takes the slot of one
  that may retire, and plan_week.py, which holds back only `retire` and
  `retire-pending`, may still improve it;
- retire_skill.py's plan() (skills) and guidance_plan() (`--target
  guidance`) answer "none" for a core item, as a second guard for a gates
  file written some other way.

A guidance section's shorten-or-merge proposal (an issue, never a removal)
is unchanged. test/issues/test_issue_retirement_exempt.py (overlay) tests
both halves.
"""

from __future__ import annotations

import json
from pathlib import Path

EXEMPT = "evals/retirement-exempt.yml"

HEADER = """# Core items (the adoption kit's content tiers): the automatic retirement
# never removes these. compute_gates.py turns a `retire` verdict for one of
# them into `retire-exempt`, and retire_skill.py opens nothing for it. The
# kit writes this file from bootstrap/content-catalog.json; skills by folder
# name, guidance sections by their agents-md/eval-coverage.yml row id.
"""

COMPUTE_EDITS = [
    ('''def finalize(skills: dict) -> None:
    """One retirement per evaluation, in place: of the skills whose verdict
    is `retire`, the one with the most arm runs (then the first by name)
    keeps it."""
    for verdict in skills.values():
        verdict["raw_verdict"] = verdict["verdict"]
''', '''def load_exempt(evals_dir: Path) -> dict:
    """The core items the retirement never removes: {"skills": set,
    "guidance": set} from <evals_dir>/retirement-exempt.yml (none when the
    file is absent). ValueError for a file that is not two lists of names."""
    path = Path(evals_dir) / "retirement-exempt.yml"
    if not path.is_file():
        return {"skills": set(), "guidance": set()}
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or set(doc) != {"skills", "guidance"} or not all(
            isinstance(doc[k], list) and all(isinstance(n, str) for n in doc[k]) for k in doc):
        raise ValueError(f"{path.name}: want two lists of names, skills and guidance")
    return {k: set(doc[k]) for k in ("skills", "guidance")}


def finalize(skills: dict, exempt=frozenset()) -> None:
    """One retirement per evaluation, in place: of the skills whose verdict
    is `retire`, the one with the most arm runs (then the first by name)
    keeps it. A core item (`exempt`, retirement-exempt.yml) never retires:
    its `retire` becomes `retire-exempt` first."""
    for verdict in skills.values():
        verdict["raw_verdict"] = verdict["verdict"]
    for name in sorted(skills):
        if name in exempt and skills[name]["verdict"] == "retire":
            skills[name]["verdict"] = "retire-exempt"
            skills[name]["reason"] = (f"{name} is a core item (evals/retirement-exempt.yml), "
                                      "which the automatic retirement never removes")
'''),
    ('''    finalize(skills)
    doc = {"schema_version": 1, "as_of": as_of,''',
     '''    finalize(skills, load_exempt(evals_dir)["skills"])
    doc = {"schema_version": 1, "as_of": as_of,'''),
    ('''    finalize(sections)
    return {"guidance": sections, "guidance_policy": policy,''',
     '''    finalize(sections, load_exempt(evals_dir)["guidance"])
    return {"guidance": sections, "guidance_policy": policy,'''),
]

RETIRE_EDITS = [
    ('''    skill = retiring(gates)
    mode = mode_of(gates)
    if skill is None:
        return {"action": "none", "mode": mode, "reason": "no skill has a retire verdict"}
''', '''    skill = retiring(gates)
    mode = mode_of(gates)
    if skill is None:
        return {"action": "none", "mode": mode, "reason": "no skill has a retire verdict"}
    if skill in compute_gates.load_exempt(REPO_ROOT / "evals")["skills"]:
        return {"action": "none", "mode": mode, "skill": skill,
                "reason": f"{skill} is a core item (evals/retirement-exempt.yml), never retired automatically"}
'''),
    ('''    else:
        kind = "retire"
        if mode == "auto" and "auto" not in allowed and fallback:
            mode_reason = GUIDANCE_NO_CREDENTIAL''',
     '''    elif section in compute_gates.load_exempt(REPO_ROOT / "evals")["guidance"]:
        return {"action": "none", "mode": mode, "section": section,
                "reason": f"{section} is a core item (evals/retirement-exempt.yml), never retired automatically"}
    else:
        kind = "retire"
        if mode == "auto" and "auto" not in allowed and fallback:
            mode_reason = GUIDANCE_NO_CREDENTIAL'''),
]


def _sub(root: Path, rel: str, pairs) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"retirement_exempt: expected 1 occurrence in {rel}, found {n}: {old[:70]!r}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def apply(root: Path, ctx) -> None:
    catalog = json.loads((ctx["kit"] / "bootstrap" / "content-catalog.json").read_text(encoding="utf-8"))
    core = {kind: sorted(i["id"] for i in catalog["items"] if i["tier"] == "core" and i["kind"] == kind)
            for kind in ("skill", "guidance")}
    if not core["skill"] or not core["guidance"]:
        raise SystemExit("retirement_exempt: the catalog lists no core skill or guidance section")
    body = "skills:\n" + "".join(f"  - {n}\n" for n in core["skill"])
    body += "guidance:\n" + "".join(f"  - {n}\n" for n in core["guidance"])
    (root / EXEMPT).write_text(HEADER + body, encoding="utf-8")
    _sub(root, "scripts/compute_gates.py", COMPUTE_EDITS)
    _sub(root, "scripts/retire_skill.py", RETIRE_EDITS)
