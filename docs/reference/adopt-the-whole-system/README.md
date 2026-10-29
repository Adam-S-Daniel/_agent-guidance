# Adopt the whole self-improvement system

A kit for building, on your own GitHub account and Claude plan, the system
this repo belongs to: fleet guidance delivered once per session, a skills
registry, git-hook checks, memory that points at its home, evals, and the
loop that improves and retires skills and guidance on its own.

**In one sentence:** the kit holds working exports of the six real repos
that run that system, with every owner name replaced, plus scripts that
create your repos, settings, environments and GitHub Apps and copy each
export in through a pull request.

## Start here

**Copy this folder out of the repo it came in first**, to a place outside
any git repository, and run Claude Code in the copy:

```bash
cp -R <your clone>/docs/reference/adopt-the-whole-system ~/adopt-kit
cd ~/adopt-kit
claude
# then, in the session:
/adopt
```

Why: Claude Code loads every `CLAUDE.md` from the folder it starts in and
from each folder above it, and a `.claude/settings.local.json` at the root
of the git repository it starts in. Inside the clone, that is the source
owner's own `CLAUDE.md` and `AGENTS.md` (their fleet's rules, for their
account), loaded into your setup session. Started at the clone's root, its
`.claude/settings.json` hooks also run: one writes the owner's guidance into
your `~/.claude/CLAUDE.md`, another installs the owner's skills. A copy
outside every repository loads none of that; only the `/adopt` skill and
your own `~/.claude/` apply. (Checked with Claude Code 2.1.312 on
2026-10-29, and against its memory and settings docs.) The copy is
self-contained: every link and script stays inside it.

The [`/adopt` skill](.claude/skills/adopt/SKILL.md) reads where your account
stands, explains the next step, and runs each script with you, dry run
first.

## Without Claude

`bootstrap/status.sh` says the same thing in a terminal: every step, which
are done (it checks GitHub, not a list you keep), and the next one with what
to run.

```bash
cp bootstrap/fleet.env.example fleet.env   # fill in OWNER, OWNER_TWO, PLAN
bash bootstrap/status.sh
```

The steps are in [docs/quick-start.md](docs/quick-start.md). Hands-on setup
is one to two days, most of it browser steps; the loop's first merged
improvement comes one to two weeks later, because it waits on the eval
budget, not on you ([what it costs](docs/before-you-start.md#what-it-costs)).

## Read more

- [How it works](docs/how-it-works.md): the six repos, the loop, and the
  rules the design follows.
- [Before you start](docs/before-you-start.md): what you need, what it costs,
  `fleet.env`, one owner or two.
- The parts, one page each: [settings and Apps](docs/settings-and-apps.md),
  [guidance](docs/guidance.md), [skills](docs/skills.md),
  [generated files](docs/generated-files.md),
  [evals and the loop](docs/evals-and-loop.md),
  [routines](docs/routines.md), [machines and cloud](docs/machine.md).
- [Week to week](docs/week-to-week.md) and [traps](docs/traps.md).
- [`bootstrap/`](bootstrap/README.md): what each script does and cannot do.
- [`starter/`](starter/README.md): the six exports. How they were made and
  what was left out: [`export/DECISIONS.md`](export/DECISIONS.md).

## Test the kit

```bash
# as an ordinary user:
unshare --user --map-current-user --pid --fork --mount-proc -- bash run-all-tests.sh
# as root (a mapped user namespace leaves `nobody` unmapped, which breaks the
# tests that drop privileges):
unshare --pid --fork --mount-proc -- bash run-all-tests.sh
```

`starter/known-failures.txt` is calibrated for the root form; run as an
ordinary user, a few ids differ. It runs every export's own test suite, the bootstrap tests, the export's
own tests (the leak scan, and that git tracks every kit file) and
`export/export.py --check`, and prints one PASS or FAIL line each. The
export check needs the source repos checked out beside the repo the kit
came in, so in a copy of the kit it prints SKIP. Read the exit code
directly, not through a pipe.
The [starter README](starter/README.md#running-the-tests) says what each
suite needs, how to run one on its own, and which failures are known.

## License

Apache License 2.0, the license of the repo this kit lives in
([`LICENSE`](LICENSE), a copy of it). The five public exports carry their
own `LICENSE`.
