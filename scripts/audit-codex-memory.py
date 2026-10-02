#!/usr/bin/env python3
"""Audit Codex 0.160.0 memory provenance without editing managed memory.

Phase 1 content is UTF-8 JSON [raw_memory, rollout_summary, rollout_slug],
with ensure_ascii=False and separators=(',', ':'). File digests cover exact
bytes. Markdown records partition every byte: preamble followed by heading
sections ending at the next heading (any level), outside fenced code. Numeric
ordinal heading paths hide heading text and distinguish duplicate headings.
Ancestor heading digests bind a section to its reviewed scope; --show includes
that heading context. SQLite queries use private snapshots because even a
mode=ro WAL connection can update shared-memory bookkeeping on the original.
The sidecar is owned by this auditor, outside both Codex-managed roots.
Decision and limits: docs/decisions/0015-audit-codex-memories-after-generation-not-at-stop.md.
"""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile


NAMESPACES = ("memories", "memories_v2")
EXPECTED_COLUMNS = {
    "stage1_outputs": {
        "thread_id": "TEXT", "source_updated_at": "INTEGER",
        "raw_memory": "TEXT", "rollout_summary": "TEXT", "rollout_slug": "TEXT",
        "generated_at": "INTEGER", "usage_count": "INTEGER", "last_usage": "INTEGER",
        "selected_for_phase2": "INTEGER",
        "selected_for_phase2_source_updated_at": "INTEGER",
    },
    "jobs": {
        "kind": "TEXT", "job_key": "TEXT", "status": "TEXT", "worker_id": "TEXT",
        "ownership_token": "TEXT", "started_at": "INTEGER", "finished_at": "INTEGER",
        "lease_until": "INTEGER", "retry_at": "INTEGER", "retry_remaining": "INTEGER",
        "last_error": "TEXT", "input_watermark": "INTEGER",
        "last_success_watermark": "INTEGER",
    },
    "consolidation_progress": {"singleton": "INTEGER", "max_thread_count": "INTEGER"},
}
SURFACES = {"phase1", "ad-hoc", "consolidated", "skill"}


class AuditError(Exception):
    """An unsupported or unreadable state; messages never contain memory."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_id(record):
    framed = [record[k] for k in ("surface", "identity", "revision", "digest")]
    return hashlib.sha256(canonical(framed).encode("utf-8")).hexdigest()


def record(surface, identity, revision, content):
    result = {"surface": surface, "identity": identity, "revision": revision,
              "digest": hashlib.sha256(content).hexdigest()}
    result["id"] = record_id(result)
    result["content"] = content
    return result


def split_home(value):
    """Same vocabulary and parsing semantics as memory-home.sh split_home.

    This validates the locator form only, without resolving a local clone or
    fetching a URL. In particular, empty slash components are ignored by the
    existing hook; this parser deliberately preserves that behavior.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    prefix = "https://github.com/"
    if value.startswith(prefix):
        parts = [p for p in value[len(prefix):].split("/") if p]
        if len(parts) >= 5 and parts[2] == "blob":
            return parts[1], "/".join(parts[4:])
        return None
    if ":" in value:
        left, _, right = value.partition(":")
        owner_repo = [p for p in left.split("/") if p]
        right = right.strip().lstrip("/")
        if len(owner_repo) == 2 and right:
            return owner_repo[1], right
    return None


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def valid_identity(surface, identity):
    if not isinstance(identity, dict):
        return False
    if surface == "phase1":
        return (set(identity) == {"namespace", "thread_id"}
                and identity["namespace"] in NAMESPACES
                and isinstance(identity["thread_id"], str) and bool(identity["thread_id"]))
    if set(identity) not in ({"path"}, {"path", "section", "heading_path"}):
        return False
    path = identity.get("path")
    if (not isinstance(path, str) or not path or path.startswith("/")
            or ".." in path.split("/") or path.split("/")[0] not in NAMESPACES):
        return False
    if "section" in identity:
        section = identity["section"]
        headings = identity["heading_path"]
        return (isinstance(section, list) and bool(section)
                and all(type(n) is int and n >= 0 for n in section)
                and isinstance(headings, list)
                and len(headings) == (0 if section == [0] else len(section))
                and all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h)
                        for h in headings))
    return True


