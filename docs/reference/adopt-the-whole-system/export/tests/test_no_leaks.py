"""Leak scan over every file of the kit.

Two lists, two rules:

* Public names (`leak_scan` in manifests/_common.yml): the source owners'
  GitHub logins, sites, machines, claude.ai and Anthropic ids, numeric
  noreply ids, the left-out platform repo, email addresses. Each base64 or
  hex run that decodes to printable text is searched too (a hit is named
  "<token> (encoded)"), and so is it for the private list. No file under
  KIT may hold one, except inside a protected span (a P2 citation or a
  `keep` literal, found with the same rules export.py uses), in this folder's
  manifests (export/manifests/*.yml: they are the substitution rules and the
  exact source text their edits match), or as a hit ALLOWED names below, by
  file and count. A new hit fails, and so does an allowance that no longer
  matches, so the list stays exact.
* Private details (the owner's personal denylist): never in the kit, so the
  list cannot be either. It is read at run time from PRIVACY_DENYLIST
  (newline-separated regular expressions) or PRIVACY_DENYLIST_FILE (a path to
  a file of them, outside the repo), the same mechanism and format as the
  skills registry's scripts/check_privacy_denylist.py: one pattern per line,
  `#` comments, matched case-insensitively. Every file's path and text is
  scanned, the manifests included, with no allowance and no protected span.
  A hit is reported as `path: pattern #N` (or `<file #K>` when the path
  itself matches), never the matched text or the pattern. With no denylist
  the test FAILS when CI=true (a green scan that checked nothing is worse
  than a red one) and is skipped locally, where a contributor may not hold
  the list.

Run: python3 -m pytest KIT/export/tests -q
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
EXPORT_DIR = HERE.parent
KIT = EXPORT_DIR.parent
STARTER = KIT / "starter"

_spec = importlib.util.spec_from_file_location("kit_export", EXPORT_DIR / "export.py")
export = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("kit_export", export)
_spec.loader.exec_module(export)

_COMMON = export.load_common()
# (name, pattern). Each is searched on its own, so one span can hit several.
TOKENS = [(t["name"], re.compile(t["regex"])) for t in _COMMON["leak_scan"]]

# Hits outside a protected span that are allowed, by KIT-relative file and
# token, with the reason. Keep each entry exact: the test fails when a count
# changes in either direction. Files under MANIFESTS_DIR need no entry.
ALLOWED: dict[str, dict[str, int]] = {
    # skills-evals: test data for a CLAUDE.md whose body is the import line
    # `@AGENTS.md`, written right after a newline escape, so the escape's
    # letter and the import read as an address. It is a file import, not an
    # email.
    "starter/skills-evals/test/issues/test_issue_real_work_scaffold.py": {"email": 1},
    "starter/skills-evals/test/issues/test_issue_repo_tests.py": {"email": 1},
}
MANIFESTS_DIR = "export/manifests/"

SKIP_PARTS = {"__pycache__", ".pytest_cache", "node_modules", ".git"}


def _protector():
    rules = list(_COMMON.get("protect") or [])
    for manifest in export.load_manifests().values():
        rules += list(manifest.get("protect") or [])
    return export.Protector.from_config(rules)


# Encoded runs: a token can hide in a base64 or hex literal (a test's Basic
# credential once decoded to the source owner's first name). Each run is
# decoded and, when the bytes are printable text, searched like the file.
_B64_RUN = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{8,}={0,2}(?![A-Za-z0-9+/=_-])")
_HEX_RUN = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){4,}(?![0-9A-Fa-f])")
_PRINTABLE = re.compile(r"[\t\n\r\x20-\x7e\u00a0-\uffff]+")


def decoded_runs(text: str):
    """(start, end, decoded text) for each base64 or hex run that decodes to printable text."""
    import base64
    import binascii
    for rx, kind in ((_HEX_RUN, "hex"), (_B64_RUN, "base64")):
        for m in rx.finditer(text):
            run = m.group(0)
            try:
                if kind == "hex":
                    raw = bytes.fromhex(run)
                else:
                    body = run.rstrip("=")
                    if len(body) % 4 == 1:
                        continue
                    alt = "-_" if ("-" in body or "_" in body) else None
                    raw = base64.b64decode(body + "=" * (-len(body) % 4), altchars=alt.encode() if alt else None,
                                           validate=True)
                decoded = raw.decode("utf-8")
            except (ValueError, binascii.Error, UnicodeDecodeError):
                continue
            if len(decoded) >= 4 and _PRINTABLE.fullmatch(decoded):
                yield m.start(), m.end(), decoded


def scan_text(text: str, protector) -> Counter:
    spans = protector.spans(text)
    hits: Counter = Counter()

    def protected(start, end):
        return any(s <= start and end <= e for s, e, _k in spans)

    for name, rx in TOKENS:
        for m in rx.finditer(text):
            if protected(m.start(), m.end()):
                continue
            hits[name] += 1
    for start, end, decoded in decoded_runs(text):
        if protected(start, end):
            continue
        for name, rx in TOKENS:
            n = sum(1 for _ in rx.finditer(decoded))
            if n:
                hits[f"{name} (encoded)"] += n
    return hits


def kit_files(root: Path = KIT):
    """(relative posix path, text) for every file and symlink under root."""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_PARTS for part in rel.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            yield rel.as_posix(), str(path.readlink())
        elif path.is_file():
            yield rel.as_posix(), path.read_bytes().decode("utf-8", errors="replace")


def scan_kit(root: Path = KIT) -> dict[str, dict[str, int]]:
    protector = _protector()
    found: dict[str, dict[str, int]] = {}
    for rel, text in kit_files(root):
        if rel.startswith(MANIFESTS_DIR):
            continue
        names = scan_text(rel, protector) + scan_text(text, protector)  # a file name can leak too
        if names:
            found[rel] = dict(sorted(names.items()))
    return found


def test_kit_has_no_leaks_outside_protected_spans():
    found = scan_kit()
    new = {f: {t: n for t, n in hits.items() if ALLOWED.get(f, {}).get(t) != n} for f, hits in found.items()}
    new = {f: h for f, h in new.items() if h}
    stale = {f: {t: n for t, n in allowed.items() if found.get(f, {}).get(t) != n} for f, allowed in ALLOWED.items()}
    stale = {f: h for f, h in stale.items() if h}
    lines = [f"  {f}: {h}" for f, h in sorted(new.items())]
    lines += [f"  stale allowance {f}: {h} (found {found.get(f, {})})" for f, h in sorted(stale.items())]
    assert not new and not stale, "leak scan:\n" + "\n".join(lines)


def test_the_scan_covers_the_whole_kit():
    tops = {rel.split("/", 1)[0] for rel, _ in kit_files()}
    assert {"starter", "export", "docs", "bootstrap", ".claude", "README.md"} <= tops, tops


# Token names by position in leak_scan, so this file spells none of them.
NAME_FIRST, NAME_WORD = TOKENS[0][0], TOKENS[1][0]


def _owner() -> str:
    return _COMMON["source_owners"][0]


def _platform() -> str:
    return next(r["literal"] for r in _COMMON["substitutions"] if r.get("name") == "C3 platform repo")


def test_scanner_flags_a_planted_literal_outside_a_citation():
    protector = _protector()
    hits = scan_text(f"ask {_owner().split('-')[0]} about {_platform()}\n", protector)
    assert hits[NAME_FIRST] == 1 and hits[_platform()] == 1


def test_scanner_allows_the_same_literal_inside_a_citation():
    protector = _protector()
    text = (f"see https://github.com/{_owner()}/{_platform()}/issues/437 and "
            f"{_platform()}#424\n")
    assert scan_text(text, protector) == Counter()


def test_scanner_allows_the_kept_published_action():
    protector = _protector()
    assert scan_text(f"uses: {_owner()}/verify-actions-lock@v1.1.0\n", protector) == Counter()


def test_scanner_finds_an_email_and_claude_and_account_ids():
    protector = _protector()
    # Invented values, each assembled at run time so this file holds none whole.
    at, plus = "@", "+"
    hits = scan_text(
        f"mail someone{at}mail.example.org or a{at}b.io; " + "trig" + "_01ABCDEFGHIJKLMNOPQRSTUV; "
        "https://claude.ai/artifact/" + "Q3xZk9WmPa7bLc2DeRf5Gh; " + "fdrl" + "_01ABCDEFGHIJ; "
        f"commit as 123456{plus}someone{at}users.noreply.github.com, not "
        f"0{plus}someone{at}users.noreply.github.com or 41898282{plus}github-actions[bot]{at}users.noreply.github.com\n",
        protector)
    assert hits["email"] == 1 and hits["claude.ai id"] == 1
    assert hits["claude.ai artifact, project or chat id"] == 1
    assert hits["Anthropic federation id"] == 1 and hits["numeric noreply id"] == 1


def test_scanner_decodes_base64_and_hex_runs():
    import base64
    protector = _protector()
    secret = f"{_owner().split('-')[0].lower()}:hunter2"
    b64 = base64.b64encode(secret.encode()).decode()
    hexed = secret.encode().hex()
    for literal in (f'"Authorization: Basic {b64}"', f"blob = {hexed}\n",
                    "x = " + base64.urlsafe_b64encode(secret.encode()).decode().rstrip("=")):
        assert scan_text(literal, protector)[f"{NAME_WORD} (encoded)"] == 1, literal
    clean = base64.b64encode(b"alex:hunter2").decode()
    assert scan_text(f'"Authorization: Basic {clean}" and 0123456789abcdef0123456789abcdef01234567\n',
                     protector) == Counter()


def test_denylist_scan_skips_only_a_lockfiles_owner_ids(tmp_path):
    number = "9" + "1234567"   # invented
    tree = tmp_path / "kit"
    wf = tree / "r" / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "actions.lock").write_text(f"        owner_id: {number}\n        repo_id: {number}\n", encoding="utf-8")
    (tree / "notes.md").write_text(f"owner_id: {number}\n", encoding="utf-8")
    hits = denylist_hits([re.compile(number)], tree)
    assert hits == ["notes.md:1: pattern #1", "r/.github/workflows/actions.lock:2: pattern #1"]


def test_denylist_scan_decodes_encoded_runs(tmp_path):
    import base64
    token = "zq" + "encoded" + "detail"
    tree = tmp_path / "kit"
    tree.mkdir()
    (tree / "a.txt").write_text("Basic " + base64.b64encode(f"{token}:pw".encode()).decode() + "\n", encoding="utf-8")
    hits = denylist_hits([re.compile(token, re.IGNORECASE)], tree)
    assert hits == ["a.txt:1: pattern #1 (encoded)"]


def test_scanner_catches_the_possessive_file_name_form():
    protector = _protector()
    name = "docs/decisions/0015-run-it-on-" + _owner().split("-")[0].lower() + "s-own-computer.md"
    assert scan_text(name, protector)[NAME_WORD] == 1


def test_scanner_reads_the_whole_kit_including_paths(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / f"{_platform()}.md").write_text("clean\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(f"made by {_owner()}\n", encoding="utf-8")
    found = scan_kit(tmp_path)
    assert found["docs/" + _platform() + ".md"] == {_platform(): 1}
    assert found["README.md"][NAME_FIRST] == 1


# ── The owner's private denylist: read from outside the repo ────────────────

ENV_PATTERNS = "PRIVACY_DENYLIST"
ENV_FILE = "PRIVACY_DENYLIST_FILE"


class DenylistError(Exception):
    """The denylist could not be loaded. The message never holds a pattern."""


def load_denylist(environ) -> list | None:
    """The compiled patterns, or None when none is configured."""
    raw = environ.get(ENV_PATTERNS)
    if not raw or not raw.strip():
        path = environ.get(ENV_FILE)
        if not path:
            return None
        resolved = Path(path).resolve()
        if resolved == KIT or KIT in resolved.parents:
            raise DenylistError(f"{ENV_FILE} must name a file outside the kit")
        try:
            raw = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise DenylistError(f"{ENV_FILE} could not be read ({type(exc).__name__})") from None
    entries = [line.strip() for line in raw.replace("\r\n", "\n").split("\n")]
    entries = [e for e in entries if e and not e.startswith("#")]
    if not entries:
        return None
    compiled = []
    for index, pattern in enumerate(entries, start=1):
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            raise DenylistError(f"pattern #{index} is not a valid regular expression") from None
    return compiled


# The one line shape the private scan does not read: an `owner_id` in a
# workflow lockfile. `gh actions-lock` writes each action publisher's public
# numeric account id there, and the kit keeps the source owner's published
# verifier with its real id (DECISIONS, "`actions.lock` keeps its publisher
# ids"); the lockfile tool rewrites it on every regeneration.
_LOCK_OWNER_ID = re.compile(r"^\s*owner_id: [0-9]+\s*$")


def _exempt_line(rel: str, line: str) -> bool:
    return rel.endswith(".github/workflows/actions.lock") and bool(_LOCK_OWNER_ID.match(line))


def denylist_hits(patterns, root: Path = KIT) -> list[str]:
    """`path: pattern #N` for every hit, never the text or the pattern."""
    out = []
    for position, (rel, text) in enumerate(kit_files(root), start=1):
        path_hits = [i for i, p in enumerate(patterns, start=1) if p.search(rel)]
        label = f"<file #{position}>" if path_hits else rel
        out += [f"{label}:path: pattern #{i}" for i in path_hits]
        for number, line in enumerate(text.splitlines(), start=1):
            if _exempt_line(rel, line):
                continue
            out += [f"{label}:{number}: pattern #{i}" for i, p in enumerate(patterns, start=1) if p.search(line)]
            for _s, _e, decoded in decoded_runs(line):
                out += [f"{label}:{number}: pattern #{i} (encoded)"
                        for i, p in enumerate(patterns, start=1) if p.search(decoded)]
    return out


def test_kit_holds_nothing_from_the_private_denylist():
    try:
        patterns = load_denylist(os.environ)
    except DenylistError as exc:
        pytest.fail(f"privacy denylist: {exc}")
    if patterns is None:
        if os.environ.get("CI") == "true":
            pytest.fail(f"no privacy denylist in CI: set {ENV_PATTERNS} from the repository secret")
        pytest.skip(f"no privacy denylist ({ENV_PATTERNS} or {ENV_FILE}); the scan did not run")
    hits = denylist_hits(patterns)
    assert not hits, "privacy denylist matched:\n  " + "\n  ".join(hits)


def test_denylist_scan_fails_on_a_planted_token_and_never_prints_it(tmp_path):
    token = "zq" + "planted" + "detail"  # invented; assembled so this file never holds it whole
    listing = tmp_path / "outside" / "denylist.txt"
    listing.parent.mkdir()
    listing.write_text(f"# comment\n\n{token}\n", encoding="utf-8")
    tree = tmp_path / "kit"
    (tree / "docs").mkdir(parents=True)
    (tree / "docs" / "a.md").write_text(f"line one\nwritten by {token.upper()}\n", encoding="utf-8")
    (tree / f"{token}.txt").write_text("clean\n", encoding="utf-8")
    patterns = load_denylist({ENV_FILE: str(listing)})
    hits = denylist_hits(patterns, tree)
    assert "docs/a.md:2: pattern #1" in hits
    assert any(h.startswith("<file #") and h.endswith(":path: pattern #1") for h in hits)
    assert all(token not in h.lower() for h in hits)
    (tree / "docs" / "a.md").write_text("line one\n", encoding="utf-8")
    (tree / f"{token}.txt").unlink()
    assert denylist_hits(patterns, tree) == []


def test_denylist_loader_fails_closed_and_names_no_pattern(tmp_path, monkeypatch):
    assert load_denylist({}) is None
    assert load_denylist({ENV_PATTERNS: "  \n# only a comment\n"}) is None
    assert [p.pattern for p in load_denylist({ENV_PATTERNS: "one\ntwo", ENV_FILE: "/nonexistent"})] == ["one", "two"]
    with pytest.raises(DenylistError, match=r"pattern #2 ") as err:
        load_denylist({ENV_PATTERNS: "fine\n(unclosed"})
    assert "unclosed" not in str(err.value)
    with pytest.raises(DenylistError, match="could not be read"):
        load_denylist({ENV_FILE: str(tmp_path / "missing.txt")})
    with pytest.raises(DenylistError, match="outside the kit"):
        load_denylist({ENV_FILE: str(KIT / "README.md")})
    # CI with no list: the scan test itself fails rather than skipping.
    monkeypatch.delenv(ENV_PATTERNS, raising=False)
    monkeypatch.delenv(ENV_FILE, raising=False)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="no privacy denylist in CI"):
        test_kit_holds_nothing_from_the_private_denylist()


# ── DECISIONS C11: live config names only the kit's repos ───────────────────
#
# Checked per starter tree, on the parsed YAML (comments are history and are
# not config):
#   repos.yml   skills_bootstrap.repos and .out_of_scope, cron_coverage.fleet
#               and .out_of_scope, exclude
#   fleet.yml   every repos[].name
#   .github/workflows/*.yml and .github/dependabot.yml
#               every string value naming <kit owner>/<repo>, and every
#               `repository:` / `repositories:` value
import yaml  # noqa: E402

KIT_REPOS = set(_COMMON["kit_repos"])
KIT_OWNERS = list(_COMMON["kit_owners"])
_OWNER_REF = re.compile(r"(?<![\w.-])(?:" + "|".join(map(re.escape, KIT_OWNERS)) + r")/([A-Za-z0-9._-]+)")


def _strings(node, key=None):
    """(key, string) for every string scalar under node; key is the nearest mapping key."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v, key)
    elif isinstance(node, str):
        yield key, node


