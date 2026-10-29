"""Normalize the JSON output shapes of the headless CLI."""

import json
import re


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sum_values(values: list) -> object:
    """Add numbers, merge dicts key by key and join lists; else the last value."""
    present = [v for v in values if v is not None]
    if present and all(_is_number(v) for v in present):
        return sum(present)
    if present and all(isinstance(v, dict) for v in present):
        keys = list(dict.fromkeys(k for v in present for k in v))
        return {k: _sum_values([v[k] for v in present if k in v]) for k in keys}
    if present and all(isinstance(v, list) for v in present):
        return [item for v in present for item in v]
    return values[-1] if values else None


def normalize_cli_result(decoded: object) -> dict:
    """Return the result object from an object or a message array.

    One headless invocation can end more than one turn: an agent that starts a
    background subagent answers, then answers again when the subagent's
    task-notification arrives, and the array carries one `type: result` object
    per turn (`result_index` 0, 1, ...; Claude Code 2.1.289). The agent's
    answer is the text of all of them, in order, so several results are
    merged:

    * `result` is their texts joined by a blank line;
    * `is_error` is true if any of them is;
    * `num_turns`, `duration_ms`, `duration_api_ms` and the numeric fields of
      `usage` are per turn on the CLI, so they are summed (lists such as
      `usage.iterations` are joined in turn order);
    * `total_cost_usd` and `modelUsage` are cumulative on the CLI ("read the
      latest result rather than summing"), so the last result's are kept,
      like every other field;
    * `merged_results` records the count.

    Zero results is still an error.

    Error details describe only the shape, never the CLI's response content.
    """
    if isinstance(decoded, dict):
        return decoded
    if not isinstance(decoded, list) or any(
            not isinstance(item, dict) for item in decoded):
        raise ValueError("CLI JSON must be an object or an array of objects")
    results = [item for item in decoded if item.get("type") == "result"]
    if not results:
        raise ValueError(
            "CLI JSON array must contain a result object (found 0)")
    if len(results) == 1:
        return results[0]
    merged = dict(results[-1])
    texts = [r.get("result") for r in results]
    merged["result"] = "\n\n".join(t for t in texts if isinstance(t, str) and t)
    merged["is_error"] = any(r.get("is_error") for r in results)
    for key in ("num_turns", "duration_ms", "duration_api_ms", "usage"):
        if any(key in r for r in results):
            merged[key] = _sum_values([r.get(key) for r in results])
    merged["merged_results"] = len(results)
    return merged


# ---------------------------------------------------------------------------
# Account-meter events (#370). With `--verbose` the message array carries a
# `type: rate_limit_event` object whose `rate_limit_info` is the account's
# usage windows as the API reported them on that call (CLI 2.1.296,
# measured 2026-10-10). Reading them costs nothing: the harness only keeps
# what a call it made anyway printed.

#: Limits on one `rate_limit_info` kept verbatim in a public summary.json,
#: each inside what scripts/ingest_routine_results.py accepts of any JSON
#: value. The real object is about 330 bytes and three levels deep. The byte
#: limit is on the compact form and does not bound what is written: indented,
#: an object of short lists is several times larger. run_eval bounds the
#: written size (USAGE_MAX_BYTES).
METER_INFO_MAX_BYTES = 2048
METER_INFO_MAX_DEPTH = 5
METER_INFO_MAX_ITEMS = 64
_MESSAGE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z")


def _plain_json(value: object, depth: int = 0) -> bool:
    """Nothing but finite numbers of ordinary size, strings, booleans, nulls
    and short containers of them, at most METER_INFO_MAX_DEPTH deep."""
    if isinstance(value, (dict, list)):
        if depth >= METER_INFO_MAX_DEPTH or len(value) > METER_INFO_MAX_ITEMS:
            return False
        if isinstance(value, dict) and not all(
                isinstance(k, str) and len(k) <= 64 for k in value):
            return False
        return all(_plain_json(v, depth + 1) for v in (
            value.values() if isinstance(value, dict) else value))
    if isinstance(value, (int, float)):
        return value == value and abs(value) < 1e15
    return value is None or isinstance(value, str)


