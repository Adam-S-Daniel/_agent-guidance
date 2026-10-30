#!/usr/bin/env python3
"""content.py: install optional library items into the kit copy's starter/.

    content.py install --kit <kit> --items <spec> --record <file> [--dry-run]
    content.py probes  --kit <kit> --record <file>

`install` resolves <spec> (comma-separated item ids from
bootstrap/content-catalog.json, `recommended` for every recommended item, or
`none`), then, in a scratch copy of starter/:

1. for each chosen item, copies library/<kind>/<id>/<tree>/files/ into
   starter/<tree>/ and applies that tree's ops.json (library/README.md lists
   the ops); `copy-if` ops run last, once every item they name is installed;
2. rebuilds what the export generates from the changed files: base.md's
   mirror (.claude/hooks/fleet-guidance.md) and the agent-checks runner
   (.githooks/agent-checks) in every tree, each tree's AGENTS.md, the
   section byte counts in eval-coverage.yml, the registry's README table and
   skills.lock, and the private registry's copy of the lock;
3. compares the scratch copy with starter/ and, without --dry-run, writes the
   files that differ back and records the installed items in <file>.

Every op checks first whether its change is already made, so a second run
changes nothing. The generated-file recipes are checked against the
unchanged starter/ before anything is applied: a recipe that no longer
reproduces the shipped files stops the run. Python classes and methods are
found by parsing the file (ast), never by a text search.

`probes` prints, for each recorded item, one line `<id>\\t<tree>\\t<path>\\t<text>`:
a file on that repo's default branch that holds the item once its starter
pull request is merged, and a line it contains (empty for "the file exists").

Exit 0 on success, 1 on any refusal, with a one-line reason. Standard
library only; needs bash and git for the generated files.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIND_DIRS = {"skill": "skills", "guidance": "guidance", "git-hook": "git-hooks"}
OPTIONAL_TIERS = ("recommended", "opinionated")
AG = "agent-guidance"
REGISTRY = "agent-skills"
PRIVATE = "agent-skills-private"
HEADING = re.compile(r"^## ")
FENCE = re.compile(r"^ {0,3}(```|~~~)")
DATED = re.compile(r"^## \d{4}-\d{2}-\d{2} ", re.M)
ADDITIONS = "## Repo-specific additions"
JUNK = {"__pycache__", ".pytest_cache", "node_modules", ".DS_Store"}


class Refused(Exception):
    pass


# ------------------------------------------------------------------ catalog
def load_catalog(kit: Path) -> dict:
    doc = json.loads((kit / "bootstrap" / "content-catalog.json").read_text(encoding="utf-8"))
    return {i["id"]: i for i in doc["items"]}


def resolve(catalog: dict, spec: str) -> tuple[list, list]:
    """(optional ids chosen, core ids named) for <spec>; Refused on anything else."""
    tokens = [t.strip() for t in spec.split(",")]
    if not spec.strip() or any(not t for t in tokens):
        raise Refused(f"--items has an empty entry: {spec!r}")
    chosen, core, unknown = set(), set(), []
    for t in tokens:
        if t == "none":
            continue
        if t == "recommended":
            chosen |= {i for i, item in catalog.items() if item["tier"] == "recommended"}
        elif t in catalog and catalog[t]["tier"] in OPTIONAL_TIERS:
            chosen.add(t)
        elif t in catalog and catalog[t]["tier"] == "core":
            core.add(t)
        else:
            unknown.append(t)
    if unknown:
        raise Refused("unknown item id(s): " + ", ".join(unknown)
                      + " (bootstrap/content-catalog.json lists the ids; or use recommended, none)")
    if "none" in tokens and len(set(tokens)) > 1:
        raise Refused("--items none cannot be combined with other items")
    return sorted(chosen), sorted(core)


def read_record(record: Path, catalog: dict) -> list:
    if not record.exists():
        return []
    try:
        doc = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"{record} is not a content choices file (want {{\"installed\": [<id>, ...]}})")
    ids = doc.get("installed") if isinstance(doc, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise Refused(f"{record} is not a content choices file (want {{\"installed\": [<id>, ...]}})")
    bad = [i for i in ids if i not in catalog or catalog[i]["tier"] not in OPTIONAL_TIERS]
    if bad:
        raise Refused(f"{record} names items the catalog does not offer: {', '.join(bad)}")
    return sorted(set(ids))


def item_dir(kit: Path, catalog: dict, item_id: str) -> Path:
    d = kit / "library" / KIND_DIRS[catalog[item_id]["kind"]] / item_id
    if not d.is_dir():
        raise Refused(f"library/{KIND_DIRS[catalog[item_id]['kind']]}/{item_id}/ is missing from the kit")
    return d


def trees_of(d: Path) -> list:
    return sorted(p for p in d.iterdir() if p.is_dir())


# ------------------------------------------------------------------ text helpers
def lines_of(text: str) -> list:
    return text.splitlines(keepends=True)


def headings(text: str) -> list:
    """[(line index, heading text)] of the `## ` lines outside fenced code."""
    out, fence = [], None
    for i, line in enumerate(lines_of(text)):
        m = FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if fence is None and HEADING.match(line):
            out.append((i, line[3:].rstrip("\n").strip()))
    return out


def section_bytes(text: str) -> dict:
    """{heading: bytes}: a section runs from its heading through the line
    before the next `## ` heading, or to the end of the file."""
    lines = lines_of(text)
    offs = [0]
    for line in lines:
        offs.append(offs[-1] + len(line.encode("utf-8")))
    hs = headings(text)
    out = {}
    for n, (i, h) in enumerate(hs):
        end = hs[n + 1][0] if n + 1 < len(hs) else len(lines)
        out[h] = offs[end] - offs[i]
    return out


def section_end(text: str, heading: str) -> int | None:
    """Character offset of the end of <heading>'s section, or None."""
    lines = lines_of(text)
    hs = headings(text)
    for n, (i, h) in enumerate(hs):
        if "## " + h == heading:
            end = hs[n + 1][0] if n + 1 < len(hs) else len(lines)
            return sum(len(x) for x in lines[:end])
    return None


