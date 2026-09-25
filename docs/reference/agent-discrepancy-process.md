# Vendor discrepancies: record, match, propose, alert

This is what happens after someone finds agent behavior that contradicts the
vendor's changelog. Entries live in
[`agent-claude-code.DISCREPANCIES.md`](agent-claude-code.DISCREPANCIES.md) and
[`agent-codex.DISCREPANCIES.md`](agent-codex.DISCREPANCIES.md).

Agents do steps 1–4 in one PR to this repo. Step 5 runs by itself once that PR
merges. Step 6 is for the repo owner.

## 1. Record

Add the entry, using the template at the top of the agent's
`DISCREPANCIES.md`.

## 2. Match it to the vendor's issue tracker

Search the vendor's issues, open and closed, before proposing anything. A
thin search is weak evidence, so record the queries you ran.

| Agent | Tracker | New-issue forms |
|---|---|---|
| Claude Code | `anthropics/claude-code` | [bug](https://github.com/anthropics/claude-code/issues/new?template=bug_report.yml), [docs](https://github.com/anthropics/claude-code/issues/new?template=documentation.yml) |
| Codex | `openai/codex` | [CLI bug](https://github.com/openai/codex/issues/new?template=3-cli.yml), [docs](https://github.com/openai/codex/issues/new?template=6-docs-issue.yml) |

How to search, depending on where you are:

- **Cloud session:** the API is blocked for vendor repos. Use WebFetch on
  `https://github.com/<vendor>/issues?q=is%3Aissue+<terms>`, or WebSearch
  restricted to `github.com/<vendor>/issues`.
- **Machine with `gh`:** `gh search issues --repo <vendor> --state all <terms>`.

Put what you found in the entry's **Vendor issues** field.

- **Matches:** each issue's link and state, for example
  `[#1234](…) (open)` or `[#1234](…) (closed, fixed in 2.1.290)`.
- **No match:** `none found — searched YYYY-MM-DD: "<query>"; "<query>"`.

Links in this repo's files are fine. Don't put vendor issue links in commit
messages or in GitHub issues or comments here: those post a backlink on the
vendor's public issue.

## 3. Decide whether to propose a vendor issue

Set **Vendor proposal** to one of these:

- `draft: [<file>](vendor-issue-drafts/<agent>/<file>.md)`. Use this when all
  of the following hold:
  - it reproduces on the current stable release;
  - the repro is minimal and contains no private data;
  - no existing vendor issue covers it;
  - it is a bug (behavior contradicts the changelog or docs) or a docs gap (an
    undocumented or ambiguous change).
- `covered by an existing issue`. If your repro adds something the issue
  lacks, also write a draft comment (step 4, with **Form** set to
  `comment on <issue link>`).
- `not proposed — <reason>`, for example: doesn't reproduce on latest, works
  as the docs describe, or can't be shown without private data.
- `submitted: <link>`, set after step 6.

## 4. Draft it

Write the draft to `docs/reference/vendor-issue-drafts/<agent>/YYYY-MM-DD-<slug>.md`.

- Each field heading must be the vendor form's field label, word for word, so
  the owner can paste it field by field.
- For dropdowns, give the exact option text. For checkboxes, say which to tick.
- Nothing private: no tokens, emails, personal paths, or internal hostnames.

```markdown
# <issue title, with the form's prefix, e.g. "[BUG] ">

- **Vendor repo:** `anthropics/claude-code`
- **Form:** [Bug Report](https://github.com/anthropics/claude-code/issues/new?template=bug_report.yml)
- **Discrepancy:** [agent-claude-code.DISCREPANCIES.md](../../agent-claude-code.DISCREPANCIES.md), entry "<entry heading>"
- **Status:** draft | submitted <link> | withdrawn — <reason>

## Fields

### What's Wrong?
…

### Steps to Reproduce
…
```

Never file a vendor issue yourself. It goes out publicly under the owner's
account, so the owner decides.

## 5. Alert (automatic)

When the PR merges, `.github/workflows/vendor-discrepancy-alert.yml` opens one
issue in this repo for each new entry and each new draft. Each issue:

- is labeled `vendor-discrepancy`;
- is assigned to the repository owner, which triggers GitHub's notification;
- quotes the entry or draft inside a code fence, so no vendor issue in it gets
  a backlink;
- links the file and lists the owner's to-dos.

The workflow recognizes an entry already alerted on by its exact title, so a
re-run never duplicates one. To run it by hand, use `workflow_dispatch`; it
defaults to a dry run.

## 6. The owner submits

1. Open the form linked in the draft and paste each field.
2. Record the result by PR:
   - in the draft: **Status** → `submitted <link>`;
   - in the entry: **Vendor issues**, **Vendor proposal** and **Status** →
     `reported upstream`.
3. Close the alert issue.

## Follow-up at every new changelog entry

Before writing the next entry in an agent's `CHANGELOG.md`, re-check that
agent's open discrepancies:

- Did a release in the new window fix it? If so, set **Status** to
  `fixed in <version>`.
- Has the linked vendor issue changed state? Update **Vendor issues**.

Update them in the same PR as the changelog entry.
