# 0008 — A downloaded binary is pinned by version and by digest, and the digest lives here

**Status:** Accepted (2026-08-20). Extends [0007](0007-sha-pins-carry-no-version-comment.md) and the pinning rule in `agents-md/base.md` to a shape neither of them reaches.

## Context

`agents-md/base.md` opens its pinning section with an absolute: "**Every
`uses:` is pinned to a full 40-character commit SHA**". The argument is about
trust, not tidiness — "a tag is a movable pointer: pinning to one gives whoever
can retag the upstream repo a shell on the runner, holding that job's token."

That rule governs `uses:`. It says nothing about a `run:` block, and four of
this repo's six workflows — `ci.yml`, `sync.yml`, `drift-report.yml`,
`skills-lock-bump.yml` — carried this:

```yaml
      - name: Install yq
        run: |
          sudo wget -qO /usr/local/bin/yq \
            https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
          sudo chmod +x /usr/local/bin/yq
```

A third-party binary, fetched over a **mutable** ref, written straight to an
executable path, marked `+x`, and run as root — every property the `uses:` rule
exists to deny, in a syntax the rule does not cover. `skills-lock-bump.yml`'s
job in particular holds an App token that can push to ~20 repos.

Nothing was watching it, and nothing could be. Dependabot has no ecosystem that
reads a URL out of a shell script, so no bump PR was ever going to arrive; the
`github-actions` ecosystem sees `uses:` and nothing else. The
`sha_pinning_required: true` repo setting is likewise a rule about actions. The
gap is not that a check was misconfigured — it is that every mechanism in the
fleet that could have noticed looks only at `uses:`.

**How much `latest` actually moves, measured on the day of this decision.** The
three most recent `mikefarah/yq` releases were `v4.53.4` (2026-08-19),
`v4.53.5` and `v4.53.6` (both 2026-08-20). Three releases inside about
thirty-six hours: two runs of the same workflow, on the same commit, hours
apart, would have installed different binaries and neither would have said so.

**The flakiness that surfaced it.** Run 32383649430 on PR #56 lost this step to
`wget` exit 4 — a network failure — four seconds in, before any test body ran.
It reded a `test` check run while a sibling run of the same context on the same
head passed. A single unretried fetch on the critical path reds CI for reasons
unrelated to any diff, and a red run that means nothing is worse than noise: it
is training to ignore a red run.

## Decision

**Pin the version, state the digest here, verify before installing, and retry
the fetch — bounded.** The step, identical in all four workflows:

- `YQ_VERSION: v4.53.3` and `YQ_SHA256: fa52a4e…` as step `env:`.
- The download goes to a temp path. Only bytes matching `YQ_SHA256` are
  installed, with `install -m 0755` — so `/usr/local/bin/yq` never exists in an
  unverified state, which `wget -O /usr/local/bin/yq` followed by `chmod +x`
  could not promise.
- Fetch and verify are **one retryable unit**, three attempts, 5s then 10s
  backoff. A truncated body is an HTTP 200 with the wrong digest, so a checksum
  miss is a transient to retry; only a run that fails all three is reporting
  something real.
- `yq --version` runs as a smoke test before the step succeeds.

**Version choice.** `v4.53.3` was published 2026-06-06 — 75 days old, and the
**newest** release that has cleared `base.md`'s 7-day cooling-off. The three
newer ones are 1, 0 and 0 days old. This is the cooling-off rule doing visible
work rather than being recited: `latest` on this day was a release hours old.

**The digest is stated in this repo, not fetched alongside the binary.** This
is the part most likely to be "improved", so the reasoning is recorded rather
than left to be re-derived. yq does publish a `checksums` asset, and fetching
it is the intuitive move — but it comes from the same host, over the same
connection, under the same tag as the binary. Anyone able to serve tampered
bytes from one URL can serve a matching `checksums` from the adjacent one, so
that check proves the download was not *corrupted* and says nothing about
whether it was *substituted*. A digest committed here is a claim this repo
makes, reviewed in a pull request, exactly like the forty hex characters in a
`uses:` pin — the trust anchor is local, which is the whole point of pinning.

The published `checksums` was not ignored, it was used **at pin time**: the
binary was downloaded, its SHA-256 computed from the bytes, and that value
matched the SHA-256 column of the release's own `checksums` (column 18, per its
`checksums_hashes_order`). Two independent derivations of one constant.

There is a second, smaller reason. `checksums` is a 32-column table whose
column order is defined by a *separate* file; reading SHA-256 out of it means
hardcoding a column index that upstream can renumber. A wrong column does not
fail loudly — it compares a SHA-384 against a SHA-256 and reports a checksum
mismatch, which reads exactly like the attack it was meant to detect.

**The step is duplicated four times rather than factored into a composite
action.** A local `uses: ./.github/actions/install-yq` is exempt from SHA
pinning (`base.md`: "`./local/path` … have nothing to pin"), but
`test_bump_workflow` asserts that *every* `uses:` in `skills-lock-bump.yml` is
a 40-hex pin, and a local ref fails it. Widening a security lint to accommodate
a refactor is the wrong direction — so the duplication stays, and
`test_yq_install_pinned` asserts all four copies are byte-identical, which is
what makes it safe.

## Consequences

- **Nothing bumps this now, and that is the real cost.** No Dependabot
  ecosystem reads a URL out of a shell script, so `v4.53.3` will sit until a
  human moves it — the same silent staleness that afflicts every pin no bot
  watches. Accepted knowingly: a pinned-and-stale yq is a known quantity, an
  unpinned one is a different binary every time it is asked for. A bump is two
  lines in four files, and `test_yq_install_pinned` fails loudly if only some
  of them move.
- **The regression cannot come back quietly.** `test_yq_install_pinned` reads
  PARSED `run:` bodies and step `env:` maps, never file text — which matters
  concretely here, because `ci.yml` now contains the literal string
  `releases/latest` inside a comment explaining the ref it stopped using. A
  grep flags that comment and would equally miss a mutable ref reached through
  a YAML anchor or a folded scalar. All five assertions were negative-controlled
  against six deliberately broken trees before being trusted; one of the six
  changed the test, by showing that a restored mutable ref tripped the vacuity
  guard and reported "a step was deleted" instead of naming the ref.
- **CI gets slower to fail, and only when it was going to fail.** A genuinely
  unreachable host now costs 15 seconds of backoff before the red run. A
  healthy fetch is unchanged.
- **This closes the gap in four files, not in the fleet.** The general lesson —
  that `run:`-block downloads are outside every pinning mechanism this account
  has — is deliberately NOT written into `agents-md/base.md` here. 0007's
  *Consequences* warn that the managed block has a length budget and that "two
  more acceptances stop the block being short enough to read", and a base.md
  edit reaches ~20 repos on the next sync. That is its own decision with its own
  blast radius, and it should be taken on its own evidence: a fleet-wide audit
  of `run:` blocks that fetch executables, which this ADR does not perform.
  Recorded here so the next reader knows the omission was chosen, not missed.
