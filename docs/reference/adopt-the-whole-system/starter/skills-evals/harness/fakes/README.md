# harness/fakes

Fake binaries shared across fixtures. A Class B eval (DESIGN.md, "Four
instruments") decides correctness by whether the agent reached a recorded root
cause, so its instrument is a stand-in for the tool the skill consults —
answering from canned payloads, refusing remote writes, and logging what
was asked. Same substitution move as `$CLAUDE_BIN` / `test/fake-claude`.

One fake lives here per faked tool. A fixture supplies **payloads**, never
code: it symlinks the binary onto its seed's `bin/` and ships its own payload
directory, so a behavior fixed here is fixed for every fixture at once.

| Fake | Stands in for | Payload dir |
|---|---|---|
| [`gh`](gh) | the GitHub CLI | `$GH_REPLAY_DIR` |

(`evals/windows-elevation-from-wsl/seed/bin/powershell.exe` predates this
directory and stays with its fixture — it is machine-specific and no second
fixture reuses it.)

## `gh`

### Wiring a fixture to it

```yaml
# evals/<skill>/fixture.yaml
env:
  PATH: "$WORKSPACE/bin:$PATH"          # the fake shadows any real gh
  GH_REPLAY_DIR: "$WORKSPACE/.gh/replay"   # a dot-dir: see below
  GH_REPO: "example-org/example-site"   # read repository and fallback 403 URL
```

```bash
# evals/<skill>/seed/bin/gh -> ../../../../harness/fakes/gh
ln -s ../../../../harness/fakes/gh evals/<skill>/seed/bin/gh
```

`$WORKSPACE` expands to the arm's temp workspace at run time (see
`harness/run_eval.py`'s `agent_env`), and `shutil.copytree` resolves the
symlink into a real executable there, so the arm gets a self-contained copy
and the repo keeps one source of truth.

**Keep the payloads in a dot-directory.** A payload tree beside the seed's own
files is part of the workspace the agent reads: it can `cat` the recorded run
log straight off disk, reach the root cause without asking `gh`, and then fail
the very check that was meant to tell those two apart. A `.gh/replay/` sibling
keeps them out of plain view, and the seed's README says nothing about them.

**And name nothing after this harness.** The arm's workspace is the agent's
cwd under `bypassPermissions`, so `pwd`, `ls -a`, `git log`, `env` and `cat
bin/gh` are all reachable. That is why the variables are `GH_REPLAY_DIR` and
`GH_REPO` rather than `FAKE_GH_*`, why the payload directory is `.gh/replay`
rather than `.fake-gh/payloads`, why `run_eval.py`'s `WORKSPACE_PREFIX` and
`SEED_COMMIT_IDENTITY` say nothing about this repository or the arm, and why
[`gh`](gh) reads as a plain offline replay of the CLI: the word "fake"
appears nowhere in it. Keep it that way — the source of truth for what an arm
may read is here, not in the file the agent gets a copy of.
`TestIssue84Review.test_nothing_the_arm_can_read_names_the_instrument`
materializes an arm workspace with the harness's own code and greps every
byte of it — and every variable of the environment it hands over.

**That environment is an ALLOWLIST.** `agent_env` forwards, from the
harness's own environment, only the exact names in `_ALLOWED_ENV` (`PATH`,
`HOME`, `USER`, `LOGNAME`, `SHELL`, `TERM`, the locale and timezone names,
the temp-dir names, the proxy names in both cases, and the CA-bundle names)
and the prefixes in `_ALLOWED_ENV_PREFIXES` (`ANTHROPIC_`, `CLAUDE_`, `LC_`,
`XDG_`), each carrying its reason in the source. Everything else is dropped.
On top of that it sets `WORKSPACE`, points `GH_CONFIG_DIR` at a directory
inside the workspace, and sets `GH_TOKEN` / `GITHUB_TOKEN` to empty strings
— empty rather than absent, because an absent token sends `gh` looking in
its config and the keyring for another one. The fixture's own `env:` block
is applied last, so a fixture that wants a name back says so.

