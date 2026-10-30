"""ADR 0012 part 1: resolve deployed context from Git objects, without delivery.

The resolver never checks out a source, executes its hooks, or launches a
model. Frozen payloads retain raw bytes and Git executable modes. Checkout
locations are explicit; revisions come from the fixture and its lock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from types import MappingProxyType
import unicodedata

import yaml

import guidance

GUIDANCE_REPOSITORY = "example-adopter/_agent-guidance"
CONTEXT_KEYS = frozenset(("repository", "revision", "guidance_revision", "budget"))
BUDGET_KEYS = frozenset(("guidance_bytes", "skill_catalog_bytes", "skill_payload_bytes"))
# Generous ceilings above deployed fixture sizes, bounding accidental or
# untrusted context expansion while keeping raw binary skill resources usable.
BUDGET_MAX = MappingProxyType({"guidance_bytes": 1024 * 1024,
                               "skill_catalog_bytes": 1024 * 1024,
                               "skill_payload_bytes": 64 * 1024 * 1024})
DEFAULT_LAYOUT = "plugins/{bundle}/skills"
_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_DIGEST = re.compile(r"(?:sha256:)?([0-9a-fA-F]{64})\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+\Z")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_MANAGED_BEGIN = '<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->'.encode()
_MANAGED_END = b"<!-- END MANAGED SECTION -->"


class ContextError(guidance.GuidanceError):
    """A named configuration refusal, covered by the harness's exit-2 path."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _controls(value: str) -> bool:
    return any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value)


def _repository(value, code="invalid_context") -> str:
    if (not isinstance(value, str) or _controls(value)
            or not _REPOSITORY.fullmatch(value)
            or any(part in (".", "..") for part in value.split("/"))):
        raise ContextError(code, "repository must be an OWNER/REPO identity without control characters")
    return value


def _keys(value, expected, where, code="invalid_context"):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ContextError(code, f"{where} must contain exactly {', '.join(sorted(expected))}")


def validate_context(fixture: dict, path) -> None:
    """Validate optional metadata at fixture load, without reading any repo.

    A null guidance_revision records a fixture blocked on provenance. It is
    valid for objective-only and existing isolation runs; resolution refuses
    it with guidance_unproven. Missing context preserves legacy isolation.
    """
    if "context" not in fixture:
        return
    context = fixture["context"]
    _keys(context, CONTEXT_KEYS, f"{path}: context")
    _repository(context["repository"])
    for key in ("revision", "guidance_revision"):
        value = context[key]
        if key == "guidance_revision" and value is None:
            continue
        if not isinstance(value, str) or not _SHA.fullmatch(value):
            raise ContextError("invalid_context", f"{path}: context.{key} must be a full 40-character commit SHA")
    _keys(context["budget"], BUDGET_KEYS, f"{path}: context.budget")
    for key, value in context["budget"].items():
        if type(value) is not int or not 0 < value <= BUDGET_MAX[key]:
            raise ContextError("invalid_context", f"{path}: context.budget.{key} must be an integer between 1 and {BUDGET_MAX[key]}, got {value!r}")


class _FixtureLoader(yaml.SafeLoader):
    """Reject top-level duplicates and every duplicate nested in context."""

    def __init__(self, stream):
        super().__init__(stream)
        self.context_nodes = set()

    def construct_mapping(self, node, deep=False):
        strict = id(node) in self.context_nodes
        top = node is self.root_node
        for key, value in node.value:
            if key.value == "context" or strict:
                pending = [value]
                while pending:
                    child = pending.pop()
                    if id(child) in self.context_nodes:
                        continue
                    self.context_nodes.add(id(child))
                    if isinstance(child, yaml.MappingNode):
                        pending.extend(v for _, v in child.value)
                    elif isinstance(child, yaml.SequenceNode):
                        pending.extend(child.value)
        seen = set()
        for key, _ in node.value:
            name = self.construct_object(key, deep=True)
            try:
                duplicate = name in seen
                seen.add(name)
            except TypeError as exc:
                raise ContextError("invalid_context" if strict else "invalid_fixture", "fixture mapping key must be scalar") from exc
            if duplicate and (strict or top or name == "context"):
                raise ContextError("duplicate_key", f"fixture repeats {name!r}")
        return super().construct_mapping(node, deep=deep)

    def get_single_data(self):
        self.root_node = self.get_single_node()
        return self.construct_document(self.root_node) if self.root_node is not None else None


