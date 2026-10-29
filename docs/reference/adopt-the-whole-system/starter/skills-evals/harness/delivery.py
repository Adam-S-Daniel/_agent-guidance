"""ADR 0012 part 2: deliver a frozen deployed context to both arms of a pair.

`context.resolve_context` freezes what a repository deploys (its adopted skill
bundles and its fleet guidance). This module turns that frozen context into
an arm's scratch user profile and takes the subject out of it, or adds it:

- skills become offline, skill-only local plugins: one directory marketplace
  inside the scratch `CLAUDE_CONFIG_DIR`, registered in that profile's
  `known_marketplaces.json`, `installed_plugins.json` and `settings.json`
  (`enabledPlugins`). Each plugin holds `.claude-plugin/plugin.json` (name and
  version only) and `skills/<skill>/` with every file of the skill, so the
  catalog names stay `bundle:skill` and no plugin hook or MCP server exists;
- guidance goes through the pinned, real `fleet-memory.sh` hook by way of
  `guidance.deliver`, with a fixed payload timestamp so the hook's delivery
  stamp is the same in both arms;
- the subject is resolved to one identity (registry, bundle, skill, digest)
  or one guidance section (file, heading, exact extent), and is removed from
  the `without` arm when the context adopts it, or added to the `with` arm
  when it does not.

Nothing here launches a model or reads a credential. In-place runs are
cloud-only (ADR 0012, "Decisions on the build"): `require_hosted` refuses a
local run with `in_place_local_unsupported`, and no credential adapter exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

import yaml

import context
import guidance

PAIRINGS = ("in_place", "isolation")
# Part 2 builds in-place pairs; the default switch is part 4.
DEFAULT_PAIRING = "isolation"
MARKETPLACE = "skills-evals-context"
PLUGIN_VERSION = "0.0.0-context"
# The registration files carry this time, never the clock.
REGISTERED_AT = "2026-01-01T00:00:00.000Z"
# The payload file's mtime. The hook stamps a delivery with the payload's last
# commit time, or with its mtime when it sits dirty in a work tree, or with 0
# outside one; fixing the mtime makes the stamp the same in every arm.
PAYLOAD_MTIME = 0
# What the hook prints before the payload inside its marked block: metadata
# comments, one per line.
_BLOCK_METADATA = b"<!-- fleet-guidance-"
_END_MARK = b"<!-- END FLEET GUIDANCE -->"
# A pinned hook may drop base.md's repository header from user memory
# (_agent-guidance's fleet-memory.sh, PAYLOAD_REPO_HEADER).
_REPO_HEADER = ("# AGENTS.md\n\n> **Managed by [`_agent-guidance`].**\n"
                "> Edit only below the `## Repo-specific additions` header.\n"
                "> Everything above it will be overwritten on the next sync.\n\n"
                ).encode("utf-8")
# Set in a hosted Claude Code session (the eval routine, ADR 0010), whose
# nested `claude` authenticates with no credential in reach.
_HOSTED_SESSION = "CLAUDE_CODE_REMOTE_SESSION_ID"
_HOSTED_ENTRYPOINTS = ("remote",)


class DeliveryError(guidance.GuidanceError):
    """A named refusal: exit 2, no paid call."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def check_pairing(value) -> str:
    if value not in PAIRINGS:
        raise DeliveryError("invalid_pairing", f"--pairing must be one of {', '.join(PAIRINGS)}")
    return value


def hosted(environ: Mapping[str, str]) -> bool:
    """Whether this process runs inside a hosted Claude Code session."""
    entrypoint = environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    return bool(environ.get(_HOSTED_SESSION)) or entrypoint.startswith(_HOSTED_ENTRYPOINTS)


def require_hosted(environ: Mapping[str, str]) -> None:
    """In-place arms run in a scratch profile with no login in it. In the
    routine the nested CLI still authenticates (ADR 0010); locally nothing
    would, and copying a credential in is deferred to its own review."""
    if not hosted(environ):
        raise DeliveryError(
            "in_place_local_unsupported",
            "an in-place pair runs only in the hosted eval routine, where the "
            "nested CLI authenticates with no credential in reach; a local "
            "credential adapter is not built (ADR 0012). Run --pairing isolation "
            "locally")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# subjects


