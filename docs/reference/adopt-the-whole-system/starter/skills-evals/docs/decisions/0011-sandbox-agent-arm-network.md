# ADR 0011: Agent arms run behind Claude Code's network sandbox, delivered by `--settings`

- **Status:** accepted (2026-10-07)
- **Issue:** none; found while preparing the real-work fixtures
  (`evals/real-work/*`), whose seeds are public repositories' pre-fix trees.
- **Decider:** the owner, who chose the `--settings` delivery on 2026-10-07 over a
  workspace `.claude/settings.json` with a `seed_guard` exception.

## Context

A real-work arm starts from a public fleet repository's tree before a merged
fix. With GitHub reachable, the agent can `git clone`, `curl` or WebFetch the
repository and read the fix, which is the fixture's answer key. The fleet's
deployed sites serve the built fix as well
(`https://example-site-a/admin/live-url-derive.js` carries cms-platform#693's).
Arms already get an empty `GH_TOKEN`/`GITHUB_TOKEN`, but a public repository
needs no token, and nothing restricted the network: ADR 0006's
`unshare --net` covers objective-check commands only.

Claude Code's sandbox (https://code.claude.com/docs/en/sandboxing,
https://code.claude.com/docs/en/settings-reference) confines Bash, PowerShell
and Monitor commands and their children: on Linux a network namespace with no
route out except a proxy that checks each hostname. It does not cover the
built-in Read, Edit, WebFetch and WebSearch tools, hooks or MCP servers.
Subagents run in the same process and use the same sandbox. By default a
sandbox that cannot start (Linux needs `bubblewrap` and `socat`) is skipped
silently.

## Decision

Every agent arm, skill or guidance, on every turn including `--resume`
follow-ups, gets `run_eval.arm_isolation_flags()`:

- `--settings` with `run_eval.arm_sandbox_settings()`: `sandbox.enabled`,
  `failIfUnavailable: true`, `allowUnsandboxedCommands: false`,
  `network.strictAllowlist: true`, `network.allowedDomains: []`, and
  `network.deniedDomains` for GitHub (`github.com`, `*.github.com`,
  `codeload.github.com`, `githubusercontent.com`, `*.githubusercontent.com`),
  the deployed sites (`example-site-a`, `example-site-b` and their subdomains)
  and `cdn.jsdelivr.net`;
- `--disallowedTools WebFetch,WebSearch`, because the sandbox does not reach
  those tools.

A CLI that refuses to start (stderr `sandbox required but unavailable`, exit
1) fails the arm with `sandbox_unavailable`. The judge, the guard and canary
probes, `deps:`/`setup:` and objective commands are not changed.

`--settings` rather than a workspace `.claude/settings.json`: command-line
settings outrank project settings, `allowUnsandboxedCommands: false` there
makes the sandbox admin-required so a repository's settings files can no
longer loosen it (`excludedCommands`, proxy ports and similar are ignored),
and `strictAllowlist` is not honored from project settings at all. The agent
can write its workspace's `.claude/`, so a file there would have needed a
tamper check; a flag needs none, and nothing lands in the workspace for
`seed_guard` or a scoring check to see.

## Consequences

- Measured on CLI 2.1.292 (2026-10-07), auto mode, with these flags: `curl`
  to `github.com` and `raw.githubusercontent.com` and `git ls-remote` against
  GitHub fail with `CONNECT tunnel failed, response 403` and the sandbox's
  `host is on the deny list`; `example.com` fails with `host is not on the
  allow list`; WebFetch is not available; the session itself (API traffic)
  completes. With `socat` missing the CLI exits 1 before any model call.
- Arms have no network from Bash at all. A fixture whose agent needs a
  package registry during the run must install it in `deps:` or `setup:`,
  which run outside the sandbox.
- A host on the CI runner or routine must have `bubblewrap` and `socat`, or
  every arm errors with `sandbox_unavailable`.
- Reads are not restricted. A sandboxed command and the Read tool can read
  anything the user can, including this checkout's
  `evals/real-work/*/checker/` and `solution.patch`. Closing that takes
  `sandbox.filesystem.denyRead` for the checkout (and sibling clones) plus
  `Read(//<path>/**)` deny rules for the Read tool, in the same `--settings`.
- With `strictAllowlist` every unnamed host is refused too, so the deny list
  is what still holds if a later change opens an allowlist: it is refused in
  every permission mode, even inside an allowed wildcard.

## Addendum: reads (2026-10-07)

The answer key is on the machine as well as on GitHub: this checkout's
`checker/` and `solution.patch`, the sibling clones beside it (in the eval
routine `skills-evals`, `_agent-guidance`, `agentskills`, `example-site-platform`
and `example-site-a` sit side by side, and example-site-platform `main` holds merged
fixes) and, on a workstation, `~/repos`, `~/sprint-out/` and the original
work's transcripts under `~/.claude/projects/`.

What the docs say, read 2026-10-07:

- https://code.claude.com/docs/en/sandboxing: by default sandboxed commands
  read "most of the machine"; `filesystem.denyRead` blocks paths and
  `allowRead` re-opens paths inside a denied region, the narrower rule
  winning; filesystem arrays merge across scopes; a `denyRead` entry does not
  stop the Read tool; paths from `Read` deny rules are merged into the
  sandbox's configuration too; a `--settings` `denyRead` or `Read(...)` deny
  locks out a repository's `allowRead` at or under it.
- https://code.claude.com/docs/en/settings-reference: sandbox paths are
  absolute or `~/`-relative; `permissions.blockReadsOutsideWorkingDirectories`
  makes Read, Grep, Glob and LSP refuse everything outside the working
  directories and denies commands HOME and the other user-file roots.
- https://code.claude.com/docs/en/permissions: `Read` rules use gitignore
  syntax, `//` for an absolute path; deny beats allow and an allow rule
  cannot carve a hole in a deny (nor can a `!` pattern, against an anchored
  rule); Read rules are applied, best effort, to Grep and Glob and to
  recognized Bash file commands such as `cat`.

Decision: `arm_sandbox_settings(checkouts, session_dir=...)` adds, for every
agent arm and turn, `sandbox.filesystem.denyRead` and matching
`permissions.deny` `Read(//<abs>)` and `Read(//<abs>/**)` rules, as absolute
paths (a guidance arm's `~` is its scratch HOME), for:

- the harness checkout and its main checkout (`harness_clone_root`:
  `core.worktree` first, read with `[include]`s followed and listed
  NUL-separated, since an `includeIf` condition may hold spaces; an include
  git cannot read makes the checkout unknown; then the ancestor whose `.git`
  is the shared git directory; then the parent of a git directory named
  `.git`; a checkout git cannot point back to fails the arm with
  `harness_clone_unknown`), a `--separate-git-dir` clone's git directory,
  and the directory holding the clone;
- every registry and guidance checkout of the run;
- the run's results directory (`--results-dir`, default `results/`:
  earlier arms' transcripts, `raw.json` and tool traces), every
  `--read-deny` directory a wrapper names (`scripts/local_eval.py` passes
  its whole results dir, so trial k's arms cannot read trials 1..k-1;
  `scripts/propose_skill_edit.py` its results root, so the candidate's arms
  cannot read the baseline run or the proposed patch), and the harness's
  session archive (`$XDG_STATE_HOME/skills-evals`, else
  `~/.local/state/skills-evals`: earlier multi-turn arms' sessions and
  propose_skill_edit's default results root), wherever they resolve;
- the harness's other directories under TMPDIR (`HARNESS_TEMP_PREFIXES`:
  other arms' workspaces and guidance scratch profiles, canary and
  propagation legs, scoring copies, `deps:` caches, objective-command
  scratch, and every workspace's `trusted-git-` metadata copy that
  `workspace_git` (#349) keeps, the arm's own included), but the arm's own
  workspace or scratch directory, by structural rules that also cover one made
  later (`<prefix>*`, or the complement of the arm's own directory's name
  within its prefix), plus the existing ones by path for commands;
- on Linux, alternate mounts of the root or HOME filesystem discovered
  through `/proc/self/mountinfo`, matched by device number and filesystem
  type. WSL drive mounts (`drvfs` or `9p` with `aname=drvfs`) and root/HOME
  aliases below `/mnt` deny `/mnt` whole, including WSLg and Windows clones;
  a drive mounted elsewhere denies its actual mountpoint. Canonical
  self-binds such as `/tmp` on `/tmp`, `/snap` on `/snap`, and HOME on
  itself stay available;
- the real HOME and the Claude Code profiles (`~/.claude` and any
  `CLAUDE_CONFIG_DIR` the harness inherited, whose `projects/` holds other
  sessions' transcripts). A deny root equal to HOME (a clone made straight
  into HOME) keeps HOME's treatment below, not a whole-HOME deny.

Checked and not denied separately: the `deps:` caches, scoring copies and
objective-command scratch exist only before or after the arm's turns (and
are covered by the TMPDIR prefixes anyway); `tool_trace.json`, `raw.json`,
`summary.json` and every other per-arm file are written under the results
directory; another arm's guidance scratch (workspace, scratch HOME and
profile) is a `skills-evals-` directory under TMPDIR; the objective-only
path's unprefixed temporary directory runs no arm. A directory that does not
exist yet when an arm starts is left out, so a concurrent run's first
archive or results written mid-arm are not denied to it; arms within one
run start after the earlier ones archived. `allowRead` re-opens
to commands git's global configuration files and PATH directories under HOME
(with a `bin`'s sibling `lib`), never one at or around a checkout. A
workspace under any denied path fails the arm with `workspace_read_denied`;
the message names the kind of path, not the path.

Each alias is denied whole: no `allowRead` exception and no Read rule
inside it. An earlier revision of this addendum kept each Windows PATH
directory under `/mnt` readable with complement Read rules; WSL appends some
15 of them, so a real skill arm's `--settings` reached 60,680 bytes against
the 64 KiB cap, and every arm on that workstation failed with
`settings_too_large`. The arm needs no Windows-side executable, so
`run_agent` drops every PATH entry under an alias (spelled there or reached
through a symlink) from the arm's environment, skill and guidance arms
alike, and builds the settings from that PATH; an arm whose `bash` or `git`
is found only under an alias fails with `toolchain_under_alias` instead of
reopening it. The carve-outs that remain, inside HOME, refuse a directory
that is, holds or lies inside any denied path but HOME itself: a checkout,
the directory holding the clone, the results, the archive, a profile or an
alias. A skill arm's settings on that workstation are now 12,695 bytes.
Mount discovery uses the longest mount prefix for HOME, including a
separate HOME filesystem, decodes mountinfo's escaped paths, and fails with
`read_rules_unsafe` if Linux mount metadata cannot establish the fences.
Other platforms add no mount fences.

The mount table and PATH are parameters, read from `/proc/self/mountinfo`
and `os.environ` only at the outermost call; `SKILLS_EVALS_MOUNTINFO` names
a mountinfo-format file to read instead (empty: no alias), which every test
that runs an arm sets, beside an explicit PATH. The deterministic
[regression tests](../../test/issues/test_issue_arm_read_isolation.py) use
mountinfo fixtures for aliases, no aliases, self-binds, separate HOME,
escaped paths, the size of a WSL-shaped table with a 30-entry Windows PATH,
PATH stripping and refused carve-outs.

`blockReadsOutsideWorkingDirectories` was not used: it also refuses the Read
tool `/usr`, the toolchains and the rest of the system, which an arm may
read, and its command side is the same HOME denial written out here.

What shaped the HOME and profile rules for the Read tool, all measured with
CLI 2.1.292 on WSL2:

- Denying HOME whole broke a skill arm: the CLI saves a large tool output
  under `~/.claude/projects/<munged workspace>/` and the agent reads it back
  with the Read tool, which a HOME deny refuses, and a Read allow rule
  cannot carve a hole in a deny. No setting moves that output alone:
  `CLAUDE_CODE_PROJECT_DIR_NAME` works only with `CLAUDE_CONFIG_DIR`, which
  moves the login too (https://code.claude.com/docs/en/env-vars,
  https://code.claude.com/docs/en/authentication), and a scratch HOME or
  profile for skill arms would need the operator's credentials copied in,
  which is the owner's call and was not done.
- So at each level down to that directory (HOME, `.claude`, `projects`),
  `_complement_patterns` deny names outside the kept and spared names
  (described below), present or future: one pattern per character of the
  kept name for names that leave it there, each strict prefix by name, and
  every longer name. Rules built from the entries present at build time
  (a first version) left a session created
  later readable, and one rule per entry overflowed argv (`E2BIG`; 3,200
  sessions).
- The matcher does not negate a bracket class (`[!c]` and `[^c]` both
  matched `c`) and ignores case (`[A-Z]` matched `t`), so "any character but
  c" is the class of `[A-Za-z0-9._+~@#%,=-]` without `c` in either case. CLI
  project names are drawn from that set; an existing entry whose name leaves
  the kept one (compared case-folded, as the matcher does) at another
  character is named literally, and a name equal to the kept one but for
  case cannot be told apart from it.
- The CLI expands a wildcard rule into the sandbox by walking what it matches
  and following symlinks: `<prefix>*/**` over a directory holding links into
  `/usr/bin` left every command with `bwrap: execvp /bin/bash: Permission
  denied`. A wildcard rule therefore carries no `/**` (a gitignore pattern
  matching a directory covers its contents; the Read tool refused a file
  inside one). A symlink at one of those levels that leaves HOME (or the
  profile) for a system or PATH directory, or for one holding TMPDIR or the
  workspace, is spared instead: the complement patterns leave its name out
  as they leave the arm's own, so no rule names it and the sandbox never
  walks into its target. What it leads to is no secret: a GitHub
  ubuntu-24.04 runner's HOME holds `.ghcup`, a link to `/usr/local/.ghcup`
  (`/etc/skel/.ghcup`, made by actions/runner-images'
  `install-haskell.sh`), and refusing it failed every arm in PR #345's CI.
  The arm fails with `read_rules_unsafe` when such a link also leads
  into or around a path that must stay denied (any other deny root: a
  checkout, an output directory, the archive, another profile; a harness
  directory under TMPDIR or TMPDIR itself; or around HOME), since the Read
  tool would reach it through the spared name, or when its name is not
  spelled from the class above. It also fails with `read_rules_unsafe` when
  any other existing entry at the same level matches the spared link's
  name ignoring case: sparing `.ghcup` would leave a separate `.GHCUP`
  directory readable too. A literal deny for that entry would also follow
  the spared link into its needed target, so the arm is refused instead.
- The settings are capped at 64 KiB (`settings_too_large`), and a CLI that
  cannot be started at all is `spawn_failed`, not a traceback.

Live probe, CLI 2.1.292, auto mode, one headless `claude -p` from a fresh
`/tmp` workspace with the generated flags: `cat` and Python `open()` of a
committed `evals/real-work/*/solution.patch` failed with `No such file or
directory`; the Read tool on it and on this clone's `README.md` was refused
with `File is in a directory that is denied by your permission settings.`;
`ls ~/repos` failed with `No such file or directory`; a file the agent wrote
in its workspace, `git status` there, git's identity and reading back a
saved 340 KB tool output all worked. The CLI offered no Grep or Glob tool in
that session. A second probe, in `bypassPermissions`, created three project
directories under `~/.claude/projects` after the settings were built (one
unrelated, one differing from the arm's own in its last character, one
extending it): the Read tool was refused on all three and on
`~/.claude/settings.json`, and allowed on the arm's own saved output.

Follow-up live probe, CLI 2.1.293 on WSL2, before and after the mount fix in
[PR #345](https://github.com/Adam-S-Daniel/skills-evals/pull/345), following the
alias finding in the [scorer review](https://github.com/Adam-S-Daniel/skills-evals/pull/347):
a real headless arm using the branch's flags could initially list
`/mnt/wslg/distro/home` and `/mnt/d/repos`, read 14,383 bytes of a committed
answer-key patch through WSLg with Bash, and read its diff header with the
Read tool. After the fix those Bash paths returned no bytes and `No such
file or directory`; the Read tool refused the same alias with `File is in a
directory that is denied by your permission settings.` A Windows executable's
two-byte header remained readable through its PATH exception (since
removed, below), and workspace
reads/writes, Python, Node and git remained usable from `/tmp`.

After the redesign above (whole-alias deny, alias PATH entries stripped), a
real `claude -p` arm (CLI 2.1.293) with the branch's flags got `No such file
or directory` from Bash for the same patch under `/mnt/wslg/distro` and for
`/mnt/c`, and `File is in a directory that is denied by your permission
settings.` from the Read tool; its PATH held no `/mnt` entry, and git,
Python, Node and workspace writes worked.

The same probes confirmed the existing Unix socket policy:
`allowAllUnixSockets: false` and `allowUnixSockets: []`. Both
`curl --unix-socket /run/docker.sock http://x/version` and the `/var/run`
spelling failed with curl exit 7 before and after; `cmd.exe /c ver` failed
with exit 1 and `UtilConnectUnix: socket failed 1`. Python failed to create
an `AF_UNIX` socket with `EPERM`, before any connection; the arm reported
`Seccomp: 2`, while an unsandboxed host control reported `Seccomp: 0` and
successfully created and connected that socket. The existing Linux seccomp
filter closes these routes, so no additional `/run` read fence is needed.
All probes and test runs used `unshare --user --map-current-user --pid --fork
--mount-proc --`; socat and libwrap were downloaded and unpacked without sudo
for the live probes, then their scratch directories were deleted. No link —
local probe evidence is at `/tmp/readdeny-alias-probe-after-compact`.

Mount discovery is a snapshot taken while building the arm's settings; it
does not discover host mounts added afterward.

Not covered: a HOME or profile entry created later whose name leaves the
kept one at a character outside the class above is readable by the Read
tool (commands still lose all of HOME); a symlink in HOME that leads
outside it is followed by the Read tool, and a spared one (above) lets the
Read tool list the system or PATH directory it leads to; a harness directory under TMPDIR
whose name equals the arm's own but for case; a HOME or profile entry
created later whose name equals a spared link's but for case; commands
cannot read the arm's own saved tool outputs, only the Read tool can.

## Addendum: hooks, Chrome and managed policy (2026-10-07)

An independent review of the merged decision found four routes around it.

- **Hooks run outside the sandbox.** An arm loads project settings and could
  write its workspace's `.claude/settings.json`, so a `SessionStart` hook
  would run unconfined on the next `--resume` turn, or within the same turn,
  since project settings reload mid-session. `disableAllHooks` was rejected:
  it would also switch off the hooks a skill or plugin under evaluation ships
  (a plugin's `hooks/hooks.json`, a SKILL.md's `hooks:`), so a `with` arm
  would measure a crippled subject. Instead the agent may not write its
  workspace's `.claude/` at all: `Edit(//<workspace>/.claude)` and
  `Edit(//<workspace>/.claude/**)` deny rules (Edit rules govern every
  built-in tool that edits files, Write and NotebookEdit included, and deny
  rules hold in every mode, `bypassPermissions` included:
  https://code.claude.com/docs/en/permissions,
  https://code.claude.com/docs/en/permission-modes) and
  `sandbox.filesystem.denyWrite` for the same directory (Bash), and the same
  for the profile the session loads as user settings (a guidance arm's
  scratch `CLAUDE_CONFIG_DIR`, else `$HOME/.claude`). A trial in which
  anything under the workspace's `.claude/`, or a guidance arm's scratch
  profile, changed during a turn fails after that turn with
  `agent_wrote_agent_config` (a later turn is not run). What was there
  before the first turn (a `with_skill` arm's skill, delivered guidance) is
  the baseline, and the CLI's own writes are allowed, measured by an strace
  of a two-turn arm: an empty `.claude/.cc-writes/` in the workspace; in the
  profile the trees `backups/`, `bridge-spawn/`, `projects/`, `seed-admin/`,
  `session-env/`, `sessions/`, `shell-snapshots/`, and the markers
  `.claude.json` (and its temp files), `plugins/cache/*/.orphaned_at` and
  `skills/synced/*/.last-complete-round`, each exempt only as a regular
  file (not a symlink or a directory, which could hold a plugin's
  `hooks/hooks.json`), the last two of at most 4 KiB; the
  `.claude.json.lock` directory only while empty. A shared profile (the
  operator's real `~/.claude`, which a skill arm uses, or an inherited
  `CLAUDE_CONFIG_DIR`) changes under every other session, so there only the
  paths the CLI loads configuration from are watched: `settings.json`,
  `settings.local.json`, `CLAUDE.md`, `agents/`, `commands/`, `hooks/`,
  `rules/`, `output-styles/`, `skills/` (but `skills/synced/`, the CLI's
  account sync) and `plugins/` whole (installed and known lists, cached
  plugins, marketplaces), but for `plugins/cache/*/.orphaned_at` markers,
  the only writes an strace of a two-turn arm measured there, exempt as
  small regular files. A file listed and then gone before it is examined
  counts as a change, never a crash, except inside an exempt tree. Another session that edits one of those during a skill
  arm fails that arm. `.claude.json`, which can name MCP servers, is not
  watched in a shared profile (the CLI rewrites it every turn); the write
  denies are what keep the agent out of it. Measured live on CLI 2.1.292, in `auto` and `bypassPermissions`:
  the Write tool on `.claude/settings.json` was refused with `File is in a
  directory that is denied by your permission settings.`; Bash `mkdir -p
  .claude/hooks` and `echo > .claude/settings.local.json` failed with
  `Read-only file system` (in `auto` the classifier refused them first);
  writes elsewhere in the workspace worked, and `.claude/` held only
  `.cc-writes/` afterwards. No fixture's task writes under `.claude/`, so no
  opt-out exists.
- **Claude in Chrome is a browser outside the sandbox**, and
  `--strict-mcp-config` does not remove it (`CLAUDE_CODE_ENABLE_CFC` or the
  account default turns it on, CLI 2.1.292). Every turn passes `--no-chrome`,
  and `run_agent` drops `CLAUDE_CODE_ENABLE_CFC` from the arm's environment
  whatever built it.
- **A managed policy can switch the sandbox off or widen it.** A managed
  value outranks `--settings` and managed arrays merge in. In CLI 2.1.292 a
  managed `sandbox.enabledPlatforms` without the platform (the CLI's names:
  `linux`, `wsl`, `macos`, `windows`) and `filesystem.disabled` turn
  confinement off with no sandbox-down line, and top-level
  `allowManagedPermissionRulesOnly` drops the `--settings` Read and Edit
  denies. `run_eval.managed_sandbox_refusal` reads the managed files
  (`run_eval.MANAGED_SETTINGS_FILES` and `_DROPINS`, which local_eval's
  settings guard also checks) and refuses the run (`managed_sandbox_policy`,
  exit 2, before any fixture, and again in `run_agent`) unless every key
  under `sandbox` and `permissions` is on an allowlist, with an accepted
  value, so a key a later CLI adds fails closed (`_MANAGED_ACCEPTED`; an
  object is accepted only at `sandbox`, `sandbox.filesystem`,
  `sandbox.network`, `sandbox.credentials` and `permissions`, every value is
  type-checked, and list elements must be strings of at most 4,096
  characters, or credential entries carrying a `path` (files) or a valid
  variable `name` (envVars), a `mode` of `deny` or `mask`, and only the
  other fields CLI 2.1.292's schema declares, each type-checked
  (`extract`, `onExtractNoMatch`, `decode`, `maskClaims`, `maskDuplicates`
  for files, and `injectHosts` only as `[]`, since a host there would get
  the real value from the proxy)):
  `sandbox.enabled: true`, `failIfUnavailable: true`,
  `allowUnsandboxedCommands: false`, `autoAllowBashIfSandboxed`,
  `enabledPlatforms` including this platform, `filesystem.denyRead`,
  `filesystem.denyWrite`, `filesystem.allowManagedReadPathsOnly`,
  `network.deniedDomains`, `network.strictAllowlist: true`,
  `network.allowManagedDomainsOnly`, `credentials.files`,
  `credentials.envVars`; the arm's own restrictive values, and nothing
  looser: `excludedCommands: []`, `enableWeakerNestedSandbox: false`,
  `enableWeakerNetworkIsolation: false`, `filesystem.disabled: false`,
  `filesystem.allowRead: []`, `filesystem.allowWrite: []`,
  `network.allowedDomains: []`, `network.allowAllUnixSockets: false`,
  `network.allowUnixSockets: []`, `network.allowLocalBinding: false`; `permissions.deny`, `ask`, `allow` (an allow rule
  never outranks a deny), `defaultMode`, `disableBypassPermissionsMode`,
  `disableAutoMode`, `blockReadsOutsideWorkingDirectories`,
  `additionalDirectories: []`; and top-level
  `allowManagedPermissionRulesOnly: false`. Everything else is refused,
  among it `filesystem.disabled: true`, `enableWeakerNestedSandbox: true`,
  `bwrapPath`, `socatPath`, a non-empty `excludedCommands`, the proxy
  ports, `allowAllUnixSockets: true`, `allowLocalBinding: true`, a non-empty
  `allowedDomains`, `allowRead`, `allowWrite` or `additionalDirectories`. The message names the file and
  key, never the value. The arm's settings also spell out the tightest
  values (`excludedCommands: []`, `allowAllUnixSockets: false`,
  `allowUnixSockets: []`, `allowLocalBinding: false`), so no user or project
  value fills them in. Other top-level managed keys (managed `hooks`, `env`)
  are not checked.

The CLI's sandbox-down lines are now matched by shape at the start of a line:
`Error: sandbox required but unavailable: `, `Sandbox Error: ` (an
initialization failure, exit 1) and `Sandbox disabled: ` (the warning
printed when commands run unsandboxed, which now fails the arm even at exit
0); a line that quotes one of them elsewhere does not count.

Not covered here: skill-creator's own `claude -p` calls in the improvement
loop (`scripts/propose_skill_edit.py`) run through the guard launcher, which
adds `--setting-sources` and `--strict-mcp-config` but none of these flags.