def insert_at(text: str, pos: int, block: str) -> str:
    """Insert <block> (whole lines) at <pos>, a line start, keeping one blank
    line between blocks."""
    block = block.rstrip("\n") + "\n"
    if pos >= len(text):
        return text.rstrip("\n") + "\n\n" + block
    return text[:pos] + block + "\n" + text[pos:]


def yaml_scalar(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(raw)
    return raw


def coverage_rows(text: str) -> list:
    """[{id, heading, file, bytes_line (index), start, end (line indexes)}]."""
    lines = lines_of(text)
    rows, cur = [], None
    for i, line in enumerate(lines):
        if line.startswith("- id: "):
            cur = {"id": line[6:].strip(), "start": i, "end": i + 1}
            rows.append(cur)
        elif cur is not None and line.startswith("  "):
            cur["end"] = i + 1
            key, _, value = line.strip().partition(": ")
            if key in ("heading", "file"):
                cur[key] = yaml_scalar(value)
            elif key == "bytes":
                cur["bytes_line"] = i
        else:
            cur = None
    return rows


# ------------------------------------------------------------------ ops
def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def need(path: Path, op: dict) -> Path:
    if not path.is_file():
        raise Refused(f"op {op['op']}: {path.name} is not in the tree ({op.get('file')})")
    return path


def module_tree(path: Path, text: str):
    try:
        return ast.parse(text, filename=str(path))
    except SyntaxError as e:
        raise Refused(f"{path.name} does not parse: {e.msg} (line {e.lineno})")


def op_insert_section(text, op, frag):
    if any("## " + h == op["heading"] for _, h in headings(text)):
        return text
    for h in op.get("follows", []):
        end = section_end(text, h)
        if end is not None:
            return insert_at(text, end, frag)
    hs = headings(text)
    if not hs:
        return insert_at(text, len(text), frag)
    return insert_at(text, sum(len(x) for x in lines_of(text)[:hs[0][0]]), frag)


def op_coverage_row(text, op, frag):
    rows = coverage_rows(text)
    if any(r["id"] == op["id"] for r in rows):
        return text
    lines = lines_of(text)
    for f in op.get("follows", []):
        for r in rows:
            if r["id"] == f:
                pos = sum(len(x) for x in lines[:r["end"]])
                block = frag.rstrip("\n") + "\n"
                return text[:pos] + "\n" + block + text[pos:]
    if rows:
        return insert_at(text, sum(len(x) for x in lines[:rows[0]["start"]]), frag)
    return insert_at(text, len(text), frag)


def op_impact_entry(text, op, frag):
    if any(line.rstrip("\n") == op["heading"] for line in lines_of(text)):
        return text
    m = DATED.search(text)
    return insert_at(text, m.start() if m else len(text), frag)


def op_insert_before(text, op, frag):
    anchor = op["anchor"]
    if frag + anchor in text:
        return text
    n = text.count(anchor)
    if n != 1:
        raise Refused(f"insert-before: the anchor occurs {n} times in {op['file']}, want 1")
    return text.replace(anchor, frag + anchor)


def op_replace(text, op, _frag):
    old, new, count = op["old"], op["new"], op.get("count", 1)
    if old not in text and new in text:
        return text
    n = text.count(old)
    if n != count:
        raise Refused(f"replace: the old text occurs {n} times in {op['file']}, want {count}")
    return text.replace(old, new)


def op_append_lines(text, _op, frag):
    if frag in text:
        return text
    return (text if text.endswith("\n") or not text else text + "\n") + frag


def op_layout_skill(text, op, _frag):
    doc = json.loads(text)
    plugin = doc["plugins"].get(op["plugin"])
    if plugin is None:
        raise Refused(f"layout-skill: {op['file']} has no plugin {op['plugin']}")
    plugin["skills"] = sorted(set(plugin["skills"]) | {op["skill"]})
    return json.dumps(doc, indent=2) + "\n"


def _start(node) -> int:
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])


