#!/usr/bin/env python3
"""Capture a claude.ai Routine's configuration into a committed, diffable file.

WHY THIS EXISTS
    `docs/routines/guidance-centralization.md` records that nothing watches
    this Routine: it is not a workflow, so it has no run history in Actions,
    nothing goes red when it stops, and both of its failure modes are silent.
    A committed snapshot does not fix that, but it converts two invisible
    things into diffs — a Routine whose configuration was edited, and a
    Routine whose prompt has drifted from the spec it claims to follow.

WHERE THE INPUT COMES FROM
    The claude.ai Routines API. Inside an agent session it is reached through
    the `claude-code-remote` MCP server's `list_triggers` tool, whose response
    is this script's input. Two things about that, both measured 2026-08-29 in
    a hosted Claude Code session, so nobody re-derives them:

      - There is no public REST endpoint to curl. `api.anthropic.com` answers
        a structured 404 for every routines-shaped path tried, and
        `mcp-proxy.anthropic.com` is outside this environment's egress
        allowlist. Do not add a hardcoded URL here on a guess; an unverified
        endpoint in a committed script is worse than no fetcher, because it
        looks like one.
      - A nested `claude -p` does NOT inherit the server. The MCP server is
        provisioned per session by the host, so a subprocess Claude Code
        reports "no MCP server named claude-code-remote is connected" and
        finds no such deferred tool.

    So the fetch is left to the caller, in whichever of the two shapes fits:

      # (a) an agent session that holds the tool: call it, save the response
      python3 scripts/capture-routine.py --id trig_... --input response.json \
          --out docs/routines/<name>.routine.md

      # (b) any command that emits the response on stdout
      python3 scripts/capture-routine.py --id trig_... --out FILE \
          --fetch-cmd some-client routines list --json

EXHAUSTIVE BY REFUSAL
    Every leaf field of the API record is classified in FIELD_POLICY as
    captured, redacted, or deliberately excluded — the same two-key shape
    `repos.yml` uses for cron coverage and skills-bootstrap scope, and for the
    same reason: an allowlist alone cannot be audited for completeness. A leaf
    this script has never seen is a REFUSAL (exit 2) naming the path, not a
    field quietly dropped from a file that still reads as complete.

THE VOLATILE HALF
    `--runtime` prints what the snapshot deliberately leaves out — enabled,
    next run, last fired, last run status, ended/suspension reason — and a
    verdict against the spec's two-week rule. It writes nothing, because those
    fields change on every fire and committing them would make the snapshot
    stale the moment the Routine runs. This is the spec's manual "is it still
    firing" check, in one command.

EXIT CODES
    0  wrote (or, with --check, matched) the snapshot, or printed --runtime
    1  --check found the file stale, or absent
    2  refused: unusable input, routine not found, unclassified field, or an
       internal inconsistency the operator has to decide about
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

# --------------------------------------------------------------------------
# Field policy. Every leaf path the API can return is classified here.
#
#   "capture"  -> rendered into the snapshot
#   "redact"   -> the PROPERTY is rendered, its VALUE is not (public repo)
#   "exclude"  -> named in the snapshot's "not captured" table, with the reason
#
# List indices are normalised to `[*]`, so one entry covers every element.
# --------------------------------------------------------------------------
FIELD_POLICY = {
    # --- identity and schedule -------------------------------------------
    "id":                                     ("capture", "trigger id"),
    "name":                                   ("capture", "display name"),
    "enabled":                                ("capture", "whether it fires"),
    "cron_expression":                        ("capture", "schedule (UTC)"),
    "run_once_at":                            ("capture", "one-shot fire time"),
    "created_at":                             ("capture", "creation time"),
    "created_kind":                           ("capture", "how it was created"),
    "created_via":                            ("capture", "surface that created it"),
    "persist_session":                        ("capture", "reuses one session vs fresh per fire"),
    "derived_state.model":                    ("capture", "model override; empty means account default"),
    "derived_state.folders_state":            ("capture", "folder attachment state"),

    # --- notifications -----------------------------------------------------
    "notifications.channel.push":             ("capture", "push on completion"),
    "notifications.channel.email":            ("capture", "email on completion"),
    "notifications.channel.slack":            ("capture", "slack on completion"),

    # --- what the fired session gets ---------------------------------------
    "job_config.ccr.session_context.allowed_tools[*]":                       ("capture", "pre-approved tools"),
    "job_config.ccr.session_context.autofix_on_pr_create":                   ("capture", "autofix on PR create"),
    "job_config.ccr.session_context.sources[*].git_repository.url":          ("capture", "attached repository"),
    "job_config.ccr.session_context.outcomes[*].git_repository.git_info.repo":       ("capture", "outcome repository"),
    "job_config.ccr.session_context.outcomes[*].git_repository.git_info.branches[*]": ("capture", "outcome branch"),
    "mcp_connections[*].name":                ("capture", "MCP connector name"),
    "mcp_connections[*].url":                 ("capture", "MCP connector endpoint"),

    # --- the prompt --------------------------------------------------------
    "job_config.ccr.events[*].data.message.content": ("capture", "the stored prompt"),
    "job_config.ccr.events[*].data.message.role":    ("capture", "message role"),
    "job_config.ccr.events[*].data.type":            ("capture", "event type"),
    "job_config.ccr.events[*].data.isSynthetic":     ("capture", "synthetic event flag"),

    # --- redacted: identifiers, in a PUBLIC repo ---------------------------
    "creator.account_uuid":                   ("redact", "account identifier"),
    "job_config.ccr.environment_id":          ("redact", "cloud environment identifier"),
    "mcp_connections[*].connector_uuid":      ("redact", "connector identifier"),
    "last_run.session_id":                    ("redact", "session identifier"),
    "job_config.ccr.events[*].data.uuid":     ("redact", "message identifier"),
    "job_config.ccr.events[*].data.session_id": ("redact", "session identifier"),

    # --- excluded: changes on every fire, so committing it guarantees a
    #     permanently stale file and a --check that is always red -----------
    "next_run_at":                            ("exclude", "runtime state: recomputed after every fire"),
    "last_fired_at":                          ("exclude", "runtime state: changes on every fire"),
    "last_run.status":                        ("exclude", "runtime state: changes on every fire"),
    "last_run.fired_at":                      ("exclude", "runtime state: changes on every fire"),
    "last_run.finished_at":                   ("exclude", "runtime state: changes on every fire"),
    "updated_at":                             ("exclude", "runtime state: server-side touch, not only operator edits"),
    "ended_reason":                           ("exclude", "runtime state: set when the routine auto-disables"),
    "suspension_reason":                      ("exclude", "runtime state: set while the subscription is paused"),

    # --- excluded: redundant ------------------------------------------------
    "derived_state.prompt":                   ("exclude", "duplicate of the event's message content; equality is asserted below"),
    "job_config.ccr.events[*].data.parent_tool_use_id": ("exclude", "always null for a routine's seed message"),
}

REDACTED = "<redacted: %s>"


class Refusal(Exception):
    """Something the script will not guess its way past."""


def flatten(node, path=""):
    """Yield (normalised_path, value) for every leaf. Lists collapse to [*]."""
    if isinstance(node, dict):
        if not node:
            yield (path, {})
            return
        for k, v in node.items():
            yield from flatten(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        if not node:
            yield (f"{path}[*]", [])
            return
        for item in node:
            yield from flatten(item, f"{path}[*]")
    else:
        yield (path, node)


def classify(record):
    """Return the set of leaf paths present. Refuse on anything undeclared."""
    seen = {p for p, _ in flatten(record)}
    unknown = sorted(p for p in seen if p not in FIELD_POLICY)
    if unknown:
        raise Refusal(
            "the API returned field(s) this script has never classified, so a "
            "snapshot would silently omit them:\n  "
            + "\n  ".join(unknown)
            + "\n\nAdd each to FIELD_POLICY as capture / redact / exclude."
        )
    return seen


def get(node, dotted, default=None):
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def load_payload(args):
    if args.fetch_cmd is not None:
        if not args.fetch_cmd:
            raise Refusal("--fetch-cmd was given no command to run")
        try:
            proc = subprocess.run(
                args.fetch_cmd, capture_output=True, text=True, timeout=args.timeout
            )
        except FileNotFoundError:
            raise Refusal("--fetch-cmd: %r is not executable" % args.fetch_cmd[0])
        except subprocess.TimeoutExpired:
            raise Refusal("--fetch-cmd timed out after %ss" % args.timeout)
        if proc.returncode != 0:
            # Status and stream name only. The body of a failed API call can
            # quote account data, and this repo is public.
            raise Refusal(
                "--fetch-cmd exited %d with %d byte(s) on stderr; not rendering "
                "a snapshot from an unsuccessful fetch" % (proc.returncode, len(proc.stderr))
            )
        raw = proc.stdout
    elif args.input == "-":
        raw = sys.stdin.read()
    else:
        try:
            with open(args.input, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            raise Refusal("could not read %s: %s" % (args.input, exc.__class__.__name__))

    if not raw.strip():
        raise Refusal("the routines payload was empty — nothing to capture")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Refusal("the routines payload is not JSON (%s at line %d)" % (exc.msg, exc.lineno))
    return payload


def select(payload, trigger_id):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise Refusal(
            "this does not look like a list_triggers response: expected an "
            "object with a `data` array"
        )
    rows = payload["data"]
    if not rows:
        raise Refusal(
            "the response carried zero routines. That is not evidence the "
            "routine is gone — it is a reason to refuse, because an empty list "
            "and an unauthorised one render identically"
        )
    for row in rows:
        if not isinstance(row, dict):
            raise Refusal("the response contains a routine that is not an object")
        if row.get("id") == trigger_id:
            return row
    raise Refusal(
        "routine %s is not in a response carrying %d routine(s). Either the id "
        "is wrong or the routine no longer exists; this script will not guess "
        "which." % (trigger_id, len(rows))
    )


def collect(record):
    """Pull the captured values out of the record, asserting what must hold."""
    events = get(record, "job_config.ccr.events") or []
    if len(events) != 1:
        raise Refusal(
            "expected exactly one seed event, found %d — the prompt is no "
            "longer unambiguous" % len(events)
        )
    prompt = get(events[0], "data.message.content")
    if not isinstance(prompt, str) or not prompt:
        raise Refusal("the routine's seed event carries no prompt text")

    derived = get(record, "derived_state.prompt")
    if derived is not None and derived != prompt:
        raise Refusal(
            "`derived_state.prompt` and the seed event's message content "
            "disagree (%d vs %d bytes). They have always been identical; which "
            "one a fired session actually receives is now an open question, so "
            "this needs a decision rather than a silent pick."
            % (len(derived), len(prompt))
        )

    ctx = get(record, "job_config.ccr.session_context") or {}
    sources = [
        get(s, "git_repository.url") for s in (ctx.get("sources") or [])
    ]
    outcomes = {}
    for o in ctx.get("outcomes") or []:
        repo = get(o, "git_repository.git_info.repo")
        outcomes[repo] = get(o, "git_repository.git_info.branches") or []

    return {
        "prompt": prompt,
        "sources": sources,
        "outcomes": outcomes,
        "allowed_tools": ctx.get("allowed_tools") or [],
        "autofix_on_pr_create": ctx.get("autofix_on_pr_create"),
        "connectors": record.get("mcp_connections") or [],
    }


def yn(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def render(record, present):
    c = collect(record)
    digest = hashlib.sha256(c["prompt"].encode("utf-8")).hexdigest()
    name = record.get("name") or "(unnamed)"

    out = []
    w = out.append
    w("# Routine snapshot: %s" % name)
    w("")
    w("> **Generated — do not edit by hand.** Regenerate with")
    w("> `scripts/capture-routine.py`; see that file's header for where the")
    w("> input comes from and why the fetch is the caller's half.")
    w(">")
    w("> This captures the Routine's **configuration**. Runtime state (when it")
    w("> last fired, what happened, when it fires next) is deliberately absent —")
    w("> it changes on every fire, so committing it would make this file stale")
    w("> the moment the routine runs. The exact fields left out, and why, are in")
    w("> [Deliberately not captured](#deliberately-not-captured); read")
    w("> `list_triggers` directly for any of them.")
    w("")
    w("The procedure this Routine follows is **not** here. It lives in")
    w("[`guidance-centralization.md`](guidance-centralization.md), and the prompt")
    w("below points at it deliberately, so the procedure is reviewable as a pull")
    w("request rather than as an untracked edit to a trigger nobody can diff.")
    w("")

    w("## Configuration")
    w("")
    w("| Property | Value |")
    w("|----------|-------|")
    rows = [
        ("Trigger id", "`%s`" % record.get("id")),
        ("Name", name),
        ("Enabled", yn(record.get("enabled"))),
        ("Schedule (cron, UTC)", "`%s`" % record["cron_expression"]
            if record.get("cron_expression") else "—"),
        ("One-shot fire time", record.get("run_once_at") or "—"),
        ("Schedule kind", "recurring" if record.get("cron_expression")
            else "one-shot" if record.get("run_once_at")
            else "neither — fires only when poked"),
        ("Created", record.get("created_at") or "—"),
        ("Created kind", "`%s`" % record["created_kind"] if record.get("created_kind") else "—"),
        ("Created via", "`%s`" % record["created_via"] if record.get("created_via") else "—"),
        ("Session binding", "fresh session per fire" if not record.get("persist_session") else "bound to one persistent session"),
        ("Model override", "`%s`" % record["derived_state"]["model"] if get(record, "derived_state.model") else "— (account default)"),
        ("Folders state", "`%s`" % get(record, "derived_state.folders_state", "—")),
        ("Autofix on PR create", yn(c["autofix_on_pr_create"])),
        ("Notify: push", yn(get(record, "notifications.channel.push"))),
        ("Notify: email", yn(get(record, "notifications.channel.email"))),
        ("Notify: slack", yn(get(record, "notifications.channel.slack"))),
        ("Creator account", REDACTED % "account identifier"),
        ("Environment id", REDACTED % "cloud environment identifier"),
    ]
    for k, v in rows:
        w("| %s | %s |" % (k, v))
    w("")

    w("## Attached repositories")
    w("")
    source_slugs = [u.rsplit("github.com/", 1)[-1] for u in c["sources"] if u]
    matched = sum(1 for s in source_slugs if c["outcomes"].get(s))
    w("%d source(s); %d of them carry an outcome branch. A fired session sees"
      % (len(c["sources"]), matched))
    w("these without `add_repo`; anything else in the account it must attach")
    w("itself, which is the coverage question the spec's §0.5 is about.")
    w("")
    w("| Repository | Outcome branch |")
    w("|------------|----------------|")
    for url in c["sources"]:
        slug = url.rsplit("github.com/", 1)[-1] if url else "?"
        branches = c["outcomes"].get(slug)
        w("| [`%s`](%s) | %s |" % (slug, url, ", ".join("`%s`" % b for b in branches) if branches else "—"))
    orphans = sorted(set(c["outcomes"]) - set(source_slugs))
    for slug in orphans:
        w("| `%s` (outcome only, not a source) | %s |" % (slug, ", ".join("`%s`" % b for b in c["outcomes"][slug])))
    w("")

    w("## Pre-approved tools")
    w("")
    w("%d entries. `preset:default` expands host-side, so this list is what the" % len(c["allowed_tools"]))
    w("Routine *adds to* that preset, not the whole tool surface a fired session")
    w("holds.")
    w("")
    w("".join("- `%s`\n" % t for t in c["allowed_tools"]).rstrip())
    w("")

    w("## MCP connectors")
    w("")
    if not c["connectors"]:
        w("None. A fired session gets no MCP connector tools.")
    else:
        w("| Name | Endpoint | Connector id |")
        w("|------|----------|--------------|")
        for conn in c["connectors"]:
            w("| `%s` | `%s` | %s |" % (conn.get("name"), conn.get("url"), REDACTED % "connector identifier"))
    w("")

    w("## Deliberately not captured")
    w("")
    w("Present in the API record, absent here on purpose. Absence in this file")
    w("means *classified and excluded*, never *not looked at* — an unclassified")
    w("field makes the script refuse rather than write a file that still reads")
    w("as complete.")
    w("")
    w("| Field | Why |")
    w("|-------|-----|")
    for path in sorted(p for p in present if FIELD_POLICY[p][0] != "capture"):
        kind, reason = FIELD_POLICY[path]
        w("| `%s` | %s (%s) |" % (path, reason, "value withheld" if kind == "redact" else "excluded"))
    w("")

    w("## Stored prompt")
    w("")
    w("%d bytes, `sha256:%s`." % (len(c["prompt"]), digest))
    w("")
    w("This is what a fired session actually receives. The spec is the authority")
    w("on the procedure; where the two disagree, `guidance-centralization.md`")
    w('says which wins and why (see "The stored prompt is behind this file").')
    w("Comparing this block against that section is the check that disagreement")
    w("is still the *known* one and not a fresh edit.")
    w("")
    # The prompt contains fenced code blocks of its own, so the wrapping fence
    # has to be longer than anything inside it or the block closes early and
    # the rest of the prompt renders as prose. Measured, not assumed: today the
    # prompt holds ``` fences and no ~ runs at all, but it is edited on
    # claude.ai by a human and this file would break silently.
    longest = max([0] + [len(m.group(0)) for m in
                         re.finditer(r"^~+", c["prompt"], re.MULTILINE)])
    fence = "~" * max(3, longest + 1)
    w("%stext" % fence)
    w(c["prompt"].rstrip("\n"))
    w(fence)
    return "\n".join(out) + "\n"


RUNTIME_STALE_DAYS = 14


def runtime_report(record):
    """The volatile half — printed, never committed.

    `docs/routines/guidance-centralization.md` records that nothing watches this
    Routine and that the check is manual: read `last_run` and `next_run_at`, and
    treat a last run older than two weeks as stopped whatever `enabled` says.
    The snapshot deliberately excludes those fields, so this mode is where they
    are answered — one command instead of reading a raw API response.
    """
    last = record.get("last_run") or {}
    lines = [
        "%s  %s" % (record.get("name") or "(unnamed)", record.get("id")),
        "  enabled:        %s" % yn(record.get("enabled")),
        "  schedule:       %s" % (record.get("cron_expression")
                                  or record.get("run_once_at") or "— (poke-only)"),
        "  next run:       %s" % (record.get("next_run_at") or "—"),
        "  last fired:     %s" % (record.get("last_fired_at") or "— (never)"),
        "  last run:       %s" % (last.get("status") or "— (no run recorded)"),
        "  last finished:  %s" % (last.get("finished_at") or "—"),
        "  ended reason:   %s" % (record.get("ended_reason") or "—"),
        "  suspended:      %s" % (record.get("suspension_reason") or "—"),
    ]

    fired = record.get("last_fired_at")
    if not fired:
        lines.append("  VERDICT: never fired — a Routine that has not run once is "
                     "not evidence that it will")
    else:
        try:
            when = datetime.datetime.fromisoformat(fired.replace("Z", "+00:00"))
            age = (datetime.datetime.now(datetime.timezone.utc) - when).days
        except ValueError:
            lines.append("  VERDICT: could not read last_fired_at as a timestamp")
        else:
            if age < 0:
                lines.append("  VERDICT: last fired in the future by %d day(s) — "
                             "the record or this machine's clock is wrong, and "
                             "neither makes it safe to call this firing" % -age)
            elif age > RUNTIME_STALE_DAYS:
                lines.append("  VERDICT: STOPPED — last fired %d days ago, past the "
                             "%d-day threshold. `enabled` says nothing here."
                             % (age, RUNTIME_STALE_DAYS))
            else:
                lines.append("  VERDICT: firing — last fired %d day(s) ago" % age)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--id", required=True, metavar="trig_...",
                    help="trigger id to capture")
    ap.add_argument("--out", metavar="FILE",
                    help="snapshot path to write (or compare, with --check). "
                         "Required unless --runtime")
    ap.add_argument("--runtime", action="store_true",
                    help="print the volatile fields the snapshot excludes — the "
                         "manual is-it-still-firing check — and write nothing")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--input", default="-", metavar="FILE",
                     help="list_triggers response; '-' (default) reads stdin")
    src.add_argument("--fetch-cmd", nargs=argparse.REMAINDER, metavar="CMD",
                     help="run CMD (no shell) and read the response from its "
                          "stdout. Consumes the rest of the command line, so it "
                          "must come last")
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if --out is stale or missing")
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds to allow --fetch-cmd (default 120)")
    args = ap.parse_args(argv)
    if not args.runtime and not args.out:
        ap.error("--out is required unless --runtime is given")

    try:
        payload = load_payload(args)
        record = select(payload, args.id)
        present = classify(record)
        if args.runtime:
            print(runtime_report(record))
            return 0
        rendered = render(record, present)
    except Refusal as exc:
        sys.stderr.write("capture-routine: refusing — %s\n" % exc)
        return 2

    if args.check:
        try:
            with open(args.out, encoding="utf-8") as fh:
                current = fh.read()
        except OSError:
            sys.stderr.write("capture-routine: %s does not exist; run without --check\n" % args.out)
            return 1
        if current != rendered:
            sys.stderr.write(
                "capture-routine: %s is stale — the Routine's configuration has "
                "changed since it was captured. Re-run without --check and "
                "review the diff.\n" % args.out
            )
            return 1
        print("capture-routine: %s matches the live routine configuration" % args.out)
        return 0

    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    os.replace(tmp, args.out)
    print("capture-routine: wrote %s (%d bytes)" % (args.out, len(rendered)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
