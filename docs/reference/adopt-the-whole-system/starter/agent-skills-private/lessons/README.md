# Lessons

This folder exists on the branch `persistent/lessons`, not on `main`. Each
lesson is one file, `lessons/<subject>.json`:

```json
{
  "subject": "verify-the-commit-not-the-push",
  "count": 3,
  "lesson": "A push reported success after a pre-commit hook refused the commit, so the branch carried the base commit only. Check git log -1 after committing, before pushing."
}
```

| Field | Type | Rule |
|---|---|---|
| `subject` | string | Lowercase letters, digits and hyphens, starting with a letter or digit, at most 64 characters. Use it as the file name too. |
| `count` | integer | How many times the lesson came up. Higher counts are picked first; on a tie, the subject that sorts first wins. |
| `lesson` | string | At most 2,000 characters, no control characters except tab and newline, and nothing redaction would change: no URL, email address, token, key, home-directory path or `name=value` secret. |

`skills-evals/scripts/pick_lesson.py pick --lessons lessons --drafted drafted.json
--plan-out plan.json` reads every `*.json` here and picks the lesson with the
highest count not yet drafted. skills-evals is public, so neither the text
nor the subject goes there: the picker names the lesson only by its id, the
sha256 hex of its subject (`pick_lesson.py id <subject>` prints it), and
`drafted.json` (`drafted-subjects.json` on skills-evals'
`persistent/eval-results`) lists ids. The draft routine reads the lesson from
this branch with its own read access. A subject is still no place for a name
or anything identifying: anyone who guesses a subject can hash it. It silently skips a
file that does not parse, a subject that breaks the rule above, a `count`
that is not an integer, and a lesson that `mine_sessions.validate_lesson()`
refuses, so check a new file against these rules before you push it.
