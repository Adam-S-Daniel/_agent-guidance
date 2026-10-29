"""Live output links the adopter's own docs, not the source owner's issues.

`scripts/sync.sh` writes every sync pull request body, in every fleet repo,
and the body linked an issue in the source owner's `_agent-guidance`
(a P2 citation, so substitution left it pointing there). An adopter's bot
would put the source owner's issue into each of the adopter's pull
requests. The link now names the README section of the adopter's own
`_agent-guidance` that records the same decision ("The CLAUDE.md bridge"),
built from the variables the body already uses for its first link. The
test that pinned the issue URL asserts the section link instead, and that
no issue link is left. Runs on the substituted tree.
"""

from __future__ import annotations

from pathlib import Path

EDITS = {
    "scripts/sync.sh": [(
        "is absent; the fleet keeps the bridge for older CLIs pending the owner\n"
        "decision in [#172](https://github.com/Adam-S-Daniel/_agent-guidance/issues/172). An\n",
        "is absent; the fleet keeps the bridge for older CLIs pending the owner\n"
        "decision recorded in [The CLAUDE.md bridge](https://github.com/${ORG}/${SELF_REPO}#the-claudemd-bridge). An\n",
    )],
    "test/run-tests.sh": [(
        '    assert_prose_contains "$pr_body_dir/protorg_repo-protected.body" '
        '"https://github.com/Adam-S-Daniel/_agent-guidance/issues/172" '
        '"protected: PR body links the owner bridge decision"\n',
        '    assert_contains "$pr_body_dir/protorg_repo-protected.body" '
        '"_agent-guidance#the-claudemd-bridge)" '
        '"protected: PR body links the bridge decision in this repo\'s README"\n'
        '    assert_not_contains "$pr_body_dir/protorg_repo-protected.body" "/issues/172" '
        '"protected: PR body links no source-owner issue"\n',
    )],
}


def apply(root: Path, ctx) -> None:
    for rel, edits in EDITS.items():
        path = root / rel
        text = path.read_text(encoding="utf-8")
        for old, new in edits:
            if text.count(old) != 1:
                raise RuntimeError(f"{rel}: expected one occurrence of {old[:60]!r}, found {text.count(old)}")
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
