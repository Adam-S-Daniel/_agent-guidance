# Reading code scanning alerts from a cloud session

Read this when a task needs a repo's code scanning (CodeQL) alerts and you are
in a claude.ai cloud session.

## Why the obvious routes fail

- The session's GitHub token gets **403** from
  `GET /repos/<owner>/<repo>/code-scanning/alerts`.
- Neither GitHub MCP server (`mcp__github__*` or the `github-mcp` connector)
  has a code scanning tool.

## 1. First choice: run `gh api` on the owner's device

Start a Remote Control session on the owner's machine (`start_remote_control`
in a project thread) and have it run the owner's own `gh` login, read-only:

```bash
gh api 'repos/<owner>/<repo>/code-scanning/alerts?state=open&per_page=100' --paginate \
  --jq '.[] | [.number, .rule.id, .most_recent_instance.location.path,
              .most_recent_instance.location.start_line] | @tsv'
```

Swap `state=open` for `fixed` or `dismissed` to confirm what a fix closed.

- The token needs the `security_events` scope. If `gh` is logged out or the
  call returns 403/404, the session reports the exact error and stops; it does
  not log in or run `gh auth refresh` on the owner's behalf.
- Keep it read-only unless the owner asked for a dismissal.
- End the Remote Control session when the read is done.

This is the authoritative list: it carries each alert's number, state and
dismissal, and it reflects the repo's real scan configuration.

## 2. Fallback: run CodeQL in the session

Use this only when no device is available. It finds the same issues but knows
nothing of alert numbers, dismissals or fixed state, and cannot dismiss.

1. Download `codeql-bundle-linux64.tar.gz` from a pinned
   [github/codeql-action release](https://github.com/github/codeql-action/releases)
   (`codeql-bundle-vX.Y.Z`, past the 7-day wait), never `latest`. It is about
   695 MB; unpack it in its own empty directory and mind the session's disk
   allowance.
2. Per language the repo's default setup scans (Security > Code scanning >
   Tool status lists them), build a database and run the default suite:

   ```bash
   codeql database create /tmp/db-<lang> --language=<lang> --source-root=.
   codeql database analyze /tmp/db-<lang> \
     codeql/<lang>-queries:codeql-suites/<lang>-code-scanning.qls \
     --format=sarif-latest --output=/tmp/<lang>.sarif
   ```

   `<lang>` is `javascript`, `python`, `ruby`, `actions` and so on, except
   that JavaScript's `--language` value is `javascript-typescript` while its
   pack stays `codeql/javascript-queries`. If the repo's scan uses the
   extended suite, run `<lang>-security-extended.qls` instead.
3. Treat a match as provisional until route 1, or the next scan on the default
   branch, confirms it.

## Evidence

2026-10-28/29, `Adam-S-Daniel/cms-platform`: the cloud session got 403 on the
alerts API. The local CodeQL fallback found 37 findings; a Remote Control
session on the owner's machine then read all 37 alerts (#1 to #37) and
confirmed each was marked fixed by the scan after
[cms-platform#913](https://github.com/Adam-S-Daniel/cms-platform/pull/913)
merged, matching the local run rule for rule and file for file.