def load_sidecar(path):
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(stable_file_bytes(path).decode("utf-8"), object_pairs_hook=no_duplicate_keys)
        if (not isinstance(data, dict) or set(data) != {"version", "entries"}
                or type(data["version"]) is not int or data["version"] != 1
                or not isinstance(data["entries"], dict)):
            raise ValueError("unsupported sidecar schema")
        for key, entry in data["entries"].items():
            if not isinstance(entry, dict):
                raise ValueError("invalid sidecar entry")
            base = {"surface", "identity", "revision", "digest"}
            if set(entry) not in (base | {"homes"}, base | {"exempt"}):
                raise ValueError("invalid sidecar entry fields")
            if (entry["surface"] not in SURFACES
                    or not valid_identity(entry["surface"], entry["identity"])
                    or not isinstance(entry["digest"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["digest"])):
                raise ValueError("invalid sidecar record")
            if entry["surface"] == "phase1":
                if type(entry["revision"]) is not int:
                    raise ValueError("invalid source revision")
            elif entry["revision"] is not None:
                raise ValueError("invalid file revision")
            if "homes" in entry:
                if (not isinstance(entry["homes"], list) or not entry["homes"]
                        or any(split_home(home) is None for home in entry["homes"])):
                    raise ValueError("invalid home")
            elif entry["exempt"] != "user":
                raise ValueError("invalid exemption")
            if key != record_id(entry):
                raise ValueError("sidecar key does not match record")
        return data
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise AuditError("sidecar unreadable or invalid (%s)" % type(exc).__name__) from None


def reject_symlinks(path):
    """Reject every symlink component, including dangling symlinks."""
    for part in (path, *path.parents):
        if part.is_symlink():
            raise AuditError("symlink in inspected path")


def check_sidecar_location(sidecar, home):
    reject_symlinks(sidecar)
    resolved = sidecar.resolve()
    for namespace in NAMESPACES:
        root = (home / namespace).resolve()
        if resolved == root or root in resolved.parents:
            raise AuditError("sidecar must be outside managed memory roots")
        database = home / (namespace + "_1.sqlite")
        if resolved in {database.resolve(), Path(str(database) + "-wal").resolve(),
                        Path(str(database) + "-shm").resolve()}:
            raise AuditError("sidecar cannot be a memory database or SQLite companion")
        if sidecar.exists():
            for target in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
                if target.exists() and os.path.samefile(sidecar, target):
                    raise AuditError("sidecar aliases a memory database")


def check_config(home):
    if os.environ.get("CODEX_SQLITE_HOME"):
        raise AuditError("CODEX_SQLITE_HOME redirection is unsupported")
    config = home / "config.toml"
    if not config.exists():
        return
    reject_symlinks(config)
    try:
        import tomllib
        data = tomllib.loads(stable_file_bytes(config).decode("utf-8"))
    except (ImportError, OSError, ValueError, UnicodeError):
        raise AuditError("config.toml unreadable or TOML parser unavailable") from None
    # This CLI intentionally has one store root. Do not silently audit the wrong
    # DB when a selected Codex profile redirects sqlite_home elsewhere.
    def redirected(value):
        if isinstance(value, dict):
            return "sqlite_home" in value or any(redirected(v) for v in value.values())
        if isinstance(value, list):
            return any(redirected(v) for v in value)
        return False
    if redirected(data):
        raise AuditError("custom sqlite_home is unsupported; use an unredirected store")


def sqlite_records(path, namespace):
    """Query a private snapshot; even read-only WAL opens can change -shm.

    Read original DB/WAL/SHM only as bytes, query copied DB/WAL using mode=ro,
    and compare originals again afterward. Refuse moving sources. This detects
    ordinary concurrent rewrites, but is not an atomic cross-file or cross-store
    snapshot; the operator should audit after generation has quiesced.
    """
    reject_symlinks(path)
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    reject_symlinks(wal)
    reject_symlinks(shm)
    paths = (path, wal, shm)
    try:
        before = {}
        for source in paths:
            if source.exists():
                stat = source.stat()
                before[source] = ((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns),
                                  stable_file_bytes(source))
        with tempfile.TemporaryDirectory(prefix="codex-memory-audit-") as temporary:
            private = Path(temporary) / path.name
            for source in (path, wal):
                if source in before:
                    target = Path(temporary) / source.name
                    target.write_bytes(before[source][1])
                    target.chmod(0o600)
            result = sqlite_snapshot_records(private, namespace)
            after = {}
            for source in paths:
                reject_symlinks(source)
                if source.exists():
                    stat = source.stat()
                    after[source] = ((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns),
                                     stable_file_bytes(source))
            if before != after:
                raise AuditError("memory database changed during audit; retry after generation")
            return result
    except OSError as exc:
        raise AuditError("memory snapshot unreadable (%s)" % type(exc).__name__) from None


def sqlite_snapshot_records(path, namespace):
    # immutable=1 ignores live WAL content. Never use it. Opening only the
    # private snapshot with mode=ro permits scratch -shm creation without
    # touching Codex's original DB, WAL, or shared-memory file.
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] != 0:
                raise AuditError("unsupported memory database user_version")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            allowed = set(EXPECTED_COLUMNS) | {"_sqlx_migrations"}
            if not set(EXPECTED_COLUMNS) <= tables or tables - allowed:
                raise AuditError("unsupported memory database tables")
            for table, expected in EXPECTED_COLUMNS.items():
                # table names are constants, never interpolated file contents.
                columns = {r[1]: r[2].upper() for r in db.execute("PRAGMA table_info(" + table + ")")}
                if columns != expected:
                    raise AuditError("unsupported memory database columns")
            if "_sqlx_migrations" in tables:
                names = {r[1] for r in db.execute("PRAGMA table_info(_sqlx_migrations)")}
                if names != {"version", "description", "installed_on", "success", "checksum", "execution_time"}:
                    raise AuditError("unsupported migration metadata columns")
                migrations = list(db.execute("SELECT version, success FROM _sqlx_migrations ORDER BY version"))
                if migrations != [(1, 1), (2, 1)]:
                    raise AuditError("unsupported or unsuccessful memory migrations")
            rows = db.execute("SELECT thread_id, source_updated_at, raw_memory, rollout_summary, rollout_slug "
                              "FROM stage1_outputs ORDER BY thread_id").fetchall()
            result = []
            for thread, revision, raw, summary, slug in rows:
                if (not isinstance(thread, str) or not thread or type(revision) is not int
                        or not isinstance(raw, str) or not isinstance(summary, str)
                        or (slug is not None and not isinstance(slug, str))):
                    raise AuditError("unsupported Phase 1 row values")
                content = canonical([raw, summary, slug]).encode("utf-8")
                result.append(record("phase1", {"namespace": namespace, "thread_id": thread}, revision, content))
            return result
    except (sqlite3.Error, OSError, UnicodeError) as exc:
        raise AuditError("memory database unreadable (%s)" % type(exc).__name__) from None


