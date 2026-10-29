#!/usr/bin/env python3
"""Audit one Claude Code memory directory and propose promotions of its facts.

A memory directory is a `MEMORY.md` index plus one Markdown note per subject,
each with YAML frontmatter (`name`, `description`, `metadata.type`,
`metadata.home`). This audit reads it and reports, without changing anything:

  missing_home   a note whose type is not `user` and whose `metadata.home` is
                 absent or in neither form ADR 0013 accepts
  unparseable    a note whose frontmatter cannot be read
  duplicates     pairs of notes whose normalized text is near-identical
  stale_dates    lines whose newest ISO date is older than --stale-days
  stale_refs     PRs, issues and files the resolver says are closed, merged
                 or missing (a home whose file is missing is a dangling home)
  index          notes MEMORY.md never names, and MEMORY.md links to notes
                 that do not exist
  oversize       files over --max-bytes

It also emits `promotions`: for a note whose home is `<owner>/<repo>:<path>`
and whose paragraphs are not all in that file yet, the target file, section
and missing text, as a proposed edit. The fleet worker makes the PR
(docs/routines/fleet-worker.md); the routine that runs this is
docs/routines/memory-curation.md, and the decision is ADR 0026.

The resolver is data, never the network: `--refs FILE` is a JSON object
mapping each reference this audit prints under `references` to a state
(`open`, `closed`, `merged`, `present`, `missing`). `--clone-root DIR` finds
local clones, as `<DIR>/<repo>` or `<DIR>/<owner>/<repo>`, to resolve file
references and read promotion targets. Python callers pass `resolver` and
`fetcher` callables to `audit()` instead.

JSON goes to stdout; `--summary FILE` also writes a short Markdown summary that
names notes by filename and never quotes their text. Exit 0, or 1 under
`--strict` when there is any finding, or 2 for a usage error.
"""

import argparse
import datetime
import difflib
import json
import os
import re
import sys

try:
    import yaml
except ImportError:  # reported per run in main(), never a crash at import
    yaml = None

INDEX = "MEMORY.md"
STALE_STATES = ("closed", "merged", "missing")
NARROW_TARGETS = ("AGENTS.md", "CLAUDE.md")
REPO_SPECIFIC = "## Repo-specific additions"

DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
URL_REF_RE = re.compile(
    r"https://github\.com/([\w.-]+)/([\w.-]+)/(?:pull|issues)/(\d+)")