def op_insert_class(text, op, frag, path):
    tree = module_tree(path, text)
    have = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == op["class"]]
    if have:
        # The export kept the class for its helpers and took out its tests:
        # add back each method of the fragment's class that the file lacks.
        names = {b.name for b in have[0].body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))}
        frag_cls = [n for n in module_tree(path, frag).body if isinstance(n, ast.ClassDef) and n.name == op["class"]]
        if not frag_cls:
            raise Refused(f"insert-class: the fragment for {op['class']} holds no such class")
        flines = lines_of(frag)
        for b in frag_cls[0].body:
            if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and b.name not in names:
                text = op_insert_method(text, {**op, "method": b.name},
                                        "".join(flines[_start(b) - 1:b.end_lineno]), path)
        return text
    main = [n for n in tree.body if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
            and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__"]
    block = frag.rstrip("\n") + "\n"
    if not main:
        return text.rstrip("\n") + "\n\n\n" + block
    pos = sum(len(x) for x in lines_of(text)[:main[0].lineno - 1])
    return text[:pos] + block + "\n\n" + text[pos:]


def op_insert_method(text, op, frag, path):
    tree = module_tree(path, text)
    cls = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == op["class"]]
    if not cls:
        raise Refused(f"insert-method: {op['file']} has no class {op['class']}")
    if any(isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and b.name == op["method"] for b in cls[0].body):
        return text
    lines = lines_of(text)
    pos = sum(len(x) for x in lines[:cls[0].end_lineno])
    head = text[:pos] if text[:pos].endswith("\n") else text[:pos] + "\n"
    return head + "\n" + frag.rstrip("\n") + "\n" + text[pos:]


TEXT_OPS = {"insert-section": op_insert_section, "coverage-row": op_coverage_row,
            "impact-entry": op_impact_entry, "insert-before": op_insert_before,
            "replace": op_replace, "append-lines": op_append_lines, "layout-skill": op_layout_skill}
AST_OPS = {"insert-class": op_insert_class, "insert-method": op_insert_method}


