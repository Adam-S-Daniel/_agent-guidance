# Routine: dashboard refresh

Runs every few hours as a claude.ai scheduled task (it needs the artifact
tool, which a cloud routine may not have). Prompt: `docs/routines/dashboard-refresh.prompt.txt`.

## May do

- Check out `persistent/eval-results` of `skills-evals` read-only.
- Run `scripts/dashboard-refresh.py` from `skills-evals`.
- List open pull requests labeled `agent-authored` in the registry,
  guidance and evals repos (public metadata only).
- Upload changed datasets to the dashboard artifact.

## May not do

- Push to any branch, or open or change any pull request or issue.
- Upload a dataset the script did not write, or text copied from the results
  branch, a pull request body or a comment.
- Put a secret or personal data in a dataset.

## Steps

1. Clone `skills-evals` and fetch `persistent/eval-results` into a separate
   directory: `git clone --branch persistent/eval-results --single-branch
   <url> results`.
2. Read the artifact's `manifest.json` (its last upload) into a temp file.
3. Run `python3 scripts/dashboard-refresh.py --results results --out datasets
   --weekly-cap <weekly_cap from evals/budget.yml> --previous-manifest
   <temp file>`. If it exits non-zero, stop and report its message.
4. Write `datasets/loop-prs.json`: for each open loop pull request (labels
   `agent-authored`; branch names `eval-improve/`, `eval-scaffold/`,
   `eval-draft/`, `retire-skill/`, `eval-guidance-improve/`): repo, number, kind, created
   date, draft or ready. Nothing else: no titles, no bodies.
5. Upload to the dashboard artifact only the files named in `changed` in
   `datasets/manifest.json`, plus `loop-prs.json` and `manifest.json`.
6. Report.

## Needs

- Connector: GitHub (read), and the artifact tool for the dashboard.
- No secret. `persistent/eval-results` of a public repo is readable without
  one; for a private repo, the GitHub connector's read access.

## Reports

Final message: the line `dashboard-refresh: ...` the script printed, the
datasets uploaded, and the artifact link.