def markdown_sections(content):
    """Partition exact bytes with lexical ATX/setext headings and code fences.

    No Markdown is rendered. ATX accepts at most three leading spaces; setext
    underlines consume the preceding nonblank line. Heading ordinals are 1-based
    within their nearest preceding lower-level heading, with [0] for preamble.
    Ancestor heading-byte digests also form part of the identity, so renaming a
    parent invalidates its descendants. Empty files have no records. This favors reviewable sections
    over attempting to infer individual model-merged facts.
    """
    lines = content.splitlines(keepends=True)
    boundaries = []
    fence = None
    previous = None
    offset = 0
    for line in lines:
        stripped = line.rstrip(b"\r\n")
        marker = re.match(rb"^ {0,3}(`{3,}|~{3,})(.*)$", stripped)
        if fence is not None:
            char, size = fence
            if re.fullmatch(rb" {0,3}" + re.escape(char) + b"{" + str(size).encode() + rb",}[ \t]*", stripped):
                fence = None
            previous = None
        elif marker and (marker[1][:1] == b"~" or b"`" not in marker[2]):
            fence = (marker[1][:1], len(marker[1]))
            previous = None
        else:
            atx = re.match(rb"^ {0,3}(#{1,6})(?:[ \t]+|$)", stripped)
            setext = re.fullmatch(rb" {0,3}(=+|-+)[ \t]*", stripped)
            if atx:
                boundaries.append((offset, len(atx[1]), line))
                previous = None
            elif setext and previous is not None:
                boundaries.append((previous, 1 if setext[1][:1] == b"=" else 2,
                                   content[previous:offset + len(line)]))
                previous = None
            else:
                previous = offset if stripped.strip() else None
        offset += len(line)
    if not content:
        return []
    sections = []
    if not boundaries or boundaries[0][0] > 0:
        sections.append(([0], [], [], content[:boundaries[0][0]] if boundaries else content))
    stack = []
    sibling_counts = {}
    for index, (start, level, heading_bytes) in enumerate(boundaries):
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = tuple(stack[-1][1]) if stack else ()
        sibling_counts[parent] = sibling_counts.get(parent, 0) + 1
        locator = list(parent) + [sibling_counts[parent]]
        heading_path = (stack[-1][2] if stack else []) + [hashlib.sha256(heading_bytes).hexdigest()]
        heading_context = (stack[-1][3] if stack else []) + [heading_bytes]
        stack.append((level, locator, heading_path, heading_context))
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(content)
        sections.append((locator, heading_path, heading_context, content[start:end]))
    return sections