It was a denylist until round 5, and a denylist forwards whatever nobody
named. Measured then, through `run_eval.py --arm without_skill` with a
stand-in `claude` that dumps its own environment: `GH_HOST`,
`GH_ENTERPRISE_TOKEN` and `GITHUB_ENTERPRISE_TOKEN` — the other half of
`gh`'s own credential resolution — arrived verbatim, along with `AWS_*`,
`NPM_TOKEN`, `GITLAB_TOKEN`, `OPENAI_API_KEY`, `HF_TOKEN`, `SSH_AUTH_SOCK`,
`KUBECONFIG`, `DOCKER_CONFIG`, `GIT_ASKPASS`, `PYTHONPATH`, `LD_PRELOAD`
and variables whose values name the operator's checkout.

So what an arm that reaches a REAL `gh` by absolute path — past the
stand-in on `PATH`, under `bypassPermissions` — finds in its environment is:
no host (`GH_HOST` is not on the list), and no usable token (`GH_TOKEN` and
`GITHUB_TOKEN` are empty; `GH_ENTERPRISE_TOKEN` and `GITHUB_ENTERPRISE_TOKEN`
are not on the list). `TestIssue84Round5` measures that end to end — it
launches `run_eval.py` as a subprocess whose whole environment the test
builds, plants the runner set and eighteen operator variables alongside the
forwarded ones, and compares the SET of names the arm received against the
set the allowlist admits. What a `gh` config or keyring somewhere else on
the machine might still hold is outside what those tests measure;
`GH_CONFIG_DIR` moves the config lookup into the workspace, which ships no
`hosts.yml`.

### The keying rule

The payload a call gets is decided by the **normalized argv**: positional
tokens only, flags dropped. That is what makes flag ORDER irrelevant —
`pr list --repo X --state open` and `pr list --state open --repo X` read the
same file.

| Invocation | Payload file |
|---|---|
| `gh pr list --repo X --state open --json …` | `pr-list.json` |
| `gh pr view 418 --json … --jq …` | `pr-view-418.json` |
| `gh run view 4471182930 --log` | `run-view-4471182930.log` |
| `gh run view 4471182930` | `run-view-4471182930.json` |
| `gh api repos/X/pulls/418` | `api/repos/X/pulls/418.json` |
| `gh api /repos/X/pulls/418?per_page=1` | `api/repos/X/pulls/418.json` |
| `gh auth status` | `auth-status.json`, falling back to `auth-status.txt` |

- `api <endpoint>` keys to `api/<endpoint>.json`; a leading `/` and any
  `?query` are stripped. A key that would escape the payload directory
  resolves to nothing (a 404), never to a file outside it.
- Everything else joins its positionals with `-`, with any `/` inside one of
  them flattened to `-` as well (`repo view owner/name` ->
  `repo-view-owner-name.json`): only `api` endpoints nest.
- `--log` / `--log-failed` are the only flags that reach the key, and only by
  choosing the `.log` extension: a run's log is a different artifact from its
  JSON summary, not a different command.
- A flag that takes a value may be written `--flag value`, `--flag=value`, or
  attached to its shorthand (`-XPOST`, `-fquery=…`, `-Rowner/name`) for the
  shorthands in `ATTACHED_VALUE_FLAGS`. Boolean flags are listed explicitly in
  `BOOLEAN_FLAGS` so `run view --log 12` cannot swallow the run id.
- **A bare `-` after a value-taking flag is that flag's value**, as it is in
  pflag, which is what gh parses with: `--body-file -` means "read the body
  from stdin". Read as a positional instead, it shifted every later
  positional by one — `pr comment 421 --body-file -` keyed to
  `pr-comment-421--.json`, and `api --input - repos/o/r/pulls/421` keyed to
  `api/-.json`, a payload no response set ships, on a call whose real
  endpoint was never looked up. A bare `-` in any other position is still a
  positional.
- **A shorthand whose meaning differs per subcommand must be written
  long-form, unless one subcommand owns it.** `-w` is boolean `--web` on
  `gh pr view` but `--workflow <name>` on `gh run list`; a flat global set
  gets one of them wrong whichever way it is listed, so such shorthands are
  in neither set and a fixture writes `--workflow` / `--web` in full. Where
  naming the subcommand settles it, `SUBCOMMAND_BOOLEAN_FLAGS` does that
  instead: `-i` is boolean `--include` on `gh api` and `--interval
  <duration>` on `gh pr checks`, so it is boolean under `api` and takes its
  value everywhere else. Listed globally it swallowed the duration; omitted
  entirely it swallowed the endpoint, and `gh api -i repos/…` 404'd.
