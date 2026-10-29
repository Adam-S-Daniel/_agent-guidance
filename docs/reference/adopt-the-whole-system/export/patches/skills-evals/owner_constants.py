"""DECISIONS C2 and C10 for skills-evals' miner test: owner constants get role names.

`test/issues/test_issue_real_work_miner.py` binds the two owner logins to
module-level names spelled after the people (one `NAME, NAME = "<login>",
"<login>"` line). After substitution the values are the kit's invented
owners, and the names are renamed to `OWNER` and `OWNER_TWO`. The names are
found from the parsed module (the one module-level tuple assignment whose
values are exactly `kit_owners`), never spelled here, and every `Name` node
that uses them is rewritten at its AST position, f-string fields included.
Then the one assertion whose list follows the logins' sort order is
reordered: the substituted names sort the other way round
("example-adopter-two/" before "example-adopter/"). Runs on the substituted
tree.
"""

from __future__ import annotations

import ast
from pathlib import Path

REL = "test/issues/test_issue_real_work_miner.py"
NEW_NAMES = ("OWNER", "OWNER_TWO")
SORTED_OLD = ('        self.assertEqual(repos, [f"{OWNER}/_agent-guidance", f"{OWNER}/skills-evals",\n'
              '                                 f"{OWNER_TWO}/example-site-b"])\n')
SORTED_NEW = ('        self.assertEqual(repos, [f"{OWNER_TWO}/example-site-b", f"{OWNER}/_agent-guidance",\n'
              '                                 f"{OWNER}/skills-evals"])\n')


def _owner_names(tree: ast.Module, owners: list) -> tuple:
    found = []
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target, value = node.targets[0], node.value
        if not (isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple)):
            continue
        values = [v.value if isinstance(v, ast.Constant) else None for v in value.elts]
        if values == owners and all(isinstance(t, ast.Name) for t in target.elts):
            found.append(tuple(t.id for t in target.elts))
    if len(found) != 1:
        raise RuntimeError(f"{REL}: expected one owner-constant assignment, found {len(found)}")
    return found[0]


def apply(root: Path, ctx) -> None:
    path = root / REL
    text = path.read_text(encoding="utf-8")
    owners = list(ctx["common"]["kit_owners"])
    tree = ast.parse(text)
    rename = dict(zip(_owner_names(tree, owners), NEW_NAMES))
    taken = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    clash = taken & set(NEW_NAMES) - set(rename)
    if clash:
        raise RuntimeError(f"{REL}: {sorted(clash)} already used")
    lines = text.splitlines(keepends=True)
    spots = sorted(((n.lineno, n.col_offset, n.end_col_offset, rename[n.id])
                    for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in rename),
                   reverse=True)
    for lineno, start, end, new in spots:
        line = lines[lineno - 1]
        # col offsets are UTF-8 byte offsets
        raw = line.encode("utf-8")
        lines[lineno - 1] = (raw[:start] + new.encode() + raw[end:]).decode("utf-8")
    out = "".join(lines)
    if out.count(SORTED_OLD) != 1:
        raise RuntimeError(f"{REL}: expected the one sorted-repos assertion, found {out.count(SORTED_OLD)}")
    out = out.replace(SORTED_OLD, SORTED_NEW)
    ast.parse(out)
    path.write_text(out, encoding="utf-8")
