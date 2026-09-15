#!/usr/bin/env python3
"""Verify fleet guidance in a saved Codex Cloud task response."""

import hashlib
import json
import sys
from pathlib import Path


BEGIN = "<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->"
END = "<!-- END FLEET GUIDANCE -->"
REPO_ADDITIONS = "## Repo-specific additions"
RAW_COMPLETED = "rawResponseItem/completed"


class CheckFailure(Exception):
    pass


def read_text(path, label):
    try:
        return Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeError):
        raise CheckFailure(f"{label} is not readable UTF-8 text") from None


def load_response(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CheckFailure("saved task response is not readable JSON") from None
    if not isinstance(value, dict):
        raise CheckFailure("saved task response is not a JSON object")
    return value


def initial_instruction_envelope(turn):
    thread_events = turn.get("thread_events")
    if not isinstance(thread_events, dict):
        raise CheckFailure("completed turn has no thread_events object")
    events = thread_events.get("events")
    if not isinstance(events, list) or not events:
        raise CheckFailure("completed turn has no event list")

    envelopes = []
    saw_output_boundary = False
    saw_assistant_message = False
    saw_turn_completed = False
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("method"), str):
            raise CheckFailure("event list contains an invalid event")
        method = event["method"]
        if method == "turn/completed":
            if not saw_assistant_message:
                raise CheckFailure("turn completed before an assistant response")
            saw_turn_completed = True
        if method != RAW_COMPLETED:
            continue

        params = event.get("params")
        item = params.get("item") if isinstance(params, dict) else None
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise CheckFailure("raw completed event has no valid item")

        item_type = item["type"]
        role = item.get("role")
        if item_type == "message" and role == "developer":
            continue
        if item_type == "message" and role == "user":
            content = item.get("content")
            if not isinstance(content, list) or not content:
                raise CheckFailure("raw user message has no content list")
            parts = []
            for part in content:
                if (
                    not isinstance(part, dict)
                    or part.get("type") != "input_text"
                    or not isinstance(part.get("text"), str)
                ):
                    raise CheckFailure("raw user message has invalid content")
                parts.append(part["text"])
            candidates = [
                part for part in parts if part.startswith("# AGENTS.md instructions for ")
            ]
            for candidate in candidates:
                if saw_output_boundary:
                    raise CheckFailure("initial instruction envelope follows assistant or tool output")
                if "\n<INSTRUCTIONS>\n" not in candidate or not candidate.rstrip().endswith("</INSTRUCTIONS>"):
                    raise CheckFailure("AGENTS instruction envelope is malformed")
                envelopes.append(candidate)
            continue

        saw_output_boundary = True
        if item_type == "message" and role == "assistant":
            saw_assistant_message = True

    if not envelopes:
        raise CheckFailure("initial AGENTS instruction envelope is absent")
    if len(envelopes) != 1:
        raise CheckFailure("initial AGENTS instruction envelope is duplicated")
    if not saw_turn_completed:
        raise CheckFailure("event stream has no completed turn")
    return envelopes[0]


def expected_cloud_block(payload):
    raw = payload.encode("utf-8")
    version = hashlib.sha256(raw).hexdigest()[:8]
    payload_with_newline = payload if payload.endswith("\n") else payload + "\n"
    verdict = (
        f"fleet-guidance: installed (v{version}, {len(raw)} bytes) "
        "— Codex Cloud setup and maintenance"
    )
    block = (
        f"{BEGIN}\n"
        f"<!-- fleet-guidance-version: {version} -->\n"
        f"{verdict}\n"
        f"{payload_with_newline}"
        f"{END}\n"
    )
    return block, verdict


def check(response_path, payload_path, repo_agents_path):
    response = load_response(response_path)
    payload = read_text(payload_path, "expected payload")
    repo_agents = read_text(repo_agents_path, "expected repo AGENTS.md")
    if not payload:
        raise CheckFailure("expected payload is empty")
    if BEGIN in payload or END in payload or any(
        line.startswith("fleet-guidance:") for line in payload.splitlines()
    ):
        raise CheckFailure("expected payload contains a reserved marker or verdict")

    addition_offsets = []
    offset = 0
    for line in repo_agents.splitlines(keepends=True):
        if line.rstrip("\r\n") == REPO_ADDITIONS:
            addition_offsets.append(offset)
        offset += len(line)
    if len(addition_offsets) != 1:
        raise CheckFailure(
            "expected repo AGENTS.md does not have exactly one repo-specific additions heading"
        )
    additions = repo_agents[addition_offsets[0]:]

    turn = response.get("current_assistant_turn")
    if not isinstance(turn, dict):
        raise CheckFailure("saved task response has no current assistant turn")
    if turn.get("type") != "assistant" or turn.get("role") != "assistant":
        raise CheckFailure("current assistant turn has an invalid identity")
    if turn.get("turn_status") != "completed" or turn.get("error") is not None:
        raise CheckFailure("assistant turn is incomplete or failed")

    envelope = initial_instruction_envelope(turn)
    block, verdict = expected_cloud_block(payload)
    if envelope.count(BEGIN) != 1 or envelope.count(END) != 1:
        raise CheckFailure("instruction envelope does not contain exactly one managed fleet block")
    if envelope.count(payload) != 1:
        raise CheckFailure("expected payload is missing, truncated, or duplicated")
    if envelope.count(block) != 1:
        raise CheckFailure("managed fleet block is incomplete or does not match the expected payload")
    block_at = envelope.find(block)
    installed_block = envelope[block_at : block_at + len(block)]
    verdict_lines = [
        line for line in installed_block.splitlines() if line.startswith("fleet-guidance:")
    ]
    if verdict_lines != [verdict]:
        raise CheckFailure("managed fleet block does not contain one installed verdict")

    if envelope.count(additions) != 1:
        raise CheckFailure("repo-specific additions are missing, truncated, or duplicated")
    additions_at = envelope.find(additions)
    if additions_at < block_at + len(block):
        raise CheckFailure("global fleet block and repo-specific additions are not separate")


def main(argv):
    if len(argv) != 4:
        print(
            "Usage: check-codex-cloud-context.py "
            "<saved-task-response.json> <expected-payload.md> <expected-repo-AGENTS.md>"
        )
        return 2
    try:
        check(argv[1], argv[2], argv[3])
    except CheckFailure as exc:
        print(f"codex-cloud-context: FAIL — {exc}")
        return 1
    print(
        "codex-cloud-context: PASS — saved response contains one exact fleet "
        "block and complete repo-specific additions"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