@dataclass(frozen=True)
class SkillSubject:
    registry: str
    bundle: str
    skill: str
    action: str  # "removed" from `without`, or "added" to `with`
    deployed_digest: str | None
    tested_digest: str
    files: Mapping[str, bytes]
    modes: Mapping[str, str]

    def record(self) -> dict:
        return {"kind": "skill", "registry": self.registry, "bundle": self.bundle,
                "skill": self.skill, "action": self.action,
                "deployed_digest": self.deployed_digest,
                "tested_digest": self.tested_digest,
                "bytes": sum(len(raw) for raw in self.files.values())}


def _split_name(name: str) -> tuple[str | None, str]:
    if not isinstance(name, str) or name.count(":") > 1:
        raise DeliveryError("invalid_subject", "a skill subject is SKILL or BUNDLE:SKILL")
    bundle, _, skill = name.rpartition(":")
    for part in ((bundle,) if bundle else ()) + (skill,):
        if not context._NAME.fullmatch(part):
            raise DeliveryError("invalid_subject", "a skill subject is SKILL or BUNDLE:SKILL")
    return bundle or None, skill


def _tree_files(root: Path) -> tuple[dict, dict]:
    """A registry checkout's skill directory as raw bytes and Git modes.
    Symlinks and special files fail closed: they are not skill content."""
    if root.is_symlink() or not root.is_dir():
        raise DeliveryError("unsafe_path", "a skill source must be a regular directory")
    files, modes = {}, {}
    for current, dirs, names in os.walk(root):
        for name in sorted(dirs + names):
            path = Path(current) / name
            st = os.lstat(path)
            rel = path.relative_to(root).as_posix()
            if stat.S_ISLNK(st.st_mode) or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                raise DeliveryError("unsafe_path", f"{root.name}/{rel} is not a regular file or directory")
            if stat.S_ISREG(st.st_mode):
                context._path(rel)
                files[rel] = path.read_bytes()
                modes[rel] = "100755" if st.st_mode & 0o111 else "100644"
    if "SKILL.md" not in files:
        raise DeliveryError("skill_not_found", f"{root.name} has no SKILL.md")
    return files, modes


def _identity(entry: dict) -> str:
    """A registries.yml entry's `OWNER/REPO`, from its GitHub URL."""
    url = str(entry.get("url", ""))
    prefix = "https://github.com/"
    identity = url[len(prefix):].rstrip("/").removesuffix(".git") if url.startswith(prefix) else ""
    try:
        return context._repository(identity, "invalid_registry")
    except context.ContextError as exc:
        raise DeliveryError("invalid_registry", f"registry {entry.get('name')!r} has no "
                            "github.com/OWNER/REPO url") from exc


def _registry_matches(registries: Mapping[str, dict], bundle: str | None, skill: str) -> list:
    out = []
    for name, entry in sorted(registries.items()):
        path, layout = Path(entry["path"]), entry["layout"]
        parts = layout.split("/")
        if not path.is_dir() or parts[-1] != "SKILL.md":
            continue
        pattern = "/".join([*parts[:-2], skill, "SKILL.md"])
        for match in sorted(path.glob(pattern)):
            if not match.resolve().is_relative_to(path.resolve()):
                raise DeliveryError("unsafe_path", "a registry skill escapes its checkout through a symlink")
            if not match.is_file():
                continue
            rel = match.relative_to(path).parts
            # plugins/<bundle>/skills/<skill>/SKILL.md names its bundle; a flat
            # registry (skills/<skill>/) is one bundle named after itself.
            found = rel[parts.index("*")] if parts.count("*") > 1 else name
            if bundle is None or found == bundle:
                out.append((_identity(entry), found, match.parent))
    return out