BLOB_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/blob/[^/\s]+/([^\s)#?>\]]+)")
SLUG_REF_RE = re.compile(r"(?<![\w./-])([\w.-]+/)?([\w.-]+)#(\d+)\b")
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


if yaml is not None:
    class FrontmatterLoader(yaml.SafeLoader):
        """SafeLoader that leaves timestamps as strings, as memory-home.sh does."""

    FrontmatterLoader.yaml_implicit_resolvers = {
        first: [r for r in resolvers if r[0] != "tag:yaml.org,2002:timestamp"]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


def split_home(value):
    """-> (owner, repo, path) | None. Same forms as memory-home.sh split_home."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    prefix = "https://github.com/"
    if v.startswith(prefix):
        v = v.split("#", 1)[0].split("?", 1)[0]
        parts = [p for p in v[len(prefix):].split("/") if p]
        if len(parts) >= 5 and parts[2] == "blob":
            return parts[0], parts[1], "/".join(parts[4:])
        return None
    if ":" in v:
        left, _, right = v.partition(":")
        owner_repo = [p for p in left.split("/") if p]
        right = right.strip().lstrip("/")
        if len(owner_repo) == 2 and right:
            return owner_repo[0], owner_repo[1], right
    return None


def parse_note(path):
    """-> dict with file, text, meta, body, error."""
    note = {"file": os.path.basename(path), "meta": {}, "body": "", "error": None}
    with open(path, encoding="utf-8", errors="replace") as fh:
        note["text"] = fh.read()
    lines = note["text"].split("\n")
    if not lines or lines[0].strip() != "---":
        note["error"] = "no YAML frontmatter block"
        note["body"] = note["text"]
        return note
    end = next((i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")), None)
    if end is None:
        note["error"] = "unterminated frontmatter block"
        return note
    note["body"] = "\n".join(lines[end + 1:])
    try:
        front = yaml.load("\n".join(lines[1:end]), Loader=FrontmatterLoader) or {}
    except yaml.YAMLError:
        note["error"] = "unparseable frontmatter"
        return note
    if not isinstance(front, dict):
        note["error"] = "frontmatter is not a mapping"
        return note
    meta = front.get("metadata") if isinstance(front.get("metadata"), dict) else {}
    meta = dict(meta)
    if "type" not in meta and isinstance(front.get("type"), str):
        meta["type"] = front["type"]
    note["meta"] = meta
    note["description"] = front.get("description") if isinstance(front.get("description"), str) else ""
    return note


def normalize(text):
    """Lowercase words only: markup, punctuation and spacing do not count."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def blocks(body):
    """Blank-line separated paragraphs, and each list item on its own."""
    out = []
    for para in re.split(r"\n\s*\n", body):
        items = re.split(r"\n(?=\s*[-*+] |\s*\d+\. )", para.strip())
        out.extend(i.strip() for i in items if normalize(i))
    return out


def references(text):
    """Every PR, issue or file reference in the text, as resolver keys."""
    refs = set()
    for owner, repo, num in URL_REF_RE.findall(text):
        refs.add("%s/%s#%s" % (owner, repo, num))
    for owner, repo, path in BLOB_RE.findall(text):
        refs.add("%s/%s:%s" % (owner, repo, path.rstrip(".,;:")))
    stripped = URL_REF_RE.sub(" ", BLOB_RE.sub(" ", text))
    for owner, repo, num in SLUG_REF_RE.findall(stripped):
        refs.add("%s%s#%s" % (owner, repo, num))
    return refs


def find_clone(roots, owner, repo):
    for root in roots:
        for candidate in (os.path.join(root, owner, repo), os.path.join(root, repo)):
            if os.path.isdir(candidate):
                return candidate
    return None


def clone_resolver(roots):
    """File references resolve against local clones; anything else is unknown."""
    def resolve(ref):
        if "#" in ref or ":" not in ref:
            return None
        parsed = split_home(ref)
        if parsed is None:
            return None
        clone = find_clone(roots, parsed[0], parsed[1])
        if clone is None:
            return None
        return "present" if os.path.isfile(os.path.join(clone, parsed[2])) else "missing"
    return resolve


def clone_fetcher(roots):
    def fetch(owner, repo, path):
        clone = find_clone(roots, owner, repo)
        if clone is None:
            return None
        full = os.path.join(clone, path)
        if not os.path.isfile(full):
            return ""  # the clone is here and the file is not
        with open(full, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return fetch


def chain(*resolvers):
    def resolve(ref):
        for r in resolvers:
            state = r(ref)
            if state is not None:
                return state
        return None
    return resolve


def stale_lines(body, cutoff):
    found = []
    for n, line in enumerate(body.split("\n"), 1):
        dates = []
        for y, m, d in DATE_RE.findall(line):
            try:
                dates.append(datetime.date(int(y), int(m), int(d)))
            except ValueError:
                continue
        if dates and max(dates) < cutoff:
            found.append({"line": n, "date": max(dates).isoformat()})
    return found


def promotion(note, owner, repo, path, fetcher):
    """-> ("proposal", dict) | ("unverified", None) | ("done", None)."""
    target = fetcher(owner, repo, path)
    if target is None:
        return "unverified", None
    have = normalize(target)
    missing = [b for b in blocks(note["body"]) if normalize(b) not in have]
    if not missing:
        return "done", None
    base = os.path.basename(path)
    return "proposal", {
        "note": note["file"],
        "repo": "%s/%s" % (owner, repo),
        "path": path,
        "target_exists": target != "",
        "section": REPO_SPECIFIC if base == "AGENTS.md" else None,
        "narrow": base in NARROW_TARGETS and target != "",
        "text": "\n\n".join(missing),
    }


def audit(memory_dir, today, stale_days=30, dup_threshold=0.8, max_bytes=4096,
          resolver=None, fetcher=None):
    """The whole report as a dict. `resolver(ref)` -> state | None;
    `fetcher(owner, repo, path)` -> text, "" for a missing file, or None."""
    resolver = resolver or (lambda ref: None)
    fetcher = fetcher or (lambda owner, repo, path: None)
    cutoff = today - datetime.timedelta(days=stale_days)
    names = sorted(n for n in os.listdir(memory_dir)
                   if n.endswith(".md") and n != INDEX
                   and os.path.isfile(os.path.join(memory_dir, n)))
    notes = [parse_note(os.path.join(memory_dir, n)) for n in names]

    f = {k: [] for k in ("missing_home", "unparseable", "duplicates", "stale_dates",
                         "stale_refs", "oversize")}
    all_refs = set()
    promotions, unverified = [], []

    for note in notes:
        if note["error"]:
            f["unparseable"].append({"note": note["file"], "reason": note["error"]})
        note_type = note["meta"].get("type")
        is_user = isinstance(note_type, str) and note_type.strip() == "user"
        home = split_home(note["meta"].get("home"))
        if home is None and not is_user:
            f["missing_home"].append(note["file"])

        refs = references(note["body"] + "\n" + note.get("description", ""))
        if home is not None:
            refs.add("%s/%s:%s" % home)
        all_refs |= refs
        for ref in sorted(refs):
            state = resolver(ref)
            if state in STALE_STATES:
                is_home = home is not None and ref == "%s/%s:%s" % home
                f["stale_refs"].append({"note": note["file"], "ref": ref, "state": state,
                                        "home": is_home})

        lines = stale_lines(note["body"], cutoff)
        if lines:
            f["stale_dates"].append({"note": note["file"], "lines": lines})

        if home is not None and not is_user and not note["error"]:
            verdict, prop = promotion(note, home[0], home[1], home[2], fetcher)
            if verdict == "proposal":
                promotions.append(prop)
            elif verdict == "unverified":
                unverified.append(note["file"])

    words = {n["file"]: normalize(n.get("description", "") + " " + n["body"]).split()
             for n in notes}
    for i, a in enumerate(notes):
        for b in notes[i + 1:]:
            wa, wb = words[a["file"]], words[b["file"]]
            if not wa or not wb:
                continue
            ratio = difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()
            if ratio >= dup_threshold:
                f["duplicates"].append({"notes": [a["file"], b["file"]],
                                        "similarity": ratio})

    index_path = os.path.join(memory_dir, INDEX)
    index = {"present": os.path.isfile(index_path), "unindexed": [], "dangling": []}
    index_text = ""
    if index["present"]:
        with open(index_path, encoding="utf-8", errors="replace") as fh:
            index_text = fh.read()
    for note in notes:
        if not re.search(r"(?<![\w.-])%s(?![\w-])" % re.escape(note["file"]), index_text):
            index["unindexed"].append(note["file"])
    for target in LINK_RE.findall(index_text):
        target = target.split("#", 1)[0]
        if (target.endswith(".md") and "/" not in target and ":" not in target
                and target not in names):
            index["dangling"].append(target)
    index["dangling"] = sorted(set(index["dangling"]))

    for name in names + ([INDEX] if index["present"] else []):
        size = os.path.getsize(os.path.join(memory_dir, name))
        if size > max_bytes:
            f["oversize"].append({"file": name, "bytes": size})

    count = (sum(len(v) for v in f.values()) + len(index["unindexed"])
             + len(index["dangling"]) + (0 if index["present"] or not notes else 1))
    return {
        "version": 1,
        "today": today.isoformat(),
        "settings": {"stale_days": stale_days, "dup_threshold": dup_threshold,
                     "max_bytes": max_bytes},
        "notes": len(notes),
        "finding_count": count,
        "findings": dict(f, index=index),
        "references": sorted(all_refs),
        "promotions": promotions,
        "promotion_unverified": unverified,
    }


def summary(report):
    """Markdown that names files and counts, never note text."""
    f = report["findings"]
    out = ["# Memory audit %s" % report["today"], "",
           "%d notes, %d findings, %d proposed promotions." % (
               report["notes"], report["finding_count"], len(report["promotions"])), ""]

    def section(title, items):
        if items:
            out.append("- **%s** (%d): %s" % (title, len(items), ", ".join(items)))

    section("No repo home", f["missing_home"])
    section("Unparseable", [u["note"] for u in f["unparseable"]])
    section("Near-duplicates", ["%s ~ %s (%.2f)" % (d["notes"][0], d["notes"][1], d["similarity"])
                                for d in f["duplicates"]])
    section("Stale dates", ["%s (%d lines)" % (s["note"], len(s["lines"])) for s in f["stale_dates"]])
    section("Stale references", ["%s: %s %s%s" % (s["note"], s["ref"], s["state"],
                                                   " (home)" if s["home"] else "")
                                 for s in f["stale_refs"]])
    if not f["index"]["present"] and report["notes"]:
        out.append("- **Index**: MEMORY.md is missing")
    section("Not in MEMORY.md", f["index"]["unindexed"])
    section("MEMORY.md links to missing notes", f["index"]["dangling"])
    section("Over the size limit", ["%s (%d bytes)" % (o["file"], o["bytes"]) for o in f["oversize"]])
    section("Promotions", ["%s -> %s:%s%s" % (p["note"], p["repo"], p["path"],
                                              " (narrow)" if p["narrow"] else "")
                           for p in report["promotions"]])
    section("Promotion target not readable here", report["promotion_unverified"])
    if report["finding_count"] == 0 and not report["promotions"]:
        out.append("Clean.")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("memory_dir")
    ap.add_argument("--today", help="YYYY-MM-DD; defaults to the local date")
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--dup-threshold", type=float, default=0.8)
    ap.add_argument("--max-bytes", type=int, default=4096)
    ap.add_argument("--refs", help="JSON object: reference -> state")
    ap.add_argument("--clone-root", action="append", default=[])
    ap.add_argument("--summary", help="also write the Markdown summary here")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any finding")
    args = ap.parse_args(argv)

    if yaml is None:
        print("memory_audit: PyYAML is not importable", file=sys.stderr)
        return 2
    if not os.path.isdir(args.memory_dir):
        print("memory_audit: not a directory: %s" % args.memory_dir, file=sys.stderr)
        return 2
    try:
        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
    except ValueError:
        print("memory_audit: --today must be YYYY-MM-DD", file=sys.stderr)
        return 2
    table = {}
    if args.refs:
        try:
            with open(args.refs, encoding="utf-8") as fh:
                table = json.load(fh)
            if not isinstance(table, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            print("memory_audit: --refs is not a readable JSON object (%s)"
                  % exc.__class__.__name__, file=sys.stderr)
            return 2

    report = audit(args.memory_dir, today, args.stale_days, args.dup_threshold,
                   args.max_bytes,
                   resolver=chain(table.get, clone_resolver(args.clone_root)),
                   fetcher=clone_fetcher(args.clone_root))
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if args.summary:
        with open(args.summary, "w", encoding="utf-8") as fh:
            fh.write(summary(report))
    return 1 if args.strict and report["finding_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