def copy_file(src: Path, dst: Path, where: str) -> None:
    data = src.read_bytes()
    if dst.is_symlink() or (dst.exists() and not dst.is_file()):
        raise Refused(f"{where} is in the way (not a regular file)")
    if dst.is_file():
        if dst.read_bytes() != data:
            raise Refused(f"{where} already exists and differs from the library's copy")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    os.chmod(dst, 0o755 if os.access(src, os.X_OK) else 0o644)


def copy_link(src: Path, files: Path, dst: Path, where: str) -> None:
    """A relative symlink that stays inside the item's files/ (a fixture's)."""
    target = os.readlink(src)
    resolved = os.path.normpath(os.path.join(os.path.dirname(str(src)), target))
    if os.path.isabs(target) or not (resolved + os.sep).startswith(str(files) + os.sep):
        raise Refused(f"{where}: the library's symlink leaves its tree ({target})")
    if dst.is_symlink():
        if os.readlink(dst) != target:
            raise Refused(f"{where} already exists and differs from the library's copy")
        return
    if dst.exists():
        raise Refused(f"{where} already exists and differs from the library's copy")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, dst)


def load_ops(tree_dir: Path) -> list:
    p = tree_dir / "ops.json"
    if not p.is_file():
        return []
    ops = json.loads(read(p)).get("ops")
    if not isinstance(ops, list):
        raise Refused(f"{p} has no ops list")
    for op in ops:
        if op.get("op") not in TEXT_OPS and op.get("op") not in AST_OPS and op.get("op") != "copy-if":
            raise Refused(f"{p}: unknown op {op.get('op')!r}")
    return ops


def safe_rel(rel: str) -> str:
    parts = Path(rel).parts
    if Path(rel).is_absolute() or ".." in parts or not parts:
        raise Refused(f"a library path leaves its tree: {rel}")
    return rel


def apply_item(starter: Path, d: Path, item_id: str) -> None:
    for tree_dir in trees_of(d):
        dest = starter / tree_dir.name
        if not dest.is_dir():
            raise Refused(f"{item_id}: starter/{tree_dir.name}/ is not in the kit")
        files = tree_dir / "files"
        if files.is_dir():
            for src in sorted(files.rglob("*")):
                if src.is_symlink():
                    rel = safe_rel(str(src.relative_to(files)))
                    copy_link(src, files, dest / rel, f"starter/{tree_dir.name}/{rel}")
                elif src.is_file():
                    rel = safe_rel(str(src.relative_to(files)))
                    copy_file(src, dest / rel, f"starter/{tree_dir.name}/{rel}")
        for op in load_ops(tree_dir):
            if op["op"] == "copy-if":
                continue
            path = need(dest / safe_rel(op["file"]), op)
            frag = read(tree_dir / "fragments" / op["fragment"]) if "fragment" in op else ""
            text = read(path)
            if op["op"] in AST_OPS:
                new = AST_OPS[op["op"]](text, op, frag, path)
            else:
                new = TEXT_OPS[op["op"]](text, op, frag)
            if new != text:
                write(path, new)


def apply_copy_if(starter: Path, kit: Path, catalog: dict, installed: set) -> None:
    for item_id in sorted(installed):
        d = item_dir(kit, catalog, item_id)
        for tree_dir in trees_of(d):
            for op in load_ops(tree_dir):
                if op["op"] == "copy-if" and set(op["requires"]) <= installed:
                    rel = safe_rel(op["to"])
                    copy_file(tree_dir / "fragments" / op["fragment"], starter / tree_dir.name / rel,
                              f"starter/{tree_dir.name}/{rel}")


# ------------------------------------------------------------------ generated files
def run(cmd: list, cwd: Path, env: dict | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})
    if proc.returncode != 0:
        raise Refused(f"`{' '.join(cmd[:3])}` in {cwd.name} exited {proc.returncode}")
    return proc.stdout


def agents_md(build: str, current: str) -> str | None:
    i = next((n for n, line in enumerate(lines_of(current)) if line.startswith(ADDITIONS)), None)
    if i is None:
        return None
    tail = "".join(lines_of(current)[i:])
    return build.rstrip("\n") + "\n" + tail.rstrip("\n") + "\n"


