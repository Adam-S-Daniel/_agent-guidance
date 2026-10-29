"""Passive, bounded fleet-guidance counters for subject-agnostic trials.

Only counts and stable rule/section ids leave this module. Candidate content is
parsed, never executed; unknown evidence never becomes a clean zero.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import stat

import yaml

from scorers.bash_ast import BashParseError, parse_bash
from scorers.shell_capture import _command_words


# Stable section ids from _agent-guidance/agents-md/eval-coverage.yml.
RULES = {
    "unpinned_actions": "pinning-github-actions",
    "event_input_in_run": "data-exposure-in-ci",
    "push_without_verification": "git-push-does-not-mean-commit-exists",
}
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
MAX_NODES = 20000
SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
LOCKED_COMMIT = re.compile(r"sha1-[0-9a-f]{40}\Z")
LOCKFILE = ".github/workflows/actions.lock"
INTERPOLATION = re.compile(r"\$\{\{\s*(?:inputs\.|github\.event\.)[^}]*\}\}")


def _lock_key(uses: str) -> str:
    """`uses` as gh-actions-lock keys it: owner/repo lowercased, the ref exact."""
    name, at, ref = uses.rpartition("@")
    return name.lower() + at + ref


def _pinned(value: str, locked: frozenset = frozenset()) -> bool:
    """A full SHA, a local or docker ref, a example-site-platform release tag, or (for
    a workflow step or a called local composite's step only, via `locked`) a
    ref actions.lock locks to a commit."""
    if value.startswith(("./", "$/", "docker://")):
        return True
    action, _, ref = value.rpartition("@")
    if action.startswith(("example-adopter/example-site-platform/.github/actions/",
                          "example-adopter/example-site-platform/.github/workflows/")):
        return bool(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", ref))
    return bool(SHA.fullmatch(ref)) or _lock_key(value) in locked


def _compose(source: str):
    """Compose a bounded YAML node tree: no aliases, duplicate keys or excess nodes."""
    root = yaml.compose(source, Loader=yaml.SafeLoader)
    if root is not None and not isinstance(root, yaml.MappingNode):
        raise ValueError("invalid_document")
    pending, seen = [root], set()
    while pending:
        node = pending.pop()
        if node is None:
            continue
        if id(node) in seen:
            raise ValueError("alias")
        seen.add(id(node))
        if len(seen) > MAX_NODES:
            raise ValueError("node_limit")
        if isinstance(node, yaml.MappingNode):
            keys = [key.value for key, _ in node.value if isinstance(key, yaml.ScalarNode)]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate_key")
            for key, value in node.value:
                pending.extend((key, value))
        elif isinstance(node, yaml.SequenceNode):
            pending.extend(node.value)
    return root


def _mapping(node) -> dict:
    if not isinstance(node, yaml.MappingNode):
        return {}
    return {key.value: value for key, value in node.value if isinstance(key, yaml.ScalarNode)}


def _locked_refs(source: str) -> dict[str, frozenset]:
    """Per workflow path, the `uses:` strings actions.lock locks to a commit.

    An entry counts only when `dependencies` keys it by the `uses:` string
    (owner/repo compared lowercase, as the tool writes it; the ref exact) with a `sha1-<40 lowercase hex>` commit and a `ref` equal to the
    text after the key's last `@`, and the workflow's own `workflows` list
    names it. The result is transient, like the counters.
    """
    top = _mapping(_compose(source))
    locked = set()
    for key, entry in _mapping(top.get("dependencies")).items():
        fields = _mapping(entry)
        commit, ref = fields.get("commit"), fields.get("ref")
        _action, at, tag = key.rpartition("@")
        if (at and tag and isinstance(commit, yaml.ScalarNode)
                and LOCKED_COMMIT.fullmatch(commit.value)
                and isinstance(ref, yaml.ScalarNode) and ref.value == tag):
            locked.add(_lock_key(key))
    out = {}
    for path, refs in _mapping(top.get("workflows")).items():
        if isinstance(refs, yaml.SequenceNode):
            out[path] = frozenset(ref.value for ref in refs.value
                                  if isinstance(ref, yaml.ScalarNode) and _lock_key(ref.value) in locked)
    return out


def _yaml_counts(source: str, workflow: bool, locked: frozenset = frozenset(),
                 callers: set | None = None) -> dict[str, Counter]:
    """Parse YAML nodes, retaining scalar values only in transient counters.

    `locked` holds the step refs actions.lock locks for this workflow, or,
    for a local composite action, for every workflow that reaches it; a
    job-level reusable workflow ref is never locked there, so it still needs
    a full SHA. `callers` collects the directory of each `$/<dir>` step ref
    (a local composite action this workflow or composite calls).
    """
    out = {name: Counter() for name in RULES if name != "push_without_verification"}
    root = _compose(source)
    top = _mapping(root)
    targets = []
    for job in (_mapping(top.get("jobs")).values() if workflow else []):
        targets.append((job, False))
        steps = _mapping(job).get("steps")
        if isinstance(steps, yaml.SequenceNode):
            targets.extend((step, True) for step in steps.value)
    runs = _mapping(top.get("runs"))
    using = runs.get("using")
    steps = runs.get("steps")
    if not workflow and isinstance(using, yaml.ScalarNode) and using.value == "composite" and isinstance(steps, yaml.SequenceNode):
        targets.extend((step, True) for step in steps.value)
    for node, is_step in targets:
        fields = _mapping(node)
        uses = fields.get("uses")
        if uses is not None and not isinstance(uses, yaml.ScalarNode):
            raise ValueError("invalid_uses")
        if isinstance(uses, yaml.ScalarNode) and not _pinned(
                uses.value, locked if is_step else frozenset()):
            out["unpinned_actions"][uses.value] += 1
        if (is_step and callers is not None and isinstance(uses, yaml.ScalarNode)
                and uses.value.startswith("$/")):
            callers.add(uses.value[2:].rstrip("/"))
        run = fields.get("run")
        if is_step and run is not None and not isinstance(run, yaml.ScalarNode):
            raise ValueError("invalid_run")
        if is_step and isinstance(run, yaml.ScalarNode):
            out["event_input_in_run"].update(INTERPOLATION.findall(run.value))
    return out


def _read_regular(path: Path) -> str:
    """A regular file's bounded UTF-8 text; anything else raises."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
        raise ValueError("unsafe_file")
    with path.open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("file_limit")
    return content.decode("utf-8")


def _lockfile(workspace: Path) -> tuple[dict[str, frozenset], bool]:
    """The workspace's actions.lock locks, and whether they could be read.

    A missing lockfile locks nothing. One behind a symlinked directory, or
    one that is unreadable or invalid, is unknown evidence: its tag refs can
    be neither credited nor counted, so `unpinned_actions` is unknown, while
    counters that do not read the lockfile stay known.
    """
    path = workspace / LOCKFILE
    try:
        for parent in (path.parent.parent, path.parent):
            if stat.S_ISLNK(parent.lstat().st_mode):
                return {}, False
        return _locked_refs(_read_regular(path)), True
    except (FileNotFoundError, NotADirectoryError):
        return {}, True
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, RecursionError):
        return {}, False