def _names_in_repos_yml(doc) -> list[str]:
    sb = doc.get("skills_bootstrap") or {}
    cc = doc.get("cron_coverage") or {}
    out = list(sb.get("repos") or [])
    out += [e["repo"] if isinstance(e, dict) else e for e in sb.get("out_of_scope") or []]
    out += list(cc.get("fleet") or []) + list(cc.get("out_of_scope") or [])
    out += [e["repo"] if isinstance(e, dict) else e for e in doc.get("exclude") or []]
    return [n.split("/")[-1] for n in out]


def _names_in_workflow(doc) -> list[str]:
    out = []
    for key, value in _strings(doc):
        out += [m.group(1).removesuffix(".git") for m in _OWNER_REF.finditer(value)]
        if key in ("repository", "repositories") and "${{" not in value:
            out += [n.split("/")[-1] for n in re.split(r"[\s,]+", value) if n]
    return out


def config_names(tree: Path) -> dict[str, list[str]]:
    """Repo names each config file in `tree` declares, by file."""
    found = {}
    files = [(tree / "repos.yml", _names_in_repos_yml)]
    files += [(p, lambda d: [e["name"].split("/")[-1] for e in d.get("repos") or []])
              for p in sorted(tree.rglob("fleet.yml")) if "node_modules" not in p.parts]
    gh = tree / ".github"
    files += [(p, _names_in_workflow) for p in sorted((gh / "workflows").glob("*.y*ml"))] if gh.is_dir() else []
    files += [(p, _names_in_workflow) for p in (gh / "dependabot.yml", gh / "dependabot.yaml") if p.is_file()]
    for path, extract in files:
        if not path.is_file():
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        found[path.relative_to(tree).as_posix()] = extract(doc)
    return found