def coverage_with_bytes(ag: Path) -> tuple[str, list]:
    """eval-coverage.yml with every row's bytes recomputed, and the rows whose
    section is not found."""
    cov_path = ag / "agents-md" / "eval-coverage.yml"
    text = read(cov_path)
    lines = lines_of(text)
    cache, missing = {}, []
    for r in coverage_rows(text):
        f = r.get("file")
        if f is None or "bytes_line" not in r:
            continue
        if f not in cache:
            p = ag / safe_rel(f)
            cache[f] = section_bytes(read(p)) if p.is_file() else {}
        b = cache[f].get(r.get("heading"))
        if b is None:
            missing.append(r["id"])
            continue
        lines[r["bytes_line"]] = f"  bytes: {b}\n"
    return "".join(lines), missing


def lock(reg: Path) -> str:
    """The registry's skills.lock as the export writes it (ref: forty zeros)."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "registry"
        shutil.copytree(reg, work, symlinks=True)
        env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
        run(["git", "init", "-q", "-b", "main"], work, env)
        run(["git", "add", "-A"], work, env)
        run(["git", "-c", "user.name=kit", "-c", "user.email=kit@example.com", "-c", "core.hooksPath=" + os.devnull,
             "commit", "-qm", "export"], work, env)
        sha = run(["git", "rev-parse", "HEAD"], work, env).strip()
        run([sys.executable, "-I", "-B", "scripts/generate_skills_lock.py", "--ref", sha], work, env)
        return read(work / "skills.lock").replace(sha, "0" * 40)


def regenerate(starter: Path) -> None:
    ag = starter / AG
    trees = sorted(p for p in starter.iterdir() if p.is_dir())
    base = (ag / "agents-md" / "base.md").read_bytes()
    runner = ag / "templates" / "agent-checks" / "agent-checks"
    for t in trees:
        mirror = t / ".claude" / "hooks" / "fleet-guidance.md"
        if mirror.is_file():
            mirror.write_bytes(base)
        hook = t / ".githooks" / "agent-checks"
        if hook.is_file():
            hook.write_bytes(runner.read_bytes())
    build = run(["bash", str(ag / "scripts" / "build-agents-md.sh")], ag)
    for t in trees:
        p = t / "AGENTS.md"
        if p.is_file():
            new = agents_md(build, read(p))
            if new is not None:
                write(p, new)
    cov, missing = coverage_with_bytes(ag)
    if missing:
        raise Refused("eval-coverage.yml rows with no section in their file: " + ", ".join(missing))
    write(ag / "agents-md" / "eval-coverage.yml", cov)
    reg = starter / REGISTRY
    run([sys.executable, "-I", "-B", "scripts/generate_readme_table.py"], reg)
    text = lock(reg)
    write(reg / "skills.lock", text)
    if (starter / PRIVATE / "skills.lock").is_file():
        write(starter / PRIVATE / "skills.lock", text)


def self_test(starter: Path) -> None:
    """The recipes reproduce the shipped generated files, before any change."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "starter"
        shutil.copytree(starter, work, symlinks=True, ignore=shutil.ignore_patterns(*JUNK))
        regenerate(work)
        drift = diff(starter, work)
    if drift:
        raise Refused("the kit's generated files do not match its recipes (re-export the kit): "
                      + ", ".join(sorted(drift)[:5]))


# ------------------------------------------------------------------ compare and write back
def files_of(root: Path) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in JUNK and d != ".git"]
        for name in filenames:
            if name in JUNK or name.endswith(".pyc"):
                continue
            p = Path(dirpath) / name
            out[str(p.relative_to(root))] = p
    return out


def diff(a: Path, b: Path) -> dict:
    """{relative path: "add" | "change"} for what <b> has that <a> lacks or holds differently."""
    fa, out = files_of(a), {}
    for rel, pb in files_of(b).items():
        pa = fa.get(rel)
        if pa is None:
            out[rel] = "add"
        elif pb.is_symlink() or pa.is_symlink():
            if not (pb.is_symlink() and pa.is_symlink() and os.readlink(pb) == os.readlink(pa)):
                out[rel] = "change"
        elif pa.read_bytes() != pb.read_bytes() or os.access(pa, os.X_OK) != os.access(pb, os.X_OK):
            out[rel] = "change"
    return out


