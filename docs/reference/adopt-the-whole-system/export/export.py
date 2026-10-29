#!/usr/bin/env python3
"""export.py: copy the real repos into the adoption kit's starter trees.

Usage:
  python3 export.py [--sources DIR] [--only REPO ...]            # write
  python3 export.py --check [--sources DIR] [--only REPO ...]    # compare
  (either form takes --skip REPO ... to leave a repo out)

For every manifest under manifests/ (except _common.yml), the
repo's tree is read at the manifest's `source_sha` from the local checkout
`<sources>/<source>` with `git archive` (never the working tree), and turned
into KIT/starter/<kit_name>/ by these steps, in order:

  1. drop `exclude` paths;
  2. add `imports` (single files taken from another checkout at a pinned sha)
     and `inputs` (files kept under export/inputs/<repo>/, substituted too);
  3. apply `edits` with `stage: source` (exact old/new pairs, on source text);
  4. on every text file: protect P2 citation and `keep` spans, apply the
     substitution rules (repo rules first, then the common ones), restore the
     protected spans. File and symlink-target paths are substituted too;
  5. apply `edits` with `stage: output` (on the substituted text);
  6. copy files from overlay/<repo>/ verbatim;
  7. run `patches` (patches/<repo>/<file>.py, each defining apply(root, ctx);
     ctx holds repo, manifest, staged, kit and common, the parsed _common.yml);
  8. run `regenerate` steps (P3: mirrors, digests, generated files), in the
     staged tree.

Everything that is specific to one repo lives in its manifest; the common
substitution map and the protected-span rules live in manifests/_common.yml.
The output is deterministic: running twice gives identical bytes. `--check`
exports into a temporary directory and exits 1 if the committed starter tree
differs (file set, bytes, exec bit or symlink target). "Committed" means
what git tracks: the index's paths, modes and blobs, not the disk, so a file
a .gitignore rule keeps out of git, or an edit not yet staged, fails the
check. Outside a git work tree it compares the disk and says so.

Needs Python 3 (stdlib) and PyYAML; `git` for the sources; whatever a
manifest's regenerate commands call (node, bash).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

EXPORT_DIR = Path(__file__).resolve().parent
KIT = EXPORT_DIR.parent
MANIFESTS = EXPORT_DIR / "manifests"
OVERLAY = EXPORT_DIR / "overlay"
PATCHES = EXPORT_DIR / "patches"
STARTER = KIT / "starter"
# KIT is <sources>/_agent-guidance/docs/reference/adopt-the-whole-system.
DEFAULT_SOURCES = KIT.parents[3]

# Protected spans are swapped for these private-use code points while the
# substitution runs, so no rule can match inside (or across) a citation.
_SENTINEL_OPEN, _SENTINEL_CLOSE, _DIGIT0 = "", "", 0xE100


class ExportError(Exception):
    pass


# ---------------------------------------------------------------- the files


@dataclass
class Entry:
    """One tree entry: a regular file (data bytes, exec bit) or a symlink."""

    data: bytes = b""
    executable: bool = False
    link: str | None = None


Tree = dict  # relative posix path -> Entry


def read_git_tree(repo_dir: Path, sha: str) -> Tree:
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        raise ExportError(f"source_sha must be a full 40-character sha, got {sha!r}")
    if not (repo_dir / ".git").exists():
        raise ExportError(f"no git checkout at {repo_dir}")
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "archive", "--format=tar", sha],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ExportError(f"git archive {sha} failed in {repo_dir}: {proc.stderr.decode(errors='replace').strip()}")
    tree: Tree = {}
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        for member in tar.getmembers():
            if member.isdir() or member.name == "pax_global_header":
                continue
            name = member.name
            if member.issym():
                tree[name] = Entry(link=member.linkname)
            elif member.isfile():
                data = tar.extractfile(member).read()
                tree[name] = Entry(data=data, executable=bool(member.mode & 0o111))
            else:
                raise ExportError(f"{repo_dir.name}: unsupported tree entry {name}")
    return tree


def read_git_file(repo_dir: Path, sha: str, path: str) -> Entry:
    tree_line = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-tree", sha, "--", path],
        capture_output=True, text=True,
    )
    if tree_line.returncode != 0 or not tree_line.stdout.strip():
        raise ExportError(f"{path} not found at {sha} in {repo_dir}")
    mode = tree_line.stdout.split()[0]
    blob = subprocess.run(
        ["git", "-C", str(repo_dir), "show", f"{sha}:{path}"], capture_output=True
    )
    if blob.returncode != 0:
        raise ExportError(f"git show {sha}:{path} failed in {repo_dir}")
    if mode == "120000":
        return Entry(link=blob.stdout.decode())
    return Entry(data=blob.stdout, executable=mode == "100755")


def read_dir_tree(root: Path) -> Tree:
    tree: Tree = {}
    if not root.exists():
        return tree
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames) + [d for d in dirnames if (Path(dirpath) / d).is_symlink()]:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                tree[rel] = Entry(link=os.readlink(p))
            else:
                tree[rel] = Entry(data=p.read_bytes(), executable=bool(p.stat().st_mode & 0o111))
    return tree


def read_index_tree(root: Path) -> Tree | None:
    """The tree git TRACKS under root: the index's paths, modes and blobs.

    `--check` compares this, not the disk, because a file a .gitignore rule
    drops is on the disk but not in any clone (a seed's `bin/gh`, a fixture's
    `.log`). None when root is not in a git work tree (a kit copied out of
    its repo), so the caller can fall back to the disk and say so.
    """
    top = subprocess.run(["git", "-C", str(root if root.exists() else root.parent), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if top.returncode != 0:
        return None
    toplevel = Path(top.stdout.strip()).resolve()
    rel_root = root.resolve().relative_to(toplevel).as_posix()
    listed = subprocess.run(["git", "-C", str(toplevel), "ls-files", "-s", "-z", "--", rel_root],
                            capture_output=True, check=True).stdout
    rows = []
    for record in listed.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, sha, stage = meta.decode().split()
        if stage != "0":
            raise ExportError(f"{path.decode()}: unmerged in the index")
        rows.append((mode, sha, path.decode("utf-8")))
    tree: Tree = {}
    if not rows:
        return tree
    batch = subprocess.run(["git", "-C", str(toplevel), "cat-file", "--batch"],
                           input=b"".join(sha.encode() + b"\n" for _m, sha, _p in rows),
                           capture_output=True, check=True).stdout
    pos = 0
    for mode, sha, path in rows:
        header_end = batch.index(b"\n", pos)
        _sha, kind, size = batch[pos:header_end].decode().split()
        start = header_end + 1
        data = batch[start:start + int(size)]
        pos = start + int(size) + 1
        if kind != "blob" or mode == "160000":
            raise ExportError(f"{path}: unsupported index entry (mode {mode})")
        rel = path[len(rel_root) + 1:]
        if mode == "120000":
            tree[rel] = Entry(link=data.decode("utf-8"))
        else:
            tree[rel] = Entry(data=data, executable=mode == "100755")
    return tree


def write_tree(tree: Tree, root: Path) -> None:
    if root.exists() or root.is_symlink():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for rel in sorted(tree):
        entry = tree[rel]
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if entry.link is not None:
            os.symlink(entry.link, dest)
        else:
            dest.write_bytes(entry.data)
            os.chmod(dest, 0o755 if entry.executable else 0o644)


def is_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def path_matches(path: str, patterns) -> bool:
    """fnmatch on the whole path (`*` crosses `/`); `dir/` matches a prefix."""
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat):
                return True
        elif fnmatch.fnmatchcase(path, pat):
            return True
    return False


# ------------------------------------------------------- spans and rules


def _encode_index(i: int) -> str:
    return _SENTINEL_OPEN + "".join(chr(_DIGIT0 + int(d)) for d in str(i)) + _SENTINEL_CLOSE


_SENTINEL_RE = re.compile(_SENTINEL_OPEN + "([-]+)" + _SENTINEL_CLOSE)


@dataclass
class Protector:
    """Finds the spans the substitution must not touch.

    `citation` spans are P2 (a link to a specific issue, pull request, commit
    or file-at-a-commit in the source owners' repos); `keep` spans are
    literals a decision keeps verbatim (for example DECISIONS C5).
    """

    rules: list = field(default_factory=list)  # (kind, name, compiled regex)

    @classmethod
    def from_config(cls, items) -> "Protector":
        rules = []
        for item in items or []:
            kind = item.get("kind", "citation")
            if kind not in ("citation", "keep"):
                raise ExportError(f"protect rule {item.get('name')!r}: kind must be citation or keep")
            if "literal" in item:
                rx = re.compile(re.escape(item["literal"]))
            else:
                rx = re.compile(item["regex"])
            rules.append((kind, item.get("name", item.get("literal", item.get("regex"))), rx))
        return cls(rules)

    def spans(self, text: str):
        """Merged (start, end, kind) spans, sorted."""
        raw = []
        for kind, _name, rx in self.rules:
            for m in rx.finditer(text):
                if m.end() > m.start():
                    raw.append((m.start(), m.end(), kind))
        raw.sort()
        merged = []
        for s, e, k in raw:
            if merged and s < merged[-1][1]:
                ps, pe, pk = merged[-1]
                merged[-1] = (ps, max(pe, e), pk if pk == k else "citation")
            else:
                merged.append((s, e, k))
        return merged

    def protect(self, text: str):
        saved = []
        out = []
        pos = 0
        for s, e, _k in self.spans(text):
            out.append(text[pos:s])
            out.append(_encode_index(len(saved)))
            saved.append(text[s:e])
            pos = e
        out.append(text[pos:])
        return "".join(out), saved

    @staticmethod
    def restore(text: str, saved) -> str:
        def back(m):
            return saved[int("".join(str(ord(c) - _DIGIT0) for c in m.group(1)))]

        return _SENTINEL_RE.sub(back, text)


def _case_forms(literal: str, to: str):
    """(regex, replacement) pairs for every case and separator form.

    `Pat-Q-Sample` -> `example-adopter` yields the literal itself, then
    pat-q-sample, pat_q_sample, PAT_Q_SAMPLE, PAT-Q-SAMPLE, and finally a
    case-insensitive catch-all that keeps the matched case style. A one-word
    literal next to an underscore takes the snake form (token_patsample ->
    token_example_adopter_two).
    """
    words = [w for w in re.split(r"[-_]", literal) if w]
    to_words = [w for w in re.split(r"[-_]", to) if w]
    to_lower_kebab = "-".join(w.lower() for w in to_words)
    to_lower_snake = "_".join(w.lower() for w in to_words)
    to_upper_snake = "_".join(w.upper() for w in to_words)
    to_upper_kebab = "-".join(w.upper() for w in to_words)
    if len(words) == 1:
        w = words[0]
        pairs = [
            (rf"(?<=_){re.escape(w.lower())}|{re.escape(w.lower())}(?=_)", to_lower_snake),
            (re.escape(literal), to),
            (re.escape(w.upper()), to_upper_snake),
            (re.escape(w.lower()), to_lower_kebab),
        ]
    else:
        pairs = [(re.escape(literal), to)]
        pairs.append((re.escape("-".join(x.lower() for x in words)), to_lower_kebab))
        pairs.append((re.escape("_".join(x.lower() for x in words)), to_lower_snake))
        pairs.append((re.escape("_".join(x.upper() for x in words)), to_upper_snake))
        pairs.append((re.escape("-".join(x.upper() for x in words)), to_upper_kebab))
    return pairs, ("-".join(words) if len(words) > 1 else words[0]), to_words


@dataclass
class Rule:
    rx: re.Pattern
    to: str | None = None          # a re template (\1, \g<name>)
    forms_ci: tuple | None = None  # (to_words,) for the case-insensitive catch-all
    sentence_case: bool = False
    paths: list | None = None
    name: str = ""

    def applies_to(self, path: str) -> bool:
        return self.paths is None or path_matches(path, self.paths)

    def replace(self, m: re.Match, text: str) -> str:
        if self.forms_ci is not None:
            got = m.group(0)
            (to_words,) = self.forms_ci
            sep = "_" if "_" in got else "-"
            if got.islower():
                return sep.join(w.lower() for w in to_words)
            if got.isupper():
                return sep.join(w.upper() for w in to_words)
            return sep.join(w[:1].upper() + w[1:].lower() for w in to_words)
        out = m.expand(self.to)
        if self.sentence_case and out[:1].islower() and _at_sentence_start(text, m.start()):
            out = out[:1].upper() + out[1:]
        return out


# One unit per marker character (or `//`), so a long run of `#`, `-` or `*`
# has exactly one parse; the nested `[#>*|-]+` form backtracked
# exponentially on a ruler comment. It matches the same strings.
_SENTENCE_PREFIX = re.compile(r"(?:^|\n)[ \t]*(?:(?:[#>*|-]|//)[ \t]*)*$")


_MARKERS = re.compile(r"^[ \t]*((?:(?:[#>*|-]|//)[ \t]*)*)")
_ENDS_SENTENCE = re.compile(r"[.!?:][)\]\"'*_`]*$")


def _at_sentence_start(text: str, pos: int) -> bool:
    before = text[max(0, pos - 80):pos]
    m = _SENTENCE_PREFIX.search(before)
    if m:
        # At the start of a line: a new sentence unless the line only wraps
        # the one before it. A wrap keeps the line's comment or quote marker
        # (or none) and opens no list item, table row or heading of its
        # own, and the line before it ends with no sentence punctuation.
        here = re.sub(r"[ \t]", "", m.group(0).lstrip("\n"))
        if any(c in here for c in "-*|"):
            return True
        line_start = pos - len(m.group(0).lstrip("\n"))
        if line_start == 0:
            return True
        prev = text[text.rfind("\n", 0, line_start - 1) + 1:line_start - 1]
        pm = _MARKERS.match(prev)
        prev_markers = re.sub(r"[ \t]", "", pm.group(1))
        content = prev[pm.end():].rstrip()
        # a list item's wrapped line drops the bullet
        if not content or prev_markers.rstrip("-*") != here:
            return True
        return bool(_ENDS_SENTENCE.search(content))
    return bool(re.search(r"[.!?]\s+(?:\*\*|_|\()?$", before)) or bool(re.search(r"\|\s*$", before))


def compile_rules(items) -> list:
    rules = []
    for item in items or []:
        paths = item.get("paths")
        name = item.get("name") or item.get("literal") or item.get("regex", "")
        sentence = bool(item.get("sentence_case"))
        if "literal" in item and item.get("forms"):
            pairs, _base, to_words = _case_forms(item["literal"], item["to"])
            for rx, to in pairs:
                rules.append(Rule(re.compile(rx), to.replace("\\", "\\\\"), None, sentence, paths, name))
            ci = "[-_]".join(re.escape(w) for w in re.split(r"[-_]", item["literal"]) if w)
            rules.append(Rule(re.compile(ci, re.IGNORECASE), None, (to_words,), sentence, paths, name + " (any case)"))
        elif "literal" in item:
            rules.append(Rule(re.compile(re.escape(item["literal"])), item["to"].replace("\\", "\\\\"), None, sentence, paths, name))
        elif "regex" in item:
            rules.append(Rule(re.compile(item["regex"]), item["to"], None, sentence, paths, name))
        else:
            raise ExportError(f"substitution {item!r} needs literal or regex")
    return rules


class Substituter:
    """One left-to-right pass; at each position the first listed rule wins.

    A single combined alternation means a replacement is never substituted
    again, and a longer, earlier-listed literal beats the bare name inside it.
    """

    def __init__(self, rules):
        self.rules = rules
        self._cache = {}

    def _combined(self, path: str):
        active = tuple(i for i, r in enumerate(self.rules) if r.applies_to(path))
        if active not in self._cache:
            if not active:
                self._cache[active] = None
            else:
                parts = []
                for i in active:
                    r = self.rules[i]
                    flags = "(?i:" if r.rx.flags & re.IGNORECASE else "(?:"
                    parts.append(f"(?P<r{i}>{flags}{r.rx.pattern}))")
                self._cache[active] = re.compile("|".join(parts))
        return self._cache[active]

    def sub(self, text: str, path: str, counts=None) -> str:
        rx = self._combined(path)
        if rx is None:
            return text

        def repl(m):
            i = int(m.lastgroup[1:])
            rule = self.rules[i]
            own = rule.rx.match(text, m.start())
            if own is None or own.end() != m.end():
                own = m  # lookarounds evaluate the same in both; fall back
            if counts is not None:
                counts[rule.name] = counts.get(rule.name, 0) + 1
            return rule.replace(own, text)

        return rx.sub(repl, text)


# --------------------------------------------------------------- manifests


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_common():
    return load_yaml(MANIFESTS / "_common.yml") or {}


def load_manifests():
    out = {}
    for path in sorted(MANIFESTS.glob("*.yml")):
        if path.name == "_common.yml":
            continue
        doc = load_yaml(path)
        if not doc or "repo" not in doc:
            raise ExportError(f"{path.name}: no `repo:` key")
        out[doc["repo"]] = doc
    return out


def protector_for(common, manifest=None) -> Protector:
    items = list((manifest or {}).get("protect") or []) + list(common.get("protect") or [])
    return Protector.from_config(items)


def substituter_for(common, manifest) -> Substituter:
    return Substituter(compile_rules((manifest.get("substitutions") or [])) + compile_rules(common.get("substitutions") or []))


def order_manifests(manifests, only=None, skip=None):
    names = list(manifests)
    for n in list(only or []) + list(skip or []):
        if n not in manifests:
            raise ExportError(f"no manifest for {n}")
    wanted = set(only or names) - set(skip or [])
    # Dependencies (`after:`) are exported too, so cross-repo regenerate
    # steps can read them, but only `wanted` ones are written or checked.
    order, seen = [], set()

    def visit(n, stack=()):
        if n in seen:
            return
        if n in stack:
            raise ExportError(f"manifest dependency cycle: {' -> '.join(stack + (n,))}")
        for dep in manifests[n].get("after") or []:
            if dep not in manifests:
                raise ExportError(f"{n}: after names unknown manifest {dep}")
            visit(dep, stack + (n,))
        seen.add(n)
        order.append(n)

    for n in sorted(wanted):
        visit(n)
    needed = sorted(set(order) & set(skip or []))
    if needed:
        raise ExportError(f"--skip {' '.join(needed)}: another exported repo needs it (`after:`)")
    return order, wanted


# ------------------------------------------------------------------ export


def _apply_edits(tree: Tree, edits, stage: str, repo: str) -> None:
    for e in edits:
        if e.get("stage", "source") != stage:
            continue
        if "why" not in e or not str(e["why"]).strip():
            raise ExportError(f"{repo}: edit on {e.get('path')} has no `why`")
        path = e["path"]
        entry = tree.get(path)
        if entry is None or entry.link is not None:
            raise ExportError(f"{repo}: edit names {path}, which is not a file in the {stage} tree")
        text = entry.data.decode("utf-8")
        old, new = e["old"], e["new"]
        want = e.get("count", 1)
        got = text.count(old)
        if got != want:
            raise ExportError(
                f"{repo}: edit on {path} ({e['why'][:60]}...) expected {want} occurrence(s) of its old text, found {got}"
            )
        entry.data = text.replace(old, new).encode("utf-8")


def _run_patch(repo: str, spec, root: Path, ctx) -> None:
    file = PATCHES / repo / spec["file"]
    if not file.is_file():
        raise ExportError(f"{repo}: patch {file} not found")
    mod_spec = importlib.util.spec_from_file_location(f"kit_patch_{repo}_{file.stem}".replace("-", "_"), file)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    mod.apply(root, ctx)


def _regenerate(repo: str, steps, root: Path, staged: dict, env_extra: dict, sources: Path) -> None:
    for step in steps or []:
        if "why" not in step:
            raise ExportError(f"{repo}: regenerate step {step!r} has no `why`")
        if "copy" in step:
            src = root / step["copy"]["from"]
            dst = root / step["copy"]["to"]
            dst.write_bytes(src.read_bytes())
        elif "copy_from_kit" in step:
            spec = step["copy_from_kit"]
            other = staged.get(spec["repo"])
            if other is None:
                raise ExportError(f"{repo}: copy_from_kit needs {spec['repo']} in `after:`")
            (root / spec["to"]).write_bytes((other / spec["from"]).read_bytes())
        elif "run" in step:
            env = dict(os.environ)
            env.update({k: str(v) for k, v in env_extra.items()})
            env["EXPORT_SOURCES"] = str(sources)
            for name, path in staged.items():
                env["EXPORT_TREE_" + re.sub(r"[^A-Za-z0-9]", "_", name).strip("_").upper()] = str(path)
            proc = subprocess.run(["bash", "-c", step["run"]], cwd=root, env=env, capture_output=True, text=True)
            if proc.returncode != 0:
                raise ExportError(
                    f"{repo}: regenerate `{step['run']}` exited {proc.returncode}:\n{proc.stdout[-2000:]}{proc.stderr[-2000:]}"
                )
        else:
            raise ExportError(f"{repo}: unknown regenerate step {step!r}")


def export_repo(name: str, manifest, common, sources: Path, staging: Path, staged: dict, report=None) -> Path:
    src_dir = sources / manifest.get("source", name)
    tree = read_git_tree(src_dir, manifest["source_sha"])

    excludes = manifest.get("exclude") or []
    patterns = [x["path"] if isinstance(x, dict) else x for x in excludes]
    for x in excludes:
        if isinstance(x, dict) and not str(x.get("why", "")).strip():
            raise ExportError(f"{name}: exclude {x.get('path')} has no `why`")
    used = set()
    for rel in list(tree):
        for pat in patterns:
            if path_matches(rel, [pat]):
                used.add(pat)
                del tree[rel]
                break
    unused = [p for p in patterns if p not in used]
    if unused:
        raise ExportError(f"{name}: exclude patterns matched nothing: {unused}")

    for imp in manifest.get("imports") or []:
        if not str(imp.get("why", "")).strip():
            raise ExportError(f"{name}: import {imp.get('path')} has no `why`")
        entry = read_git_file(sources / imp["repo"], imp["sha"], imp["path"])
        dest = imp.get("to", imp["path"])
        if dest in tree:
            raise ExportError(f"{name}: import target {dest} already exists in the tree")
        tree[dest] = entry

    # `inputs`: files kept in the kit (export/inputs/<repo>/...) that are not
    # committed in any source repo, such as a prompt that lives only in
    # claude.ai. They join the tree here, so they are substituted like the rest.
    for inp in manifest.get("inputs") or []:
        if not str(inp.get("why", "")).strip():
            raise ExportError(f"{name}: input {inp.get('to')} has no `why`")
        src = EXPORT_DIR / "inputs" / name / inp["from"]
        if not src.is_file():
            raise ExportError(f"{name}: input {src} not found")
        if inp["to"] in tree:
            raise ExportError(f"{name}: input target {inp['to']} already exists in the tree")
        tree[inp["to"]] = Entry(data=src.read_bytes(), executable=False)

    edits = manifest.get("edits") or []
    _apply_edits(tree, edits, "source", name)

    protector = protector_for(common, manifest)
    # `protect_except`: files where P2 citation spans are NOT protected (a
    # test fixture that only looks like a citation, and the data it pins);
    # `keep` spans still are.
    keep_only = Protector([r for r in protector.rules if r[0] == "keep"])
    protect_except = [x["path"] if isinstance(x, dict) else x for x in manifest.get("protect_except") or []]
    for x in manifest.get("protect_except") or []:
        if not isinstance(x, dict) or not str(x.get("why", "")).strip():
            raise ExportError(f"{name}: protect_except entries need a path and a why")
        if not any(path_matches(rel, [x["path"]]) for rel in tree):
            raise ExportError(f"{name}: protect_except {x['path']} matches no file")
    subst = substituter_for(common, manifest)
    counts = {} if report is not None else None
    out: Tree = {}
    for rel in sorted(tree):
        entry = tree[rel]
        new_rel = subst.sub(rel, rel)
        if new_rel in out:
            raise ExportError(f"{name}: two paths substitute to {new_rel}")
        if entry.link is not None:
            out[new_rel] = Entry(link=subst.sub(entry.link, rel))
            continue
        if is_text(entry.data):
            text = entry.data.decode("utf-8")
            prot = keep_only if path_matches(rel, protect_except) else protector
            masked, saved = prot.protect(text)
            text = Protector.restore(subst.sub(masked, rel, counts), saved)
            out[new_rel] = Entry(data=text.encode("utf-8"), executable=entry.executable)
        else:
            out[new_rel] = Entry(data=entry.data, executable=entry.executable)

    _apply_edits(out, edits, "output", name)

    overlay = OVERLAY / name
    for rel, entry in read_dir_tree(overlay).items():
        if rel.endswith(("/__pycache__", ".pyc")) or "/__pycache__/" in f"/{rel}":
            continue
        out[rel] = entry

    root = staging / manifest["kit_name"]
    write_tree(out, root)
    ctx = {"repo": name, "manifest": manifest, "staged": dict(staged), "kit": KIT, "common": load_common()}
    for spec in manifest.get("patches") or []:
        if not str(spec.get("why", "")).strip():
            raise ExportError(f"{name}: patch {spec.get('file')} has no `why`")
        _run_patch(name, spec, root, ctx)
    _regenerate(name, manifest.get("regenerate"), root, staged, manifest.get("regenerate_env") or {}, sources)
    if report is not None:
        report[name] = counts
    return root


def diff_trees(a: Tree, b: Tree):
    problems = []
    for rel in sorted(set(a) | set(b)):
        x, y = a.get(rel), b.get(rel)
        if x is None:
            problems.append(f"only in the fresh export: {rel}")
        elif y is None:
            problems.append(f"only in the committed tree: {rel}")
        elif x.link != y.link:
            problems.append(f"symlink differs: {rel}")
        elif x.data != y.data:
            problems.append(f"content differs: {rel}")
        elif x.executable != y.executable:
            problems.append(f"exec bit differs: {rel}")
    return problems


def _strip_junk(tree: Tree) -> Tree:
    return {k: v for k, v in tree.items() if "__pycache__/" not in k and not k.endswith(".pyc") and ".pytest_cache/" not in k}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if a committed starter tree differs from a fresh export")
    ap.add_argument("--sources", default=os.environ.get("KIT_EXPORT_SOURCES", str(DEFAULT_SOURCES)),
                    help="directory holding the source checkouts (default: the folder above this repo)")
    ap.add_argument("--only", nargs="*", help="manifest repo names to export (default: all)")
    ap.add_argument("--skip", nargs="*", default=[],
                    help="manifest repo names to leave out, e.g. one whose source this machine cannot read "
                         "(refused when another exported repo needs it)")
    ap.add_argument("--report", action="store_true", help="print how often each substitution rule fired")
    args = ap.parse_args(argv)

    try:
        common = load_common()
        manifests = load_manifests()
        order, wanted = order_manifests(manifests, args.only, args.skip)
        sources = Path(args.sources).resolve()
        report = {} if args.report else None
        with tempfile.TemporaryDirectory(prefix="kit-export-") as tmp:
            staging = Path(tmp)
            staged = {}
            for name in order:
                staged[name] = export_repo(name, manifests[name], common, sources, staging, staged, report)
            failed = False
            for name in order:
                if name not in wanted:
                    continue
                kit_name = manifests[name]["kit_name"]
                fresh = read_dir_tree(staged[name])
                dest = STARTER / kit_name
                if args.check:
                    committed = read_index_tree(dest)
                    if committed is None:
                        print(f"note: starter/{kit_name} is not in a git work tree; comparing the files on disk")
                        committed = _strip_junk(read_dir_tree(dest))
                    problems = diff_trees(committed, fresh)
                    if problems:
                        failed = True
                        print(f"STALE starter/{kit_name} ({name}): {len(problems)} difference(s)")
                        for p in problems[:50]:
                            print(f"  {p}")
                    else:
                        print(f"OK starter/{kit_name} ({name}): {len(fresh)} files match a fresh export")
                else:
                    write_tree(fresh, dest)
                    print(f"wrote starter/{kit_name} ({name}): {len(fresh)} files")
            if report is not None:
                for name, counts in report.items():
                    print(f"substitutions in {name}:")
                    for rule, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                        print(f"  {n:6d}  {rule}")
        return 1 if failed else 0
    except ExportError as exc:
        print(f"export.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