TREES = sorted(p.name for p in STARTER.iterdir() if p.is_dir())


@pytest.mark.parametrize("tree", TREES)
def test_config_names_only_kit_repos(tree):
    stray = {f: sorted(set(n for n in names if n not in KIT_REPOS))
             for f, names in config_names(STARTER / tree).items()}
    stray = {f: n for f, n in stray.items() if n}
    assert not stray, f"{tree}: config names repos outside the kit (DECISIONS C11): {stray}"


def test_config_check_flags_a_stray_repo(tmp_path):
    (tmp_path / "repos.yml").write_text("cron_coverage:\n  fleet: [skills-evals, my-blog]\n", encoding="utf-8")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "w.yml").write_text(
        "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v7.0.1\n"
        f"        with: {{repository: {KIT_OWNERS[0]}/side-project}}\n"
        f"      - run: echo {KIT_OWNERS[0]}/agentskills\n", encoding="utf-8")
    found = config_names(tmp_path)
    assert found["repos.yml"] == ["skills-evals", "my-blog"]
    assert sorted(found[".github/workflows/w.yml"]) == ["agentskills", "side-project", "side-project"]


# C11 for routine prompts: a Routine is configured from these files (the
# prompt, the attached repositories), so a repo they name outside history is
# a repo the adopter's Routine would be told to read. Every `<kit owner>/<repo>`
# in them must be a kit repo, except inside a section whose heading says
# "(source system history)" (to the next heading of the same or a higher
# level) and inside a protected span (a P2 citation).
ROUTINE_PROMPTS = [
    "agent-guidance/docs/routines/*.md",
    "agent-guidance/docs/reference/agent-changelog-routine.md",
    "skills-evals/docs/routine-prompt*.md",
    "skills-evals/dashboard/tasks/*.md",
]
HISTORY_MARK = "(source system history)"
_HEADING = re.compile(r"^(#{1,6}) ", re.M)