def walk_files(root):
    reject_symlinks(root)
    if not root.exists():
        return []
    if not root.is_dir():
        raise AuditError("memory surface root is not a directory")
    result = []
    def unreadable(exc):
        raise AuditError("memory surface walk unreadable (%s)" % type(exc).__name__) from None
    for base, directories, files in os.walk(root, followlinks=False, onerror=unreadable):
        # Internal extension workspace Git metadata is tooling, not memory.
        directories[:] = sorted(d for d in directories if d != ".git")
        for name in directories + sorted(files):
            child = Path(base) / name
            reject_symlinks(child)
            if not child.is_dir():
                if not child.is_file():
                    raise AuditError("unsupported special file in memory surface")
                result.append(child)
    return sorted(result)


def stable_file_bytes(path):
    """Read an unchanged regular file twice; reject visible writer movement."""
    reject_symlinks(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise AuditError("memory surface is not a regular file")
    content = path.read_bytes()
    reject_symlinks(path)
    middle = path.lstat()
    repeated = path.read_bytes()
    reject_symlinks(path)
    after = path.lstat()
    def signature(value):
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns)
    if (signature(before) != signature(middle) or signature(before) != signature(after)
            or len(content) != before.st_size or content != repeated):
        raise AuditError("memory file changed during audit; retry after generation")
    return content


def file_records(home, namespace):
    root = home / namespace
    reject_symlinks(root)
    if root.exists() and not root.is_dir():
        raise AuditError("memory store root is not a directory")
    targets = []
    for name in ("MEMORY.md", "memory_summary.md", "raw_memories.md"):
        path = root / name
        reject_symlinks(path)
        if path.exists():
            targets.append(("consolidated", path, True))
    for path in walk_files(root / "rollout_summaries"):
        if path.suffix.lower() == ".md":
            targets.append(("consolidated", path, True))
    for path in walk_files(root / "skills"):
        targets.append(("skill", path, path.suffix.lower() == ".md"))
    for path in walk_files(root / "extensions" / "ad_hoc" / "notes"):
        targets.append(("ad-hoc", path, False))
    result = []
    for surface, path, markdown in targets:
        content = stable_file_bytes(path)
        identity = {"path": path.relative_to(home).as_posix()}
        if markdown:
            for locator, heading_path, heading_context, section in markdown_sections(content):
                item = record(surface, dict(identity, section=locator, heading_path=heading_path), None, section)
                item["heading_context"] = heading_context
                result.append(item)
        elif content:
            result.append(record(surface, identity, None, content))
    return result


def scan(home):
    reject_symlinks(home)
    check_config(home)
    if home.exists() and not home.is_dir():
        raise AuditError("Codex home is not a directory")
    if home.exists():
        known_names = set(NAMESPACES)
        for namespace in NAMESPACES:
            known_names.update(namespace + "_1.sqlite" + suffix for suffix in ("", "-wal", "-shm"))
        for path in home.iterdir():
            if path.name.startswith("memories") and path.name not in known_names:
                raise AuditError("unknown memory store layout or database version")
    records = []
    for namespace in NAMESPACES:
        database = home / (namespace + "_1.sqlite")
        reject_symlinks(database)
        if not database.exists():
            for suffix in ("-wal", "-shm"):
                companion = Path(str(database) + suffix)
                reject_symlinks(companion)
                if companion.exists():
                    raise AuditError("memory database companion exists without its database")
        if database.exists():
            records.extend(sqlite_records(database, namespace))
        records.extend(file_records(home, namespace))
    return records