def rate_limit_events(decoded: object) -> list[dict]:
    """One `{"at", "info"}` per meter event in a `--verbose` message array,
    in order. `info` is the event's `rate_limit_info`, verbatim, or None when
    it is not an object within the limits above. `at` is the timestamp of the
    nearest message before the event that carries one (the event has none of
    its own), else None. A single result object (no `--verbose`) has no
    events. Never raises on an unexpected shape."""
    events, at = [], None
    for message in decoded if isinstance(decoded, list) else ():
        if not isinstance(message, dict):
            continue
        stamp = message.get("timestamp")
        if isinstance(stamp, str) and _MESSAGE_TIME.fullmatch(stamp):
            at = stamp
        if message.get("type") != "rate_limit_event":
            continue
        info = message.get("rate_limit_info")
        kept = (isinstance(info, dict) and _plain_json(info) and len(
            json.dumps(info).encode("utf-8")) <= METER_INFO_MAX_BYTES)
        events.append({"at": at, "info": info if kept else None})
    return events


# Paths in a CLI diagnostic: a quoted absolute path (spaces allowed), a
# file:// URL, a ~/ path, a Windows drive path, then a bare absolute path.
_PATH_PATTERNS = (
    (re.compile(r"(['\"`])(?:/|~/|[A-Za-z]:[\\/])[^'\"`\n]*\1"), r"\1<path>\1"),
    (re.compile(r"file:///?[^\s'\"`]*"), "<path>"),
    (re.compile(r"(?<![\w/.-])~/[^\s'\"`]*"), "<path>"),
    (re.compile(r"(?<![\w])[A-Za-z]:\\[^\s'\"`]*"), "<path>"),
    (re.compile(r"(?<![\w:/.-])/(?:[\w.@+-]+/)+[\w.@+-]*"), "<path>"),
)
_SUBTYPE = re.compile(r"[A-Za-z0-9_]{1,40}")


def failed_run_detail(stdout: str, stderr: str, limit: int = 300) -> str:
    """A bounded, content-free description of a CLI run that exited non-zero.

    Detail strings reach `summary.json` and `report.md` on the public results
    branch. With `--verbose` the failing run's stdout is the whole message
    array (cwd, tool and connector names, every tool call and its output), so
    stdout is never echoed. stderr is the CLI's own diagnostics: the first
    `limit` characters with absolute paths replaced. With no stderr the detail
    is the shape of stdout: the result's subtype and error flag when it parses,
    else its length.
    """
    text = stderr.strip()
    if text:
        text = redact_paths(text)
        return text if len(text) <= limit else text[:limit] + "..."
    try:
        result = normalize_cli_result(json.loads(stdout))
    except (ValueError, TypeError):
        return f"stdout {len(stdout)} chars"
    subtype = result.get("subtype")
    parts = []
    if isinstance(subtype, str) and _SUBTYPE.fullmatch(subtype):
        parts.append(f"result subtype {subtype}")
    if isinstance(result.get("is_error"), bool):
        parts.append(f"is_error {str(result['is_error']).lower()}")
    return ", ".join(parts) or f"stdout {len(stdout)} chars"


# ---------------------------------------------------------------------------
# Tool-call trace (#89). `raw.json` keeps only the result objects, so a run's
# tool calls were invisible after the fact. `tool_trace` keeps a compact,
# bounded, redacted line per tool call and per tool result, written beside
# raw.json as `transcripts/tool_trace.json`. That directory is gitignored and
# never committed to `persistent/eval-results`, but eval.yml uploads this file
# (never raw.json, #289) in a workflow artifact, which on a public repository
# anyone signed in can download, so every string is redacted before it is kept.

TOOL_TRACE_SCHEMA_VERSION = 1
#: Characters kept of a tool call's input summary and of a tool result.
TRACE_INPUT_CHARS = 200
TRACE_OUTPUT_CHARS = 300
TRACE_NAME_CHARS = 100
#: Hard cap on the serialized events of one trial (every CLI call of one arm
#: run). Events past it are counted in `omitted_events`, never kept.
TRACE_MAX_BYTES = 64 * 1024
#: Redaction scans the WHOLE string before it is cut: redaction can shrink
#: text (a long path becomes `<path>`), so a window cut first would pull an
#: unredacted, half-cut secret from the window's edge into the kept head. A
#: string longer than this is not scanned and nothing of it is kept.
_REDACT_MAX_CHARS = 1 << 20
#: A tool-use id, kept only when it has this shape (it pairs calls with
#: results); anything else is dropped rather than echoed.
_TOOL_USE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
#: Input keys that name what a call did, most telling first; an input with
#: none of them is summarized as its JSON.
_INPUT_KEYS = ("command", "file_path", "path", "pattern", "url", "query",
               "skill", "subagent_type", "description", "prompt")