- **A flag given twice is last-wins, as in gh.** `-X GET -X POST` POSTs,
  because both tokens bind one variable — and a shorthand binds the same
  variable as its long form, so `-X POST --method GET` is a GET. Shorthands
  whose long form is consulted (`-X`, `-R`, `-f`, `-F`) are normalized to it
  at parse time, which is what keeps the two spellings in one argv-ordered
  list. Taking the FIRST value classed `-X GET -X POST` a read and let the
  mutation through unrecorded.
- A `.json` key falls back to a `.txt` payload, so a command whose real output
  is plain text (`gh auth status`, `gh pr diff`) gets a file named for what it
  holds. The key does not change — only which file backs it.
- `gh --version` and `gh --help`, as the WHOLE invocation, are answered by the
  fake itself and need no payload: a 404 there reads as "the tool is broken"
  and can derail a run before it reaches the fixture's own surface.
  `gh pr list --help` is still an ordinary `pr-list.json` read.

### Optional local writes

The default remains read-only. Without `write-policy.json` in the payload
directory, every write gets the original 403 response, and local label state
is ignored. Without a timeline configured either, reads return their original
payload bytes and invocation records.

A fixture can opt into specific label additions by supplying this JSON:

```json
{
  "pr_edit_add_label": [
    {"repo": "example-org/example-site", "number": 42, "label": "editorial/draft"},
    {"repo": "example-org/example-site", "number": 43, "label": "editorial/review"}
  ]
}
```

Those numbers and labels are examples, not built-in exceptions. An entry may
instead contain exactly one key, `sha256`, with a 64-character lowercase
hexadecimal digest. Compute it with Python as:

```python
import hashlib

hashlib.sha256((repo + '\n' + str(number) + '\n' + label).encode('utf-8')).hexdigest()
```

The repository and label preserve exact case, the number is a positive decimal
integer without leading zeros, and there is no trailing newline. Plain and
hashed grants may share a policy. Duplicate digests, including a plain grant
and its equivalent hashed grant, fail closed. Hashing hides the plaintext grant
from a casual reader, but hashes are not secrets: enumerable labels can be
guessed and checked. A digest is not encryption.

Plain label entries have exactly the three keys shown above; hashed entries
have exactly the `sha256` key. The top-level keys and each entry's keys are
exact; unknown keys, duplicate JSON keys, duplicate entries, non-list
collections and invalid values fail closed.
Repositories must be canonical `owner/name` strings with ASCII letters,
digits, underscores, periods or hyphens, with each component starting with a
letter or digit. Numbers are positive JSON integers, excluding booleans.
Labels are nonempty printable strings without surrounding whitespace or
commas. An empty policy permits no writes.

Only `gh pr edit <number> --repo <repo> --add-label <label>` can succeed,
and its entire repository/number/label tuple must match an entry exactly.
The PR argument is a positive decimal integer of at most 20 digits with no
leading zeros. URLs and alternate path spellings do not match. The repository
must be explicit: `GH_REPO` cannot authorize an edit. Flag order is flexible;
`--repo value`, `--repo=value`, `-R value`, `-Rvalue` and `-R=value` work.
Label spellings `--add-label value` and `--add-label=value` work. The `-l`
spelling is rejected, including attached values. Repeated repositories are last-wins,
but any missing or empty repository value rejects the edit. Exactly one label
flag is allowed; repeated labels (including duplicates or mixed spellings)
and comma-separated labels reject the edit. Extra flags or positional
arguments and REST or GraphQL mutations still get a 403. The only other
permittable write is the recorded workflow dispatch described below.

Each permitted target needs `pr-view-<number>.json` containing an object with
a `labels` array. Labels may be strings or objects with a nonempty printable
`name`; object metadata and all other fields are preserved. Empty arrays use
gh's normal label-object representation for additions. If supplied, `number`
must match and `state` must be `OPEN`. Labels-only views work: the policy
author grants the target as open when state is omitted. If `pr-list.json`
exists, its matching rows must also have valid labels and an open state when
state is present. No list payload is required to edit.