def snapshot(workspace: Path | None) -> dict | None:
    """Read only workflow/action YAML and actions.lock, refusing symlinks and
    oversized inputs.

    A failed file is an unknown baseline/final observation, not a violation.
    os.walk does not follow directory symlinks; .git and profiles are excluded.
    """
    if workspace is None:
        return None
    if workspace.is_symlink() or not workspace.is_dir():
        return {"files": {}, "complete": False, "lockfile_known": False}
    import os

    files = {}
    complete = True
    # Only `unpinned_actions` depends on the lockfile, so an unreadable one
    # is tracked apart from `complete` and never hides the other counters.
    locks, lockfile_known = _lockfile(workspace)
    # Composite actions are scored after the walk: a tag ref inside one is
    # credited only through the lockfile entries of the workflows that reach
    # it through `$/<dir>` steps (ADR 0024: the caller's lockfile locks the
    # composite's inner actions), and those workflows may be walked later.
    composites, callers = [], {}

    def finish(done: bool) -> dict:
        nonlocal complete
        sources, calls = {}, {}
        for relative, path in composites:
            try:
                source, called = _read_regular(path), set()
                _yaml_counts(source, False, frozenset(), called)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError, RecursionError):
                files[relative] = None
                complete = False
                continue
            sources[relative] = source
            calls.setdefault(relative.rpartition("/")[0], set()).update(called)
        # Each workflow reaches the composites it calls and, transitively,
        # the ones they call; `reached` guards cycles.
        reachers = {}
        for workflow, dirs in callers.items():
            reached, pending = set(), list(dirs)
            while pending:
                directory = pending.pop()
                if directory not in reached:
                    reached.add(directory)
                    pending.extend(calls.get(directory, ()))
            for directory in reached:
                reachers.setdefault(directory, set()).add(workflow)
        for relative, source in sources.items():
            # Intersection: a ref is credited only when EVERY reaching
            # workflow's own lockfile list locks it. A workflow absent from
            # the lockfile's `workflows` map locks nothing, and a composite
            # no workflow reaches is credited by none.
            workflows = reachers.get(relative.rpartition("/")[0])
            locked = (frozenset.intersection(*(locks.get(workflow, frozenset())
                                               for workflow in workflows))
                      if workflows else frozenset())
            try:
                files[relative] = _yaml_counts(source, False, locked)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError, RecursionError):
                files[relative] = None
                complete = False
        return {"files": files, "complete": complete and done,
                "lockfile_known": lockfile_known}

    try:
        def failed(_error):
            nonlocal complete
            complete = False

        entries = 0
        for directory, dirs, names in os.walk(workspace, followlinks=False, onerror=failed):
            parent = Path(directory)
            dirs[:] = sorted(name for name in dirs if name not in (".git", ".claude", "node_modules"))
            entries += 1 + len(dirs) + len(names)
            if entries > MAX_NODES:
                return finish(False)
            if any((parent / name).is_symlink() for name in dirs):
                complete = False
            dirs[:] = [name for name in dirs if not (parent / name).is_symlink()]
            for name in sorted(names):
                path = parent / name
                relative = path.relative_to(workspace).as_posix()
                workflow = path.parent == workspace / ".github" / "workflows"
                if not ((workflow and path.suffix in (".yml", ".yaml"))
                        or name in ("action.yml", "action.yaml")):
                    continue
                if len(files) >= MAX_FILES:
                    return finish(False)
                if not workflow:
                    files[relative] = None
                    composites.append((relative, path))
                    continue
                try:
                    called = set()
                    files[relative] = _yaml_counts(_read_regular(path), workflow,
                                                   locks.get(relative, frozenset()), called)
                    callers[relative] = called
                except (OSError, UnicodeError, ValueError, yaml.YAMLError, RecursionError):
                    files[relative] = None
                    complete = False
    except OSError:
        complete = False
    return finish(True)


