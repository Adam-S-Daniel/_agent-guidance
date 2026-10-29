#!/usr/bin/env python3
"""pick_lesson.py: choose one lesson and name it by an opaque id.

    pick_lesson.py pick --lessons DIR --drafted drafted.json --plan-out plan.json
    pick_lesson.py record --drafted drafted.json --lesson-id ID
    pick_lesson.py id SUBJECT

Lessons are private (the private registry's persistent/lessons) and this repo
is public, so neither a lesson's text nor its subject may leave the pick step:
not in a workflow_dispatch input, a run name, a log, an artifact or
persistent/eval-results. A lesson is named everywhere public by its id, the
sha256 hex of its subject (lesson_id below); the draft routine reads the
lesson text from the private registry itself and finds the file by that id.

pick   DIR holds lessons/<subject>.json files: {"subject": "<slug>", "count":
       <int>, "lesson": "<redacted text>"}. drafted.json is a list of lesson
       ids already drafted. Picks the lesson with the highest count, skipping
       drafted ones and any lesson mine_sessions.validate_lesson() refuses
       (over 2,000 characters, control characters, or anything redaction would
       change). Writes plan.json {"fires": [{"mode": "draft", "lesson_id"}]}
       for dispatch_fires.py and prints the id; exit 1 with no output when no
       lesson qualifies.
record adds ID to drafted.json (sorted, unique), creating it when missing.
id     prints SUBJECT's lesson id (for finding a lesson by hand).
Exit 2 bad input.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mine_sessions import validate_lesson  # noqa: E402

SUBJECT = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")
LESSON_ID = re.compile(r"\A[0-9a-f]{64}\Z")


def lesson_id(subject):
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


def pick(lessons, drafted):
    best = None
    for les in lessons:
        if not isinstance(les, dict) or not isinstance(les.get("subject"), str) \
                or not SUBJECT.match(les["subject"]) or lesson_id(les["subject"]) in drafted \
                or isinstance(les.get("count"), bool) or not isinstance(les.get("count"), int) \
                or validate_lesson(les.get("lesson")) is not None:
            continue
        if best is None or (les["count"], best["subject"]) > (best["count"], les["subject"]):
            best = les
    return best


def load_drafted(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, list) or not all(isinstance(x, str) and LESSON_ID.match(x) for x in doc):
        raise ValueError("drafted.json must be a list of lesson ids")
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick")
    p.add_argument("--lessons", required=True)
    p.add_argument("--drafted", required=True)
    p.add_argument("--plan-out", required=True)
    r = sub.add_parser("record")
    r.add_argument("--drafted", required=True)
    r.add_argument("--lesson-id", required=True)
    i = sub.add_parser("id")
    i.add_argument("subject")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "id":
            if not SUBJECT.match(a.subject):
                raise ValueError("bad subject")
            print(lesson_id(a.subject))
            return 0
        if a.cmd == "record":
            if not LESSON_ID.match(a.lesson_id):
                raise ValueError("bad lesson id")
            ids = sorted(set(load_drafted(a.drafted)) | {a.lesson_id})
            with open(a.drafted, "w", encoding="utf-8") as f:
                json.dump(ids, f, indent=2)
                f.write("\n")
            return 0
        drafted = set(load_drafted(a.drafted))
    except (OSError, ValueError) as e:
        print(f"pick_lesson: {e if isinstance(e, ValueError) else 'cannot read drafted.json'}", file=sys.stderr)
        return 2
    lessons = []
    for path in sorted(glob.glob(os.path.join(a.lessons, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                lessons.append(json.load(f))
        except (OSError, ValueError):
            continue
    best = pick(lessons, drafted)
    if not best:
        return 1
    lid = lesson_id(best["subject"])
    with open(a.plan_out, "w", encoding="utf-8") as f:
        json.dump({"fires": [{"mode": "draft", "lesson_id": lid}]}, f)
    print(lid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