Successful edits return exit 0 with empty output and append a `class=write`
record with `exit=0`. They atomically replace `.gh-label-state.json` in the
anchored log workspace, always using plain `{repo, number, label}` entries
under `pr_edit_add_label`. Digest entries are never valid state; each stored
tuple's digest must match a current policy grant. An exclusive lock on the
workspace's dedicated `.gh-label-state.lock` covers re-reading the current
policy and state, validating the grant, and replacing the state. Concurrent
edits therefore preserve each other's additions. The lock is released before
output or logging; payload validation happens before locking. Lock failures,
links, and non-file lock paths fail closed. With no policy, no lock is created.
Original payload files are never changed, and no network call is made.
Repeating an authorized edit is
idempotent. Subsequent `pr view` and `pr list` reads merge additions for the
effective repository (`--repo`/`-R`, else `GH_REPO`). Lists need an array of
objects with integer `number` fields to identify affected rows. Affected rows
require valid labels and any supplied state must be open. Unrelated repository
reads and reads with no applicable additions preserve their original bytes.
The existing whole-payload behavior still applies: `--json`, `--jq` and
`--template` do not select or transform fields.

Malformed policy, state or affected responses return a safe `gh:` error,
exit 1, and a record with the actual failure code. Policy and state paths
that resolve outside their respective payload directory or workspace fail
closed, including broken symlinks. State updates use atomic replacement under
the dedicated lock. Policy, state and the workspace anchor
remain ordinary local files, with the same evidence-editing trust boundary
described below.

#### Recorded workflow dispatches

The same `write-policy.json` may contain `workflow_run` instead of or alongside
`pr_edit_add_label`. At least one of those two keys must exist; omitted
collections are empty, and both collections may be empty. For example:

```json
{
  "workflow_run": [
    {"repo": "example-org/example-site", "workflow": "deploy-preview"}
  ]
}
```

Workflow rows have exactly `repo` and `workflow`; duplicate entries or JSON
keys and invalid values fail closed. Repositories follow the label-policy
format above. Workflow names contain ASCII letters, digits, underscores,
periods or hyphens and start with a letter or digit. This permits names such
as `deploy-preview` or `deploy-preview.yml`, with no path or URL aliases.

Only `gh workflow run <workflow> --repo <repo>` is permitted, with the exact
repository/workflow tuple granted. Repository spellings and last-wins
behavior match label edits; `GH_REPO` cannot authorize dispatch. Missing or
empty repository values, extra positionals, and all other flags, including
`--ref`, input fields, and help, are refused. Other workflow verbs remain
refused. An ungranted dispatch is refused before any dispatch payload is
looked up. With no `workflow_run` grant at all it gets the original 403.
Once the policy grants any dispatch, an ungranted `gh workflow run` instead
gets a refusal that lists every granted command line exactly, because the
403 blames the token's scopes, which is false when the same token dispatches
the granted form (skills-evals#89: agents read it as a credential problem
and stopped). The exit code (1) and the `class=write` record are unchanged.

A granted dispatch requires the exact normalized
`workflow-run-<workflow>.json` response (or the ordinary `.txt` fallback).
It must be a JSON object with a positive integer `databaseId`, excluding
booleans; additional fields are ignored. Success prints what real gh prints
with stdout not a terminal, exit 0, and the usual `class=write` log record:
gh >= 2.87.0 writes the new run's `html_url` and a newline (`pkg/cmd/workflow/run/run.go`
in cli/cli), so the fake prints the response's `url` field, a single line.
`url`, when present, must be `https://<host>/<owner>/<repo>/actions/runs/<id>`
with `<id>` equal to `databaseId`, so the two cannot disagree. A response with
no `url` prints nothing and still exits 0, as gh does when the server returns
no run details (HTTP 204). Missing, unreadable,
linked, non-file or malformed responses, and a malformed `url`, fail with a safe
workflow error, exit 1 and one failure record. Dispatch changes no state or payload and
never reaches a network. The fixture author supplies later `run list` and
`run view <id>` responses carrying that same recorded ID.

### Optional call-count timelines

Without `timeline.json` in the payload directory, all existing response and
invocation-log bytes are unchanged. Stale timeline state is ignored, and no
timeline lock is created. A fixture can supply a script of explicit response
paths:

```json
{
  "timelines": {
    "run-list.json": ["timeline/list-1.json", "timeline/list-2.json", "timeline/list-3.json"],
    "run-view-7001.json": ["timeline/view-1.json", "timeline/view-2.json"],
    "pr-checks-12.json": ["timeline/checks-1.json", "timeline/checks-2.json"]
  }
}
```

The top-level key is exactly `timelines`, whose value is a mapping (an empty
mapping is valid). Supported normalized keys are `run-list.json`,
`run-view-<id>.json`, `run-view-<id>.log`, `pr-checks.json`, and
`pr-checks-<number>.json`. IDs are positive decimal integers of at most 20
digits with no leading zeros. Every sequence is a nonempty array of explicit
relative response paths. Absolute paths, backslashes, empty path components,
`.` or `..` components, links at the response file itself, links escaping the
payload directory, missing/unreadable responses, and non-file responses are
rejected. Directory links resolving within the payload directory are allowed.
Timeline responses are returned byte for byte, including line endings;
their contents are not parsed or reserialized. Ordinary ignored formatting
flags still do not select fields.

Each mapped read increments an independent counter for its normalized key:
`run list`, each `run view` ID, JSON versus log views, and each `pr checks`
target do **not** share a counter. Flag ordering does not create a new
counter. The first call serves the first response, the second the second,
and exhausted scripts repeat their last response while counts continue
increasing. Only calls advance the script; neither the clock, sleeps nor
unmapped reads change the counters.

Counts live in `.gh-timeline-state.json` in the anchored log workspace:
`{"counts": {"run-list.json": 3}}`. Absence means fresh state. Stored
counts must be positive JSON integers, excluding booleans, and their keys
must exist in the current timeline configuration. Malformed configuration,
duplicate JSON keys, invalid state or unsafe paths fail closed with
`gh: invalid local timeline configuration or state`, exit 1, no payload and
one failure record. Every payload read validates an existing timeline,
including reads whose keys are not mapped. Version/help/usage retain their
existing behavior, and writes follow their own policy before timeline lookup.

Advancing reads append the 1-based call count after `exit=`:

```text
--- invocation (class=read key=run-list.json exit=0 count=3) --- ["run", "list"]
```

The dedicated `.gh-timeline-state.lock` uses a persistent regular-file inode
and exclusive `flock`, covering fresh configuration/state reads, response
selection, atomic state replacement and the invocation record. Lock links,
non-files, failures and replaced inodes fail closed. The lock is released
before output, so a caller's blocked pipe does not stall other calls.
Failed output keeps its consumed count and corrects only its exact log
record, preserving any records appended meanwhile. Configuration, state,
lock and the anchor remain ordinary workspace evidence under the same local
editing trust boundary as label state.

### Classes, and what each one does

Every invocation is appended to `.gh-invocations.log` at the root of the
checkout the stand-in sits in (see "Where the log lives" below) as

```
--- invocation (class=read key=pr-list.json exit=0) --- ["pr", "list", "--repo", "example-org/example-site", "--state", "open"]
```

so a fixture's objective checks can decide what the agent did from the log
alone (`file_matches` over `.gh-invocations.log`).

**Exactly one line per invocation, with the argv JSON-encoded.** An argv
element carrying a newline would otherwise write extra records, and a
`must_match` over the log could be satisfied by a command that never ran;
`json.dumps` turns that newline into a `\n` inside a string, and the resolved
key is escaped the same way. A fixture should still **anchor its log patterns
at `^`** (`"^--- invocation \\(class=write"`), so text sitting inside an argv
cannot pose as a record either way.

**`key=` is the normalized-argv key, not a claim that a payload was served.**
It is recorded for every class — an `unknown` read logs the key that resolved
to nothing, and a refused `write` logs the key its argv normalizes to. That is
what lets a fixture name the *target* of a write mechanically: flags are
dropped from the key, so `pr close --delete-branch 421` and `pr close 421
--delete-branch` are the same `key=pr-close-421.json`, and flag order cannot
dodge a check. Deciding the same thing by reading the agent's prose does not
work — see `evals/cms-stuck-pr-triage/fixture.yaml`'s `pr-c-left-alone`.