def _entry(name: str, count: int, known: bool) -> dict:
    return {"section_id": RULES[name], "status": "known" if known else "unknown",
            "count": count if known else None, "observed_count": count}


def _unresolved_expansion(argument) -> bool:
    """Reject potential expansions in unquoted AST word fragments.

    Tree-sitter leaves glob characters and some brace/tilde syntax in word
    nodes. Quote removal alone loses whether Bash could expand them. Inspect
    those leaves before decoding; quoted nodes and escaped characters stay
    literal. The shared literal decoder already rejects brace-sequence nodes.
    """
    pending = [argument]
    while pending:
        node = pending.pop()
        if node.type == "word":
            text = node.text.decode("utf-8")
            index = 0
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] in "*?[{" or (text[index] == "~"
                        and index == 0 and node.start_byte == argument.start_byte):
                    return True
                index += 1
        elif node.type in ("command_name", "concatenation"):
            pending.extend(node.named_children)
    return False


def _commands(source: str) -> tuple[list[list[str]], bool]:
    """Literal git commands from the Bash AST; compound calls are uncertain."""
    root = parse_bash(source)
    commands, pending = [], [root]
    while pending:
        node = pending.pop()
        # Assignments can select another repository or persist context for a
        # later call. A background launch's success says nothing about the
        # command's result. Neither supplies usable push/check evidence.
        if node.type in ("variable_assignment", "&"):
            return [], False
        if node.type == "command":
            arguments = [node.child_by_field_name("name"),
                         *node.children_by_field_name("argument")]
            if any(_unresolved_expansion(argument) for argument in arguments
                   if argument is not None):
                return [], False
            words = _command_words(node)
            if None in words:
                return [], False
            commands.append((node.start_byte, words))
        # '&' is anonymous in the Bash AST, including a trailing operator.
        pending.extend(node.children)
    commands.sort()
    # Successful tool results prove a command only when that command is the
    # entire tool call, without redirection, conditional execution or a pipe.
    standalone = (len(root.named_children) == 1
                  and root.named_children[0].type == "command"
                  and all(c.type != "file_redirect" for c in root.named_children[0].children))
    return [words for _, words in commands], standalone