def load_fixture_yaml(raw: str | bytes, path) -> dict:
    """The fixture's existing YAML contract, with strict context parsing."""
    try:
        fixture = yaml.load(raw, Loader=_FixtureLoader)
    except ContextError:
        raise
    except (yaml.YAMLError, UnicodeError, ValueError) as exc:
        raise ContextError("invalid_fixture", f"{path}: fixture YAML is malformed") from exc
    if not isinstance(fixture, dict):
        raise ContextError("invalid_fixture", f"{path} must be a YAML mapping of fixture keys, got {type(fixture).__name__}"
                           + (" (the file is empty)" if fixture is None else f": {fixture!r}"))
    validate_context(fixture, path)
    return fixture


def parse_context_repos(values: list[str] | None) -> dict[str, Path]:
    """Parse repeatable OWNER/REPO=PATH flags; never open or resolve paths."""
    result = {}
    for value in values or ():
        if not isinstance(value, str) or "=" not in value or _controls(value):
            raise ContextError("invalid_context_repo", "--context-repo requires OWNER/REPO=PATH without control characters")
        name, path = value.split("=", 1)
        _repository(name, "invalid_context_repo")
        if not path or not path.strip() or name in result:
            raise ContextError("invalid_context_repo", "--context-repo paths must be nonempty and repository identities unique")
        result[name] = Path(path)
    return result


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_digest(files: Mapping[str, bytes]) -> str:
    # Same manifest as propagation/arms.py and the registry lock generator.
    return _sha256(b"".join(name.encode("utf-8") + b"\0" + _sha256(raw).encode("ascii") + b"\n"
                           for name, raw in sorted(files.items())))


@dataclass(frozen=True)
class SkillTree:
    registry: str
    revision: str
    bundle: str
    skill: str
    digest: str
    files: Mapping[str, bytes]
    modes: Mapping[str, str]
    catalog: bytes


@dataclass(frozen=True)
class FrozenContext:
    manifest: Mapping
    skills: tuple[SkillTree, ...]
    guidance: bytes
    digest: str
    # Part 2's delivery inputs: each guidance source file's raw bytes in
    # assembly order (base first), the pinned hook, and the pinned section
    # manifest's rows, so a subject is cut or added per source file.
    guidance_files: tuple[tuple[str, bytes], ...] = ()
    hook: bytes | None = None
    guidance_rows: tuple[Mapping, ...] = ()