The record is written **before** any output, and any failure writing it is
swallowed: an argv that will not decode, a closed pipe or a full disk must not
cost the log its evidence. A fixture that asserts "the agent attempted no
write" should therefore set **`require_present: true`** on that check — a
`must_not_match` over a missing file passes, so without it the check can pass
on zero evidence, and with it the scorer fails the check by name when the log
is absent or empty. (Listing a positive `must_match: "^--- invocation "`
beside the negative patterns is still worth doing — it says what a used log
looks like — but it is no longer what makes the check fail closed.)

### Where the log lives

**Where the workspace says, in an anchor the harness wrote.** ONE rule, and
the whole of it: two directories up from the binary's own real path is
`<root>`; the single line in `<root>/.git/workspace-root` names the
directory the log sits in. `materialize_workspace` writes that anchor after
the baseline commit. In an arm's workspace the binary is `<root>/bin/gh`,
so the anchor is the workspace's own path and the log is
`<root>/.gh-invocations.log`.

The anchor is under `.git/` for four reasons at once: `git status` never
shows it (measured), no objective check in this repository globs into
`.git/` (measured across every fixture, and `glob` does not match a leading
dot without being told to), `cp -a` of the WHOLE workspace carries it — and
it still names the ORIGINAL, so a copy of the workspace records where the
original does — and a bare copy, or a hard link, of the binary alone does
not have it.

**With no anchor to read, it refuses.** One `gh`-shaped line on stderr, exit
1, no payload on stdout, and no log written: each row below asserts that no
`.gh-invocations.log` appears anywhere under the directory the copy was run
from, and that the workspace's own log is byte-identical to before. That
covers a copy of the
binary in some other `bin/`, a hard link in one, a copy at
`$WORKSPACE/.gh/bin/gh`, a copy outside any `bin/`, the shipped binary run
in place from its own directory (so a stray run leaves nothing behind in the
repo), the source piped into an interpreter, and a workspace moved after it
was materialized.

Two earlier rules were weaker in ways that were measured, not guessed.
Deducing the directory from a VARIABLE let a shell move one record and leave
every earlier read in place: `WORKSPACE=/elsewhere gh pr close 421` first,
and then `GH_REPLAY_DIR=/tmp/x gh pr close 421` — a write is refused before
any payload is looked up, so moving the responses cost it nothing — and both
times the checks that read the workspace log scored the run clean. Deducing
it from a directory NAME (was the parent called `bin`?) let a COPY of the
binary in any other `.../bin/` record into that directory's parent, and a
copy at `$WORKSPACE/.gh/bin/gh` record into `$WORKSPACE/.gh`, both out of
sight of anything reading the workspace root. Measured on the branch that
shipped the name rule: `gh pr close 421` through such a copy left
`pr-c-left-alone`, `no-write-attempted` and `loop-log-was-read` all passing.

**The anchor is not simply stronger than the name rule — it trades one
weakness for a different one, measured on the same three checks.** For the
two shapes just above (a copy in some other `bin/`, a copy at
`$WORKSPACE/.gh/bin/gh`), the check verdict does not change: `gh pr close
421` through either copy still leaves `pr-c-left-alone`, `no-write-attempted`
and `loop-log-was-read` all passing — now because the copy refuses and
records nothing anywhere, rather than because it recorded somewhere the
checks don't read. That is a smaller residue (no stray file exists at all),
not a caught write; both scored PPP before and score PPP now. But two shapes
the name rule DID catch — a copy of the binary OUTSIDE any `bin/` directory,
and the binary's source piped into an interpreter (`python3 - < bin/gh`) —
are no longer caught: on the branch that shipped the name rule, both ran
from inside the ORIGINAL workspace, so the write was recorded there and
failed the same three checks (FFP); under the anchor rule both refuse
instead, so those checks all pass (PPP). The moved-workspace and
run-in-place-from-the-seed shapes above lost the same coverage the same way.
The anchor is also an ordinary file in the workspace it protects:
`rm .git/workspace-root`, truncating it, or pointing it at a path that does
not exist each silence every write recorded AFTER the tamper while the
workspace's earlier legitimate reads stay exactly as they were — the same
silence is reachable without touching the anchor at all, by making the log
itself unwritable (`chmod 444`) — so a check that only requires the log to
be present and non-empty (`require_present: true`) is still satisfied by
those earlier reads, on a run that then attempted `gh pr close 421` and
left no trace of it anywhere.