def codex_version():
    # Bootstrap may create helper directories even for --version. Use an
    # independent temporary profile, never the inspected store, and pass no
    # credential-bearing environment variables. The installed version does
    # not depend on a user's configuration.
    try:
        with tempfile.TemporaryDirectory(prefix="codex-memory-version-") as temporary:
            env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": temporary,
                   "CODEX_HOME": temporary, "CLAUDE_CONFIG_DIR": temporary,
                   "LANG": "C.UTF-8"}
            result = subprocess.run(["codex", "--version"], env=env, capture_output=True,
                                    text=True, timeout=5, check=False)
            version = result.stdout.strip()
            if result.returncode == 0 and re.fullmatch(r"codex-cli [0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version):
                return version
    except (OSError, subprocess.SubprocessError, UnicodeError):
        pass
    return "undetectable"


def atomic_sidecar(path, data):
    if not path.parent.is_dir():
        raise AuditError("sidecar parent directory does not exist")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".memory-homes-", delete=False) as output:
            temporary = output.name
            os.chmod(temporary, 0o600)
            json.dump(data, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        # Validate exactly what will be installed, rather than trusting a
        # serializer or accidentally dropping an existing reviewed entry.
        if load_sidecar(Path(temporary)) != data:
            raise AuditError("sidecar serialization check failed")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def arguments(argv):
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS permits global options either before or after the subcommand.
    common.add_argument("--codex-home", default=argparse.SUPPRESS)
    common.add_argument("--sidecar", default=argparse.SUPPRESS)
    common.add_argument("--show", action="store_true", default=argparse.SUPPRESS,
                        help="include reviewed content; may contain personal data")
    parser = argparse.ArgumentParser(description=__doc__, parents=[common])
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("audit", parents=[common], help="report unattested records (default)")
    attest = commands.add_parser("attest", parents=[common], help="attest a currently reviewed record")
    attest.add_argument("--record", required=True)
    choice = attest.add_mutually_exclusive_group(required=True)
    choice.add_argument("--home", action="append", help="repo:path or GitHub blob URL; repeatable")
    choice.add_argument("--exempt-user", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    command = args.command or "audit"
    home = Path(getattr(args, "codex_home", None) or os.environ.get("CODEX_HOME")
                or "~/.codex").expanduser().absolute()
    sidecar = Path(getattr(args, "sidecar", None) or home / "memory-homes.json").expanduser().absolute()
    if command == "audit":
        print(canonical({"event": "store", "codex_version": codex_version(),
                         "codex_home": str(home), "roots": [str(home / n) for n in NAMESPACES]}))
    try:
        check_sidecar_location(sidecar, home)
        try:
            attestations = load_sidecar(sidecar)
        except AuditError:
            if command == "attest":
                print("audit-codex-memory: refused-invalid-sidecar; no write", file=sys.stderr)
                return 3
            raise
        records = scan(home)
        if command == "attest":
            if args.home and any(split_home(value) is None for value in args.home):
                raise AuditError("home must be owner/repo:path or a GitHub blob URL")
            selected = next((r for r in records if r["id"] == args.record), None)
            if selected is None:
                raise AuditError("record is not current; audit and review it again")
            entry = {key: selected[key] for key in ("surface", "identity", "revision", "digest")}
            if args.home:
                entry["homes"] = args.home
            else:
                entry["exempt"] = "user"
            if selected["id"] not in {r["id"] for r in scan(home)}:
                raise AuditError("record changed during attestation; review it again")
            attestations["entries"][selected["id"]] = entry
            atomic_sidecar(sidecar, attestations)
            print(canonical({"event": "attested", "id": selected["id"]}))
            return 0
        findings = 0
        for item in records:
            attested = attestations["entries"].get(item["id"])
            verdict = "unattested" if attested is None else ("homed" if "homes" in attested else "exempt-user")
            findings += verdict == "unattested"
            output = {k: v for k, v in item.items() if k not in {"content", "heading_context"}}
            output["verdict"] = verdict
            if getattr(args, "show", False):
                # Preserve arbitrary bytes in review output without replacing
                # invalid UTF-8: JSON explicitly identifies the hex encoding.
                try:
                    output["content"] = item["content"].decode("utf-8")
                except UnicodeDecodeError:
                    output["content_hex"] = item["content"].hex()
                if "heading_context" in item:
                    output["heading_context"] = []
                    for heading in item["heading_context"]:
                        try:
                            output["heading_context"].append({"text": heading.decode("utf-8")})
                        except UnicodeDecodeError:
                            output["heading_context"].append({"hex": heading.hex()})
            print(canonical(output))
        if not records:
            print("audit-codex-memory: no memory store (absent or empty supported surfaces)")
        print(canonical({"event": "result", "records": len(records), "findings": findings}))
        return 1 if findings else 0
    except (AuditError, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, AuditError) else type(exc).__name__
        print("audit-codex-memory: unsupported/unreadable: " + reason, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
