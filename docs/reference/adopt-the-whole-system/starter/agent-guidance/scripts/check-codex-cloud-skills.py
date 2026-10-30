#!/usr/bin/env python3
"""Verify the initial Codex Cloud skill catalog in a saved task response."""

import json
import re
import sys
from pathlib import Path

from codex_cloud_response import CheckFailure, load_response, validated_turn


NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/([A-Za-z0-9][A-Za-z0-9._-]*)\Z")
ENTRY = re.compile(r"- (.+?): (.+) \(file: (/.+/SKILL\.md)\)\Z")
FLEET_PATH = re.compile(r"/\.agents/skills/[^/]+/SKILL\.md\Z")
RAW_COMPLETED = "rawResponseItem/completed"


def read_json(path, label):
    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise CheckFailure(f"{label} has a duplicate JSON field")
            value[key] = item
        return value

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_fields)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CheckFailure(f"{label} is not readable JSON") from None


def skill_keys(value, where):
    if not isinstance(value, dict):
        raise CheckFailure(f"{where} must be an object")
    names = []
    for key in value:
        match = KEY.fullmatch(key) if isinstance(key, str) else None
        if not match:
            raise CheckFailure(f"{where} has an invalid bundle/skill key")
        names.append(match.group(1))
    return names


def expected_skills(path):
    value = read_json(path, "expected skills file")
    if not isinstance(value, dict):
        raise CheckFailure("expected skills file must be an object")
    if "skills" not in value:
        raise CheckFailure("expected skills file has no skills field")

    if isinstance(value["skills"], list):
        if set(value) != {"skills"}:
            raise CheckFailure("skills manifest has unknown fields")
        expected = {}
        for entry in value["skills"]:
            if not isinstance(entry, dict) or not {"name"} <= set(entry) <= {"name", "file", "description"}:
                raise CheckFailure("manifest skill must have a name and recognized fields")
            name = entry["name"]
            if not isinstance(name, str) or not NAME.fullmatch(name):
                raise CheckFailure("manifest skill has an invalid name")
            file = entry.get("file", f"/.agents/skills/{name}/SKILL.md")
            validate_file(file)
            description = entry.get("description")
            if description is not None and (not isinstance(description, str) or not description.strip()):
                raise CheckFailure("manifest skill has an invalid description")
            if name in expected:
                raise CheckFailure("manifest lists a skill name twice")
            expected[name] = {"file": file, "exact": "file" in entry, "description": description}
        return expected

    names = skill_keys(value["skills"], "lock.skills")
    sources = value.get("sources", [])
    if not isinstance(sources, list):
        raise CheckFailure("lock.sources must be a list")
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise CheckFailure("lock source must be an object")
        if "skills" in source:
            names.extend(skill_keys(source["skills"], f"lock.sources[{index}].skills"))
    if len(names) != len(set(names)):
        raise CheckFailure("lock names two skills with the same installed basename")
    return {
        name: {"file": f"/.agents/skills/{name}/SKILL.md", "exact": False, "description": None}
        for name in names
    }


def validate_file(file):
    if not isinstance(file, str) or not file.startswith("/") or not file.endswith("/SKILL.md"):
        raise CheckFailure("catalog or manifest has an invalid SKILL.md file path")
    if any(part in ("", ".", "..") for part in file[1:].split("/")):
        raise CheckFailure("catalog or manifest has an invalid SKILL.md file path")


def parse_catalog(text):
    if not text.startswith("<skills_instructions>\n") or not text.endswith("\n</skills_instructions>"):
        raise CheckFailure("initial skills instruction block is incomplete")
    lines = text.splitlines()
    if lines.count("### Available skills") != 1:
        raise CheckFailure("initial skills catalog heading is missing or duplicated")
    start = lines.index("### Available skills") + 1
    entries = {}
    for line in lines[start:-1]:
        match = ENTRY.fullmatch(line)
        if not match:
            raise CheckFailure("initial skills catalog is malformed or truncated")
        name, description, file = match.groups()
        if not NAME.fullmatch(name) or not description.strip():
            raise CheckFailure("initial skills catalog contains an invalid skill")
        validate_file(file)
        if name in entries:
            raise CheckFailure("initial skills catalog duplicates a skill name")
        entries[name] = {"file": file, "description": description}
    return entries


def initial_catalog(turn):
    thread_events = turn.get("thread_events")
    if not isinstance(thread_events, dict):
        raise CheckFailure("completed turn has no thread_events object")
    events = thread_events.get("events")
    if not isinstance(events, list) or not events:
        raise CheckFailure("completed turn has no event list")

    catalogs = []
    saw_boundary = False
    saw_assistant = False
    saw_completed = False
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("method"), str):
            raise CheckFailure("event list contains an invalid event")
        method = event["method"]
        if method == "turn/completed":
            if not saw_assistant:
                raise CheckFailure("turn completed before an assistant response")
            saw_completed = True
        if method == "item/completed":
            saw_boundary = True
        if method != RAW_COMPLETED:
            continue

        params = event.get("params")
        item = params.get("item") if isinstance(params, dict) else None
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise CheckFailure("raw completed event has no valid item")
        if item["type"] == "message" and item.get("role") == "developer":
            content = item.get("content")
            if not isinstance(content, list):
                raise CheckFailure("raw developer message has no content list")
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "input_text" or not isinstance(part.get("text"), str):
                    raise CheckFailure("raw developer message has invalid content")
                candidate = part["text"]
                if candidate.startswith("<skills_instructions>"):
                    if saw_boundary:
                        raise CheckFailure("skills catalog follows user, assistant, or tool output")
                    catalogs.append(parse_catalog(candidate))
            continue
        saw_boundary = True
        if item["type"] == "message" and item.get("role") == "assistant":
            saw_assistant = True

    if not saw_completed:
        raise CheckFailure("event stream has no completed turn")
    if len(catalogs) != 1:
        raise CheckFailure("initial skills catalog is absent or duplicated")
    return catalogs[0]


def check(response_path, expected_path):
    expected = expected_skills(expected_path)
    catalog = initial_catalog(validated_turn(load_response(response_path)))
    for name, specification in expected.items():
        actual = catalog.get(name)
        if actual is None:
            raise CheckFailure(f"expected skill {name} is missing from initial catalog")
        if specification["exact"]:
            if actual["file"] != specification["file"]:
                raise CheckFailure(f"expected skill {name} has the wrong SKILL.md file path")
        elif not actual["file"].endswith(specification["file"]):
            raise CheckFailure(f"expected skill {name} has the wrong SKILL.md file path")
        if specification["description"] is not None and actual["description"] != specification["description"]:
            raise CheckFailure(f"expected skill {name} has the wrong catalog description")
    unexpected = sorted(
        name for name, entry in catalog.items()
        if FLEET_PATH.search(entry["file"]) and name not in expected
    )
    if unexpected:
        raise CheckFailure("initial catalog contains unlisted fleet skills")
    return len(expected)


def main(argv):
    if len(argv) != 3:
        print("Usage: check-codex-cloud-skills.py <saved-task-response.json> <skills.lock|expected-skills.json>")
        return 2
    try:
        count = check(argv[1], argv[2])
    except CheckFailure as exc:
        print(f"codex-cloud-skills: FAIL — {exc}")
        return 1
    print(f"codex-cloud-skills: PASS — {count} expected skills in one initial developer catalog")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