REDACTED = "<redacted>"
EMAIL = "<email>"
# Environment variable names whose values are credentials. `pat` only as a
# whole name part (GH_PAT, PAT_X), never inside a word such as PATH.
_SECRET_NAME = re.compile(
    r"(?i)(token|secret|passw(or)?d|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|auth|cookie|session|(?<![a-z])pat(?![a-z]))")
#: Every repetition below is bounded or anchored on a literal, so one scan of
#: a large tool output stays linear.
_KEY_NAME = (r"(?:token|secret|passw(?:or)?d|api[_-]?key|access[_-]?key|"
             r"private[_-]?key|[_-]key|credential|auth|cookie|"
             r"(?<![A-Za-z])pat(?![A-Za-z]))")
#: Shapes of credentials, wherever they appear.
_SECRET_PATTERNS = (
    # Any BEGIN ... PRIVATE KEY ... block (PEM, OpenSSH, PGP "PRIVATE KEY
    # BLOCK"), through its END line or, if that is missing, the end of text.
    (re.compile(r"-----BEGIN[A-Z0-9 ]{0,40}PRIVATE KEY[A-Z0-9 ]{0,40}-----"
                r"[\s\S]*?(?:-----END[A-Z0-9 ]{0,40}PRIVATE KEY[A-Z0-9 ]{0,40}"
                r"-----|\Z)"), REDACTED),
    # URL userinfo: scheme://user:pass@host, https://x-access-token:...@host.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]{0,30}://)[^\s/?#@'\"`<>]{1,512}@"),
     r"\1" + REDACTED + "@"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
     REDACTED),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), REDACTED),
    (re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{10,}"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\b(?:xox[abeprs]|xapp)-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
     REDACTED),
    # Authorization / Proxy-Authorization: the whole value, any scheme.
    (re.compile(r"(?i)\b(authorization[\"']?\s*[:=]\s*[\"']?)[^\r\n\"'`]+"),
     r"\1" + REDACTED),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1" + REDACTED),
    # curl -u / --user / --proxy-user user:password.
    (re.compile(r"(?<!\S)(-u|--user|--proxy-user)(\s+|=)([\"']?)"
                r"[^\s\"':]{0,256}:[^\s\"']*"),
     r"\1\2\3" + REDACTED),
    # .netrc: `password v` anywhere, `login v` at a line start or after
    # `machine host`.
    (re.compile(r"(?i)\b(passw(?:or)?d[ \t]+)(?![=:])\S+"), r"\1" + REDACTED),
    (re.compile(r"(?im)((?:^[ \t]*|\bmachine[ \t]+\S{1,256}[ \t]+)login[ \t]+)\S+"),
     r"\1" + REDACTED),
    # NAME=value / "name": "value" / X-Api-Key: value where the name says
    # credential: `env` output, an export, a header, a config file read back.
    # The name starts only where a name can (not mid-word), so a long run of
    # name characters is tried once, not at every offset.
    (re.compile(r"(?i)((?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]{0,128}" + _KEY_NAME
                + r"[A-Za-z0-9_.-]{0,128}"
                r"[\"']?\s*[=:]\s*[\"']?)[^\s\"',;}]+"),
     r"\1" + REDACTED),
    # Email addresses (personal data never goes in a public log).
    (re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@"
                r"(?:[A-Za-z0-9-]{1,63}\.){1,8}[A-Za-z]{2,24}\b"), EMAIL),
)


def secret_values(*environments: dict) -> list[str]:
    """The values of every credential-named variable in `environments`,
    longest first, so one never leaves a tail of another behind. Values
    shorter than 8 characters are skipped: replacing `1` or `true` everywhere
    would only destroy the trace."""
    found = {value for env in environments for name, value in env.items()
             if isinstance(value, str) and len(value) >= 8
             and _SECRET_NAME.search(name)}
    return sorted(found, key=len, reverse=True)