class _Git:
    """A read-only object view. No working-tree file is an input."""

    def __init__(self, identity: str, repositories: Mapping[str, Path]):
        path = repositories.get(identity)
        if path is None or not isinstance(path, (str, os.PathLike)):
            raise ContextError("repository_unavailable", f"no checkout mapping supplied for {identity}")
        self.identity, self.path = identity, Path(path)
        # Ignore ambient object/replace-ref overrides and credential settings.
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_NO_REPLACE_OBJECTS="1")
        self.run("rev-parse", "--git-dir", code="repository_unavailable")

    def run(self, *args, code="missing_object") -> bytes:
        # This object reader is separate from workspace Git. Only its audited
        # object-query shapes may reach Git; filters, external diff drivers,
        # network commands and writes are never part of frozen resolution.
        read_only = all(isinstance(arg, str) for arg in args) and (
            args == ("rev-parse", "--git-dir")
            or (len(args) == 4 and args[:3] == ("rev-parse", "--verify", "--end-of-options")
                and args[3].endswith("^{commit}") and _REF.fullmatch(args[3][:-9]))
            or (len(args) == 3 and args[:2] == ("cat-file", "-e")
                and args[2].endswith("^{commit}") and _SHA.fullmatch(args[2][:-9]))
            or (len(args) == 3 and args[:2] in (("cat-file", "blob"), ("ls-tree", "-rz"),
                                             ("log", "--format=%H %ct")) and _SHA.fullmatch(args[2]))
            or (len(args) == 4 and args[:3] == ("show", "-s", "--format=%ct") and _SHA.fullmatch(args[3])))
        if not read_only:
            raise ContextError("unsupported_git_read", "frozen context permits only audited Git object reads")
        try:
            result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.path), *args],
                                    env=self.env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise ContextError(code, f"cannot read Git objects for {self.identity}") from exc
        if result.returncode:
            raise ContextError(code, f"cannot read {args[0]} object for {self.identity}")
        return result.stdout

    def revision(self, ref: str) -> str:
        value = self.run("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip().decode("ascii")
        if not _SHA.fullmatch(value):
            raise ContextError("missing_object", f"{self.identity}: ref did not resolve to a commit")
        self.run("cat-file", "-e", value + "^{commit}")
        return value

    def tree(self, revision: str) -> dict:
        entries = {}
        for entry in self.run("ls-tree", "-rz", revision).split(b"\0"):
            if not entry:
                continue
            metadata, path = entry.split(b"\t", 1)
            try:
                name = path.decode("utf-8")
                mode, kind, oid = metadata.decode("ascii").split()
            except (UnicodeError, ValueError) as exc:
                raise ContextError("unsafe_path", f"{self.identity}: Git path is not valid UTF-8") from exc
            _path(name)
            if not _SHA.fullmatch(oid):
                raise ContextError("missing_object", f"{self.identity}: invalid object ID")
            entries[name] = (mode, kind, oid)
        return entries

    def read(self, tree: dict, path: str, *, optional=False, code="missing_object") -> bytes | None:
        entry = tree.get(path)
        if entry is None:
            if optional:
                return None
            raise ContextError(code, f"{self.identity}: {path} is absent at the pinned revision")
        mode, kind, oid = entry
        if mode not in ("100644", "100755") or kind != "blob":
            raise ContextError("unsafe_git_mode", f"{self.identity}: {path} has unsupported mode {mode}")
        return self.run("cat-file", "blob", oid)


def _path(value: str):
    if (_controls(value) or value.startswith("/") or "\\" in value
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ContextError("unsafe_path", "Git object paths must be relative, unambiguous, and free of control characters")


def _json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContextError("invalid_lock", "skills.lock repeats a JSON key")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (UnicodeError, ValueError) as exc:
        raise ContextError("invalid_lock", "skills.lock is not valid UTF-8 JSON") from exc


def _name(value, where):
    if not isinstance(value, str) or not _NAME.fullmatch(value) or value in (".", ".."):
        raise ContextError("invalid_lock", f"{where} must be a simple name")
    return value


def _source(raw: dict, where: str, *, primary=False) -> dict:
    allowed = {"registry", "ref", "bundles", "layout"}
    if primary:
        allowed.add("generated_from")
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ContextError("invalid_lock", f"{where} has unknown keys or is not a mapping")
    if not {"registry", "ref", "bundles"} <= set(raw):
        raise ContextError("invalid_lock", f"{where} requires registry, ref, and bundles")
    identity = _repository(raw["registry"], "invalid_lock")
    ref = raw["ref"]
    if (not isinstance(ref, str) or not _REF.fullmatch(ref) or ".." in ref
            or ref.endswith("/") or "//" in ref):
        raise ContextError("invalid_lock", f"{where}.ref is malformed")
    bundles = raw["bundles"]
    if (not isinstance(bundles, list) or not bundles
            or any(not isinstance(value, str) for value in bundles)
            or len(bundles) != len(set(bundles))):
        raise ContextError("invalid_lock", f"{where}.bundles must be a nonempty list of unique names")
    for bundle in bundles:
        _name(bundle, f"{where}.bundle")
    layout = raw.get("layout", DEFAULT_LAYOUT)
    if (not isinstance(layout, str) or not layout or _controls(layout)
            or "{" in layout.replace("{bundle}", "") or "}" in layout.replace("{bundle}", "")
            or "\\" in layout or any(part in ("", ".", "..") for part in layout.split("/"))):
        raise ContextError("invalid_layout", f"{where}.layout must be a relative path with only a {{bundle}} placeholder")
    generated = raw.get("generated_from")
    if generated is not None and (not isinstance(generated, str) or not _SHA.fullmatch(generated)):
        raise ContextError("invalid_lock", f"{where}.generated_from must be a full commit SHA")
    return {"registry": identity, "ref": ref, "bundles": bundles, "layout": layout,
            "generated_from": generated if primary else None}


def _lock(raw: bytes) -> tuple[dict, list[dict]]:
    lock = _json(raw)
    if (not isinstance(lock, dict) or set(lock) - {"registry", "ref", "bundles", "skills", "sources", "generated_from"}
            or not {"registry", "ref", "bundles", "skills"} <= set(lock)):
        raise ContextError("invalid_lock", "skills.lock has unknown or missing keys")
    primary = _source({key: value for key, value in lock.items() if key not in ("skills", "sources")},
                      "skills.lock", primary=True)
    extras = lock.get("sources", [])
    if not isinstance(extras, list):
        raise ContextError("invalid_lock", "skills.lock.sources must be a list")
    sources = [primary] + [_source(value, "skills.lock.sources") for value in extras]
    adopted = [bundle for source in sources for bundle in source["bundles"]]
    if len(adopted) != len(set(adopted)):
        raise ContextError("invalid_lock", "a bundle is claimed by more than one source")
    skills = lock["skills"]
    if not isinstance(skills, dict):
        raise ContextError("invalid_lock", "skills.lock.skills must be a mapping")
    for key, value in skills.items():
        if not isinstance(key, str) or key.count("/") != 1:
            raise ContextError("invalid_lock", "skill keys must be bundle/skill")
        bundle, skill = key.split("/")
        _name(bundle, "skill bundle")
        _name(skill, "skill name")
        if bundle not in adopted or not isinstance(value, str) or not _DIGEST.fullmatch(value):
            raise ContextError("invalid_lock", "skill digests must name adopted bundles and SHA-256 values")
    return lock, sources


def _catalog(bundle: str, skill: str, raw: bytes) -> bytes:
    # Catalog cost is the qualified name and description, not the full body.
    try:
        text = raw.decode("utf-8")
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "---":
            raise ValueError("no front matter")
        end = next(i for i, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---")
        metadata = yaml.safe_load("".join(lines[1:end]))
        description = metadata["description"]
        if not isinstance(description, str) or _controls(description.replace("\n", "").replace("\r", "")):
            raise ValueError("invalid description")
    except (UnicodeError, ValueError, KeyError, TypeError, StopIteration, yaml.YAMLError) as exc:
        raise ContextError("invalid_lock", f"{bundle}/{skill}: SKILL.md needs UTF-8 YAML front matter and a description") from exc
    return yaml.safe_dump({"name": f"{bundle}:{skill}", "description": description},
                          sort_keys=False, allow_unicode=True).encode("utf-8")


def _skills(raw: bytes | None, repositories: Mapping[str, Path]) -> tuple[tuple[SkillTree, ...], list[dict]]:
    if raw is None:
        return (), []
    lock, sources = _lock(raw)
    payloads, manifests = [], []
    for index, source in enumerate(sources):
        git = _Git(source["registry"], repositories)
        # The generator leaves the primary ref symbolic but records its
        # immutable resolution. A moving HEAD must not replace deployed bytes.
        declared = source["ref"]
        pin = source["generated_from"] or declared
        revision = git.revision(pin)
        if index == 0 and _SHA.fullmatch(declared) and source["generated_from"] and declared.lower() != revision:
            raise ContextError("invalid_lock", "primary ref contradicts generated_from")
        tree = git.tree(revision)
        source_files = {}
        bundles = []
        for bundle in source["bundles"]:
            prefix = source["layout"].replace("{bundle}", bundle) + "/"
            paths = {name[len(prefix):]: entry for name, entry in tree.items() if name.startswith(prefix)}
            # Refuse symlink/gitlink roots even though neither can hide children
            # in ls-tree -r. They must not look like an empty adopted bundle.
            roots = [name for name in tree if prefix.rstrip("/") == name]
            for root in roots:
                git.read(tree, root)
            actual = set()
            for name in paths:
                if name.count("/") == 1 and name.endswith("/SKILL.md"):
                    actual.add(name.split("/", 1)[0])
                elif "/" not in name and paths[name][0] not in ("100644", "100755"):
                    raise ContextError("unsafe_git_mode", f"{bundle}: unsupported skill directory mode")
            expected = {key.split("/", 1)[1] for key in lock["skills"] if key.startswith(bundle + "/")}
            if not actual or actual != expected:
                raise ContextError("inventory_mismatch", f"{bundle}: adopted bundle inventory differs from skills.lock")
            bundle_files = {}
            for skill in sorted(actual):
                _name(skill, "skill directory")
                files, modes = {}, {}
                for name, entry in paths.items():
                    if not name.startswith(skill + "/"):
                        continue
                    rel = name[len(skill) + 1:]
                    files[rel] = git.read(tree, prefix + name)
                    modes[rel] = entry[0]
                    bundle_files[name] = files[rel]
                    source_files[prefix + name] = files[rel]
                observed = _file_digest(files)
                wanted = _DIGEST.fullmatch(lock["skills"][f"{bundle}/{skill}"]).group(1).lower()
                if observed != wanted:
                    raise ContextError("digest_mismatch", f"{bundle}/{skill}: skill tree differs from skills.lock")
                payloads.append(SkillTree(source["registry"], revision, bundle, skill, observed,
                                          _freeze(files), _freeze(modes), _catalog(bundle, skill, files["SKILL.md"])))
            bundles.append({"name": bundle, "digest": _file_digest(bundle_files), "skills": sorted(actual)})
        manifests.append({"registry": source["registry"], "ref": declared, "revision": revision,
                          "layout": source["layout"], "digest": _file_digest(source_files), "bundles": bundles})
    return tuple(payloads), manifests


def _yaml(raw: bytes, code: str, where: str):
    class Strict(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            seen = set()
            for key, _ in node.value:
                name = self.construct_object(key, deep=True)
                try:
                    if name in seen:
                        raise ContextError(code, f"{where}: duplicate YAML key")
                    seen.add(name)
                except TypeError as exc:
                    raise ContextError(code, f"{where}: invalid YAML key") from exc
            return super().construct_mapping(node, deep=deep)
    try:
        return yaml.load(raw, Loader=Strict)
    except (yaml.YAMLError, UnicodeError, ValueError) as exc:
        raise ContextError(code, f"{where}: malformed YAML") from exc


def _section_list(value, where: str) -> list[str]:
    if (not isinstance(value, list) or any(not isinstance(item, str) or not _NAME.fullmatch(item) for item in value)
            or len(set(value)) != len(value)):
        raise ContextError("guidance_unproven", f"{where}: sections must be a list of unique simple names")
    return value


def _managed(raw: bytes | None) -> tuple[list[str], str, bytes] | None:
    if raw is None:
        return None
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != _MANAGED_BEGIN:
        return None
    try:
        end = next(i for i, line in enumerate(lines) if line.rstrip(b"\r\n") == _MANAGED_END)
    except StopIteration:
        return None
    # Header metadata is a lexical comment format, not Markdown code shape.
    sections, mode, body_start = None, "full", None
    for i, line in enumerate(lines[1:end], 1):
        value = line.rstrip(b"\r\n")
        if value.startswith(b"<!-- Sections: ") and value.endswith(b" -->"):
            if sections is not None:
                return None
            try:
                names = value[len(b"<!-- Sections: "):-len(b" -->")].decode("ascii")
            except UnicodeError:
                return None
            sections = [] if names == "none" else names.split(" ")
        elif value.startswith(b"<!-- Mode: ") and value.endswith(b" -->"):
            try:
                mode = value[len(b"<!-- Mode: "):-len(b" -->")].decode("ascii")
            except UnicodeError:
                return None
        elif value == b"":
            body_start = i + 1
            break
    if sections is None or mode not in ("stub", "full") or body_start is None:
        return None
    _section_list(sections, "managed AGENTS.md")
    return sections, mode, b"".join(lines[body_start:end])


def _guidance(repository: str, revision: str, pin: str, repositories: Mapping[str, Path]) -> tuple[bytes, dict, dict]:
    consumer = _Git(repository, repositories)
    tree = consumer.tree(consumer.revision(revision))
    managed_raw = consumer.read(tree, "AGENTS.md", optional=True)
    metadata = _managed(managed_raw)
    if metadata is None and managed_raw is not None:
        raise ContextError("guidance_unproven", "pinned repository has no complete managed section list")
    sections, mode, shipped = metadata if metadata is not None else ([], "copy", None)
    source = _Git(GUIDANCE_REPOSITORY, repositories)
    source_revision = source.revision(pin)
    source_tree = source.tree(source_revision)
    base = source.read(source_tree, "agents-md/base.md")
    registry_raw = source.read(source_tree, "repos.yml")
    registry = _yaml(registry_raw, "guidance_unproven", "repos.yml")
    if not isinstance(registry, dict):
        raise ContextError("guidance_unproven", "guidance repos.yml must be a mapping")
    defaults = _section_list(registry.get("default_sections", []), "repos.yml.default_sections")
    config_raw = consumer.read(tree, ".agents-sync.yml", optional=True)
    if config_raw is None:
        configured = defaults
    else:
        config = _yaml(config_raw, "guidance_unproven", ".agents-sync.yml")
        if not isinstance(config, dict):
            raise ContextError("guidance_unproven", ".agents-sync.yml must be a mapping")
        selected = config.get("sections")
        configured = _section_list([] if selected is None else selected, ".agents-sync.yml.sections")
    if metadata is None and configured:
        raise ContextError("guidance_unproven", "a payload copy alone cannot prove adopted opt-in section bytes")
    if configured != sections:
        raise ContextError("guidance_unproven", "managed section list contradicts pinned opt-in configuration")
    manifest_raw = source.read(source_tree, "agents-md/eval-coverage.yml", optional=True)
    if manifest_raw is None and sections:
        raise ContextError("missing_object", "adopted guidance sections require agents-md/eval-coverage.yml")
    manifest = _yaml(manifest_raw, "invalid_guidance_manifest", "agents-md/eval-coverage.yml") if manifest_raw is not None else None
    if manifest is not None:
        if (not isinstance(manifest, list) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                                                or not isinstance(row.get("file"), str) for row in manifest)
                or len({row["id"] for row in manifest}) != len(manifest)):
            raise ContextError("invalid_guidance_manifest", "guidance manifest must be a list of uniquely identified source rows")
    files = [{"path": "agents-md/base.md", "digest": _sha256(base), "bytes": len(base)}]
    sources = [("agents-md/base.md", base)]
    suffix = b""
    for section in sections:
        path = f"agents-md/sections/{section}.md"
        raw = source.read(source_tree, path, code="missing_section")
        if not any(row["file"] == path for row in manifest):
            raise ContextError("invalid_guidance_manifest", f"{path}: adopted section is absent from the source manifest")
        suffix += b"\n" + raw
        files.append({"path": path, "digest": _sha256(raw), "bytes": len(raw)})
        sources.append((path, raw))
    if metadata is not None:
        managed_source = base if mode == "full" else source.read(source_tree, "agents-md/stub.md")
        # Exactly the build script's one framing newline, never arbitrary trim.
        if shipped != managed_source + suffix + b"\n":
            raise ContextError("guidance_unproven", "managed AGENTS.md bytes differ from the pinned source assembly")
    copied = consumer.read(tree, ".claude/hooks/fleet-guidance.md", optional=True)
    if copied is not None and copied != base:
        raise ContextError("guidance_unproven", "shipped fleet-guidance.md bytes differ from the pinned base")
    if mode in ("stub", "copy") and copied is None:
        raise ContextError("guidance_unproven", "full guidance requires a complete managed block or a shipped payload copy")
    hook = source.read(source_tree, ".claude/hooks/fleet-memory.sh", optional=True)
    if hook is None and sections:
        raise ContextError("missing_object", "adopted guidance sections require the pinned fleet-memory hook")
    payload = base + suffix
    proof = {"revision": source_revision, "sections": sections, "files": files,
             "digest": _sha256(payload), "bytes": len(payload), "mode": mode,
             "managed_digest": _sha256(managed_raw) if managed_raw is not None else None,
             "payload_copy_digest": _sha256(copied) if copied is not None else None,
             "registry_digest": _sha256(registry_raw),
             "optin_digest": _sha256(config_raw) if config_raw is not None else None,
             "manifest_state": "present" if manifest_raw is not None else "absent",
             "manifest_digest": _sha256(manifest_raw) if manifest_raw is not None else None,
             "hook_state": "present" if hook is not None else "absent",
             "hook_digest": _sha256(hook) if hook is not None else None}
    rows = tuple(_freeze(dict(row)) for row in manifest or ())
    return payload, proof, {"files": tuple(sources), "hook": hook, "rows": rows}


def resolve_context(context: dict, repositories: Mapping[str, Path]) -> FrozenContext:
    """Resolve one frozen context, verify deployed bytes, and enforce budgets.

    Callers receive a deeply immutable manifest, immutable skill payloads,
    and assembled guidance bytes. The pin must pass the same eligibility rule
    and byte proof as validate_guidance_revision.
    """
    validate_context({"context": context}, "context")
    if context["guidance_revision"] is None:
        raise ContextError("guidance_unproven", "fixture is blocked pending a proven guidance_revision")
    git = _Git(context["repository"], repositories)
    revision = git.revision(context["revision"])
    # The eligibility half of validate_guidance_revision: a default-branch
    # ancestor no newer than the context commit. _guidance below is the bytes.
    _require_eligible(context["repository"], revision, context["guidance_revision"], repositories)
    tree = git.tree(revision)
    lock = git.read(tree, "skills.lock", optional=True)
    skills, sources = _skills(lock, repositories)
    guidance_bytes, proof, delivered = _guidance(context["repository"], revision, context["guidance_revision"], repositories)
    measurements = {"guidance_bytes": len(guidance_bytes),
                    "skill_catalog_bytes": sum(len(skill.catalog) for skill in skills),
                    "skill_payload_bytes": sum(len(raw) for skill in skills for raw in skill.files.values())}
    for key, measured in measurements.items():
        if measured > context["budget"][key]:
            raise ContextError("budget_exceeded", f"{key} measured {measured} bytes exceeds limit {context['budget'][key]}")
    # Names, modes, and bytes participate: the lock's content-only digest is
    # retained separately, so executable-mode changes cannot alias contexts.
    assembly = {"guidance": _sha256(guidance_bytes), "skills": [
        {"registry": skill.registry, "bundle": skill.bundle, "skill": skill.skill,
         "files": [{"path": name, "mode": skill.modes[name], "digest": _sha256(raw)}
                   for name, raw in sorted(skill.files.items())]} for skill in skills]}
    digest = _sha256(json.dumps(assembly, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    manifest = {"schema_version": 1, "repository": context["repository"], "revision": revision,
                "lock": {"state": "present" if lock is not None else "absent",
                         "digest": _sha256(lock) if lock is not None else None},
                "sources": sources, "skills": [{"registry": skill.registry, "revision": skill.revision,
                                                 "bundle": skill.bundle, "skill": skill.skill,
                                                 "digest": skill.digest, "bytes": sum(map(len, skill.files.values()))}
                                                for skill in skills],
                "guidance": proof, "measurements": measurements, "budget": dict(context["budget"]),
                "digest": digest}
    return FrozenContext(_freeze(manifest), skills, guidance_bytes, digest,
                         delivered["files"], delivered["hook"], delivered["rows"])


def read_guidance_file(frozen: FrozenContext, path: str, repositories: Mapping[str, Path]) -> bytes:
    """One file of the pinned guidance revision, by its manifest path: the
    source of a section the context does not adopt, added to a `with` arm."""
    _path(path)
    source = _Git(GUIDANCE_REPOSITORY, repositories)
    revision = frozen.manifest["guidance"]["revision"]
    return source.read(source.tree(source.revision(revision)), path, code="missing_section")


@dataclass(frozen=True)
class GuidanceRevisionMatch:
    revision: str
    matching_revisions: int


def _guidance_candidates(repository: str, revision: str, repositories: Mapping[str, Path]) -> list[tuple[str, int]]:
    consumer = _Git(repository, repositories)
    commit = consumer.revision(revision)
    cutoff = int(consumer.run("show", "-s", "--format=%ct", commit).strip())
    source = _Git(GUIDANCE_REPOSITORY, repositories)
    tip = source.revision("refs/remotes/origin/main")
    candidates = []
    # Walk every ancestor, including commits that did not touch guidance.
    for row in source.run("log", "--format=%H %ct", tip).decode("ascii").splitlines():
        pin, stamp = row.split()
        timestamp = int(stamp)
        if timestamp <= cutoff:
            candidates.append((pin, timestamp))
    return candidates


def _require_eligible(repository: str, revision: str, pin: str,
                      repositories: Mapping[str, Path]) -> str:
    """The pin, lowercased, when it is an origin/main ancestor no newer than
    the context commit; else guidance_unproven. Bytes are not checked here."""
    if not isinstance(pin, str) or not _SHA.fullmatch(pin):
        raise ContextError("guidance_unproven", "guidance pin must be a full commit SHA")
    pin = pin.lower()
    try:
        eligible = {candidate for candidate, _ in _guidance_candidates(repository, revision, repositories)}
    except ContextError as exc:
        raise ContextError("guidance_unproven", "guidance pin eligibility cannot be established") from exc
    if pin not in eligible:
        raise ContextError("guidance_unproven", "guidance pin is not an eligible default-branch ancestor")
    return pin


def validate_guidance_revision(repository: str, revision: str, pin: str,
                               repositories: Mapping[str, Path]) -> None:
    """Prove a declared pin's default-branch ancestry, time, and exact bytes."""
    try:
        pin = _require_eligible(repository, revision, pin, repositories)
        _guidance(repository, revision, pin, repositories)
    except ContextError as exc:
        raise ContextError("guidance_unproven", "guidance pin does not prove eligible deployed bytes") from exc


def find_guidance_revision(repository: str, revision: str, repositories: Mapping[str, Path]) -> GuidanceRevisionMatch:
    """Find exact byte proofs among origin/main ancestors no newer than context.

    This is a scaffold/migration helper. Evaluation uses the resulting pin
    directly. Count every matching eligible revision. Choose greatest commit
    timestamp, then lexicographically smallest full SHA to break ties.
    Timestamps limit eligibility; only exact byte proofs establish a match.
    """
    _repository(repository)
    if not isinstance(revision, str) or not _SHA.fullmatch(revision):
        raise ContextError("invalid_context", "context revision must be a full commit SHA")
    try:
        matches = []
        for pin, timestamp in _guidance_candidates(repository, revision, repositories):
            try:
                _guidance(repository, revision, pin, repositories)
                matches.append((pin, timestamp))
            except ContextError as exc:
                if exc.code == "repository_unavailable":
                    raise
                continue
        if matches:
            selected = min(matches, key=lambda item: (-item[1], item[0]))[0]
            return GuidanceRevisionMatch(selected, len(matches))
    except ContextError as exc:
        raise ContextError("guidance_unproven", "no accessible local guidance revision proves the shipped bytes") from exc
    raise ContextError("guidance_unproven", "no locally reachable guidance revision matches the shipped bytes and opt-ins")
