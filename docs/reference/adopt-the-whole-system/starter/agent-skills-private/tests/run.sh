#!/usr/bin/env bash
# tests/run.sh: agent-skills-private ships documentation only. Checks the two
# READMEs exist and that the example lesson in lessons/README.md has the
# shape skills-evals/scripts/pick_lesson.py reads. Needs python3.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ in a tree that copy-starter.sh may copy
cd "$(dirname "${BASH_SOURCE[0]}")/.."

failed=0
for f in README.md lessons/README.md; do
  if [ -s "$f" ]; then echo "ok $f exists"; else echo "FAIL: $f is missing or empty"; failed=1; fi
done
if python3 -I - lessons/README.md <<'PY'
import json, re, sys
text = open(sys.argv[1], encoding="utf-8").read()
blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
assert len(blocks) == 1, "expected one json block"
doc = json.loads(blocks[0])
assert set(doc) == {"subject", "count", "lesson"}, sorted(doc)
assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", doc["subject"]), doc["subject"]
assert isinstance(doc["count"], int) and not isinstance(doc["count"], bool)
assert isinstance(doc["lesson"], str) and 0 < len(doc["lesson"]) <= 2000
PY
then echo "ok the example lesson has the picker's shape"; else echo "FAIL: the example lesson in lessons/README.md"; failed=1; fi
if [ "$failed" = 0 ]; then echo "PASS docs: 3 checks passed"; else echo "FAIL docs"; fi
exit "$failed"