def redact_paths(text: str) -> str:
    """Absolute paths replaced with `<path>` (the patterns above)."""
    for pattern, replacement in _PATH_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(text: str, secrets: list[str] = ()) -> str:
    """`text` with known secret values, credential shapes and absolute paths
    replaced."""
    for value in secrets:
        text = text.replace(value, REDACTED)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return redact_paths(text)


def _clip(text: str, limit: int, secrets: list[str]) -> str:
    """Redact ALL of `text`, then cut it to `limit` characters. Never cut
    first: a cut can split a secret so no pattern matches the part kept."""
    if len(text) > _REDACT_MAX_CHARS:
        return f"<{len(text)} chars, too large to redact>"
    kept = redact(text, secrets)
    return kept if len(kept) <= limit else kept[:limit] + "..."


def _input_summary(value: object) -> str:
    if isinstance(value, dict):
        for key in _INPUT_KEYS:
            if isinstance(value.get(key), str):
                return value[key]
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return ""


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content
                         if isinstance(block, dict)
                         and isinstance(block.get("text"), str))
    return ""


def _tool_use_id(value: object) -> str | None:
    return value if isinstance(value, str) and _TOOL_USE_ID.fullmatch(value) else None


def tool_events(decoded: object, secrets: list[str] = ()) -> list[dict]:
    """One redacted, truncated event per tool call and per tool result in a
    `--verbose` message array, in order. A single result object (no
    `--verbose`) has none. Never raises on an unexpected shape: a message or
    block that is not what the CLI documents is skipped."""
    if not isinstance(decoded, list):
        return []
    events = []
    for message in decoded:
        if not isinstance(message, dict) or message.get("type") not in (
                "assistant", "user"):
            continue
        body = message.get("message")
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, list):
            continue
        subagent = message.get("parent_tool_use_id") is not None
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                summary = _input_summary(block.get("input"))
                clipped_input = _clip(summary, TRACE_INPUT_CHARS, secrets)
                name = block.get("name")
                events.append({
                    "kind": "tool_use",
                    "id": _tool_use_id(block.get("id")),
                    "name": _clip(name if isinstance(name, str) else "",
                                  TRACE_NAME_CHARS, secrets),
                    "input": clipped_input,
                    "input_chars": len(summary),
                    **({"input_incomplete": True} if clipped_input != summary else {}),
                    "subagent": subagent})
            elif block.get("type") == "tool_result":
                text = _result_text(block.get("content"))
                clipped_output = _clip(text, TRACE_OUTPUT_CHARS, secrets)
                events.append({
                    "kind": "tool_result",
                    "id": _tool_use_id(block.get("tool_use_id")),
                    "is_error": block.get("is_error") is True,
                    "output": clipped_output,
                    "output_chars": len(text),
                    **({"output_incomplete": True} if clipped_output != text else {}),
                    "subagent": subagent})
    return events


def bounded_tool_trace(calls: list[list[dict]],
                       max_bytes: int = TRACE_MAX_BYTES, *,
                       complete: bool = True) -> dict:
    """The `tool_trace.json` document for one trial: each CLI call's events
    (`tool_events`), tagged with the call's index (0 is the prompt, then each
    follow-up), kept in order until their serialized size would pass
    `max_bytes`; the rest are only counted. `complete` records whether every
    attempted CLI call supplied its full verbose message array. The cap also
    makes the stored evidence incomplete, even when every call was decoded."""
    events, size, omitted = [], 0, 0
    for index, call in enumerate(calls):
        for event in call:
            event = {"call": index, **event}
            cost = len(json.dumps(event).encode("utf-8"))
            if omitted or size + cost > max_bytes:
                omitted += 1
                continue
            size += cost
            events.append(event)
    return {
        "schema_version": TOOL_TRACE_SCHEMA_VERSION,
        "limits": {"input_chars": TRACE_INPUT_CHARS,
                   "output_chars": TRACE_OUTPUT_CHARS,
                   "max_bytes": max_bytes},
        "calls": len(calls),
        "events": events,
        "omitted_events": omitted,
        "complete": complete is True and omitted == 0,
    }