def resolve_skill_subject(frozen: context.FrozenContext, name: str,
                          registries: Mapping[str, dict], *, registry: str | None = None,
                          candidate: Path | None = None) -> SkillSubject:
    """The one skill `name` (SKILL or BUNDLE:SKILL) names.

    Adopted by the context: the deployed copy, removed from `without` (the
    deployed version is what a keep-or-remove run tests). Not adopted: the
    unique copy in the run's registry checkouts, added to `with`. Two copies
    are an error, never a sorted pick.
    """
    bundle, skill = _split_name(name)
    if registry is not None:
        context._repository(registry, "invalid_subject")
    adopted = [tree for tree in frozen.skills
               if tree.skill == skill and (bundle is None or tree.bundle == bundle)
               and (registry is None or tree.registry == registry)]
    if len(adopted) > 1:
        raise DeliveryError("ambiguous_skill", f"{skill} is adopted from "
                            f"{len(adopted)} bundles ({', '.join(t.bundle for t in adopted)}); "
                            "qualify it as BUNDLE:SKILL")
    if adopted:
        tree = adopted[0]
        subject = SkillSubject(tree.registry, tree.bundle, tree.skill, "removed",
                               tree.digest, tree.digest, tree.files, tree.modes)
        return _skill_candidate(subject, candidate)
    matches = [match for match in _registry_matches(registries, bundle, skill)
               if registry is None or match[0] == registry]
    if not matches:
        raise DeliveryError("skill_not_found", f"{name} is neither adopted by the context "
                            "nor present in a registry checkout")
    if len(matches) > 1:
        raise DeliveryError("ambiguous_skill", f"{name} matches {len(matches)} registry "
                            f"copies ({', '.join(f'{r}:{b}' for r, b, _ in matches)}); "
                            "qualify it as BUNDLE:SKILL")
    registry, found, path = matches[0]
    context._name(found, "subject bundle")
    files, modes = _tree_files(path)
    if any(tree.bundle == found and tree.registry != registry for tree in frozen.skills):
        raise DeliveryError("catalog_collision", "subject bundle is deployed from another registry")
    subject = SkillSubject(registry, found, skill, "added", None,
                           context._file_digest(files), files, modes)
    return _skill_candidate(subject, candidate)


def _skill_candidate(subject: SkillSubject, candidate: Path | None) -> SkillSubject:
    if candidate is None:
        return subject
    try:
        files, modes = _tree_files(Path(candidate))
    except OSError as exc:
        raise DeliveryError("candidate_unreadable", "skill candidate could not be read") from exc
    # The CLI may use front matter's name for its catalog entry. A candidate
    # cannot rename the identity while retaining its directory's old name.
    context._catalog(subject.bundle, subject.skill, files["SKILL.md"])
    if _catalog_name(files["SKILL.md"]) not in (None, subject.skill):
        raise DeliveryError("candidate_identity_mismatch", "candidate catalog name differs from the subject")
    return replace(subject, files=files, modes=modes, tested_digest=context._file_digest(files))


def _catalog_name(raw: bytes) -> str | None:
    context._catalog("catalog", "entry", raw)
    lines = raw.decode("utf-8").splitlines()
    end = next(i for i, line in enumerate(lines[1:], 1) if line == "---")
    metadata = yaml.safe_load("\n".join(lines[1:end]))
    name = metadata.get("name")
    if name is not None and (not isinstance(name, str) or context._controls(name)):
        raise DeliveryError("invalid_skill_metadata", "catalog name must be plain text")
    return name


@dataclass(frozen=True)
class SectionSubject:
    section: str
    file: str
    heading: str
    action: str
    # The extent in its source file, in characters and in UTF-8 bytes.
    start_char: int
    end_char: int
    start_byte: int
    end_byte: int
    raw: bytes  # the source file's bytes
    deployed_digest: str | None
    tested_digest: str
    tested_raw: bytes

    def record(self) -> dict:
        return {"kind": "guidance", "section": self.section, "file": self.file,
                "heading": self.heading, "action": self.action,
                "start_char": self.start_char, "end_char": self.end_char,
                "start_byte": self.start_byte, "end_byte": self.end_byte,
                "bytes": self.end_byte - self.start_byte, "tested_bytes": len(self.tested_raw),
                "deployed_digest": self.deployed_digest,
                "tested_digest": self.tested_digest}


def _extent(raw: bytes, heading: str, where: str) -> tuple[int, int, int, int]:
    """`heading`'s `##` extent in `raw`: guidance.h2_extents' arithmetic (a
    real Markdown parse, so a fenced `##` is not a heading), on the raw bytes
    decoded without newline translation, as character and byte offsets."""
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise DeliveryError("invalid_guidance_encoding", f"{where} is not UTF-8") from exc
    try:
        span = guidance._extent_of(text, heading, where)
    except guidance.GuidanceError as exc:
        raise DeliveryError("section_not_found", str(exc)) from exc
    start, end = span["start"], span["end"]
    return start, end, len(text[:start].encode("utf-8")), len(text[:end].encode("utf-8"))