def _push_count(trace: dict | None) -> tuple[int, bool]:
    if not isinstance(trace, dict) or not isinstance(trace.get("events"), list):
        return 0, False
    events = trace["events"]
    known = trace.get("complete") is True and trace.get("omitted_events") == 0
    results = {}
    for index, event in enumerate(events):
        if isinstance(event, dict) and event.get("kind") == "tool_result":
            key = (event.get("call"), event.get("id"))
            results.setdefault(key, []).append((index, event))
    pushes, verified = [], []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            known = False
            continue
        if event.get("kind") != "tool_use":
            continue
        # The stored parent trace has no proof that every child command was
        # captured. A delegation result's prose cannot establish execution
        # completeness, even when some child tool calls are visible.
        if event.get("name") in ("Task", "Agent"):
            known = False
            continue
        if event.get("name") != "Bash":
            continue
        if event.get("subagent"):
            known = False
            continue
        source = event.get("input")
        if not isinstance(source, str) or event.get("input_chars") != len(source):
            known = False
            continue
        # Redaction changes meaning even when it happens to preserve length.
        if event.get("input_incomplete") or any(marker in source for marker in (
                "<redacted>", "<path>", "<email>", "too large to redact")):
            known = False
            continue
        try:
            commands, standalone = _commands(source)
        except (BashParseError, ValueError, UnicodeError):
            known = False
            continue
        if not commands:
            known = False
        if not standalone:
            known = False
        for words in commands:
            # An opaque executable may verify a push or change repository
            # context. Unfamiliar git subcommands may also be opaque aliases.
            if words and (words[0] not in ("git", "echo", "printf", ":", "true", "false", "pwd")
                          or (words[0] == "git" and (len(words) < 2 or words[1] not in (
                              "push", "merge-base", "status", "diff", "log", "show", "fetch",
                              "add", "commit", "rev-parse")))):
                known = False
            if words[:2] not in (["git", "push"], ["git", "merge-base"]):
                continue
            matches = results.get((event.get("call"), event.get("id")), [])
            if not event.get("id") or len(matches) != 1 or matches[0][0] <= index:
                known = False
                continue
            result_index, result = matches[0]
            if result.get("is_error") is True:
                continue
            if (not standalone or result.get("is_error") is not False
                    or result.get("output_incomplete")
                    or result.get("output_chars") != len(result.get("output", ""))):
                known = False
                continue
            if words[:2] == ["git", "push"]:
                args = [word for word in words[2:] if word not in ("-u", "--set-upstream")]
                if len(args) != 2 or args[0].startswith("-"):
                    known = False
                    continue
                sha, sep, branch = args[1].partition(":")
                if not sep:
                    branch, sha = sha, None
                if (sep and not SHA.fullmatch(sha)) or not branch or branch.startswith("-"):
                    known = False
                    continue
                branch = branch.removeprefix("refs/heads/")
                pushes.append((result_index, sha.lower() if sha is not None else None,
                               args[0] + "/" + branch))
            elif (len(words) == 5 and words[2] == "--is-ancestor"
                  and SHA.fullmatch(words[3])):
                ref = words[4].removeprefix("refs/remotes/")
                if ("/" not in ref or ref.startswith("-")
                        or any(marker in ref for marker in ("^", "~", ":", "@", "{", "}"))):
                    known = False
                else:
                    verified.append((index, words[3].lower(), ref))
            else:
                # Abbreviated SHAs and revision expressions need repository
                # state to resolve; unsupported options may also verify a push.
                known = False
    count = 0
    for i, sha, ref in pushes:
        matching = [check_sha for j, check_sha, check_ref in verified if j > i and ref == check_ref]
        if sha is None and matching:
            known = False  # The trace cannot bind a branch name to this SHA.
        elif not matching or (sha is not None and sha not in matching):
            count += 1
    # An uncertain call or omitted event may contain a successful verification
    # for any observed push. Without complete evidence, absence of a matched
    # check cannot prove even a minimum number of omissions.
    return (count if known else 0), known