def write_record(record: Path, installed: list) -> None:
    record.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=record.parent, prefix=".content.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"installed": installed}, f, indent=2)
        f.write("\n")
    os.replace(tmp, record)


def install(kit: Path, spec: str, record: Path, dry_run: bool) -> int:
    catalog = load_catalog(kit)
    chosen, core = resolve(catalog, spec)
    before = read_record(record, catalog)
    starter = kit / "starter"
    for c in core:
        print(f"note: {c} is a core item, already in starter/")
    self_test(starter)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "starter"
        shutil.copytree(starter, work, symlinks=True, ignore=shutil.ignore_patterns(*JUNK))
        for item_id in chosen:
            apply_item(work, item_dir(kit, catalog, item_id), item_id)
        installed = sorted(set(before) | set(chosen))
        apply_copy_if(work, kit, catalog, set(installed))
        regenerate(work)
        changes = diff(starter, work)
        trees = sorted({rel.split("/", 1)[0] for rel in changes})
        verb = "would " if dry_run else ""
        for t in trees:
            mine = sorted(r for r in changes if r.split("/", 1)[0] == t)
            adds = sum(changes[r] == "add" for r in mine)
            print(f"starter/{t}: {verb}add {adds} file(s), {verb}change {len(mine) - adds}")
            for r in mine:
                print(f"  {changes[r]}: {r}")
        if not dry_run:
            for rel in sorted(changes):
                src, dst = work / rel, starter / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst, follow_symlinks=False)
    new = [i for i in chosen if i not in before]
    names = ", ".join(new) if new else "nothing new"
    print(f"items: {verb}install {names}; installed after this run: {', '.join(installed) or 'none'}")
    if not changes:
        print("starter/ already holds every chosen item; nothing to change")
    if not dry_run:
        write_record(record, installed)
        print(f"recorded the choices in {record}")
    print("changed-trees: " + " ".join(trees))
    return 0


def probes(kit: Path, record: Path) -> int:
    catalog = load_catalog(kit)
    if not record.is_file():
        raise Refused(f"no content choices recorded yet ({record}); run bootstrap/add-content.sh")
    for item_id in read_record(record, catalog):
        item = catalog[item_id]
        d = item_dir(kit, catalog, item_id)
        if item["kind"] == "skill":
            files = d / REGISTRY / "files"
            skill = sorted(p for p in files.rglob("SKILL.md"))
            if not skill:
                raise Refused(f"{item_id}: the library has no SKILL.md for it")
            print(f"{item_id}\t{REGISTRY}\t{skill[0].relative_to(files)}\t")
        elif item["kind"] == "guidance":
            op = next(o for o in load_ops(d / AG) if o["op"] == "insert-section")
            print(f"{item_id}\t{AG}\t{op['file']}\t{op['heading']}")
        else:
            # Probe for the hook's own `note_ran <id>` line: a fragment's
            # first line can be one the core runner already has (rules-dir's
            # `case "$path" in` is), which would pass before the hook merged.
            want = f"note_ran {item_id}"
            hits = [(o["file"], line) for o in load_ops(d / AG) if o["op"] == "insert-before"
                    for line in read(d / AG / "fragments" / o["fragment"]).splitlines()
                    if line.strip() == want]
            if not hits:
                raise Refused(f"{item_id}: no insert-before fragment adds `{want}`")
            print(f"{item_id}\t{AG}\t{hits[0][0]}\t{hits[0][1]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("action", choices=["install", "probes"])
    ap.add_argument("--kit", required=True, type=Path)
    ap.add_argument("--record", required=True, type=Path)
    ap.add_argument("--items")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.action == "probes":
            return probes(a.kit.resolve(), a.record)
        if a.items is None:
            raise Refused("--items is required")
        return install(a.kit.resolve(), a.items, a.record, a.dry_run)
    except Refused as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