def resolve_section_subject(frozen: context.FrozenContext, section: str,
                            repositories: Mapping[str, Path], *,
                            candidate: Path | None = None) -> SectionSubject:
    """The section's row in the pinned manifest, located in its source file.
    In a delivered file it is removed from `without`; in an opt-in file the
    repository did not adopt, it is added to `with`."""
    rows = [row for row in frozen.guidance_rows if row.get("id") == section]
    if len(rows) != 1:
        raise DeliveryError("section_not_found", f"{section!r} is not one row of the "
                            "pinned guidance manifest")
    row = rows[0]
    path, heading = row.get("file"), row.get("heading")
    if not isinstance(path, str) or not isinstance(heading, str) or not heading:
        raise DeliveryError("section_not_found", f"{section!r} has no file and heading")
    delivered = dict(frozen.guidance_files)
    if path in delivered:
        raw, action = delivered[path], "removed"
    else:
        raw, action = context.read_guidance_file(frozen, path, repositories), "added"
    start_char, end_char, start, end = _extent(raw, heading, path)
    digest = _sha256(raw[start:end])
    tested_raw = raw[start:end]
    if candidate is not None:
        candidate = Path(candidate)
        if candidate.is_symlink() or not candidate.is_file():
            raise DeliveryError("unsafe_path", "a guidance candidate must be a regular file")
        try:
            candidate_raw = candidate.read_bytes()
        except OSError as exc:
            raise DeliveryError("candidate_unreadable", "guidance candidate could not be read") from exc
        _, _, candidate_start, candidate_end = _extent(candidate_raw, heading, "guidance candidate")
        tested_raw = candidate_raw[candidate_start:candidate_end]
    return SectionSubject(section, path, heading, action, start_char, end_char, start,
                          end, raw, digest if action == "removed" else None,
                          _sha256(tested_raw), tested_raw)


def guidance_payload(frozen: context.FrozenContext, subject: SectionSubject | None,
                     role: str) -> bytes:
    """The guidance bytes an arm gets. Without a section subject, or for the
    arm that keeps the context as deployed, the frozen assembly itself.
    Otherwise the section's extent is cut from its own source file (the
    file's introduction, sibling sections and the assembly's delimiters
    stay), or appended as one more delimited source."""
    files = [(path, raw) for path, raw in frozen.guidance_files]
    if subject is not None:
        if subject.action == "removed" and role == "without":
            files = [(path, raw[:subject.start_byte] + raw[subject.end_byte:]
                      if path == subject.file else raw) for path, raw in files]
        elif role == "with" and subject.action == "removed":
            files = [(path, raw[:subject.start_byte] + subject.tested_raw + raw[subject.end_byte:]
                      if path == subject.file else raw) for path, raw in files]
        elif subject.action == "added" and role == "with":
            files.append((subject.file, subject.tested_raw))
    payload = files[0][1] + b"".join(b"\n" + raw for _, raw in files[1:])
    if subject is None or (subject.action == "added" and role == "without") or (
            subject.action == "removed" and role == "with"
            and subject.tested_digest == subject.deployed_digest):
        if payload != frozen.guidance:
            raise DeliveryError("guidance_assembly_mismatch",
                                "per-file assembly differs from the frozen guidance")
    return payload


# ---------------------------------------------------------------------------
# skills as plugins


def plugins_root(config: Path) -> Path:
    return Path(config) / "plugins" / "marketplaces" / MARKETPLACE / "plugins"