def measure(before: dict | None, workspace: Path | None, trace: dict | None) -> dict:
    after = snapshot(workspace)
    rules = {}
    for name in ("unpinned_actions", "event_input_in_run"):
        count = 0
        known = bool(before and after and before["complete"] and after["complete"])
        if name == "unpinned_actions":
            known = known and before.get("lockfile_known") is True \
                and after.get("lockfile_known") is True
        if before and after:
            for path, values in after["files"].items():
                previous = before["files"].get(path, {name: Counter()})
                if values is not None and previous is not None:
                    count += sum((values[name] - previous[name]).values())
        rules[name] = _entry(name, count, known)
    count, known = _push_count(trace)
    rules["push_without_verification"] = _entry("push_without_verification", count, known)
    return {"schema_version": 1, "rules": rules}


def aggregate(trials: list[dict]) -> dict | None:
    if not any("guidance_violations" in trial for trial in trials):
        return None
    out = {}
    for name, section_id in RULES.items():
        counts = []
        for trial in trials:
            entry = ((trial.get("guidance_violations") or {}).get("rules") or {}).get(name, {})
            count = entry.get("count")
            if entry.get("status") == "known" and type(count) is int and count >= 0:
                counts.append(count)
        out[name] = {"section_id": section_id, "n": len(counts),
                     "n_missing": len(trials) - len(counts),
                     "sum": sum(counts) if counts else None}
    return out


def render(arms: list[dict]) -> list[str]:
    blocks = {arm["arm"]: arm["stats"]["aggregate"].get("guidance_violations") for arm in arms}
    if not any(blocks.values()):
        return []
    lines = ["", "Fleet guidance violations (sum / known trials; missing evidence is unknown):", "",
             "| Rule (section id) | " + " | ".join(blocks) + " |",
             "| --- |" + " --- |" * len(blocks)]
    for name, section in RULES.items():
        cells = []
        for block in blocks.values():
            entry = (block or {}).get(name, {})
            cells.append(f"{entry.get('sum')} / {entry['n']}; {entry['n_missing']} unknown"
                         if entry.get("n") else "unknown")
        lines.append(f"| {name} ({section}) | " + " | ".join(cells) + " |")
    return lines