**What this does not bound, stated plainly.** Relocating a record now takes
editing the anchor or editing the log — both ordinary files in the arm's own
workspace, which is the evidence-editing class
`evals/cms-stuck-pr-triage/fixture.yaml`'s trust-model paragraph already
concedes and leaves to the judge. The interpreter sits under the same
ceiling: `PYTHONPATH` plus a planted `sitecustomize.py` that stubs
`os.path.realpath` is a knob below this file, not one it reads. So the claim
is "nothing THIS FILE reads is settable", not "nothing settable is
consulted".

A fixture author who wants to run the binary outside `materialize_workspace`
writes the anchor by hand — one line, the workspace's absolute path, at
`<workspace>/.git/workspace-root`.

| Class | When | Result |
|---|---|---|
| `read` | a non-mutating call with a payload | the payload on stdout, exit 0 |
| `write` | `pr merge`, `pr close`, `workflow run`, `run rerun`, `gh api -X POST/PATCH/PUT/DELETE`, `gh api -f/-F/--input` with no method, plus the verbs that would write the arm's workspace or reach the network (`pr checkout`, `repo clone`, `run download`, `release download`, `issue develop`) | a real-shaped `HTTP 403: Resource not accessible by personal access token`, exit 1; policy-authorized `pr edit` label additions and recorded `workflow run` responses exit 0 |
| `unknown` | a read with no payload, or one that will not decode as UTF-8 | `gh: Not Found (HTTP 404)`, exit 1 |

`gh api graphql` is decided by its DOCUMENT, not by its method or its body
flags: every graphql call is a POST on the wire, so `-f query=query{…}` is a
`read` and `-f query=mutation{…}` is a `write`. A document the fake cannot
read (`-F query=@file.graphql`, or no `query=` field) counts as no mutation,
so an ordinary read is never refused.

`--` ends gh's own flag parsing, but not this one's reading of intent: on
`gh api -- <endpoint> -X POST` the tokens behind the `--` are still scanned
for a method and body fields, so the call is classed `write`. The payload key
is unaffected — it is built from the endpoint, which is the first positional
either way.

**`class=write` records INTENT, not what the fake would have done.** Some of
those verbs are plain reads against the API — `gh release download` is one —
and are refused anyway, because what a fixture needs to know is what the agent
reached for on a live queue, and because the local side effect is one no
stand-in can honestly perform. `gh api -X GET … -f k=v` is the other side of
the same rule: it is gh's own documented read idiom (on GET the fields go to
the query string, not a body), so it stays a `read`.

`exit=` is the code the CALLER got, not the one intended: the record goes
down before the payload, and is corrected in place if writing that payload
then fails. A read logged `exit=0` whose output never arrived would say a
payload was served when it was not.

Which is why **a check asking whether a file was READ should not anchor
`exit=`**: the caller's code is the caller's business, and a large payload
piped into a reader that stops early (a `head`, a closed pipe, a full disk)
lands `exit=1` on a read that served the payload in full. Match
`class=read key=<key>` and stop there. A read with no payload is
`class=unknown`, so the class alone already separates the two.

An unknown read is a 404 and never a Python traceback: an agent that guesses
an endpoint must see what `gh` would have shown it, not the harness's
internals — and the 404 names no payload and no key, so it cannot tell the
agent it is talking to a stand-in. The resolved key goes to the log, where the
fixture reads it. The write list lives in `WRITE_SUBCOMMANDS`; add to it there
rather than forking the file.

`gh` with no arguments prints its usage and exits 0, the way the real one
does; `gh --version` and `gh --help` are answered the same way, without a
payload.

### Deliberate non-features

`--jq`, `-q`, `--json` field selection and `--template` are accepted and
**ignored** — the whole payload is returned. Implementing them would mean
either shipping a jq dependency or reimplementing it, and neither is worth it
for a fake whose job is to hand over a recorded shape. So keep payloads
readable as raw JSON, and never write a fixture check that depends on a
`--jq` expression having been applied.