def _write(path: Path, raw: bytes, mode: str = "100644") -> None:
    """A new file, never a merge: an existing destination is an error."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as handle:
        handle.write(raw)
    os.chmod(path, 0o755 if mode == "100755" else 0o644)


def _write_skill(root: Path, files: Mapping[str, bytes], modes: Mapping[str, str]) -> None:
    if os.path.lexists(root):
        raise DeliveryError("install_collision", f"{root.parent.parent.name}:{root.name} "
                            "is already installed")
    root.mkdir(parents=True)
    for rel, raw in sorted(files.items()):
        context._path(rel)
        _write(root / rel, raw, modes.get(rel, "100644"))


def install_plugins(config: Path, skills, bundles) -> None:
    """Register `bundles` as local plugins and write each skill tree into its
    bundle. Every arm of a pair registers the same bundles; only the
    subject's own directory differs between them."""
    config = Path(config)
    market = config / "plugins" / "marketplaces" / MARKETPLACE
    root = plugins_root(config)
    bundles = sorted(set(bundles))
    for bundle in bundles:
        context._name(bundle, "bundle")
        _write(root / bundle / ".claude-plugin" / "plugin.json",
               json.dumps({"name": bundle, "version": PLUGIN_VERSION}).encode())
        (root / bundle / "skills").mkdir(parents=True, exist_ok=True)
    for tree in skills:
        _write_skill(root / tree.bundle / "skills" / tree.skill, tree.files, tree.modes)
    _write(market / ".claude-plugin" / "marketplace.json", json.dumps(
        {"name": MARKETPLACE, "owner": {"name": "skills-evals"},
         "plugins": [{"name": b, "source": f"./plugins/{b}"} for b in bundles]},
        indent=2).encode())
    _write(config / "plugins" / "known_marketplaces.json", json.dumps(
        {MARKETPLACE: {"source": {"source": "directory", "path": str(market)},
                       "installLocation": str(market), "lastUpdated": REGISTERED_AT}},
        indent=2).encode())
    # Recorded as installed, so the CLI resolves the directory source and
    # writes nothing into the profile (measured, CLI 2.1.293).
    _write(config / "plugins" / "installed_plugins.json", json.dumps(
        {"version": 2, "plugins": {f"{b}@{MARKETPLACE}": [
            {"scope": "user", "installPath": str(root / b), "version": PLUGIN_VERSION,
             "installedAt": REGISTERED_AT, "lastUpdated": REGISTERED_AT}] for b in bundles}},
        indent=2).encode())
    _write(config / "settings.json", json.dumps(
        {"enabledPlugins": {f"{b}@{MARKETPLACE}": True for b in bundles}}, indent=2).encode())


def remove_skill(config: Path, subject: SkillSubject) -> None:
    """Take the subject's verified directory out of its plugin."""
    path = plugins_root(config) / subject.bundle / "skills" / subject.skill
    files, _ = _tree_files(path)
    if context._file_digest(files) != subject.deployed_digest:
        raise DeliveryError("subject_digest_mismatch", f"{subject.bundle}:{subject.skill} "
                            "does not hold the deployed bytes")
    shutil.rmtree(path)


def add_skill(config: Path, subject: SkillSubject) -> None:
    _write_skill(plugins_root(config) / subject.bundle / "skills" / subject.skill,
                 subject.files, subject.modes)


def installed_skills(config: Path) -> dict[str, str]:
    """`bundle:skill` -> content digest, read back from the profile."""
    out = {}
    root = plugins_root(config)
    if not root.is_dir():
        return out
    for bundle in sorted(p for p in root.iterdir() if p.is_dir()):
        skills = bundle / "skills"
        for skill in sorted(p for p in skills.iterdir()) if skills.is_dir() else ():
            files, _ = _tree_files(skill)
            out[f"{bundle.name}:{skill.name}"] = context._file_digest(files)
    return out


def check_skills(config: Path, workspace: Path, expected: Mapping[str, str],
                 subject: SkillSubject | None, role: str, *, home: Path | None = None) -> None:
    """The filesystem proof: exactly the expected plugin skills, and the
    subject present once in `with`, nowhere in `without`, under any name."""
    found = installed_skills(config)
    if found != dict(expected):
        raise DeliveryError("context_manifest_mismatch", "installed skills differ from "
                            "the arm's expected manifest")
    if subject is None:
        return
    digests = {subject.tested_digest, subject.deployed_digest} - {None}
    identity = f"{subject.bundle}:{subject.skill}"
    # The frozen manifest binds this bundle to subject.registry; its catalog
    # key and the verified digest identify the deployed or candidate copy.
    copies = [name for name, digest in found.items() if name == identity and digest in digests]
    aliases = [name for name, digest in found.items() if name != identity and digest in digests]
    roots = [Path(workspace) / ".claude", Path(config)]
    if home is not None:
        roots.append(Path(home) / ".claude")
    canonical = plugins_root(config).resolve()
    for root in roots:
        for current, dirs, names in os.walk(root, followlinks=False):
            directory = Path(current)
            if directory.resolve() == canonical:
                dirs[:] = []
                continue
            if any((directory / name).is_symlink() for name in dirs):
                raise DeliveryError("subject_alias_present", "an unverified plugin or skill alias is present")
            if "SKILL.md" not in names:
                continue
            files, _ = _tree_files(directory)
            catalog_name = _catalog_name(files["SKILL.md"])
            if (directory.name == subject.skill or context._file_digest(files) in digests
                    or (catalog_name is not None and catalog_name.rsplit(":", 1)[-1] == subject.skill)):
                aliases.append(directory.name)
    for name in found:
        bundle, skill = name.split(":", 1)
        catalog_name = _catalog_name((plugins_root(config) / bundle / "skills" / skill / "SKILL.md").read_bytes())
        catalog_identity = (catalog_name if catalog_name is not None and ":" in catalog_name
                            else f"{bundle}:{catalog_name}")
        if catalog_identity == identity and name != identity:
            aliases.append(name)
    if aliases:
        raise DeliveryError("subject_alias_present", f"{subject.skill} also reaches the "
                            "arm outside its plugin")
    if len(copies) != (1 if role == "with" else 0):
        raise DeliveryError("subject_presence_mismatch",
                            f"{role} arm holds {len(copies)} copies of {subject.skill}")