def _without_history(text: str) -> str:
    out, skip_level = [], None
    for line in text.splitlines(keepends=True):
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and HISTORY_MARK in line:
                skip_level = level
        out.append("\n" if skip_level is not None else line)
    return "".join(out)


def prompt_repo_refs(text: str, protector) -> list[str]:
    text = _without_history(text)
    spans = protector.spans(text)
    names = []
    for m in _OWNER_REF.finditer(text):
        if any(a <= m.start() < b for a, b, _kind in spans):
            continue
        names.append(m.group(1).rstrip(".").removesuffix(".git"))
    return names


def _prompt_files():
    for pattern in ROUTINE_PROMPTS:
        yield from sorted(STARTER.glob(pattern))
    yield KIT / "docs" / "routines.md"


def test_routine_prompts_name_only_kit_repos():
    protector = _protector()
    files = list(_prompt_files())
    assert len(files) >= 10, files
    stray = {}
    for path in files:
        bad = sorted(set(n for n in prompt_repo_refs(path.read_text(encoding="utf-8"), protector)
                         if n not in KIT_REPOS))
        if bad:
            stray[path.relative_to(KIT).as_posix()] = bad
    assert not stray, f"routine prompts name repos outside the kit (DECISIONS C11): {stray}"


def test_routine_prompt_check_skips_only_marked_history():
    owner = KIT_OWNERS[0]
    text = (f"# Prompt\n\nRead {owner}/skills-evals and {owner}/side-project.\n\n"
            f"## Baseline {HISTORY_MARK}\n\nOnce {owner}/old-scratch.\n\n### Run 3\n\n{owner}/old-two\n\n"
            f"## Steps\n\nThen {owner}/after-history.\n")
    assert prompt_repo_refs(text, _protector()) == ["skills-evals", "side-project", "after-history"]
