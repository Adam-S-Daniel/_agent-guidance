## A GitHub 404 means "not authorized", not "not there"

GitHub answers 404, not 403, to a caller not allowed to know a private repo
exists, so every 404 is ambiguous: gone, or invisible to that credential.

- **Probe the repo, not the object.** If `GET /repos/<owner>/<repo>/pulls`
  404s too, the repo is invisible (a scope gap); if only the object does, it
  is gone.
- **Try the other connector first** (a mid-session MCP reconnect can bring
  up a server blind to a private repo the other reads;
  `add_repo` "already attached" is session scope).
- **Git is a separate credential path:** `git ls-remote origin '<ref>'` asks
  "does this branch exist", `git merge-base --is-ancestor <sha> origin/main`
  "was it merged"; neither touches the API.
- Never report a repo, PR or branch gone on a 404 alone; say which
  credential could not see it and what you checked with.