def expected_skills(frozen: context.FrozenContext, subject: SkillSubject | None,
                    role: str) -> dict[str, str]:
    out = {f"{t.bundle}:{t.skill}": t.digest for t in frozen.skills}
    if subject is not None:
        name = f"{subject.bundle}:{subject.skill}"
        if subject.action == "removed" and role == "without":
            del out[name]
        elif role == "with":
            out[name] = subject.tested_digest
    return out


def skills_digest(expected: Mapping[str, str]) -> str:
    return _sha256(json.dumps(sorted(expected.items()), separators=(",", ":")).encode())


# ---------------------------------------------------------------------------
# guidance


def deliver_guidance(hook: bytes | None, payload: bytes, *, scratch: Path,
                     home: Path, config: Path) -> dict:
    """Run the pinned hook on `payload` into `config/CLAUDE.md` and prove the
    marked block holds exactly those bytes."""
    if hook is None:
        raise DeliveryError("missing_object", "the pinned guidance revision has no "
                            "fleet-memory hook to deliver with")
    hook_dir = Path(scratch) / "guidance-hook"
    hook_path = hook_dir / guidance.HOOK_REL
    if not os.path.lexists(hook_path):
        _write(hook_path, hook, "100755")
    elif hook_path.is_symlink() or hook_path.read_bytes() != hook:
        raise DeliveryError("delivery_failed", "the materialized hook changed between deliveries")
    info = guidance.deliver(hook_dir, scratch=Path(scratch), dest_dir=Path(config),
                            home=Path(home), payload=payload, payload_mtime=PAYLOAD_MTIME)
    if info["returncode"] != 0 or not info["installed"]:
        raise DeliveryError("delivery_failed", f"the hook exited {info['returncode']} and "
                            f"the marked block is {'present' if info['installed'] else 'absent'}")
    raw = (Path(config) / "CLAUDE.md").read_bytes()
    header = (guidance.BEGIN_MARK.encode("utf-8") + b"\n"
              + f"<!-- fleet-guidance-version: {_sha256(payload)[:8]} -->\n".encode()
              + f"<!-- fleet-guidance-delivered: {PAYLOAD_MTIME} -->\n".encode())
    wanted = {header + payload + _END_MARK + b"\n"}
    if payload.startswith(_REPO_HEADER):
        wanted.add(header + payload[len(_REPO_HEADER):] + _END_MARK + b"\n")
    if raw not in wanted:
        raise DeliveryError("delivery_failed", "the complete delivered file or metadata differs from the payload")
    body = raw[len(header):]
    return {"bytes": len(payload), "digest": _sha256(payload),
            "block_digest": _sha256(body), "hook_verdict": info["verdict"]}


def block_body(dest: Path) -> bytes:
    """The marked block after its metadata lines, through the end mark."""
    raw = Path(dest).read_bytes()
    begin = guidance.BEGIN_MARK.encode("utf-8") + b"\n"
    if raw.count(begin) != 1:
        raise DeliveryError("delivery_failed", "the delivered file does not hold one marked block")
    rest = raw.split(begin, 1)[1]
    while rest.startswith(_BLOCK_METADATA):
        rest = rest.split(b"\n", 1)[1] if b"\n" in rest else b""
    return rest
