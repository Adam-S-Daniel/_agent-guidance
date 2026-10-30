#!/usr/bin/env python3
"""Compute a PROPOSED model roster: which models this harness SHOULD be
running against today.

IT DOES NOT DECIDE WHAT RUNS. The roster the harness runs on is
`evals/roster.yml`, committed on `main` — ruleset-protected and
pull-request-only, so a seat or a `catalogue_seen` entry cannot appear
there or vanish from there without a commit that went through a pull
request: a human review and merge under `roster_mode: proposal`, or the
same branch protection plus a passing `test` check under `roster_mode:
auto` (ADR 0003) — never a direct push either way. See
docs/decisions/0001-roster-trusted-on-main.md. What this module produces is
a DIFF against that file, published with every seat's reason in words and
its numerator and denominator beside it, for a human to merge or not
(`roster_mode: proposal`) or for the automation to merge once CI passes
(`roster_mode: auto`). `run_eval.select_models` never reads this module's
output.

A PURE FUNCTION OVER FILES. `compute_roster()` takes already-parsed documents
and a frozen `now`, and returns the roster dict — no network, no clock, no
environment. The network calls in the feature live in
`scripts/refresh_models.py`, which produces this module's `models_doc`, and
the one CLI call in `scripts/probe_model_defaults.py`, which produces
`defaults_doc`.

WHO IS AN ARM (the owner's decision, 2026-09-22). Two rules, and the second one
is subordinate to the first:

  1. USAGE SEATS. Every available model at or above the policy's entry bar
     (`arm_enter_usage_pct` of rankable, attributable census turns over
     `arm_enter_window_weeks`) is an arm, with its share in its reason.
  2. NEWEST PER QUALIFYING TIER. In a tier that rule 1 already seated
     someone in, the newest available model past the cooling-off is ALSO
     an arm if it is not one already — and its reason says so in words,
     naming the qualifying share it rides on. A TIER NO MODEL OF WHICH
     CLEARS THE ENTRY BAR GETS NO ARM FROM THIS RULE, however new its
     newest model is; that model is listed under `excluded` with that
     reason. This is what the rule used to be — newest per tier, across
     every tier — and why the arm set shrank: a tier the fleet does not
     measurably use was being measured anyway.

  THE NO-CENSUS FALLBACK IS UNTOUCHED, and it is not an exception so much
  as the same sentence read at its limit: with no usable usage there is no
  usage-qualified tier, and a roster must not be empty. So whenever the
  enter window carries no usable evidence — any of `_census_verdict`'s
  eight verdicts, or a fresh census whose enter window alone fails one of
  the ranked-usage floors — rule 2 reverts to NEWEST PER TIER ACROSS ALL
  TIERS, with the existing "no fresh census (…)" reasons naming which of
  the eight it was. Restricting it there would seat nobody.

  A usable census that simply names no model at the entry bar is neither
  of those cases: it is evidence, and what it is evidence of is that no
  tier qualifies. The only seats left are previous arms held over the exit
  bar, and if there are none `main()` refuses to publish a roster with no
  arms (rc 3) and the committed roster stands — which is the honest
  outcome, not a hole.

  VENDOR DEFAULTS OVERRIDE BOTH RULES WHERE THEY RESOLVE (the owner's decision,
  2026-09-27, #202, ADR 0002). In a tier whose default model the freshly
  installed Claude Code CLI reports (#203) and that model is available this
  run, usage only decides whether the tier is on the roster; the seat goes
  to the default with no cooling-off, and a previous arm it supersedes retires by `superseded_exit_weeks` rather
  than the exit window. A previous arm newer than the default gets the
  ordinary exit check (#203 round 1); the default's own previous seat, with
  nothing else putting its tier on the roster, is held while the TIER's
  combined share clears the exit bar (#203 round 2). A default governs the
  models of its own FAMILY word only, so a peer family in the same rung
  (`[fable, mythos]`) keeps the two rules above. A family the probe FAILED
  for, OR one the probe ANSWERED for but this run's catalogue does not
  otherwise match (not available, an ambiguous snapshot), is FROZEN for
  that run (#203 probe round 1; round 7, R7-1, routed the mismatch case
  through the identical freeze — the two used to diverge behind a guessed
  "effective default", which rounds 4 through 7 each found a way to retire
  or exclude a seat a clean week keeps; one path is sound): its previous
  arms keep their seats, none is retired but a model gone from the Models
  API, and `defaults_failed` (a failure) or `defaults_mismatched` (a
  mismatch — its own wording throughout, never "probe failed", so a human
  can tell the two apart) says so. While it still holds a seat the Models
  API lists, it gets no new seat at all (#203 probe round 3, R3-1); only a
  family that would otherwise vanish from the roster is seated — by the
  usage entry bar (rule 1, #203 probe round 2), or, with no usable enter
  window, as the newest in its tier, as the no-probe fallback would (R3-6).
  An answer naming a model of the wrong tier or family is a probe failure
  too, as is one naming a model with no `created_at` to start its
  predecessor's buffer from (R3-2; R5-1, #203 probe round 5): the CLI said
  something nonsensical about its own alias, or named a model this run has
  nothing to measure a buffer from. THE GOVERNING GUARANTEE (R5-2, round 5;
  made structural in round 7): a single run whose probe answer is a
  failure or a mismatch changes no seat a clean run would not. A
  persistent mismatch that HOLDS a listed seat is surfaced loudly (a
  warning, a summary line, a tracking issue) so a human can fix the probe.
  A previous arm held only under its DATED id still counts as that seat
  (R5-3, round 5; fixed for a SEATED default too in round 7, R7-2: the
  `model_id == default_id` branch had missed the same alias collapse the
  superseded-arm buffer already used). Nothing about a freeze is carried to
  the next run. See `_resolve_defaults` and `_default_rung_decision`. Every
  other tier keeps the two rules above verbatim.

Inputs
  models_doc   {"fetched_at": ..., "models": [{id, created_at, ...}, ...]} —
               availability, straight from GET /v1/models. Trusted for
               availability within the run that fetched it.
  census_doc   {"generated_at": ..., "weeks": [...], "counts": {model: {week: n}}}
               — usage, published to `persistent/eval-results` as usage/latest.json by
               scripts/model_usage_census.py. THE ONE INPUT STILL WRITTEN BY
               A JOB ON ANOTHER MACHINE, and trusted for nothing: it can
               shape a proposal and nothing else. Optional: absent, older
               than the policy's freshness window, dated in the future, or
               empty over the window, and the roster falls back to "newest
               per tier" and says which of those it was in every reason.
  policy       evals/roster-policy.yml — every threshold, plus the tier ladder.
  defaults_doc {"probed_at", "harness_version", "defaults": {alias: model id},
               "skipped": [alias], "errors": {alias: class}} from
               scripts/probe_model_defaults.py. Optional and untrusted;
               absent, and the roster is byte-for-byte the one computed
               without it; unreadable, junk, eval.yml's `{"probe_exit": ...}`
               stand-in, or answering for no alias at all, and every family
               it did not skip is frozen for the run.
  previous     THE COMMITTED `evals/roster.yml`. Trusted, and the source of
               the observation history (`catalogue_seen`). Present and
               unreadable is FATAL (`TrustedRosterUnreadable`): that is a
               defect in this repository, not a fact about an untrusted
               input, and the proposal an empty `previous` would produce —
               seat every live model — is the shape a reviewer is most
               likely to wave through.

THE CENSUS IS AN INPUT, NOT AN INVARIANT: a row that is not
`model -> {week: count}`, a count that is not a number, a `catalogue_seen`
entry that is not `{id, last_seen}` — each is skipped with a one-line named
message, never a traceback, and never with the offending value echoed into a
public log. It also carries a SIZE BOUND (`CENSUS_MAX_KEYS`,
`CENSUS_MAX_BYTES`), past which the run refuses rather than let an untrusted
document decide how much work it does.

Output — roster/latest.json on `persistent/eval-results`, an EXHIBIT read by no
decision, plus `proposal`, which is what `eval.yml` acts on:
  {generated_at, source: {models_api_at, census_at, admin_report_at},
   defaults: {source: "claude-code-cli <version>", probed_at,
              resolved: {alias: id}, unresolved: [{alias, reason}]}
                                  -- only when a probe resolved an alias;
                                  published for the reviewer, never committed,
   defaults_failed: {alias: class} -- only when a family is frozen this run,
   defaults_document_failed: class -- only when the whole document failed,
   defaults_mismatched: {alias: {id, class}} -- only when the probe answered
                                  an id this run's catalogue does not match;
                                  that family is frozen exactly like a
                                  failed probe (R7-1), kept in its own field
                                  and wording so a human can tell the two
                                  apart,
   arms: [{id, reason}], judge: {id, reason, is_arm}, preflight: {id, reason},
   unranked: [{id, reason}], excluded: [{id, reason}],
   compared_to_previous: bool, previous_state: "compared"|"none",
   retired_since_last: [...], added_since_last: [...],
   catalogue_seen: [{id, last_seen}, ...],
   proposal: {status: "same"|"differs",
              changes: [{kind, field, from, to, reason}, ...]}}

`catalogue_seen` is the union of every model id the Models API has been
observed to list, each with the date it was last seen (property 5,
DESIGN.md). It comes IN from the committed roster and goes OUT in the
proposal; it no longer round-trips through `persistent/eval-results`, and it is no
longer capped or exempted — an entry leaves when its `last_seen` is older
than the policy's window, and by no other route. See
`_update_catalogue_seen`.

Every entry carries its reason IN WORDS. The explorer tool renders them, and a
roster nobody can explain is one nobody will override when it is wrong.
`judge.is_arm` is the one thing a reason cannot carry: the runner has to refuse
a judge that is also an arm, and it cannot do that by reading prose.

NO MODEL ID APPEARS IN THIS FILE. Tier comes from the family word in the id
itself, matched against the ladder in roster-policy.yml.

This module must never read the environment, and must print nothing to
stdout but `render_summary`'s Markdown (N7, #129 review round 7). Neither
rule is visible from inside this file, which is why they are written here.
eval.yml's roster step exports the Models API bearer for
`scripts/refresh_models.py` and runs this module in the SAME shell, so that
credential IS in this process's environment even though nothing here wants
it; and that step's stdout goes to `$GITHUB_STEP_SUMMARY` and the public
job log, so anything else printed there is published. "A pure function over
files" is the property that makes both rules free to keep.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from timeweeks import complete_weeks_since, parse_ts, window_weeks
from timeweeks import iso_week  # noqa: F401 -- re-exported: identity-checked
                                 # by test_roster_and_census_share_one_week_implementation

# The Models API has no price field, so "cheapest" and "most capable" are both
# read off the tier ladder rather than off a number. Within one tier the newest
# model is taken as the current one — which is true of every tier transition so
# far (a new version supersedes its predecessor at the same or lower price).
# If that ever stops holding, this is the assumption to revisit first.

#: An id that is another id plus a date is a pinned snapshot of it, not a
#: second model. See `alias_map`.
#: `\Z`, not `$`: `$` also matches just before a trailing newline, so a
#: census key `<alias>-YYYYMMDD\n` (a stray newline, not a real snapshot
#: id) folded onto `<alias>` and inflated its measured share — measured:
#: a false ~99.9% instead of a real 50.0% (N9, #129 review round 6).
SNAPSHOT_SUFFIX = re.compile(r"^(?P<base>.+)-[0-9]{8}\Z")


def _stderr(message: str) -> None:
    print(f"roster: {message}", file=sys.stderr)


def committed_roster_problems(document) -> list[str]:
    """Return every violation of the reviewed roster-file contract.

    This is deliberately shared by CI's committed-file test and the proposal
    publication sink.  A generated file is only useful when a reviewer can
    merge it without making the default model selection invalid.
    """
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["the roster is not a mapping"]
    if document.get("schema") != 1:
        problems.append(f"`schema` is {document.get('schema')!r}, not 1")
    arms = document.get("arms")
    arm_ids: list[str] = []
    if not isinstance(arms, list) or not arms:
        problems.append("`arms` is not a non-empty list")
    else:
        for index, entry in enumerate(arms):
            if not (isinstance(entry, dict)
                    and isinstance(entry.get("id"), str) and entry["id"]):
                problems.append(f"`arms[{index}]` has no non-empty string `id`")
            else:
                arm_ids.append(entry["id"])
    for seat in ("judge", "preflight"):
        entry = document.get(seat)
        if not (isinstance(entry, dict)
                and isinstance(entry.get("id"), str) and entry["id"]):
            problems.append(f"`{seat}` has no non-empty string `id`")
    judge_entry = document.get("judge")
    if isinstance(judge_entry, dict):
        if judge_entry.get("is_arm") is not False:
            problems.append("`judge.is_arm` is not False; a model must not "
                            "grade its own run")
        if isinstance(judge_entry.get("id"), str) and judge_entry["id"] in arm_ids:
            problems.append("the judge id is also an arm; a model must not "
                            "grade its own run")
    # B2 (adversarial round 3 on #209): `generated_at`'s own DATE (its first
    # 10 characters, e.g. "2026-09-28T00:00:00Z" -> "2026-09-28"), read once
    # here so every `catalogue_seen` entry below can be checked against it.
    # A missing/unparseable `generated_at` skips this comparison (its own
    # absence is already a separate problem, appended below); it never
    # raises here.
    generated_at_raw = document.get("generated_at")
    generated_at_date = None
    if isinstance(generated_at_raw, str) and generated_at_raw.strip():
        try:
            generated_at_date = datetime.strptime(
                generated_at_raw[:10], "%Y-%m-%d").date()
        except ValueError:
            generated_at_date = None
    seen = document.get("catalogue_seen")
    if not isinstance(seen, list):
        problems.append("`catalogue_seen` is not a list")
    else:
        for index, entry in enumerate(seen):
            if not (isinstance(entry, dict)
                    and isinstance(entry.get("id"), str) and entry["id"]
                    and isinstance(entry.get("last_seen"), str)):
                problems.append(f"`catalogue_seen[{index}]` is not "
                                f"{{id, last_seen}} with string values")
                continue
            try:
                last_seen_date = datetime.strptime(
                    entry["last_seen"], "%Y-%m-%d").date()
            except ValueError:
                problems.append(f"`catalogue_seen[{index}]`'s `last_seen` "
                                "is not an ISO YYYY-MM-DD date")
                continue
            # A planted `last_seen` later than this document's own
            # `generated_at` can never age out under `_update_catalogue_seen`
            # (its computed age against `now` is negative forever) — caught
            # here instead, at the one gate every proposal and every
            # committed file must pass (B2, round 3 on #209).
            if generated_at_date is not None and last_seen_date > generated_at_date:
                problems.append(f"`catalogue_seen[{index}]`'s `last_seen` "
                                "is later than `generated_at`")
    seen_ids = [e["id"] for e in (seen if isinstance(seen, list) else [])
                if isinstance(e, dict) and isinstance(e.get("id"), str)]
    for label, ids in (("arms", arm_ids), ("catalogue_seen", seen_ids)):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            problems.append(f"{len(duplicates)} id(s) appear more than "
                            f"once in `{label}`")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        problems.append("`provenance` is not a mapping")
    else:
        for key in ("seeded", "from"):
            if not (isinstance(provenance.get(key), str)
                    and provenance[key].strip()):
                problems.append(f"`provenance.{key}` is not a non-empty string")
    if not (isinstance(document.get("generated_at"), str)
            and document["generated_at"].strip()):
        problems.append("`generated_at` is not a non-empty string")
    return problems


def load_policy(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


#: Every numeric threshold roster.py reads from evals/roster-policy.yml,
#: with the check each one must pass. A missing, wrong-typed, or negative
#: value used to reach `compute_roster` unchecked and either KeyError deep
#: inside it or silently miscompute (a string threshold, say, compares
#: correctly against nothing) — `_validate_policy` fails loudly, naming the
#: key, before any of that.
_THRESHOLD_CHECKS = {
    "cooling_off_days": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    "arm_enter_usage_pct": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0,
    "arm_enter_window_weeks": lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
    "arm_exit_usage_pct": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0,
    "arm_exit_window_weeks": lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
    "census_max_age_days": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    "min_ranked_turns": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    "min_ranked_share": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1,
    "catalogue_seen_max_age_days": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    "superseded_exit_weeks": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
}


def _valid_tier_rung(rung) -> bool:
    """A rung is a non-empty string, or a non-empty list of non-empty
    strings (peers ranking identically — see `tier_rungs`)."""
    if isinstance(rung, str):
        return bool(rung)
    if isinstance(rung, list):
        return bool(rung) and all(isinstance(word, str) and word for word in rung)
    return False


def validate_policy(policy: dict) -> None:
    """Raise `ValueError`, naming the offending key, for a missing,
    wrong-typed, `None`, or out-of-range threshold, or a malformed
    `tiers` ladder. Called from `main()` before any of
    `evals/roster-policy.yml` reaches `compute_roster`.

    `tiers` is not a numeric threshold, so it lived outside
    `_THRESHOLD_CHECKS` and a missing or malformed one sailed through
    unvalidated (N6, #129 review round 6) — `tier_rungs` then KeyErrored
    on a missing key, or silently misbehaved on a malformed rung, instead
    of failing loudly and by name at the same point every other bad
    threshold does.
    """
    if not isinstance(policy, dict):
        raise ValueError("roster policy is not a mapping")
    for key, check in _THRESHOLD_CHECKS.items():
        if key not in policy:
            raise ValueError(f"roster policy is missing `{key}`")
        if not check(policy[key]):
            raise ValueError(f"roster policy `{key}` is invalid: {policy[key]!r}")
    if "tiers" not in policy:
        raise ValueError("roster policy is missing `tiers`")
    tiers = policy["tiers"]
    if not (isinstance(tiers, list) and tiers and all(_valid_tier_rung(r) for r in tiers)):
        raise ValueError(f"roster policy `tiers` is invalid: {tiers!r}")


def read_json(path: str | Path | None) -> tuple[dict | None, str | None]:
    """(document, problem). Absent is not a problem; unreadable is.

    CI materializes the optional inputs with `git show ... || true`, which
    leaves an EMPTY file behind when the branch or the path does not exist yet
    — the first run, every time. That is a legitimate "not published", not a
    failure, so it must not raise and must not be reported as a problem. A
    file that IS there and cannot be parsed is a different thing, and the
    summary says so rather than claiming nothing was published.
    """
    if path is None:
        return None, None
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return None, None
    try:
        with open(p, encoding="utf-8") as f:
            document = json.load(f)
    # `RecursionError` as well as the parse/IO errors (N4, #129 review
    # round 7): a deeply nested document — 100k open brackets is enough —
    # exhausts the decoder's stack rather than failing to parse, and used
    # to escape as a traceback carrying the runner's absolute paths, where
    # this module's docstring promises a one-line named message about
    # every untrusted input. `ValueError` covers `json.JSONDecodeError`
    # and `UnicodeDecodeError` (both subclasses) and anything else the
    # decoder raises for a value it cannot represent; both are named
    # anyway, because which one fired is the useful half of the message.
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError,
            RecursionError) as exc:
        return None, f"{p.name} is present but unreadable ({type(exc).__name__})"
    if not isinstance(document, dict):
        return None, f"{p.name} is not a JSON object"
    return document, None


def load_json(path: str | Path | None) -> dict | None:
    """read_json without the problem half, for callers that cannot act on it."""
    return read_json(path)[0]


class TrustedRosterUnreadable(Exception):
    """The COMMITTED roster is present and cannot be read (#147, ADR 0001).

    A REPO DEFECT, not an untrusted input, and that is the whole reason it
    is fatal where every other bad document here is a named one-line skip.
    `evals/roster.yml` lives on `main`, behind a ruleset and a reviewed
    commit; if it does not parse, somebody merged a broken file, and the
    honest response is to refuse and say so rather than to compute a
    proposal against nothing and publish the difference as though the
    roster had been empty. Publishing that difference would propose
    seating every live model at once, which is exactly the shape a
    reviewer is most likely to wave through.

    The `previous_state: "unavailable"` this replaces was the opposite
    posture, and it was right while `previous` came off `persistent/eval-results`:
    an unreadable file there is an ordinary fact about an unprotected
    branch, and carrying on was the only option. It is not an ordinary
    fact about `main`.
    """


def read_trusted_roster(path: str | Path | None) -> tuple[dict | None, str | None]:
    """(document, problem) for the COMMITTED roster — YAML or JSON, by
    CONTENT rather than by extension.

    The trusted roster is YAML because a human edits it in a reviewed pull
    request; a `--previous` pointing at a published `roster/latest.json` is
    JSON. JSON is a subset of YAML 1.2, so one parser reads both and there
    is no extension sniff to get wrong.

    Absent or empty is NOT a problem — it is a genuine first run, and the
    same `git show ... || true` shape `read_json` documents leaves an empty
    file behind. Present-and-unparseable IS one, and `main()` turns it into
    `TrustedRosterUnreadable`.
    """
    if path is None:
        return None, None
    file_path = Path(path)
    if not file_path.is_file() or file_path.stat().st_size == 0:
        return None, None
    try:
        with open(file_path, encoding="utf-8") as f:
            document = yaml.safe_load(f)
    except (yaml.YAMLError, ValueError, OSError, RecursionError) as exc:
        return None, f"{file_path.name} is present but unreadable ({type(exc).__name__})"
    if not isinstance(document, dict):
        return None, f"{file_path.name} is not a mapping"
    return document, None


def tier_rungs(policy: dict) -> list[list[str]]:
    """The capability ladder, weakest rung first, each rung a list of peers.

    A rung is written as a bare family word, or as a list of words that rank
    identically (`[fable, mythos]` — same tier, same price, different access
    programme). Peers matter: a model whose family word the ladder does not
    know is unranked, takes no seat, and — worse — used to sit in the usage
    denominator, shrinking every ranked model's share towards zero.
    """
    rungs: list[list[str]] = []
    for rung in policy["tiers"]:
        words = [rung] if isinstance(rung, str) else list(rung)
        rungs.append([str(word).lower() for word in words])
    return rungs


def tier_words(policy: dict) -> list[str]:
    """Every family word on the ladder, flattened."""
    return [word for rung in tier_rungs(policy) for word in rung]


def rung_of(model_id: str, rungs: list[list[str]]) -> int | None:
    """The model's rung index, from the family word in its own id.

    Token match on `-`, not a substring search: a substring test would let an
    id that merely CONTAINS a family word inside a longer token claim that
    tier. Unrecognised family word -> None -> unranked.
    """
    tokens = set(str(model_id).lower().split("-"))
    for index, rung in enumerate(rungs):
        if tokens.intersection(rung):
            return index
    return None


def family_of(model_id: str, rungs: list[list[str]]) -> str | None:
    """The FAMILY WORD in a model's id: the ladder word that placed it on
    its rung (`fable` or `mythos`, though both sit on one rung). A vendor
    default
    governs the models of its own family only (R2-4, #203 round 2): peers
    in one rung are different access programmes, not one family."""
    rung = rung_of(model_id, rungs)
    if rung is None:
        return None
    tokens = set(str(model_id).lower().split("-"))
    return next(word for word in rungs[rung] if word in tokens)


def rung_label(rungs: list[list[str]], index: int) -> str:
    """What to call a rung in a reason: `opus`, or `fable/mythos` for peers."""
    return "/".join(rungs[index])


#: A SAFETY shape for a previous-arm id, not a "is this a real model" check
#: — that's `rung_of`/`unranked_ids`, applied elsewhere, and deliberately
#: NOT required here: a previous arm whose family word has since left the
#: ladder must still be recognized (and retired FOR that reason) rather
#: than silently dropped as malformed. This shape only excludes what a
#: real id never contains and a control-character injection needs: no
#: newline, no `:`, no other punctuation — see `_clean_previous_arms`.
PREVIOUS_ARM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def alias_map(ids) -> dict[str, str]:
    """{dated snapshot id: the undated alias it pins}, for ids that both exist.

    An id of the form `<alias>-YYYYMMDD` is the same model as `<alias>` with a
    version pinned to it. Left alone the two take two arm seats and split one
    model's usage across two census keys. The collapse only happens when the
    alias is itself present in `ids` — a catalogue that publishes only dated
    ids has no alias to collapse onto, and every one of them stands on its own.
    """
    known = {i for i in ids if isinstance(i, str)}
    mapping: dict[str, str] = {}
    for model_id in sorted(known):
        match = SNAPSHOT_SUFFIX.match(model_id)
        if match and match.group("base") in known:
            mapping[model_id] = match.group("base")
    return mapping


def _usage_alias_map(api_ids, other_ids, seat_aliases: dict,
                     live_order) -> dict[str, str]:
    """{id: the id whose numerator its census turns belong to}, for USAGE.

    B1 (#129 review round 7). Round 6's B1 built ONE wide map,
    `alias_map(api_ids + counts + previous_arms + catalogue_seen)`, and
    `usage_share` used it for the MODEL's own target as well as for census
    keys. A catalogue listing TWO dated snapshots of one base, with the bare
    alias present only in the previous roster's `arms` or `catalogue_seen`,
    then folded BOTH snapshots onto that bare alias: each one's numerator
    collected the other's turns and both were published "carries 100.0%" —
    seating a snapshot on 0.99% of the window, keeping a previous arm with
    no turns of its own instead of retiring it at 0.0%, and consuming the
    last non-arm model so that the judge became an arm and
    `run_eval.select_models` refused every unpinned fixture. No hostile
    input is needed to reach it: a run whose catalogue lists the bare alias
    records it in `catalogue_seen` BY DESIGN, and the next run's own
    published roster is the input that reproduces it.

    The rule, in one line: AN ID THE MODELS API RETURNS THIS RUN IS NEVER
    RE-TARGETED BY THE WIDE MAP.

    1. A live catalogue id's target is its SEAT identity — `seat_aliases`,
       the catalogue-only map. Two catalogue ids the seat map keeps
       distinct therefore always have distinct targets, so their numerators
       are disjoint. (Two snapshots of a bare alias that IS itself in the
       catalogue are NOT kept distinct by the seat map: they are one model,
       one seat and one share, which is what that map is for.)
    2. Every other id — a departed previous arm, a `catalogue_seen` history
       entry, a census key naming neither — folds by the wide map, which is
       what keeps round 6's B1 fixed: a previous arm published under a dated
       id that has since left the API, whose usage the census records under
       the undated alias, still folds onto that alias and still counts in
       the denominator.
    3. One exception to 2, and it is what makes the documented
       dated-snapshot-ONLY catalogue shape work: a bare alias that is NOT
       itself in the catalogue while dated snapshots of it ARE names exactly
       one live model. `live_order` is the run's own capability order
       (weakest and oldest first), so the NEWEST live snapshot claims it —
       the same model that holds the seat. That keeps the whole map a
       FUNCTION: a census key is attributed to at most one live id.

    THREE INVARIANTS, each pinned by a test (A1, #129 review round 8):

    (i)   IDEMPOTENT. Folding a key twice gives what folding it once
          gives, for every key in the domain. Every consumer folds
          exactly once, so a key that needs two hops is a key whose
          turns land somewhere no numerator is looking.
    (ii)  Every VALUE is either a live catalogue id, or a non-live id
          that no live id claims. A value that is both non-live and
          claimed is a census key stranded one hop short of the model
          whose work it is.
    (iii) The numerators PARTITION the attributable denominator: every
          attributable ranked census key is credited to exactly one
          model — a live model, or, for a since-retired id nobody
          claims, itself — so the live models' shares plus the shares
          of unclaimed retired ids come to 100%. "No share sums past
          100%" is the weaker half of this and cannot see turns lost
          from every numerator at once.
    """
    return _compose_alias_chains(
        _usage_alias_hops(api_ids, other_ids, seat_aliases, live_order))


def _usage_alias_hops(api_ids, other_ids, seat_aliases: dict,
                      live_order) -> dict[str, str]:
    """`_usage_alias_map`'s rules (1), (2) and (3), ONE HOP EACH — the map
    before `_compose_alias_chains` follows every chain to its end.

    SPLIT OUT IN #129 REVIEW ROUND 13, and the composed map is still the
    only thing any consumer reads: `_usage_alias_map` is unchanged in
    behaviour and is still where the rules and their three invariants are
    written down. What needed the intermediate form is a question the
    composed map cannot answer, because composition DISCARDS the hops it
    walked through: WHICH IDS DOES A CHAIN GO THROUGH? See
    the ageing rule's own comment in `_update_catalogue_seen` for why
    that question stopped being asked (#147).
    """
    live = {i for i in api_ids if isinstance(i, str)}
    wide = alias_map(list(api_ids) + list(other_ids))
    mapping: dict[str, str] = {}
    for model_id in live:  # (1)
        target = seat_aliases.get(model_id, model_id)
        if target != model_id:
            mapping[model_id] = target
    for model_id in other_ids:  # (2)
        if not isinstance(model_id, str) or model_id in live:
            continue
        target = wide.get(model_id, model_id)
        if target != model_id:
            mapping[model_id] = target
    claimed: dict[str, str] = {}
    for model_id in live_order:  # (3) — last write wins: the newest
        match = SNAPSHOT_SUFFIX.match(model_id)
        if match and match.group("base") not in live:
            claimed[match.group("base")] = model_id
    for base, model_id in claimed.items():
        mapping[base] = seat_aliases.get(model_id, model_id)
    return mapping


def _compose_alias_chains(hops: dict[str, str]) -> dict[str, str]:
    """Every key of `hops` re-pointed at the END of its chain.

    COMPOSE, and it is invariant (i) that needs it (A1, #129 review
    round 8). Rules (2) and (3) each move a key ONE hop, and a key can
    need both: a non-live dated id folds onto its bare alias by (2), and
    that bare alias folds onward onto the newest live snapshot by (3).
    Every consumer applies this map exactly once (`aliases.get(candidate,
    candidate)`), so such a key used to land on the bare alias — which
    `catalogue_seen` makes ATTRIBUTABLE, so its turns joined the
    denominator, while it equalled no live model's target, so no
    numerator collected them. Measured through the two-run chain
    roster-policy.yml documents: 5000 turns inside the denominator and
    outside every numerator, a live snapshot not seated at all, and a
    previous arm retired "below the 2% exit bar (0.0% ...)" on its own
    family's usage. Following each chain to its end is still a FUNCTION
    into at most one id per key, so rule (1)'s disjointness survives.

    To its END, not one hop further than round 8 needed: `while`, not
    `if`. Round 8's own tests pinned two hops only, so `while` -> `if`
    left the suite green while a three-hop chain lost its arm entirely
    (S2, #129 review round 9). The floor is TestIssue67Review9::
    test_a_three_hop_census_key_still_reaches_the_live_snapshot — census
    key, `catalogue_seen` entry, previous arm, live snapshot, one hop
    each.
    """
    mapping = dict(hops)
    for model_id in list(mapping):
        seen = {model_id}
        target = mapping[model_id]
        while target in mapping and target not in seen:
            seen.add(target)
            target = mapping[target]
        mapping[model_id] = target
    return mapping


def _is_attributable(folded: str, api_ids: set[str] | None,
                     api_ids_folded: set[str] | None,
                     previous_arms_folded: set[str],
                     catalogue_seen_folded: set[str]) -> bool:
    """Whether a census key names something this harness can actually
    credit. `api_ids=None` means "no catalogue context was given" — every
    caller inside compute_roster always gives one; the handful of tests
    that call usage_share()/_in_window_totals() directly, on already-real
    ids, with no context at all, get the old unfiltered behavior instead
    of an empty catalogue.

    Exactly three routes, checked below, and this IS the whole set (#129
    review round 6 deleted two more that used to sit beside them, and
    round 11 and its continuation the redundant bare spelling of two of
    the three — see the end of this docstring):

    `folded in api_ids_folded` (`api_ids` run through the SAME alias map
    as `folded`) covers two shapes at once. An ordinary bare catalogue id
    is a fixed point of its own fold (nothing maps it further), so it
    reappears in `api_ids_folded` unchanged — this is what makes a bare
    `candidate in api_ids` check redundant, see below. And a candidate
    recorded under the bare alias of a dated-snapshot-ONLY catalogue entry
    (roster-policy.yml's own documented shape: the catalogue publishes
    ONLY `<alias>-YYYYMMDD`, never the bare `<alias>`) still matches,
    because `alias_map` folds the catalogue id ONTO that alias and lands
    it in `api_ids_folded` — measured: 2000 turns under a verbatim
    catalogue id once read as CENSUS_UNRANKED without this route.

    `previous_arms_folded` (`previous_arms` run through the SAME alias
    map) is the missing sibling of the route above, for a previous
    roster's arm instead of a live catalogue id: a previous arm published
    under a DATED id, whose usage the census records under the UNDATED
    alias, matches via the fold even after the dated id has left the
    Models API entirely — measured: dropping 5000 real turns from the
    denominator and inflating an unrelated model's share from ~2.0% to a
    false ~97.1% (B1, #129 review round 6 — the alias map compute_roster
    builds must itself fold `previous_arms`/`catalogue_seen` in for this
    route to have anything to match; see compute_roster's `aliases`
    comment).

    Two more routes used to sit here — bare, unfolded `candidate in
    api_ids`/`folded in api_ids`, and `candidate in previous_arms`/
    `folded in previous_arms` — and #129 review round 6 deleted them as
    PROVABLY dead, not merely untested: for any id `x` that is a genuine
    member of `api_ids` (or `previous_arms`), `api_ids_folded` (or
    `previous_arms_folded`) is built by folding EVERY element of that same
    set through the identical alias map, so `x`'s own fold is already one
    of its elements by construction — `x in api_ids` (or `previous_arms`)
    therefore never fires without `folded in api_ids_folded` (or
    `previous_arms_folded`) also firing for `x` as the candidate. Dropping
    them left every existing test green with no other code path changed.
    `catalogue_seen = set(api_ids) | previous.catalogue_seen`
    (compute_roster) is a superset of `api_ids` besides, which is a second,
    independent reason a bare `api_ids` check added nothing.

    Being ranked (a recognised family word in the id) is necessary but NOT
    sufficient: a proxy or routing alias can paste extra segments onto a
    real family word and still be `rung_of()`-ranked (see
    test/run_tests.py::TestIssue67Review3 for a worked example), without
    being a model this policy can seat or credit — nobody's numerator ever
    matches it, so letting it into a denominator only pushes every real
    arm's share toward zero, and letting it into `_in_window_totals`'s
    ranked total makes an otherwise entirely unattributable census read as
    "usable" evidence to retire a real previous arm at 0.0%. Closing that
    (see `_in_window_totals`) for a proxy alias is the same fix `other`
    already had for a value with no family word at all — a second route to
    the identical failure. Widening attribution to both spellings of the
    catalogue id (above) must not widen it to a proxy alias too: neither of
    TestIssue67Review3's worked proxy-alias examples folds to, or ever
    equals, an api id or a previous arm under either spelling —
    TestIssue67Review4 carries the regression floor for that.

    `catalogue_seen_folded` (`catalogue_seen` run through the SAME alias
    map, exactly as the other two sets are) is a THIRD, independent route
    to attribution, and replaces round 4's `_canonical_id_re` (an id-SHAPE
    check, withdrawn):
    shape cannot tell a since-retired real model from a plausibly-named
    proxy, and it shipped with three holes — a Unicode decimal digit in the
    version segment matched `\\d` and could false-retire a real arm; the
    pre-#67 legacy id shape (family word AFTER a leading numeric segment,
    rather than right after `claude-`) never matched at all, starving a
    since-retired legacy model out of the denominator; and a plausible but
    entirely invented version number, in no catalogue and never an arm,
    was attributed outright. TestIssue67Review5 carries the worked examples
    for all three. `catalogue_seen` is EVIDENCE instead of shape: the union of
    every id the Models API has ever listed across runs (see
    `compute_roster`), so a real model that has left the Models API and
    was never a previous arm is still credited for as long as this
    harness's own history has actually observed it — the property
    `usage_share`'s own docstring states — while a proxy/routing alias
    that merely carries a family word was never a catalogue id and so
    never enters `catalogue_seen` in the first place. FIRST-RUN CAVEAT:
    with no history yet (`catalogue_seen` empty, e.g. a genuine first run,
    or a previous roster that could not be read), a model retired before
    this harness ever observed it is unattributable — there is no evidence
    to credit, only the shape it USED to have, which is exactly the
    unreliable signal this replaces. This also covers the MIGRATION case:
    the `previous.json` already sitting on `persistent/eval-results` predates this
    field entirely, so the first run after this merges behaves exactly
    like a genuine first run. S3 (#129 review round 6) adds a SECOND
    migration, for `catalogue_seen`'s own shape change (a bare id string
    to `{id, last_seen}`) — see `_clean_catalogue_seen`.

    THE HISTORY SET IS FOLDED, and was not until B1' (#129 review round
    10). `api_ids` and `previous_arms` were both matched through the alias
    map and `catalogue_seen` was matched RAW, so a since-retired model the
    harness observed under a DATED id credited nothing to the undated
    alias the census records its usage under — the same one-directional
    reading of the alias relation B1' fixes in the caps. It is also what
    makes the caps' invariant mean the same thing in both lists: an entry
    that folds onto a census key keeps that key attributable, whichever of
    the two lists the entry is in. This grants a planter nothing new — an id
    planted in `catalogue_seen` already attributes ITSELF, so planting
    `X-00000000` now attributes exactly what planting `X` always did —
    and it only ever fires for ids the map relates, which is bounded by
    the live catalogue and the census.

    BOTH BARE `candidate in ...` CHECKS ARE GONE — `catalogue_seen_folded`
    in N-3 (#129 review round 11), and `previous_arms_folded` beside it in
    that round's continuation — on the rule F-2 states. THE INVARIANT THAT
    MAKES EACH REDUNDANT is `_usage_alias_map`'s own invariant (i): every
    VALUE of the alias map is a fixed point of it. Both sets here are
    folded sets, so every element of either IS such a value; a candidate
    that is in one therefore folds onto itself, and the `folded in ...`
    sibling fires for exactly the same candidates. That is a proof rather
    than a sampling result, and the measurements agree with it — the
    `catalogue_seen` check fired 71,395 times over 6,000 corpus runs and
    never once alone, and the `previous_arms` one 32,035 times over the
    whole test suite, also never once alone. Deleting either leaves the
    suite green, which is what F-2 says to do about a clause with no
    mutation that can turn it red.

    THREE PARAMETERS WENT WITH THOSE CLAUSES (nit 2, #129 review round
    12), a round later than they should have: `candidate`,
    `previous_arms` and `catalogue_seen` — the raw, unfolded id and the
    two unfolded sets — were the only things the deleted checks read, and
    nothing in the body has read them since. F-2's rule is about a clause
    with no red mutation; a PARAMETER with no reader is the same defect
    one step smaller, and leaving it in place is what lets a deleted
    check be written back without a signature change to notice it.
    `test_two_dead_clauses_stay_deleted` asserts the signature as well as
    the body now, so neither can come back quietly. `api_ids` stays: it
    is read, as the `None` sentinel below.
    """
    if api_ids is None:
        return True
    return (folded in (api_ids_folded or ())
           or folded in previous_arms_folded
           or folded in catalogue_seen_folded)


def _fold_set(ids: set[str], aliases: dict) -> set[str]:
    """`ids` run through `aliases` — the recurring "fold a set the same way
    a single id gets folded" step `api_ids_folded`, `previous_arms_folded`
    and `catalogue_seen_folded` all are."""
    return {aliases.get(i, i) for i in ids}


# THE ANCHORED DENOMINATOR IS GONE, AND SO IS THE COUNT-ONLY NOTICE BESIDE
# IT (#147, ADR 0001 decision 4, which names both constants). The veto
# refused a retirement while more than 1% of the exit window's denominator
# was attributable ONLY through the previous roster; the notice said out
# loud when that fraction was material. Both existed for one reason: the
# previous roster came off `persistent/eval-results` and could not be believed, so the
# run measured how much of its own denominator rested on it.
#
# THE MEASUREMENT THAT SHOWS THEY NOW DECIDE NOTHING. `previous` is
# `evals/roster.yml`, committed on a ruleset-protected branch, so that
# fraction is no longer a measure of doubt — it is a measure of how much of
# the window's usage belongs to models this repository has REVIEWED AND
# COMMITTED a record of. A veto keyed on it refuses precisely the honest
# case (a real since-retired arm with in-window turns) and refuses nothing
# an attacker can still reach, because the attacker's remaining reach is
# the CENSUS, which sits in both denominators at once and so cancels out of
# the difference entirely. The deleted comment said as much itself: it was
# structurally blind to the DELETION direction, and none of rounds 13 and
# 14's three surviving defects was reachable by any threshold on that
# number.
#
# WHAT REPLACES THEM IS NOT A SHARPER THRESHOLD. It is that a retirement is
# a PROPOSAL now (decision 2): published with its numerator and denominator
# in words, pushed to `roster/proposal`, and merged by a human or not
# merged at all. The irreversible half of the decision is the merge, and a
# person makes it.


#: What `_format_share` says about a nonzero share too small for any of
#: its fixed rungs to render as anything but zero (N3, #129 review round
#: 9). Prose, deliberately: it cannot be read as a number equal to any
#: bar, where the scientific-notation rendering it replaces both could and
#: told the reader nothing useful. One decimal place past the smallest
#: fixed rung (`:.6f`), so the two never disagree.
_SHARE_FLOOR_TEXT = "under 0.000001"


def _format_share(value: float, bar: float, *, under: bool = False) -> str:
    """A share percentage, rendered against its own bar.

    In the "at or above" direction (`under=False`, the default), landing
    exactly on the bar is correct, not contradictory — "carries 10.0% ...
    at or above the 10% entry bar" at a true 10.0% — so one decimal place
    always suffices.

    In the "below"/"under" direction (`under=True`), landing on the bar
    at one decimal reads as self-contradictory — "below the 2% exit bar
    (2.0%)" at a true 1.96%, or still at two decimals for 1.9999% — and a
    genuinely tiny NONZERO share can round all the way to "0.0" and read
    as no usage at all about a model that carried real turns. Escalate
    through 1, 2, 3, 4, then 6 decimal places until the rendering both
    differs from the bar and, for a nonzero value, does not read as
    "0.0...0"; `:.6g` catches a value so small even 6 decimal places round
    it away, and `:.17g` — enough digits to round-trip any float — is the
    last resort.

    EVERY rendering is checked against the bar, the fallbacks included (N3,
    #129 review round 7). `:.6g` used to be returned unchecked, and six
    SIGNIFICANT digits is not six decimal places: a share of 1.99999975%
    renders there as exactly "2", so the reason read "below the 2% exit bar
    (2% of rankable census usage)" — a sentence that contradicts itself
    about a share that really is under the bar.

    The last numeric rung is `repr`, not `:.17g` (F4, #129 review round
    8). Seventeen significant digits round-trip any float, but they are
    not the SHORTEST decimal that does: `:.17g` of a share of 1.9999999
    renders "1.9999998999999999", which is unreadable and wrong-looking
    about a number the reason quotes exactly, where `repr` gives
    "1.9999999" — the shortest string that reads back as the same float,
    so it satisfies `float(text) != bar` just as reliably.

    NO RENDERING CARRIES SCIENTIFIC NOTATION (N3, #129 review round 9).
    Both of the last two rungs produce it for a small enough share —
    `:.6g` and `repr` of 1e-9 are both "1e-09" — so a published reason
    read "below the 2% exit bar (1e-09% of rankable census usage)", and
    "1e-09%" is not a quantity a reader can weigh against a 2% bar at a
    glance. A rung that renders an exponent is skipped, and a share too
    small for every FIXED rung to be anything but zero falls through to
    `_SHARE_FLOOR_TEXT` — "under 0.000001", one decimal place past the
    smallest fixed rung. That is prose rather than a number, so it can
    never read as equal to the bar in any direction, which is the F4
    property it has to keep. The trailing `fixed[-1]` is not that case:
    it is the one where the value is large enough to render but every
    rendering equals the bar. That used to be unreachable, because
    `under=True` was only ever asked about a share strictly under its
    bar; it is reachable now, because `_usage_words` renders a proposal's
    share with `under=True` whatever that share is. The fall-through is
    what serves it: the value is returned as the number itself rather
    than as a floor, which is the only honest rendering of a share that
    really does equal the bar. What must NOT come back is a claim in the
    sentence around it that the number clears anything.
    """
    if not under:
        return f"{value:.1f}"
    ladder = [f"{value:.{spec}}"
              for spec in ("1f", "2f", "3f", "4f", "6f", "6g")]
    ladder.append(repr(value))
    fixed = [text for text in ladder if "e" not in text and "E" not in text]
    for text in fixed:
        if float(text) != bar and (value == 0 or float(text) != 0.0):
            return text
    if value and all(float(text) == 0.0 for text in fixed):
        return _SHARE_FLOOR_TEXT
    return fixed[-1]


def usage_numbers(counts: dict, model_id: str, weeks: list[str],
                  rungs: list[list[str]], aliases: dict | None = None,
                  api_ids=None, previous_arms=None, catalogue_seen=None) -> tuple[int, int]:
    """(numerator, denominator) of RANKED, ATTRIBUTABLE census usage over
    `weeks` — the turns credited to `model_id` and the turns the whole
    rankable, attributable window holds. `usage_share` is the percentage
    of the second the first is.

    SPLIT OUT OF `usage_share` (#147) because a PROPOSAL has a reader a
    threshold does not. A share is what a policy bar compares against; the
    two counts are what a human merging a seat change needs in order to
    check the claim, and a percentage with no denominator behind it asks
    them to take one on trust. Both numbers come from ONE walk over the
    census, so the share and the counts beside it can never disagree.

    The denominator counts every model the census saw THAT THE LADDER CAN
    PLACE, including ones the Models API no longer lists — work done on a
    since-retired model still happened, and the question is what share of the
    fleet's real work this model did. It EXCLUDES models the ladder cannot
    place, and the census's own `other` bucket with them: an unranked model
    takes no roster seat, so leaving its usage in the denominator only pushes
    every ranked model under the entry bar (measured: a model at 60 turns a
    week computed at 5.7% against 1000 unranked turns a week).

    `api_ids`/`previous_arms`/`catalogue_seen`, when given, ALSO exclude a
    ranked-but-unattributable key — see `_is_attributable`. That exclusion
    is deliberately narrower than "not currently in the catalogue": a
    since-retired id this harness's own history has actually seen in the
    Models API (`catalogue_seen`) still counts here, which is what keeps
    the "including ones the Models API no longer lists" property above
    actually true rather than only in the docstring — only a proxy/routing
    alias that merely carries a family word is excluded, because it was
    never a catalogue id and so never entered `catalogue_seen` either.
    FIRST-RUN CAVEAT: with no `catalogue_seen` history yet, a model retired
    before this harness's first run is unattributable — its usage silently
    drops from the denominator until a run observes it directly (see
    `_is_attributable`) — including the MIGRATION case where the
    `previous.json` already on `persistent/eval-results` predates this field, so the
    first run after this merges behaves like a genuine first run; S3
    (#129 review round 6) adds a second migration on top, for
    `catalogue_seen`'s own bare-string-to-`{id, last_seen}` shape change.
    Every call inside compute_roster gives `api_ids` and `previous_arms`;
    omitted (both default None), the denominator is every ranked key
    regardless of catalogue membership, the pre-#67-review-round-3
    behavior a few direct tests of the raw arithmetic rely on.

    A dated snapshot's usage is folded onto its alias — one model, one share.
    """
    aliases = aliases or {}
    api_ids_set = None if api_ids is None else set(api_ids)
    api_ids_folded = None if api_ids_set is None else _fold_set(api_ids_set, aliases)
    previous_arms_set = set(previous_arms) if previous_arms else set()
    previous_arms_folded = _fold_set(previous_arms_set, aliases)
    catalogue_seen_set = set(catalogue_seen) if catalogue_seen else set()
    catalogue_seen_folded = _fold_set(catalogue_seen_set, aliases)
    wanted = set(weeks)
    target = aliases.get(model_id, model_id)
    total = 0
    mine = 0
    for candidate, by_week in (counts or {}).items():
        if rung_of(candidate, rungs) is None:
            continue
        folded = aliases.get(candidate, candidate)
        if not _is_attributable(folded, api_ids_set, api_ids_folded,
                                previous_arms_folded, catalogue_seen_folded):
            continue
        for week, n in (by_week or {}).items():
            if week in wanted:
                total += n
                if folded == target:
                    mine += n
    return mine, total


def usage_share(counts: dict, model_id: str, weeks: list[str],
                rungs: list[list[str]], aliases: dict | None = None,
                api_ids=None, previous_arms=None, catalogue_seen=None) -> float:
    """Percent of RANKED, ATTRIBUTABLE census usage over `weeks` that
    `model_id` carries — `usage_numbers`' two counts as one percentage.

    Every property of the denominator is written on `usage_numbers`; this
    function is the division and the division's own arithmetic hazard, and
    nothing else.
    """
    mine, total = usage_numbers(counts, model_id, weeks, rungs, aliases,
                                api_ids=api_ids, previous_arms=previous_arms,
                                catalogue_seen=catalogue_seen)
    # Integer multiplication FIRST, then a single true division of two ints
    # — not `100.0 * mine / total`, which multiplies the float `100.0` by
    # the int `mine` and so converts `mine` to a float before dividing at
    # all. `usage_share` is called directly by a few tests on hand-built
    # counts that bypass `_clean_counts`'s own bounds; this form stays safe
    # for those PROVIDED `mine`/`total` are ints, as every real cell is —
    # `_clean_counts` coerces and bounds every cell to `[0, MAX_WEEKLY_TURNS]`
    # before compute_roster ever calls this. For int cells, Python's int/int
    # true division computes the exact ratio's correctly-rounded float
    # without ever needing `mine` or `total` to fit in a float individually,
    # unlike `100.0 * mine`, which converts `mine` to a float FIRST and
    # overflows to `inf` near the top of a float's range. It is NOT safe on
    # its own for a FLOAT cell bypassing `_clean_counts`: ONE cell around
    # `1e308` overflows just `100 * mine` to `inf`, dividing out to `inf`
    # (published as "carries inf% of census usage"); TWO such cells can
    # additionally overflow `total` itself (their sum, `2e308`, is also
    # past a float's max) to `inf`, and `inf / inf` is `nan`. Nor is it safe
    # for an int pair whose quotient is itself too large to represent as a
    # float (raises OverflowError converting it) — none of these shapes are
    # reachable through compute_roster's own int-coerced, bounded cells,
    # only through a hand-built dict a test constructs directly.
    return 0.0 if total == 0 else (100 * mine) / total


def _version_key(model_id: str) -> tuple:
    """Version components compared NUMERICALLY: `-4-10` sorts above `-4-9`.

    Only the tie-break — `created_at` decides first — but on a tie it decides
    which model is "the newest in its tier", and a string compare gets that
    exactly backwards for any family that reaches a two-digit minor.

    `isdecimal()`, not `isdigit()`: `isdigit()` is True for characters (e.g.
    the superscript `²`) that `int()` then refuses with a ValueError.
    `isdecimal()` is exactly the set `int()` accepts.
    """
    parts = []
    for token in str(model_id).split("-"):
        parts.append((1, int(token), "") if token.isdecimal() else (0, 0, token))
    return tuple(parts)


def _rank(model: dict, rungs: list[list[str]]) -> tuple:
    """Capability sort key: rung first, then newest, then version."""
    created = parse_ts(model.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
    return (rung_of(model["id"], rungs), created, _version_key(model["id"]))


def _clean_models(models_doc: dict, warn) -> list[dict]:
    """Model entries that are dicts with a well-formed, string `id`.
    Everything else named.

    The Models API is trusted; the FILE is not — it is written by another job
    and read off a public branch, and a `models` list holding a string or an
    entry with no id used to raise a TypeError three frames down.

    The id is shape-checked with `PREVIOUS_ARM_ID_RE`, the same shape
    `_clean_previous_arms` requires: an id this loose (or a bare type check
    alone) reaches `unranked`/`excluded` and from there render_summary's
    Markdown, which eval.yml prints to stdout — where GitHub parses `::`
    workflow commands — or reaches `catalogue_seen`, published verbatim to
    the public `persistent/eval-results` branch and read back as `previous`'s own
    `catalogue_seen` next run (`catalogue_seen` does not itself reach
    render_summary's Markdown). A hostile id is dropped the same way a
    bad-shaped `entry` is, and the warning names no value — only the count.
    """
    entries = (models_doc or {}).get("models")
    if not isinstance(entries, list):
        warn("models document has no `models` list; treating the catalogue as empty")
        return []
    clean = []
    skipped = 0
    malformed = 0
    for entry in entries:
        if not (isinstance(entry, dict) and isinstance(entry.get("id"), str)
                and entry["id"]):
            skipped += 1
        elif not PREVIOUS_ARM_ID_RE.match(entry["id"]):
            malformed += 1
        else:
            clean.append(entry)
    if skipped:
        warn(f"models document: skipped {skipped} entry/entries without a string `id`")
    if malformed:
        warn(f"models document: skipped {malformed} entry/entries with a "
             f"malformed model-id-shaped `id`")
    return clean


#: A week is 604800 seconds; no real account's turn rate gets within two
#: orders of magnitude of one turn a second, sustained for a week. Anything
#: above this in a single census cell is a bad value, not real usage — see
#: `_clean_counts`'s upper-bound check.
MAX_WEEKLY_TURNS = 10 ** 7

# THE TIERING IS GONE WITH THE CAPS IT ORDERED (#147, ADR 0001 decision 4,
# which names the class). It answered one question — do the LIVE CATALOGUE
# or the CENSUS name this entry, and how loudly — in three forms, and every
# one of them existed so that a cap or an ageing exemption could decide
# which entries of an UNTRUSTED previous roster to keep without reading
# anything that file's writer controlled. Six review rounds of that
# reasoning are recorded in `evals/roster-policy.yml`'s comment block, and
# they are kept there deliberately: the reasoning is what stops the
# mechanism being reinvented, and it is now reasoning about a problem this
# repository does not have.
#
# THE MEASUREMENT THAT SHOWS IT DECIDES NOTHING: with the caps and both
# ageing exemptions deleted, nothing called any of its three methods. A
# tiering with no reader is not belt-and-braces — it is a mechanism whose
# next maintainer will assume something depends on it.

#: A SIZE BOUND ON THE CENSUS DOCUMENT, in model keys (ADR 0001's "what
#: stays"). The census is the one input that is still attacker-writable
#: after #147 — it comes off `persistent/eval-results` — and every other bound on an
#: untrusted input went with the caps it belonged to, because those caps
#: existed to approximate a trusted history and the trusted history is a
#: file on `main` now. What did NOT go away is that an unbounded document
#: can exhaust the runner: `compute_roster` walks the census once per
#: window per model, so the work is linear in keys times seats.
#:
#: 50,000 keys is two orders of magnitude past anything honest — the real
#: census names the handful of models one account has run — and an order
#: of magnitude inside the shape this module has been measured against
#: (380,000 in-window keys, a 16.2 MB file, 6.87s). It is a refusal, not a
#: trim: a trim would publish a proposal whose contents an untrusted input
#: chose, and `eval.yml` turns a refusal into a `::warning::` with the
#: committed roster still deciding every model the eval runs on.
CENSUS_MAX_KEYS = 50_000

#: The same bound in BYTES, checked before the file is parsed at all —
#: keys alone cannot bound a document that is one key holding a
#: hundred-megabyte string, and `json.load` has already allocated it by the
#: time a key count could fire. 32 MiB is twice the largest census this
#: module has been run against.
CENSUS_MAX_BYTES = 32 * 1024 * 1024


def _clean_counts(counts, warn) -> dict:
    """{model: {week: int}}, validated. Nothing that fails validation is counted.

    No offending VALUE is ever quoted back: the census is public, and an old
    census on the branch predates the key allowlist that keeps it that way.
    """
    if counts is None:
        return {}
    if not isinstance(counts, dict):
        warn("census `counts` is not an object; treating the census as empty")
        return {}
    cleaned: dict[str, dict[str, int]] = {}
    bad_rows = 0
    bad_cells = 0
    for model_id, by_week in counts.items():
        if not isinstance(model_id, str) or not isinstance(by_week, dict):
            bad_rows += 1
            continue
        for week, n in by_week.items():
            if not isinstance(week, str):
                bad_cells += 1
                continue
            # `bool` is an `int` SUBCLASS in Python — `int(True)` is `1`,
            # `int(False)` is `0` — so a census cell holding the JSON
            # literal `true`/`false` sailed through every check below and
            # silently became a real count instead of being rejected as
            # the wrong shape. Checked before `isinstance(n, int)` too:
            # that check alone would not otherwise catch it.
            if isinstance(n, bool):
                bad_cells += 1
                continue
            # An actual `int`, nothing coerced to one. `int(n)` used to
            # accept far more than a real census cell (a JSON number) ever
            # produces: `int(1.9)` silently truncates a float instead of
            # rejecting it; `int("5")` accepts a JSON STRING; `int("5_0")`
            # is `50` (Python's int() honors the digit-group underscore
            # numeric-literal syntax inside a string); `int("٥")` is `5`
            # (int() accepts non-ASCII Unicode decimal digits). None of
            # these is a value `json.load` ever hands back for a JSON
            # number — every one is a distinct, silent, wrong-typed cell
            # that used to become a real count.
            if not isinstance(n, int):
                bad_cells += 1
                continue
            value = n
            # A negative count is not a real turn tally — accepting it at
            # face value let it sum straight into usage_share's totals
            # (a `-99` cell once yielded a 'carries 10000.0%' reason, and a
            # cancelling +/- pair could zero a census's only ranked usage
            # while `_census_verdict` still read it as usable).
            if value < 0:
                bad_cells += 1
                continue
            # An upper bound, not just a lower one: `1e308` (a huge but
            # finite float) or a several-hundred-digit JSON integer both
            # pass `int()` cleanly — Python ints are arbitrary precision —
            # and used to ride straight into usage_share's arithmetic, where
            # multiplying a value that large by a float either silently
            # produces `inf` (published as "carries inf% of census usage")
            # or raises OverflowError converting it to a float at all. A
            # week cannot hold more turns than MAX_WEEKLY_TURNS; reject
            # anything above it as a bad cell, the same as a negative one.
            if value > MAX_WEEKLY_TURNS:
                bad_cells += 1
                continue
            cleaned.setdefault(model_id, {})[week] = value
    if bad_rows:
        warn(f"census `counts`: skipped {bad_rows} row(s) that are not "
             f"model -> {{week: count}}")
    if bad_cells:
        # "not a usable count", not "not a number": a negative value and one
        # above MAX_WEEKLY_TURNS both ARE numbers — they are rejected for
        # being out of range, a boolean for being the wrong type entirely,
        # and only a genuinely non-numeric/non-finite cell for failing to
        # parse as a number at all. One wording covers all four honestly.
        warn(f"census `counts`: skipped {bad_cells} weekly count(s) that are "
             f"not a usable count")
    return cleaned


# THE TWO 500-ENTRY CAPS AND THE CARRY CEILING ARE GONE (#147, ADR 0001
# decision 4, which names all three constants), and so is the tier
# ordering they evicted by.
#
# WHAT THEY WERE FOR. The previous roster came off `persistent/eval-results`, so its
# `arms` and `catalogue_seen` lists were unbounded public input: anyone
# with write access could add 500 entries, and six review rounds went into
# finding an order that decided which of them survived WITHOUT reading
# anything the writer of that file controlled. The answer they converged
# on — rank by the live catalogue and the census, carry whole everything
# neither names, and refuse to publish at all past 10,000 unorderable
# entries — is an approximation of a trusted history, which is exactly
# what ADR 0001 says to delete rather than keep beside the real thing.
#
# THE MEASUREMENT THAT SHOWS THEY NOW DECIDE NOTHING. The lists they
# bounded come from `evals/roster.yml`, and a line cannot appear there
# without a commit that went through a pull request on a ruleset-protected
# branch — human-reviewed under `roster_mode: proposal`, or merged by the
# automation only once CI passes under `roster_mode: auto` (ADR 0003); never
# a direct push either way. So the input the caps bounded is bounded by
# that PR gate, and every remaining effect of a
# cap was on HONEST data: a repository with more than 500 genuinely
# observed models would have had its own reviewed history evicted, by an
# order chosen to defeat an attacker who can no longer write the file.
# The denial of service the ceiling's own comment documented — 10,001
# planted lines refusing the refresh for good, and not clearing itself —
# goes with them, because those lines would now have to be merged.
#
# WHAT REPLACES THEM AS THE BOUND ON AN UNTRUSTED INPUT is
# `CENSUS_MAX_KEYS`/`CENSUS_MAX_BYTES`, over the census, which is the one
# document still written by a job on another machine.



class RosterRefusal(Exception):
    """Refuse to publish, rather than publish a roster whose contents an
    untrusted input decided. Raised by the caps, caught by `main()`, which
    prints it and exits nonzero. The message names counts only — never an
    id, never a value — like every other warning about an untrusted input.
    """


def _clean_previous_arms(previous, warn) -> list[str]:
    """The previous roster's arm ids — ONE list (#147).

    IT RETURNED TWO, `reported` and `carried`, and the split was entirely
    the cap's (F3, #129 review round 8): `reported` was every arm the
    previous roster named, so a real departed arm still reached
    `retired_since_last` even when 500 filler entries had capped it out of
    what was carried forward, and `carried` was the capped list that
    attribution and the hold-over check read. With no cap the two are
    equal by construction, and returning one list twice under two names is
    an invitation to make them differ again.

    A malformed entry is skipped, not fatal.

    Shape-checked with `PREVIOUS_ARM_ID_RE`, not just type-checked: every
    id from here is interpolated verbatim into render_summary's Markdown,
    which eval.yml prints to stdout — where GitHub parses `::` workflow
    commands. An offender is dropped the same way a bad-shaped `entry` is,
    and the warning names no value — only the count.

    Dedup is a SET membership test, not `entry not in ids` over the
    growing output list — the latter is O(n^2) and measured at 5.4s for
    40,000 entries, 37s for 100,000, publishing a 2.7MB roster with no
    warning at all. It has no deterministic regression floor and is not
    getting one: its only symptom is wall-clock time, and a timing
    assertion is a flaky test, not a floor (N8, #129 review round 7).

    THE 500-ENTRY CAP THAT USED TO BOUND THIS LIST IS GONE (#147, ADR
    0001 decision 4), and so is the tier ordering it evicted by. Six
    review rounds went into finding an order that could decide which
    entries of an UNBOUNDED, PUBLICLY-WRITABLE `arms` list survived
    WITHOUT reading anything that list's own writer controlled. This list
    comes from `evals/roster.yml` now, so it is bounded by review: a line
    cannot get into it without a merged commit. Every remaining effect of
    the cap was on honest data — a repository with more than 500 reviewed
    arms would have had its own history evicted by an order chosen to
    defeat an attacker who can no longer write the file.

    THE TWO LISTS STAY FOR NOW and are equal (F3, #129 review round 8):
    `reported` is every arm the previous roster named, which is what
    `added_since_last`/`retired_since_last` compare against, and `carried`
    is what attribution and the hold-over check read. The split existed
    because the cap made them differ.
    """
    if previous is None:
        return []
    entries = previous.get("arms") if isinstance(previous, dict) else None
    if entries is None:
        return []
    if not isinstance(entries, list):
        warn("previous roster: `arms` is not a list; comparing against nothing")
        return []
    seen: set[str] = set()
    ids = []
    skipped = 0
    for entry in entries:
        if (isinstance(entry, dict) and isinstance(entry.get("id"), str)
                and entry["id"] and PREVIOUS_ARM_ID_RE.match(entry["id"])):
            if entry["id"] not in seen:
                seen.add(entry["id"])
                ids.append(entry["id"])
        else:
            skipped += 1
    if skipped:
        warn(f"previous roster: skipped {skipped} `arms` entry/entries that are "
             f"not an object with a well-formed model-id-shaped `id`")
    return ids


def _as_date(moment: datetime) -> str:
    """A `catalogue_seen` date: UTC, zero-padded, re-readable by `parse_ts`.

    `strftime("%Y-%m-%d")` does neither of the last two on this platform
    (A2/F2, #129 review round 8). It renders an offset-aware timestamp's
    LOCAL date, so `2026-09-01T23:00:00-08:00` published `2026-09-01`, a
    day early, and `2026-09-02T01:00:00+05:00` published `2026-09-02`, a
    day late — the same fix `source.census_at` already had. And it does
    not zero-pad a year below 1000, so `0001-01-01` published as
    `1-01-01`, which `parse_ts` refuses: `_update_catalogue_seen` then
    read the entry as "seen today" (`parse_ts(last_seen) or now`), an
    entry two thousand years past the age window survived it, and 500
    such plants sorted as the newest history there is and evicted the
    real one. A date this harness writes and cannot read back is one
    that silently stops ageing.

    RAISES `OverflowError` for a moment whose UTC conversion falls outside
    `datetime`'s own range — a year-1 timestamp with a POSITIVE offset is
    the reachable case, since `parse_ts` keeps the offset (S1, #129 review
    round 9). Every caller that passes a moment derived from untrusted
    input catches it and skips the entry; `now` is this process's own
    clock and cannot reach it.

    AND RAISES NOTHING ELSE (N-1, #129 review round 10). `parse_ts` returns
    an AWARE datetime or None, `.astimezone` on an aware one can only
    overflow, and `.date().isoformat()` raises nothing — so a caller
    catching `ValueError` or `OSError` here is catching what cannot
    happen. `_clean_catalogue_seen` used to; it no longer does.
    """
    return moment.astimezone(timezone.utc).date().isoformat()


def _clean_catalogue_seen(previous, warn, now: datetime) -> list[dict]:
    """The previous roster's `catalogue_seen` history, as `{"id",
    "last_seen"}` entries (S3, #129 review round 6). Accepts the bare
    string shape this field used to publish — on EVERY read, not for a
    bounded number of runs (N5, #129 review round 7: the docstring used to
    promise "one migration run", which nothing enforces and nothing needs
    to). What is actually true is that the shape is accepted on read and
    always REPUBLISHED in the `{id, last_seen}` shape, so after this
    harness's own first run the bare string can only ever come back from a
    producer this harness does not control — which is exactly the untrusted
    input the rest of this function is about. A bare string carries no age
    information at all, so it is rewritten
    with `last_seen` = today — seeing it in a previous roster is the
    only evidence there is, and treating it as "seen today" costs at
    most one extra `catalogue_seen_max_age_days` window before an id
    that stops being genuinely re-observed ages out on its own (see
    `_update_catalogue_seen`, which is what actually EVICTS an entry —
    this function only reads and shape-validates what came in). A
    `last_seen` in the future (a hand-edited or clock-skewed entry) is
    clamped to today rather than trusted, so a single future-dated plant
    cannot buy itself unlimited immunity from the age check.

    A malformed entry is skipped, not fatal — same treatment as
    `_clean_previous_arms`, because this is committed roster history: an
    old or hand-edited entry should not stop the next observation from
    publishing a corrected `catalogue_seen` output. So is an entry whose
    `last_seen` PARSES but cannot then be converted to a UTC date (S1,
    #129 review round 9) — counted separately, because which of the two
    happened is the useful half of the message, and skipped the same way.
    """
    if previous is None:
        return []
    entries = previous.get("catalogue_seen") if isinstance(previous, dict) else None
    if entries is None:
        return []
    if not isinstance(entries, list):
        warn("previous roster: `catalogue_seen` is not a list; starting empty")
        return []
    today = _as_date(now)
    by_id: dict[str, str] = {}
    migrated = 0
    skipped = 0
    unconvertible = 0
    for entry in entries:
        if isinstance(entry, str) and entry and PREVIOUS_ARM_ID_RE.match(entry):
            by_id[entry] = today
            migrated += 1
            continue
        if (isinstance(entry, dict) and isinstance(entry.get("id"), str)
                and entry["id"] and PREVIOUS_ARM_ID_RE.match(entry["id"])
                and isinstance(entry.get("last_seen"), str) and entry["last_seen"]):
            parsed = parse_ts(entry["last_seen"])
            if parsed is None:
                skipped += 1
                continue
            # The PARSED date, re-rendered — never the raw string (N1,
            # #129 review round 7). `parse_ts` strips its input before
            # comparing it against `now`, but the raw value used to be
            # stored here and republished verbatim, so a `\r\n` wrapped
            # around a date reached roster/latest.json on the public
            # branch as literal control characters. Same fix
            # `source.census_at` already had.
            #
            # The conversion itself can FAIL on a value `parse_ts`
            # accepted (S1, #129 review round 9): `parse_ts` keeps
            # whatever offset the entry carried, and converting a year-1
            # timestamp with a POSITIVE offset to UTC lands before
            # `datetime.min`, so `_as_date` raises `OverflowError` —
            # measured through `main()` as rc 1, a ten-line traceback
            # carrying the runner's absolute paths, and NO roster
            # published, on a single planted entry. Round 7's N4 guard
            # wraps the JSON load and cannot reach this. An entry whose
            # date this module cannot convert asserts nothing, so it gets
            # the treatment every other malformed entry gets: counted,
            # skipped, and never quoted back.
            # `OverflowError` ALONE (N-1, #129 review round 10). It used
            # to catch `ValueError` and `OSError` beside it, and neither
            # is reachable from what `parse_ts` returns: it hands back an
            # AWARE datetime or None, `.astimezone` on an aware one can
            # only overflow `datetime`'s range, and `.date().isoformat()`
            # raises nothing at all. Both were green as mutations because
            # nothing could reach them, and a defence nothing can reach is
            # deleted this round rather than kept as belt-and-braces (F-2).
            # `main()`'s sibling guard on `census_at` keeps its wider catch
            # because the comment there records the whole branch as
            # unreachable already, for a reason about the policy's numbers
            # rather than about this expression.
            try:
                rendered = today if parsed > now else _as_date(parsed)
            except OverflowError:
                unconvertible += 1
                continue
            by_id[entry["id"]] = rendered
            continue
        skipped += 1
    if migrated:
        warn(f"previous roster: migrated {migrated} `catalogue_seen` "
             f"entry/entries from the bare-string shape")
    if skipped:
        warn(f"previous roster: skipped {skipped} `catalogue_seen` entry/entries "
             f"that are not a well-formed {{id, last_seen}} object or a "
             f"model-id-shaped string")
    if unconvertible:
        warn(f"previous roster: skipped {unconvertible} `catalogue_seen` "
             f"entry/entries whose `last_seen` cannot be converted to a UTC "
             f"date")
    return [{"id": model_id, "last_seen": last_seen}
           for model_id, last_seen in by_id.items()]


def _update_catalogue_seen(api_ids, previous_entries: list[dict], now: datetime,
                           policy: dict, warn) -> list[dict]:
    """This run's `catalogue_seen` history: refresh and age.

    Every id THIS run's Models API actually listed gets its `last_seen`
    refreshed to today — that is the only way an id's clock resets. Every
    other previously-seen id keeps its own `last_seen`, and is DROPPED
    once that is older than `policy["catalogue_seen_max_age_days"]`. That
    is the ONLY way an id leaves this history: the two exemptions that
    used to sit beside the age, and the 500-entry cap that used to sit
    after it, are all deleted (#147, ADR 0001 decision 4). A model id
    planted directly in a previous roster that the Models API never
    actually returns has no way to get its `last_seen` refreshed, so it
    ages out on its own.

    Ageing out ends a plant's future effect. It
    does not undo a retirement the plant already caused (N6, #129 review
    round 7): a model whose measured share the fabricated usage pushed
    under the exit bar is proposed for retirement, and once it is merged the
    model is no longer a previous arm at all — so the exit bar no longer
    applies to it, and a trickle of real usage (a dozen turns a week, say)
    never re-seats it. It comes back only by clearing the ENTRY bar, by
    being the newest in its tier, or by hand.

    `catalogue_seen` stays a superset of `api_ids` — every live id is
    stamped today by the loop above and nothing evicts it — which is the
    property `usage_share`'s docstring and `compute_roster`'s callers
    rely on.
    """
    today = _as_date(now)
    by_id = {e["id"]: e["last_seen"] for e in previous_entries}
    for model_id in api_ids:
        by_id[model_id] = today
    max_age = timedelta(days=policy["catalogue_seen_max_age_days"])
    survivors: dict[str, str] = {}
    aged_out = 0
    for model_id, last_seen in by_id.items():
        seen_at = parse_ts(last_seen) or now
        if now - seen_at > max_age:
            # NO EXEMPTIONS ANY MORE (#147, ADR 0001 decision 4). Two used
            # to sit here — keep an entry this run's census still names,
            # and keep an entry the usage alias map needs as a HOP — and
            # both existed because `last_seen` was written by whoever
            # wrote `previous.json` on an unprotected branch, so a
            # back-dated date could drop a real model's turns out of the
            # attributable denominator and no local rule could tell that
            # apart from an honest expiry.
            #
            # THE MEASUREMENT THAT SHOWS THEY NOW DECIDE NOTHING: this
            # history is `evals/roster.yml`'s `catalogue_seen`, committed
            # on a ruleset-protected branch through a pull request. A
            # `last_seen` here is a date that passed that PR gate — a
            # human reviewer's merge under `roster_mode: proposal`, or the
            # automation's merge once CI passed under `roster_mode: auto`
            # (ADR 0003) — so "the record was tampered with" is no
            # longer one of the two readings of an old date — the only
            # remaining reading is the true one, that this harness has not
            # observed the model in that long. The census-names-it
            # exemption then keeps an entry the trusted record says should
            # go, and the needed-hop exemption did the same for a chain;
            # both make the trusted history disagree with itself, which is
            # the one thing it is for. Round 14's BLOCKER 2 — a quiet
            # window ageing a needed hop out for good — is closed by the
            # record surviving in a reviewed file rather than by a rule
            # that guesses which dates to disbelieve.
            aged_out += 1
            continue
        survivors[model_id] = last_seen
    if aged_out:
        warn(f"catalogue_seen: dropped {aged_out} entry/entries older than "
             f"the {policy['catalogue_seen_max_age_days']}-day window")
    # NO LENGTH CAP (#147). What is left is every surviving entry, in id
    # order. The cap that used to sit here evicted accumulated history by
    # an order built from the live catalogue and the census — an order
    # designed so that an untrusted `previous.json` could not choose its
    # own survivors. This history is committed on `main` now, so its
    # length is a reviewed fact and evicting from it would only ever
    # discard something a human merged.
    return [{"id": model_id, "last_seen": last_seen}
           for model_id, last_seen in sorted(survivors.items())]



def _in_window_totals(counts: dict, weeks: set[str], rungs: list[list[str]],
                      aliases: dict | None = None, api_ids=None,
                      previous_arms=None, catalogue_seen=None) -> tuple[int, int]:
    """(raw_total, ranked_total) of census usage over `weeks`.

    `ranked_total` mirrors `usage_share`'s own denominator — it excludes
    `other`, every id the tier ladder cannot place, and (when `api_ids`/
    `previous_arms`/`catalogue_seen` are given, as every call inside
    compute_roster does — see `_is_attributable`) every ranked-but-
    unattributable key such as a proxy alias that merely carries a family
    word without being a real catalogue model, a previous arm, or an id
    this harness's own history has observed. Computed once and handed to
    `_census_verdict`, so its "is there usage evidence" check agrees with
    the number every arm's share is actually divided by, rather than
    reading raw counts that can be nonzero while the ranked denominator is
    zero (a census entirely routed through Bedrock/Vertex/a proxy lands
    every count under `other`, or — the second route to the same gap — a
    proxy alias that happens to carry a family word).
    """
    aliases = aliases or {}
    api_ids_set = None if api_ids is None else set(api_ids)
    api_ids_folded = None if api_ids_set is None else _fold_set(api_ids_set, aliases)
    previous_arms_set = set(previous_arms) if previous_arms else set()
    previous_arms_folded = _fold_set(previous_arms_set, aliases)
    catalogue_seen_set = set(catalogue_seen) if catalogue_seen else set()
    catalogue_seen_folded = _fold_set(catalogue_seen_set, aliases)
    raw_total = 0
    ranked_total = 0
    for candidate, by_week in (counts or {}).items():
        ranked = rung_of(candidate, rungs) is not None
        if ranked:
            folded = aliases.get(candidate, candidate)
            ranked = _is_attributable(folded, api_ids_set, api_ids_folded,
                                      previous_arms_folded,
                                      catalogue_seen_folded)
        for week, n in (by_week or {}).items():
            if week in weeks:
                raw_total += n
                if ranked:
                    ranked_total += n
    return raw_total, ranked_total


#: `_census_verdict`'s machine-readable half. Reasons are for humans; this is
#: for callers deciding what else follows (e.g. whether `census_at` is
#: recorded), so they do not have to reconstruct it from a string prefix.
CENSUS_UNREADABLE = "unreadable"
CENSUS_ABSENT = "absent"
CENSUS_FUTURE = "future"
CENSUS_STALE = "stale"
CENSUS_EMPTY = "empty"
CENSUS_UNRANKED = "unranked"
CENSUS_FRESH = "fresh"

#: Verdicts whose census WAS published and current enough to be worth citing
#: as provenance, even though it is not usable evidence — see `CENSUS_EMPTY`
#: and `CENSUS_UNRANKED` below. `CENSUS_UNREADABLE` is deliberately absent:
#: a file that failed to parse has no `generated_at` to cite.
CENSUS_PUBLISHED_CODES = (CENSUS_EMPTY, CENSUS_UNRANKED, CENSUS_FRESH)


def _census_verdict(census_doc, raw_total: int, ranked_total: int, policy, now,
                    census_problem: str | None = None):
    """(usable, note, code) — is there usage evidence for the window, and why not.

    Eight ways to have none, and they are NOT the same fact: a file that is
    present but failed to parse (distinct from nothing being published at
    all — `read_json` already tells the two apart, but `main()` used to
    collapse them by discarding the problem and passing `census_doc=None`
    either way), nothing published, a timestamp in the future (clock skew or
    a hand edit — every week then falls outside the window while the age
    check reads as fresh), a census older than the freshness window, a
    census that is present and current and simply holds nothing for these
    weeks, a census that holds usage but none of it is usage this policy can
    rank (every count fell under `other`, an unranked id, or an id ranked
    but unattributable — a proxy alias, say, which the ladder DOES place but
    which names no real catalogue model or previous arm; see
    `_is_attributable`), a census whose ranked, attributable total is
    nonzero but still under `policy["min_ranked_turns"]` — almost the whole
    fleet routed through `other` with a handful of stray ranked turns is not
    evidence of anything, even though `ranked_total == 0` alone would not
    have caught it — and a census whose ranked total clears that ABSOLUTE
    floor yet is still a vanishing fraction of the raw window total: the
    absolute floor alone let `{other: 100000/wk, some-model: 20 turns
    total}` read as usable evidence over 800,020 raw turns, retiring a
    previous arm with no counted usage of its own at a literal 0.0%.
    `policy["min_ranked_share"]` is that RELATIVE guard. Each says so in its
    own words, because "fell back to newest per tier" without the cause is
    a roster nobody can debug.
    """
    if census_problem:
        # `read_json`'s message already names the file and the exception
        # class only — never file content — so it is safe to surface
        # verbatim as far down as an arm's own reason.
        return False, census_problem, CENSUS_UNREADABLE
    census_at = parse_ts((census_doc or {}).get("generated_at"))
    if census_doc is None or census_at is None:
        return False, "no fresh census (none published)", CENSUS_ABSENT
    if census_at > now:
        return False, ("no fresh census (its generated_at is in the future, so "
                       "every week of usage falls outside the window — clock "
                       "skew or a hand edit)"), CENSUS_FUTURE
    age = (now - census_at).days
    if now - census_at > timedelta(days=policy["census_max_age_days"]):
        return False, (f"no fresh census (last published {age} days ago, over the "
                       f"{policy['census_max_age_days']}-day window)"), CENSUS_STALE
    if raw_total == 0:
        return False, ("census published but empty over the window (no usage "
                       "recorded for any model in these weeks)"), CENSUS_EMPTY
    if ranked_total == 0:
        return False, ("census published but holds no usage this policy can "
                       "rank over the window (every count in these weeks is "
                       "`other`, an id the tier ladder cannot place, or an id "
                       "neither the Models API nor the previous roster "
                       "attributes)"), \
            CENSUS_UNRANKED
    if ranked_total < policy["min_ranked_turns"]:
        return False, (f"census published but holds only {ranked_total} "
                       f"rankable, attributable turn(s) over the window — "
                       f"under the {policy['min_ranked_turns']}-turn floor, "
                       f"too little to be evidence of anything"), \
            CENSUS_UNRANKED
    if ranked_total < policy["min_ranked_share"] * raw_total:
        bar = 100 * policy["min_ranked_share"]
        pct = 100 * ranked_total / raw_total
        return False, (f"census published but only {ranked_total} of "
                       f"{raw_total} raw turns over the window are rankable, "
                       f"attributable usage ({_format_share(pct, bar, under=True)}% "
                       f"— under the {bar:g}% relative floor), too little to "
                       f"be evidence of anything"), \
            CENSUS_UNRANKED
    return True, "", CENSUS_FRESH


#: Bounds on the vendor-defaults document (#202; the CLI probe since #203).
#: It is written by `scripts/probe_model_defaults.py` from the output of a
#: freshly installed CLI, so it is an untrusted input like the census: every
#: field is type-checked, sized, and shape-checked before any of it can reach
#: a reason — and so the Markdown `render_summary` prints to a log where `::`
#: is a workflow command.
DEFAULTS_MAX_ALIASES = 16
DEFAULTS_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}\Z")
#: The probe's own model-id shape: lowercase letters, digits, dashes, dots.
DEFAULTS_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}\Z")
#: An error the probe recorded: one of its fixed classes or an exception's
#: class name. No colon, so no `::` can reach a reason.
DEFAULTS_ERROR_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}\Z")
#: The CLI's version line, as the probe and eval.yml's install step reduce it.
DEFAULTS_VERSION_RE = re.compile(r"^[A-Za-z0-9._() -]{1,80}\Z")
#: What a family is when the probe could not establish its default (#203
#: probe round 1): frozen for this run — its previous arms held, none of its
#: models retired but one gone from the Models API, and none newly seated
#: unless the family holds no seat the Models API still lists (#203 probe
#: round 3, R3-1). Never carried to the next run.
FROZEN_WORDS = ("held this run: none retired except models gone from the "
                "Models API, and none added unless the family holds no seat "
                "the Models API still lists")
#: The CATALOGUE-MISMATCH classes (#203 probe round 2, R2-1; narrowed in
#: round 3, R3-2; narrowed again in round 5, R5-1): the probe answered for
#: the alias, but this run's Models API catalogue does not match the answer
#: — not available, an ambiguous snapshot. They FREEZE exactly like a probe
#: failure (round 7, R7-1 — see `compute_roster`), so a one-week mismatch
#: neither adds nor retires what a clean week would not; only the wording
#: (`mismatch_words`) says a mismatch rather than a failure.
#: `wrong-tier`, `wrong-family` and `no-created-at` are PROBE FAILURES
#: (R3-2; R5-1 for the last): the CLI said something nonsensical about its
#: own alias, or named a model with nothing to start its predecessor's
#: buffer from, so they freeze, as do the document-level classes below and
#: any class the probe itself recorded in `errors`. Each matches
#: `DEFAULTS_ERROR_RE`, so none can carry `::` into a reason or a log.
UNRESOLVED_NOT_AVAILABLE = "not-available"
UNRESOLVED_AMBIGUOUS = "ambiguous-snapshot"
UNRESOLVED_WRONG_TIER = "wrong-tier"
UNRESOLVED_WRONG_FAMILY = "wrong-family"
#: A PROBE FAILURE since R5-1 (#203 probe round 5): the CLI named a model
#: with no `created_at` to start its predecessor's buffer from, so its
#: family freezes with the failure's own wording, not a mismatch's.
UNRESOLVED_NO_CREATED_AT = "no-created-at"
UNRESOLVED_ERROR = "unrecognised-error"
DOCUMENT_UNREADABLE = "unreadable"
DOCUMENT_NOT_AN_OBJECT = "not-an-object"
DOCUMENT_NO_DEFAULTS = "no-defaults"
DOCUMENT_TOO_MANY = "too-many-aliases"
DOCUMENT_NO_USABLE = "no-usable-defaults"
DOCUMENT_RESOLVED_NOTHING = "resolved-nothing"
#: eval.yml's stand-in for a probe script that exited non-zero and wrote no
#: document (#203 probe round 2, R2-4): `{"probe_exit": "nonzero"}`.
DOCUMENT_PROBE_EXITED = "probe-exited"
#: What each document-level class means, in words, for a reason or summary.
DOCUMENT_WORDS = {DOCUMENT_PROBE_EXITED: "the probe script exited with an error"}


def mismatch_words(model_id: str, alias: str, cls: str) -> str:
    """The wording of a catalogue mismatch (R2-1, R3-4): never "probe
    failed" or "unknown", because the probe answered."""
    return (f"the CLI's default `{model_id}` for `{alias}` does not match this "
            f"run's catalogue ({cls})")


def _defaults_provenance(document: dict) -> tuple[str, str | None]:
    """(source, probed_at) of a defaults document, shape-checked: a version
    that is not a plain version line, or a timestamp that does not parse,
    never reaches a reason."""
    version = document.get("harness_version")
    if not (isinstance(version, str) and DEFAULTS_VERSION_RE.match(version)
            and version.strip()):
        version = "(version unknown)"
    else:
        # Strip trailing " (Claude Code)" suffix if present (exact, case-sensitive).
        version = version.rstrip()
        if version.endswith(" (Claude Code)"):
            version = version[:-len(" (Claude Code)")]
    probed = parse_ts(document.get("probed_at"))
    try:
        probed_at = (probed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                     if probed else None)
    except (OverflowError, ValueError, OSError):
        probed_at = None
    return f"claude-code-cli {version.strip()}", probed_at


#: Why a wrong-tier or wrong-family answer freezes (R3-2).
NONSENSICAL = ("a nonsensical answer about its own alias, treated as a probe "
               "failure")


def _seat_default_candidates(candidates: list[tuple], rungs: list[list[str]],
                             warn) -> tuple[dict, list, dict, dict]:
    """(by_family, unresolved, failed, mismatched) from `(alias, rung,
    model | None, why, class, answered)` candidates — one per alias, `model`
    the match or None with `why` and its class, and `answered` the id the
    probe answered, or None when it recorded an error for the alias.

    An alias resolves only to a model OF ITS OWN FAMILY WORD (R2-4, #203
    round 2): a rung can hold peer families (`[fable, mythos]`, a different
    access programme), and one family's default neither supersedes nor is
    "older than" another family's models. So two peer aliases naming models
    of their own families both resolve, and a peer alias naming ANOTHER
    family's model is unresolved on its own, leaving every other alias
    alone. `by_family` is also the published `resolved` map; `failed` maps
    every alias the PROBE failed for to its class, and `mismatched` every
    alias the probe answered for but this run's catalogue does not match to
    `{id, class}` — both name a family FROZEN this run (R2-1, #203 probe
    round 2; folded into one freeze in round 7, R7-1), a mismatch keeping
    only its own wording. An answer naming a model of
    the WRONG TIER or FAMILY is a failure, not a mismatch (R3-2): the CLI
    said something nonsensical about its own alias.
    """
    unresolved: list[dict] = []
    failed: dict[str, str] = {}
    mismatched: dict[str, dict] = {}

    def unresolve(alias, why, cls, answered, *, nonsensical=False):
        if answered is None or nonsensical:
            unresolved.append({"alias": alias, "reason": why})
            failed[alias] = cls
            warn(f"model default for `{alias}` not resolved: {why}; the "
                 f"`{alias}` family is {FROZEN_WORDS}")
            return
        said = f"{mismatch_words(answered, alias, cls)}: {why}"
        unresolved.append({"alias": alias, "reason": said})
        mismatched[alias] = {"id": answered, "class": cls}
        warn(f"model default for `{alias}`: {said}; the `{alias}` family is "
             f"{FROZEN_WORDS}")

    by_family: dict[str, str] = {}
    for alias, rung, model, why, cls, answered in candidates:
        if model is None:
            unresolve(alias, why, cls, answered)
            continue
        found_rung = rung_of(model["id"], rungs)
        if found_rung != rung:
            unresolve(alias, f"the CLI named `{answered}` as its default for "
                             f"`{alias}`, but `{model['id']}` sits in the "
                             f"{rung_label(rungs, found_rung)} tier, not the "
                             f"{rung_label(rungs, rung)} tier — {NONSENSICAL}",
                      UNRESOLVED_WRONG_TIER, answered, nonsensical=True)
            continue
        family = family_of(model["id"], rungs)
        if family != alias:
            unresolve(alias, f"the CLI named `{answered}` as its default for "
                             f"`{alias}`, but `{model['id']}` is a model of the "
                             f"{family} family, not the {alias} family — "
                             f"{NONSENSICAL}",
                      UNRESOLVED_WRONG_FAMILY, answered, nonsensical=True)
            continue
        if parse_ts(model.get("created_at")) is None:
            # R5-1 (#203 probe round 5): no `created_at` is a PROBE FAILURE,
            # not a catalogue mismatch — the family freezes, wording
            # following the freeze wording, exactly as a wrong-tier or
            # wrong-family answer does (R3-2).
            unresolve(alias, f"`{model['id']}` has no parseable created_at to "
                             f"start its predecessor's buffer from",
                      UNRESOLVED_NO_CREATED_AT, answered, nonsensical=True)
            continue
        by_family[alias] = model["id"]
    return (dict(sorted(by_family.items())), unresolved, failed,
            dict(sorted(mismatched.items())))


def _dated_candidates(model_id: str, available: list[dict]) -> list[str]:
    """Every available `<model_id>-YYYYMMDD` (#203 probe round 1): the
    mirror of the dated->undated collapse, for a CLI that answers with an
    undated id the catalogue lists only in dated form."""
    return sorted(m["id"] for m in available
                  if (match := SNAPSHOT_SUFFIX.match(m["id"]))
                  and match.group("base") == model_id)


def _seated_form(previous_arm: str, *, listed: set, snapshots: dict,
                 available: list[dict]) -> str | None:
    """The id that represents `previous_arm` in THIS run's catalogue, or
    `None` if nothing does (#203 probe round 9, R9-1).

    The Models API is free to switch which of a model's two spellings it
    lists between runs — dated one run, undated the next, or back — and a
    previous arm must still be recognized under whichever one it is now:
    listed as-is -> itself; a dated snapshot whose undated alias is listed
    -> the alias (`snapshots`, the existing collapse `alias_map` computes
    from THIS run's catalogue); a dated id the catalogue does NOT list
    while it lists the undated base -> the base (the mirror direction);
    an undated id the catalogue does not list while it lists exactly ONE
    dated snapshot of it -> that dated id (two or more is ambiguous, so
    neither is preferred). Shared by `frozen_held_ids` (a frozen family's
    previous arms are held under whichever form is listed) and
    `seated_in_tier` (a default's tier stays on the roster through
    whichever form of another previous arm is seated) so the two answer
    the identical question about a rename the same way.
    """
    if previous_arm in snapshots:
        return snapshots[previous_arm]
    if previous_arm in listed:
        return previous_arm
    match = SNAPSHOT_SUFFIX.match(previous_arm)
    if match and match.group("base") in listed:
        return match.group("base")
    if not match:
        dated = _dated_candidates(previous_arm, available)
        if len(dated) == 1:
            return dated[0]
    return None


def _resolve_defaults(defaults_doc, available: list[dict], snapshots: dict,
                      rungs: list[list[str]], warn, *,
                      problem: str | None = None) -> dict | None:
    """The vendor-default model of each family the CLI probe resolved, as an
    id (#202; `scripts/probe_model_defaults.py` since #203) — and the
    families it could NOT establish, which are frozen for this run (#203
    probe round 1).

    None only when there is no document at all and no problem reading one
    (a run with no probe, e.g. locally): the roster is then BYTE-FOR-BYTE
    the one computed without it. Otherwise a dict whose `failed` maps each
    frozen family word to a class, and `document_failed` names a
    document-level class when the whole document was unusable.

    A FAMILY IS FROZEN on a PROBE FAILURE OR A CATALOGUE MISMATCH (#203
    probe round 2, R2-1; folded into one freeze in round 7, R7-1). A PROBE
    FAILURE freezes: the probe recorded an error for its alias, it answered
    with a model of the wrong tier or family (R3-2, round 3), it answered
    with a model that has no `created_at` to start a predecessor's buffer
    from (R5-1, round 5), or the document as a whole is unreadable, junk,
    the stand-in for a probe that exited non-zero (`probe-exited`, R2-4), or
    answered for no ladder alias at all; then every family on the ladder is
    frozen except an alias the probe listed as `skipped` (the CLI echoed it
    back: not one of its aliases, so never a default to lose). A CATALOGUE
    MISMATCH freezes too, through the identical rule, described next. A
    ladder word the document does not mention at all is not frozen. The
    freeze is per run: nothing about it is carried to the next.

    A CATALOGUE MISMATCH: the probe answered, but the id is not an available
    model this run (not available, an ambiguous snapshot). `mismatched`
    names it, and the family FREEZES exactly like a probe failure (R7-1,
    see `compute_roster`), kept in its own field and wording so a human can
    tell the two apart. An answer in the wrong tier or family, or one with
    no `created_at`, is a FAILURE (R3-2; R5-1): the CLI said something
    nonsensical about its own alias, or named a model with nothing to start
    its predecessor's buffer from.

    An alias resolves when its id is one of this run's available models —
    or a dated snapshot the catalogue collapses onto one, which then stands
    for it, or a dated `<base>-YYYYMMDD` the catalogue does not list while
    it lists `<base>` (R2-1(b)), or an undated id the catalogue lists as
    exactly ONE dated `<id>-YYYYMMDD` (two or more is ambiguous) — AND the
    family word in that id is the alias itself. Every warning names classes
    and validated ids only, never the document's text; `problem` is
    `main()`'s "the file is there and unreadable".
    """
    if defaults_doc is None and not problem:
        return None
    ladder = [word for rung in rungs for word in rung]
    info = {"source": None, "probed_at": None, "by_family": {}, "resolved": {},
            "unresolved": [], "failed": {}, "mismatched": {},
            "document_failed": None, "published": False}

    def whole_document(cls: str, said: str, errors: dict, skipped: set) -> dict:
        warn(f"model defaults unavailable ({said}); every family on the tier "
             f"ladder the probe did not skip is {FROZEN_WORDS}")
        info["document_failed"] = cls
        info["failed"] = {word: errors.get(word, cls) for word in ladder
                          if word not in skipped}
        return info

    if defaults_doc is None:
        return whole_document(DOCUMENT_UNREADABLE, "the defaults file is present "
                              "but unreadable", {}, set())
    if not isinstance(defaults_doc, dict):
        return whole_document(DOCUMENT_NOT_AN_OBJECT, "the defaults document is "
                              "not an object", {}, set())
    if "probe_exit" in defaults_doc:
        # eval.yml's stand-in: the probe script itself failed (R2-4).
        return whole_document(DOCUMENT_PROBE_EXITED,
                              DOCUMENT_WORDS[DOCUMENT_PROBE_EXITED], {}, set())
    raw_errors = defaults_doc.get("errors")
    errors: dict[str, str] = {}
    if isinstance(raw_errors, dict) and len(raw_errors) <= DEFAULTS_MAX_ALIASES:
        for alias, error in raw_errors.items():
            if isinstance(alias, str) and DEFAULTS_ALIAS_RE.match(alias):
                errors[alias] = (error if isinstance(error, str)
                                 and DEFAULTS_ERROR_RE.match(error)
                                 else UNRESOLVED_ERROR)
    raw_skipped = defaults_doc.get("skipped")
    skipped = ({a for a in raw_skipped if isinstance(a, str)
                and DEFAULTS_ALIAS_RE.match(a) and a not in errors}
               if isinstance(raw_skipped, list)
               and len(raw_skipped) <= DEFAULTS_MAX_ALIASES else set())
    entries = defaults_doc.get("defaults")
    if not isinstance(entries, dict) or not entries:
        said = (", ".join(f"{a} {e}" for a, e in sorted(errors.items()))
                or "no error recorded")
        return whole_document(DOCUMENT_NO_DEFAULTS, f"the Claude Code probe "
                              f"resolved no alias: {said}", errors, skipped)
    if len(entries) > DEFAULTS_MAX_ALIASES:
        return whole_document(DOCUMENT_TOO_MANY, f"the defaults document names "
                              f"{len(entries)} aliases, past the "
                              f"{DEFAULTS_MAX_ALIASES}-alias bound", errors, skipped)
    ids: dict[str, str] = {}
    junk = 0
    for alias, model_id in entries.items():
        if (isinstance(alias, str) and DEFAULTS_ALIAS_RE.match(alias)
                and isinstance(model_id, str) and DEFAULTS_MODEL_RE.match(model_id)):
            ids[alias] = model_id
        else:
            junk += 1
    if junk:
        warn(f"model defaults document: skipped {junk} entry/entries that are "
             f"not a family alias mapped to a model id")
    if not ids:
        return whole_document(DOCUMENT_NO_USABLE, "the defaults document names no "
                              "usable defaults", errors, skipped)

    source, probed_at = _defaults_provenance(defaults_doc)
    by_id = {m["id"]: m for m in available}
    candidates: list[tuple] = []
    unresolved: list[dict] = []
    for alias in sorted(set(ids) | set(errors)):
        rung = next((i for i, words in enumerate(rungs) if alias in words), None)
        if rung is None:
            unresolved.append({"alias": alias, "reason": "not a family word on "
                                                         "the tier ladder"})
            warn(f"model default alias `{alias}` is not a family word on the "
                 f"tier ladder; ignored")
            continue
        if alias not in ids:
            said = ("an error was recorded" if errors[alias] == UNRESOLVED_ERROR
                    else errors[alias])
            candidates.append((alias, rung, None,
                               f"the Claude Code probe failed ({said})",
                               errors[alias], None))
            continue
        model_id = ids[alias]
        seat_id = snapshots.get(model_id, model_id)
        undated = SNAPSHOT_SUFFIX.match(model_id)
        if seat_id not in by_id and undated and undated.group("base") in by_id:
            # A dated id the catalogue does not list, while it lists the
            # undated alias: the same model (R2-1(b), the mirror of F6).
            seat_id = undated.group("base")
        if seat_id not in by_id:
            dated = _dated_candidates(model_id, available)
            if len(dated) == 1:
                seat_id = dated[0]
            elif dated:
                candidates.append((alias, rung, None,
                                   f"`{model_id}` is not an available model this "
                                   f"run, and {len(dated)} dated snapshots of it "
                                   f"are, so which one it means is ambiguous",
                                   UNRESOLVED_AMBIGUOUS, model_id))
                continue
            else:
                candidates.append((alias, rung, None,
                                   f"`{model_id}` is not an available model this run",
                                   UNRESOLVED_NOT_AVAILABLE, model_id))
                continue
        candidates.append((alias, rung, by_id[seat_id], None, None, model_id))
    by_family, more, failed, mismatched = _seat_default_candidates(
        candidates, rungs, warn)
    info.update({"source": source, "probed_at": probed_at, "by_family": by_family,
                 "resolved": dict(by_family), "unresolved": unresolved + more,
                 "failed": failed, "mismatched": mismatched, "published": True})
    if not by_family and not mismatched:
        # Present and well-formed, and still the probe answered for no
        # ladder alias: it told this run nothing it can act on, so the
        # ladder is frozen with the DOCUMENT-level class. (An answer the
        # catalogue does not match is not this case: the probe worked, and
        # `mismatched` already freezes that family with its own wording,
        # R2-1/R7-1.)
        warn(f"model defaults: the Claude Code probe resolved no family to an "
             f"available model; every family on the tier ladder the probe did "
             f"not skip is {FROZEN_WORDS}")
        info["document_failed"] = DOCUMENT_RESOLVED_NOTHING
        info["failed"] = {word: failed.get(word, DOCUMENT_RESOLVED_NOTHING)
                          for word in ladder if word not in skipped}
    return info


def _default_rung_decision(model: dict, rung: int, label: str, default_id: str,
                           defaults_info: dict, *, available, rungs, policy, now,
                           counts, aliases, api_ids, previous_arms,
                           catalogue_seen, census_doc, usable, stale_note,
                           enter_usable, enter_share, qualifier, holdover,
                           seated_in_tier, retire_notes, retire_windows,
                           tier_holdover, family,
                           previous_arm_ids=None) -> tuple[str | None, str | None]:
    """(arm reason, exclusion reason) for a model in a tier whose vendor
    default resolved — the owner's decision of 2026-09-27 (#202, ADR 0002).

    Only reached for a family whose vendor default this run's catalogue
    actually matches: a family the probe FAILED for, or one it answered for
    but this run's catalogue does not match (not available, an ambiguous
    snapshot), FREEZES instead — see `compute_roster` — and never calls this
    function (#203 probe round 7, R7-1: the two used to diverge behind an
    "effective default" guess; now every catalogue mismatch is routed
    through the same freeze a probe failure gets).

    Usage keeps ONE job here: deciding whether the tier is on the roster.
    Inside it the seat goes to the default, at once and with no cooling-off;
    a model the default supersedes earns no seat from its usage, and a
    previous arm it supersedes keeps its seat only through the
    `superseded_exit_weeks` buffer below. A model NEWER than the default is
    a preview the vendor has not made the default, and earns no new seat; a
    previous arm newer than it, and the default itself when nothing else puts
    its tier on the roster, get rule 3's exit check through `holdover`
    (#203 round 1). Superseded retirements are recorded in `retire_notes`
    for `retired_since_last`.

    Only models of the default's own FAMILY reach this function (R2-4, #203
    round 2), and "the tier" below means that family's models: `qualifier`,
    `seated_in_tier` and `tier_holdover` are all scoped to it. A seated
    default with nothing else putting its tier on the roster is held while
    the tier's COMBINED share clears the exit bar (R2-3), not its own: the
    fleet moving from one version to the next inside a tier that sits
    between the two bars must not leave the tier with no arm.
    """
    model_id = model["id"]
    by_id = {m["id"]: m for m in available}
    probed = (f"probed {defaults_info['probed_at']}" if defaults_info["probed_at"]
              else "probed at an unknown time")
    default_words = (f"vendor default for the {label} tier per "
                     f"{defaults_info['source']} ({probed})")
    enter_bar = policy["arm_enter_usage_pct"]
    enter_span = f"over the last {policy['arm_enter_window_weeks']} weeks"
    # A previous arm held under its DATED id (R5-3, #203 probe round 5) is
    # still a previous arm once its undated alias appears in the catalogue:
    # `previous_arm_ids` also names that alias, so both the check below and
    # the superseded-arm buffer check use it rather than the literal
    # `previous_arms` — R7-2 (#203 probe round 7): a seated vendor default
    # held only under its dated id was missing this and so fell through to
    # "the tier is not on the roster" although its own alias-collapsed seat
    # cleared the exit bar.
    held_arm_ids = previous_arm_ids if previous_arm_ids is not None else previous_arms

    if model_id == default_id:
        # "The tier is on the roster" never rests on the default's OWN
        # previous seat (#203 round 1): that made a seated default
        # permanent. Another previous arm in the tier counts only while it
        # is itself still seated this run (`seated_in_tier`), and a default
        # that is a previous arm with nothing else keeping its tier on gets
        # rule 3's exit check, exactly as any other previous arm does.
        if qualifier is not None:
            qualifying_id, qualifying_share = qualifier
            why = (f"the tier qualifies by usage: `{qualifying_id}` carries "
                   f"{_format_share(qualifying_share, enter_bar)}% of rankable "
                   f"census usage {enter_span} (at or above the {enter_bar}% "
                   f"entry bar)")
        elif seated_in_tier:
            seat, prev_arm, renamed = seated_in_tier[0]
            # N3 (adversarial round 10): when the previous roster's own
            # spelling `prev_arm` isn't in THIS run's catalogue at all —
            # the Models API renamed it between runs, not the ordinary
            # same-run dated/undated collapse the ASSUMPTION in
            # roster-policy.yml already treats as one model — say so,
            # instead of calling the renamed form `seat` itself a
            # "previous arm", which the previous roster never spelled that
            # way.
            named = (f"`{seat}` (previous arm `{prev_arm}`)" if renamed else
                    f"previous arm `{seat}`")
            why = f"the tier is on the roster because {named} is in it and still seated"
        elif not enter_usable:
            why = (f"{stale_note}; every tier is on the roster under the "
                   f"no-census fallback" if not usable else
                   "the enter window holds too little rankable usage to qualify "
                   "any tier, so every tier is on the roster under the fallback")
        elif model_id in held_arm_ids:
            held = tier_holdover(model_id)
            if held is None:
                return None, None
            why = held
        else:
            # A peer rung names the family: another family's usage in the
            # same rung does not put this default's tier on (R2-4).
            whose = f"`{family}` " if len(rungs[rung]) > 1 else ""
            return None, (f"excluded from the arm set: {default_words}, but the "
                          f"tier is not on the roster — no {whose}model in it "
                          f"carries {enter_bar}% of rankable census usage "
                          f"{enter_span} and no other previous {whose}arm in it "
                          f"is still seated")
        return f"{default_words}, seated with no cooling-off; {why}", None

    default_model = by_id[default_id]
    if _rank(model, rungs) > _rank(default_model, rungs):
        if model_id in held_arm_ids:
            # A previous arm the vendor has not (or not yet, or no longer)
            # made the default gets rule 3's exit check rather than an
            # immediate retirement (#203 round 1): a default that lags a
            # release by one run must not seat it one week and drop it,
            # still heavily used, the next.
            held = holdover(model_id)
            if held is not None:
                return (f"{held}; newer than the vendor default `{default_id}` "
                        f"for the {label} tier, so it keeps a previous seat "
                        f"but could not earn a new one"), None
        return None, (f"excluded from the arm set: newer than the vendor default "
                      f"`{default_id}` for the {label} tier (a release the vendor "
                      f"has not made the default), so it takes no seat while the "
                      f"tier has a known default")

    superseded = f"superseded by `{default_id}`, the {default_words}"
    if model_id not in held_arm_ids:
        share = enter_share.get(model_id)
        if share is not None and share >= enter_bar:
            return None, (f"excluded from the arm set: {superseded}; its "
                          f"{_format_share(share, enter_bar)}% of rankable census "
                          f"usage {enter_span} keeps the tier on the roster but "
                          f"earns it no seat")
        return None, None

    weeks_needed = policy["superseded_exit_weeks"]
    if weeks_needed == 0:
        retire_notes[model_id] = (f"{superseded}, and `superseded_exit_weeks` is "
                                  f"0, so it retires as soon as it is superseded")
        return None, None
    held = "held over from the previous roster"
    census_at = parse_ts((census_doc or {}).get("generated_at"))
    started = parse_ts(default_model.get("created_at"))
    try:
        complete, buffer_weeks = (
            complete_weeks_since(started, min(census_at, now), weeks_needed)
            if census_at and started else (0, []))
    except (OverflowError, ValueError):
        complete, buffer_weeks = 0, []
    if complete < weeks_needed:
        return (f"{held}: {superseded}, and inside its {weeks_needed}-week buffer "
                f"— {complete} of {weeks_needed} complete ISO week(s) since that "
                f"release are covered by the census"), None
    if not usable:
        return f"{held}: {superseded}; {stale_note}, so there is no evidence to retire it", None
    span = f"the {weeks_needed} complete week(s) {', '.join(buffer_weeks)}"
    raw_total, ranked_total = _in_window_totals(
        counts, set(buffer_weeks), rungs, aliases=aliases, api_ids=api_ids,
        previous_arms=previous_arms, catalogue_seen=catalogue_seen)
    if ranked_total < policy["min_ranked_turns"]:
        return (f"{held}: {superseded}; a ranked, attributable total of "
                f"{ranked_total} turn(s) over {span} is under the "
                f"{policy['min_ranked_turns']}-turn floor, too little to be "
                f"evidence of anything, so there is no evidence to retire it"), None
    if ranked_total < policy["min_ranked_share"] * raw_total:
        bar = 100 * policy["min_ranked_share"]
        pct = 100 * ranked_total / raw_total
        return (f"{held}: {superseded}; only {ranked_total} of {raw_total} raw "
                f"turns over {span} are rankable, attributable usage "
                f"({_format_share(pct, bar, under=True)}% — under the {bar:g}% "
                f"relative floor), too little to be evidence of anything, so "
                f"there is no evidence to retire it"), None
    share = usage_share(counts, model_id, buffer_weeks, rungs, aliases,
                        api_ids=api_ids, previous_arms=previous_arms,
                        catalogue_seen=catalogue_seen)
    exit_bar = policy["arm_exit_usage_pct"]
    if share >= exit_bar:
        return (f"{held}: {superseded}, but still "
                f"{_format_share(share, exit_bar)}% of rankable census usage "
                f"over {span} (at or above the {exit_bar}% exit bar)"), None
    retire_notes[model_id] = (f"{superseded}; "
                              f"{_format_share(share, exit_bar, under=True)}% of "
                              f"rankable census usage over {span}, under the "
                              f"{exit_bar}% exit bar")
    retire_windows[model_id] = buffer_weeks
    return None, None


def _holdover_reason(model_id: str, *, usable, stale_note, exit_usable,
                     exit_ranked_total, exit_raw_total, counts, exit_weeks,
                     rungs, aliases, api_ids, previous_arms, catalogue_seen,
                     policy, tier: tuple | None = None,
                     retire_notes: dict | None = None) -> str | None:
    """RULE 3: the reason a PREVIOUS arm keeps its seat, or None when the
    census measures it under the exit bar and it retires.

    One implementation, shared by every caller (#203 round 1): the ordinary
    arms loop, a vendor default that is itself a previous arm and no longer
    has its tier on the roster by any other route, and a previous arm newer
    than its tier's default. A retirement it implies is reported by
    `compute_roster`'s `retired_since_last` loop, with the exit-window share.

    `tier` is `(label, family, ids)` for a SEATED VENDOR DEFAULT (R2-3,
    #203 round 2): the share compared against the exit bar is then the
    combined share of every model in `ids` — the same numerators over the
    same denominator `usage_numbers` gives each one — and a retirement is
    recorded in `retire_notes` saying the share was the tier's.
    """
    reason = None
    if not usable:
        # Staleness is not evidence of disuse. Retiring a previous arm
        # because nobody published a census retires it on NO evidence
        # — measured: an arm at 33% usage dropped when the census was
        # 21 days old. The only retirement a stale census supports is
        # a model that left the Models API, handled below.
        reason = (f"held over from the previous roster: {stale_note}, so "
                  f"there is no evidence to retire it")
    elif not exit_usable:
        # The census is fresh overall, but the EXIT window's own
        # ranked total is under one of the two floors — the same
        # "not enough evidence to act on" gap as the stale case
        # above, just scoped to this window rather than the whole
        # census. Names WHICH floor held, absolute checked first
        # (matching _census_verdict's own ordering).
        #
        # N5 (#129 review round 6): unreachable under
        # evals/roster-policy.yml's shipped numbers (see the
        # comment above `enter_raw_total`/`exit_raw_total`) — kept
        # rather than deleted, and made reachable and pinned by
        # TestIssue67Review6's two exit-side floor-note tests via
        # a test-only policy whose exit window is shorter than its
        # enter one, so this text is not dead code with no test
        # able to reach it.
        if exit_ranked_total < policy["min_ranked_turns"]:
            floor_note = (f"an exit-window ranked, attributable total "
                         f"of {exit_ranked_total} turn(s) over the "
                         f"last {policy['arm_exit_window_weeks']} "
                         f"weeks is under the "
                         f"{policy['min_ranked_turns']}-turn floor, "
                         f"too little to be evidence of anything")
        else:
            pct = (100 * exit_ranked_total / exit_raw_total
                  if exit_raw_total else 0.0)
            bar = 100 * policy["min_ranked_share"]
            floor_note = (f"only {exit_ranked_total} of "
                         f"{exit_raw_total} raw turns over the last "
                         f"{policy['arm_exit_window_weeks']} weeks are "
                         f"rankable, attributable usage "
                         f"({_format_share(pct, bar, under=True)}% — under the "
                         f"{bar:g}% relative floor for this window), "
                         f"too little to be evidence of anything")
        reason = (f"held over from the previous roster: {floor_note}, so "
                  f"there is no evidence to retire it")
    elif tier is not None:
        label, family, ids = tier
        mine, total = _combined_numbers(
            counts, ids, exit_weeks, rungs, aliases, api_ids=api_ids,
            previous_arms=previous_arms, catalogue_seen=catalogue_seen)
        share = 0.0 if total == 0 else (100 * mine) / total
        bar = policy["arm_exit_usage_pct"]
        combined = (f"the {label} tier's combined share — every `{family}` model "
                    f"in it, which is what a seated default's exit check measures")
        if share >= bar:
            reason = (f"held over from the previous roster: {combined} — is still "
                      f"{_format_share(share, bar)}% of rankable census usage "
                      f"over the last {policy['arm_exit_window_weeks']} weeks "
                      f"(at or above the {bar}% exit bar)")
        elif retire_notes is not None:
            retire_notes[model_id] = (
                f"{combined} — is below the {bar}% exit bar for the last "
                f"{policy['arm_exit_window_weeks']} weeks "
                f"({_format_share(share, bar, under=True)}% of rankable census "
                f"usage)")
    else:
        held = usage_share(counts, model_id, exit_weeks, rungs, aliases,
                          api_ids=api_ids, previous_arms=previous_arms,
                          catalogue_seen=catalogue_seen)
        if held >= policy["arm_exit_usage_pct"]:
            reason = (f"held over from the previous roster: still "
                      f"{_format_share(held, policy['arm_exit_usage_pct'])}% "
                      f"of rankable census usage over the last "
                      f"{policy['arm_exit_window_weeks']} weeks (at or above "
                      f"the {policy['arm_exit_usage_pct']}% exit bar)")
        # NO `else` BRANCH ANY MORE (#147). An arm measurably
        # under the exit bar simply gets no reason here, falls out
        # of `arms`, and is reported in `retired_since_last` with
        # its share — and, since #147, in the PROPOSAL with its
        # numerator and denominator beside it. What used to sit
        # here was the anchored-denominator veto: a refusal to act
        # on a retirement whose denominator `previous.json`
        # supplied too much of. `previous` is the committed roster
        # now, so "too much of it comes from the previous roster"
        # is a statement about a reviewed file rather than a
        # measure of doubt, and the veto refused exactly the
        # honest case. See the comment where
        # the anchored-denominator constant used to be.
    return reason


def _combined_numbers(counts: dict, ids, weeks, rungs, aliases, *, api_ids,
                      previous_arms, catalogue_seen) -> tuple[int, int]:
    """(numerator, denominator) of several models' usage taken together:
    `usage_numbers`' numerators summed over one shared denominator — the
    tier share a seated default's exit check reads (R2-3, #203 round 2)."""
    mine, total = 0, 0
    for model_id in ids:
        own, total = usage_numbers(counts, model_id, weeks, rungs, aliases,
                                   api_ids=api_ids, previous_arms=previous_arms,
                                   catalogue_seen=catalogue_seen)
        mine += own
    return mine, total


def _cooling_off_cleared(days: int) -> str:
    """How a reason says a model cleared the cooling-off.

    At 0 — the shipped value since the owner's decision of 2026-09-27
    (#202) — there is no cooling-off to be past, and "past the 0-day
    cooling-off" reads as a rule that exists and was satisfied. A positive
    value keeps the wording every earlier roster used.
    """
    if days == 0:
        return "no cooling-off applies: `cooling_off_days` is 0"
    return f"past the {days}-day cooling-off"


def compute_roster(models_doc: dict, census_doc: dict | None, policy: dict,
                   previous: dict | None, now: datetime,
                   admin_doc: dict | None = None, warn=None,
                   census_problem: str | None = None,
                   previous_problem: str | None = None,
                   defaults_doc: dict | None = None,
                   defaults_problem: str | None = None) -> dict:
    warn = warn or _stderr
    # FATAL, and it is the first thing checked (#147, ADR 0001). `previous`
    # is the COMMITTED roster now. A present-but-unreadable one is a repo
    # defect on a protected branch, not a fact about an unprotected one, so
    # there is no honest roster to compute a proposal against — and the
    # proposal an empty `previous` would produce ("seat every live model")
    # is the shape a reviewer is most likely to wave through.
    if previous_problem:
        raise TrustedRosterUnreadable(
            f"refusing to compute a roster: the committed roster is "
            f"{previous_problem}, which is a defect in this repository "
            f"rather than a fact about an untrusted input")
    rungs = tier_rungs(policy)

    entries = _clean_models(models_doc, warn)
    api_ids = [m["id"] for m in entries]
    ranked = [m for m in entries if rung_of(m["id"], rungs) is not None]
    unranked = [{"id": m["id"],
                 "reason": ("no family word from the tier ladder appears in its "
                            "id, so it cannot be ranked and takes no roster seat")}
                for m in entries if rung_of(m["id"], rungs) is None]
    unranked_ids = {u["id"] for u in unranked}

    raw_counts = (census_doc or {}).get("counts")
    # THE ONE BOUND LEFT ON AN UNTRUSTED INPUT (ADR 0001's "what stays").
    # Counts only, never a key, like every other message about this file.
    if isinstance(raw_counts, dict) and len(raw_counts) > CENSUS_MAX_KEYS:
        raise RosterRefusal(
            f"refusing to read the census: it names {len(raw_counts)} model "
            f"keys, past the {CENSUS_MAX_KEYS}-key bound — an untrusted "
            f"document may not decide how much work this run does")
    counts = _clean_counts(raw_counts, warn)

    # Two alias maps, deliberately. SEATING may only collapse a snapshot onto
    # an alias the catalogue actually offers — an alias that exists solely as
    # an old census key is not a model anyone can run, so `seat_aliases`
    # stays catalogue-only. USAGE is widened past that (see
    # `_usage_alias_map`): a departed previous arm, a `catalogue_seen`
    # history entry and a census key all fold over api ids, census keys,
    # previous arms AND catalogue_seen, because a previous arm published
    # under a dated id that has since left the API, whose usage the census
    # records under the undated alias, is in NEITHER `api_ids` NOR `counts`
    # — only its undated alias is (in `counts`) — and `alias_map` needs BOTH
    # spellings present in the ids it is given to create the fold at all.
    # Leaving previous_arms/catalogue_seen out of that call meant it never
    # saw the dated id, so `previous_arms_folded` downstream stayed
    # identical to `previous_arms` and never matched the undated candidate
    # — measured: 5000 real turns dropped from the denominator, inflating an
    # unrelated model's share from ~1.96% to a false ~97.1%. What the
    # widening may NOT do is re-target an id the Models API returns THIS
    # run: that is B1 of round 7, and `_usage_alias_map` is where the rule
    # lives. This is a SEPARATE mechanism from _is_attributable's
    # catalogue_seen check: that one credits a since-retired real id's own
    # usage even when neither of ITS spellings ever held (or holds) a seat.
    seat_aliases = alias_map(api_ids)
    snapshots = {m["id"]: seat_aliases[m["id"]]
                 for m in ranked if m["id"] in seat_aliases}

    available = [m for m in ranked if m["id"] not in snapshots]
    available.sort(key=lambda m: _rank(m, rungs))
    live_order = [m["id"] for m in available]

    # The windows are computed before selection because relevance is decided by
    # IN-WINDOW census turns (A, #129 review round 10): a census key whose
    # usage all falls outside the window is evidence of nothing current,
    # and 600 such keys used to hand 600 plants named after them a
    # relevance the census had no evidence for. They are read again below
    # for the shares themselves.
    enter_weeks = window_weeks(now, policy["arm_enter_window_weeks"])
    exit_weeks = window_weeks(now, policy["arm_exit_window_weeks"])
    window_union = set(enter_weeks) | set(exit_weeks)
    count_turns: dict[str, int] = {}
    for candidate, by_week in counts.items():
        in_window = sum(n for week, n in by_week.items() if week in window_union)
        if in_window > 0:
            count_turns[candidate] = in_window

    # Computed before the wide alias map below: USAGE attribution (B1,
    # #129 review round 6) needs both a previous roster's arm ids and its
    # catalogue history to fold a previous arm published under a DATED id
    # that has since left the API, whose usage the census records under
    # the UNDATED alias — see the two-alias-maps comment above.
    #
    # Two lists, and which one each reader wants is the whole of F3 (#129
    # review round 8): `previous_arms` is every arm the previous roster
    # named, and is what `added_since_last`/`retired_since_last` compare
    # against, so a departed arm is reported whether or not this run can
    # say anything about it. The same full reviewed list feeds attribution,
    # the alias map and the hold-over check.
    previous_arms = _clean_previous_arms(previous, warn)

    # The union of every id the Models API has EVER listed across runs: this
    # run's api ids plus whatever the previous roster already accumulated,
    # refreshed and aged by `_update_catalogue_seen` (S3, #129 review
    # round 6). Read back next run as `previous`'s own `catalogue_seen` —
    # see `_is_attributable`'s FIRST-RUN CAVEAT for what an empty history
    # means. `catalogue_seen_entries` is the PUBLISHED `{id, last_seen}`
    # shape; `catalogue_seen` stays a plain set of ids for every downstream
    # membership check (`_is_attributable`, `_fold_set`'s callers, etc.).
    previous_seen = _clean_catalogue_seen(previous, warn, now)

    catalogue_seen_entries = _update_catalogue_seen(
        api_ids, previous_seen, now, policy, warn)
    catalogue_seen = {e["id"] for e in catalogue_seen_entries}

    # Built AFTER `available` is ordered:
    # rule (3) of `_usage_alias_map` needs this run's own capability order
    # to decide which live snapshot a bare alias that is not itself in the
    # catalogue names, and the wide map folds over the reviewed history.
    aliases = _usage_alias_map(
        api_ids, list(counts) + previous_arms + list(catalogue_seen),
        seat_aliases, live_order)


    raw_total, ranked_total = _in_window_totals(
        counts, window_union, rungs, aliases=aliases,
        api_ids=api_ids, previous_arms=previous_arms,
        catalogue_seen=catalogue_seen)
    usable, stale_note, census_code = _census_verdict(
        census_doc, raw_total, ranked_total, policy, now,
        census_problem=census_problem)
    # The absolute/relative floor above is measured over the 8-week UNION,
    # which answers "is there fresh evidence at all". Seating and retiring
    # are separate questions, each measured over its OWN window: the 4-week
    # enter window is a strict subset of the union here, so a handful of
    # ranked turns can clear the union floor while the enter window itself
    # carries next to none — one lone turn there used to compute a 100.0%
    # entry share and seat a non-newest model on it (the ABSOLUTE floor,
    # applied per window). The RELATIVE floor (S1, #129 review round 6)
    # needs the same per-window treatment: an enter window dominated by
    # `other` can clear the union's relative floor on OTHER weeks' ranked
    # usage while carrying almost none of its own — measured: `other` at
    # 1,000,000/week for the enter window's weeks plus a real model
    # elsewhere in the union cleared the union's 100,025-of-4,100,025
    # relative floor while the enter window itself held 25 ranked turns
    # against 4,000,025 raw ones, computing a false 100.0% entry share.
    # Kept symmetric for the exit side even though, for this policy's
    # shipped numbers (arm_exit_window_weeks >= arm_enter_window_weeks),
    # the exit window IS the union and both `exit_usable` floors are
    # always identical to `usable`'s own — nothing guarantees that
    # relation stays true if the numbers ever change, and
    # TestIssue67Review6 exercises the exit-side branch below with a
    # test-only policy whose exit window is shorter than its enter one.
    enter_raw_total, enter_ranked_total = _in_window_totals(
        counts, set(enter_weeks), rungs, aliases=aliases,
        api_ids=api_ids, previous_arms=previous_arms,
        catalogue_seen=catalogue_seen)
    exit_raw_total, exit_ranked_total = _in_window_totals(
        counts, set(exit_weeks), rungs, aliases=aliases,
        api_ids=api_ids, previous_arms=previous_arms,
        catalogue_seen=catalogue_seen)
    enter_usable = (usable and enter_ranked_total >= policy["min_ranked_turns"]
                   and enter_ranked_total >= policy["min_ranked_share"] * enter_raw_total)
    exit_usable = (usable and exit_ranked_total >= policy["min_ranked_turns"]
                  and exit_ranked_total >= policy["min_ranked_share"] * exit_raw_total)
    # Provenance: the timestamp of the census this roster actually read. A
    # census that was published and simply held nothing usable for these
    # weeks HAS a timestamp worth recording — dropping it made "we read a
    # census and it said nothing" indistinguishable from "nobody published
    # one". A stale or future-dated census was not read, so it records
    # nothing. Branches on the verdict CODE, not on the reason string's
    # prefix — a human-facing sentence is not a stable thing to match on.
    # Published as the PARSED timestamp, re-rendered (N8, #129 review round
    # 6) — not the raw string: `parse_ts` strips it before ever comparing
    # it against `now`, but the raw value used to be published verbatim,
    # so a trailing newline or `\r` reached latest.json, summary.md, and
    # eval.yml's stdout as a literal control character. A published code
    # only ever follows a successful parse (see `_census_verdict`), so
    # this re-parse cannot fail here.
    census_at_parsed = (parse_ts((census_doc or {}).get("generated_at"))
                        if census_code in CENSUS_PUBLISHED_CODES else None)
    # `.astimezone(timezone.utc)` FIRST (N2, #129 review round 7):
    # `parse_ts` keeps whatever offset the census carried, and
    # `strftime("...Z")` on an offset-aware timestamp stamps a `Z` on the
    # LOCAL wall clock — a `+05:00` census published five hours early and
    # looking perfectly canonical, which is worse than looking wrong.
    #
    # Guarded the same way `_clean_catalogue_seen`'s sibling conversion is
    # (S1, #129 review round 9), even though it is not reachable today:
    # `.astimezone(timezone.utc)` raises `OverflowError` on a year-1
    # timestamp with a positive offset, and a census that old is STALE, so
    # `census_code` is never one of `CENSUS_PUBLISHED_CODES` and
    # `census_at_parsed` stays None. That reasoning is about the policy's
    # numbers, not about this expression, and provenance is not worth a
    # traceback: on a conversion this module cannot do, record no
    # provenance and say so in a count-free line.
    try:
        census_at_published = (
            census_at_parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if census_at_parsed else None)
    except (OverflowError, ValueError, OSError):
        census_at_published = None
        warn("census `generated_at` cannot be converted to a UTC timestamp; "
             "recording no census provenance")

    newest_by_rung: dict[int, str] = {}
    for model in available:  # sorted weakest-first, oldest-first within a rung
        newest_by_rung[rung_of(model["id"], rungs)] = model["id"]

    # --- which models, and so which TIERS, qualify on usage ---------------
    # Computed in a pass of its own because the newest-per-tier rule below
    # is no longer a fact about one model: since the owner's 2026-09-22 decision
    # it only seats the newest model of a tier that ALREADY HAS a
    # usage-qualified arm, so the second pass has to know the answer for
    # every model in the tier before it can decide about any of them. The
    # shares are computed once here and reused, not recomputed per model.
    #
    # `enter_share` is populated only when `enter_usable`: with no usable
    # enter-window evidence no model can clear the entry bar, so there is
    # no usage-qualified tier to restrict anything to — see the fallback
    # note on the newest branch below.
    enter_share: dict[str, float] = {}
    if enter_usable:
        for model in available:
            enter_share[model["id"]] = usage_share(
                counts, model["id"], enter_weeks, rungs, aliases,
                api_ids=api_ids, previous_arms=previous_arms,
                catalogue_seen=catalogue_seen)
    #: rung -> (id, share) of the tier's LARGEST usage-qualified share. The
    #: newest model this tier seats "rides on" that share and names it, so
    #: a reader of the reason can check the one number the seat rests on.
    #: `available` is iterated in its own sorted order and the comparison is
    #: strict, so a tie is broken by that order rather than by dict order.
    qualifying_by_rung: dict[int, tuple[str, float]] = {}
    for model in available:
        model_id = model["id"]
        share = enter_share.get(model_id)
        if share is None or share < policy["arm_enter_usage_pct"]:
            continue
        rung = rung_of(model_id, rungs)
        best = qualifying_by_rung.get(rung)
        if best is None or share > best[1]:
            qualifying_by_rung[rung] = (model_id, share)
    #: The same, per FAMILY word, for a vendor default: a default governs its
    #: own family only (R2-4, #203 round 2), so a peer family's usage in the
    #: same rung does not put the default's tier on the roster.
    qualifying_by_family: dict[str, tuple[str, float]] = {}
    for model in available:
        share = enter_share.get(model["id"])
        if share is None or share < policy["arm_enter_usage_pct"]:
            continue
        family = family_of(model["id"], rungs)
        best = qualifying_by_family.get(family)
        if best is None or share > best[1]:
            qualifying_by_family[family] = (model["id"], share)

    # --- vendor defaults (#202, ADR 0002; the CLI probe since #203) --------
    # A tier whose default resolved is seated by `_default_rung_decision`
    # below and never reaches the usage/newest/hold-over rules. A family the
    # probe FAILED for, OR one it answered for but this run's catalogue does
    # not otherwise match (not available, an ambiguous snapshot), is FROZEN
    # for this run (#203 probe round 1; round 7, R7-1, folded the mismatch
    # case into the same freeze): falling back to the usage rules for one
    # bad week retired a seated default, or seated a preview, that the next
    # clean week then had to undo — a flip on no evidence. Frozen, its
    # previous arms are held and none is retired. While the family still
    # holds a seat the Models API lists, it gets NO new seat at all (#203
    # probe round 3, R3-1): a usage seat there was a seat the next clean
    # week retired. Only a family that would otherwise vanish from the
    # roster — no held arm left in the API — gets a seat: by the usage entry
    # bar (#203 probe round 2), or, with no usable enter window, as the
    # newest in its tier, exactly as the no-probe fallback seats it (R3-6).
    # A mismatch keeps its own wording throughout — `mismatch_words`, never
    # "probe failed" — so a human can tell the two apart, but the seats
    # follow the identical rule (round 7's governing simplification: a
    # single run whose probe answer is a failure OR a mismatch changes no
    # seat a clean run would not, through one path rather than two). Nothing
    # is carried to the next run (the owner's decision). Every other family
    # keeps the rules verbatim, cooling-off included; no document at all
    # leaves the roster byte for byte the one computed without one.
    defaults_info = _resolve_defaults(defaults_doc, available, snapshots,
                                      rungs, warn, problem=defaults_problem)
    #: family word -> the id of that family's resolved vendor default.
    default_by_family = defaults_info["by_family"] if defaults_info else {}
    #: family word -> the class its probe failed with: frozen this run.
    frozen = defaults_info["failed"] if defaults_info else {}
    #: family word -> {id, class}: the probe answered, the catalogue does not
    #: match (R2-1). Frozen exactly like a probe failure since R7-1, but
    #: kept in its own field and its own wording (`mismatch_words`) so a
    #: human can tell a failure from a mismatch.
    mismatched = defaults_info["mismatched"] if defaults_info else {}
    #: A previous arm published under a dated id whose undated alias the
    #: catalogue now lists is the same seat renamed, so a frozen family
    #: holds it on the alias rather than losing it to "no new seat" — and
    #: the mirror direction, an undated previous arm the catalogue now
    #: lists only in dated form (#203 probe round 9, R9-1): `_seated_form`
    #: answers both through the one rule, keyed on THIS run's catalogue.
    listed = {m["id"] for m in available}
    frozen_held_ids = set(previous_arms)
    for p in previous_arms:
        seat = _seated_form(p, listed=listed, snapshots=snapshots,
                            available=available)
        if seat is not None:
            frozen_held_ids.add(seat)

    def holds_listed_seat(family: str) -> bool:
        """Whether `family` has a previous arm the Models API still lists
        this run (R3-1): the case in which a frozen family takes no new
        seat."""
        return any(m["id"] in frozen_held_ids for m in available
                   if family_of(m["id"], rungs) == family)

    def freeze_words(family: str) -> str:
        """The diagnostic phrase for a frozen family — a probe failure's
        "vendor default ... unknown this run", or a mismatch's own wording
        (R7-1, #203 probe round 7) — never "probe failed" for a mismatch,
        which answered."""
        if family in mismatched:
            entry = mismatched[family]
            return mismatch_words(entry["id"], family, entry["class"])
        return (f"vendor default for `{family}` unknown this run (probe: "
                f"{frozen[family]})")

    def freeze_kind(family: str) -> str:
        """"a failed probe" or "a catalogue mismatch" — the noun phrase every
        freeze reason names itself by (R7-1)."""
        return "a catalogue mismatch" if family in mismatched else "a failed probe"

    # `default_by_family` (family word -> the vendor default its seats are
    # decided on this run) doubles as the set of families reaching
    # `_default_rung_decision` at all: a frozen or mismatched family never
    # does (R7-1) — it is decided in the freeze branch below instead.

    #: Why a previous arm this rule dropped left, for `retired_since_last`
    #: and the proposal — and, for a superseded arm measured over its
    #: buffer weeks, which weeks its evidence covers.
    retire_notes: dict[str, str] = {}
    retire_windows: dict[str, list[str]] = {}
    #: A retired default's evidence is its TIER's usage (R2-3): the ids
    #: whose turns the proposal's numerator sums.
    retire_evidence_ids: dict[str, list[str]] = {}

    def holdover(model_id: str) -> str | None:
        return _holdover_reason(
            model_id, usable=usable, stale_note=stale_note,
            exit_usable=exit_usable, exit_ranked_total=exit_ranked_total,
            exit_raw_total=exit_raw_total, counts=counts, exit_weeks=exit_weeks,
            rungs=rungs, aliases=aliases, api_ids=api_ids,
            previous_arms=previous_arms, catalogue_seen=catalogue_seen,
            policy=policy)

    def family_ids(family: str) -> list[str]:
        return [m["id"] for m in available if family_of(m["id"], rungs) == family]

    def tier_holdover(model_id: str) -> str | None:
        family = family_of(model_id, rungs)
        ids = family_ids(family)
        reason = _holdover_reason(
            model_id, usable=usable, stale_note=stale_note,
            exit_usable=exit_usable, exit_ranked_total=exit_ranked_total,
            exit_raw_total=exit_raw_total, counts=counts, exit_weeks=exit_weeks,
            rungs=rungs, aliases=aliases, api_ids=api_ids,
            previous_arms=previous_arms, catalogue_seen=catalogue_seen,
            policy=policy,
            tier=(rung_label(rungs, rung_of(model_id, rungs)), family, ids),
            retire_notes=retire_notes)
        if reason is None:
            retire_evidence_ids[model_id] = ids
        return reason

    # --- who is an arm, and why ------------------------------------------
    arms: list[dict] = []
    excluded: list[dict] = []

    def default_rung_decision(model: dict, *, seated_in_tier=()):
        rung = rung_of(model["id"], rungs)
        family = family_of(model["id"], rungs)
        return _default_rung_decision(
            model, rung, rung_label(rungs, rung), default_by_family[family],
            defaults_info, available=available, rungs=rungs, policy=policy,
            now=now, counts=counts, aliases=aliases, api_ids=api_ids,
            previous_arms=previous_arms, catalogue_seen=catalogue_seen,
            census_doc=census_doc, usable=usable, stale_note=stale_note,
            enter_usable=enter_usable, enter_share=enter_share,
            qualifier=qualifying_by_family.get(family), holdover=holdover,
            seated_in_tier=seated_in_tier,
            retire_notes=retire_notes, retire_windows=retire_windows,
            tier_holdover=tier_holdover, family=family,
            previous_arm_ids=frozen_held_ids)

    #: A tier's default is decided LAST (#203 round 1): whether its tier is
    #: on the roster can rest on another previous arm in it that is itself
    #: still seated this run, so every other model in the tier is decided
    #: first. Its entry is put back in capability order afterwards.
    deferred_defaults: list[dict] = []
    for model in available:
        model_id = model["id"]
        rung = rung_of(model_id, rungs)
        label = rung_label(rungs, rung)
        family = family_of(model_id, rungs)
        if family in frozen or family in mismatched:
            # R7-1 (#203 probe round 7): a catalogue mismatch freezes its
            # family exactly like a probe failure — one path, not two — and
            # differs only in the words `freeze_words`/`freeze_kind` give.
            unknown = freeze_words(family)
            kind = freeze_kind(family)
            enter_bar = policy["arm_enter_usage_pct"]
            share = enter_share.get(model_id) if enter_usable else None
            created = parse_ts(model.get("created_at"))
            age_days = (now - created).days if created else None
            if model_id in frozen_held_ids:
                arms.append({"id": model_id, "reason": (
                    f"{unknown}; held; none retired on {kind}")})
            elif holds_listed_seat(family):
                # R3-1 (#203 probe round 3): the family is still on the
                # roster, so a freeze changes none of its seats — a usage
                # seat here was one the next clean week retired.
                excluded.append({"id": model_id, "reason": (
                    f"excluded from the arm set: {unknown}; no new seat on "
                    f"{kind} while the family holds a seat the Models "
                    f"API still lists")})
            elif share is not None and share >= enter_bar:
                # The family holds no seat the API still lists, so without
                # this it would vanish from the roster: rule 1 needs no
                # default (#203 probe round 2, R2-1(c); narrowed in R3-1).
                arms.append({"id": model_id, "reason": (
                    f"carries {_format_share(share, enter_bar)}% of rankable "
                    f"census usage over the last "
                    f"{policy['arm_enter_window_weeks']} weeks (at or above the "
                    f"{enter_bar}% entry bar); {unknown}, and the family holds "
                    f"no seat the Models API still lists, so this seat rests "
                    f"on usage alone")})
            elif (not enter_usable and newest_by_rung.get(rung) == model_id
                  and age_days is not None
                  and age_days >= policy["cooling_off_days"]):
                # R3-6: with no usable enter window, the no-probe fallback
                # seats every tier's newest; a freeze must not empty a
                # family that fallback would have kept.
                newest_words = (f"newest model in the {label} tier, {age_days} "
                                f"days old ({_cooling_off_cleared(policy['cooling_off_days'])})")
                fallback = (newest_words if usable
                            else f"{stale_note}; fell back to newest per tier — "
                                 f"{newest_words}")
                arms.append({"id": model_id, "reason": (
                    f"{fallback}; {unknown}, and the family holds no seat the "
                    f"Models API still lists, so it falls back to newest per "
                    f"tier as with no probe")})
            else:
                measured = ("which it does not clear" if enter_usable else
                            "which cannot be measured this run, and it is not "
                            "the newest in its tier past the cooling-off")
                excluded.append({"id": model_id, "reason": (
                    f"excluded from the arm set: {unknown}; no new seat on "
                    f"{kind} but by the {enter_bar}% usage entry bar "
                    f"(the family holds no seat the Models API still lists), "
                    f"{measured}")})
            continue
        if family in default_by_family:
            if model_id == default_by_family[family]:
                deferred_defaults.append(model)
                continue
            seat, exclusion = default_rung_decision(model)
            if seat:
                arms.append({"id": model_id, "reason": seat})
            elif exclusion:
                excluded.append({"id": model_id, "reason": exclusion})
            continue
        raw_created = model.get("created_at")
        created = parse_ts(raw_created)
        age_days = (now - created).days if created else None
        is_newest = newest_by_rung.get(rung) == model_id
        old_enough = age_days is not None and age_days >= policy["cooling_off_days"]
        qualifier = qualifying_by_rung.get(rung)

        reason = None
        if enter_usable:
            share = enter_share[model_id]
            if share >= policy["arm_enter_usage_pct"]:
                reason = (f"carries {_format_share(share, policy['arm_enter_usage_pct'])}% "
                          f"of rankable census usage over the last "
                          f"{policy['arm_enter_window_weeks']} weeks "
                          f"(at or above the {policy['arm_enter_usage_pct']}% entry bar)")
        if reason is None and is_newest and old_enough:
            newest_words = (f"newest model in the {label} tier, {age_days} days old "
                            f"({_cooling_off_cleared(policy['cooling_off_days'])})")
            if not enter_usable:
                # THE FALLBACK, UNCHANGED (and the reason the restriction
                # below is not stated unconditionally): with no usable
                # enter-window usage there is no usage-qualified tier at
                # all, so restricting the rule to those tiers would seat
                # nobody and publish an empty roster. Every tier's newest
                # takes a seat, exactly as before, and says which of
                # `_census_verdict`'s eight ways of having no evidence it
                # was — except where `usable` held and only the enter
                # window's own floor failed, which has no `stale_note` of
                # its own to quote.
                reason = (newest_words if usable
                          else f"{stale_note}; fell back to newest per tier — "
                               f"{newest_words}")
            elif qualifier is not None:
                # THE RULE THE OWNER DECIDED ON 2026-09-22. The newest model in
                # a tier is an arm only where the tier is one the fleet
                # demonstrably uses — that is, where some model in it
                # already cleared the entry bar. It rides on that model's
                # share, so the reason names the share and the model it
                # belongs to; a percentage with no owner would be
                # unreadable next to the seat's own 0-ish usage.
                qualifying_id, qualifying_share = qualifier
                reason = (f"{newest_words}, in a tier that qualifies by usage: "
                          f"`{qualifying_id}` carries "
                          f"{_format_share(qualifying_share, policy['arm_enter_usage_pct'])}% "
                          f"of rankable census usage over the last "
                          f"{policy['arm_enter_window_weeks']} weeks (at or above "
                          f"the {policy['arm_enter_usage_pct']}% entry bar)")
            # else: no seat. A tier no model of which carries
            # `arm_enter_usage_pct` of the fleet's usage gets no arm from
            # this rule, however new its newest model is. The `excluded`
            # entry below says so.
        if reason is None and model_id in frozen_held_ids:
            reason = holdover(model_id)
        if reason:
            arms.append({"id": model_id, "reason": reason})
        elif created is None:
            why = "absent" if not raw_created else "unparseable"
            excluded.append({"id": model_id, "reason": (
                f"excluded from the arm set: its created_at is {why}, so the "
                f"{policy['cooling_off_days']}-day cooling-off cannot be checked")})
        elif is_newest and not old_enough:
            excluded.append({"id": model_id, "reason": (
                f"excluded from the arm set: newest in the {label} tier but only "
                f"{age_days} days old, inside the {policy['cooling_off_days']}-day "
                f"cooling-off")})
        elif is_newest and enter_usable and qualifier is None:
            # The newest model of a tier the fleet does not demonstrably
            # use. Said out loud rather than left as an absence: a seat
            # the old rule granted and this one does not is exactly the
            # kind of change a reader of the proposal needs a sentence
            # for, and "every entry carries its reason in words" (property
            # 2, DESIGN.md) is what the roster is for.
            excluded.append({"id": model_id, "reason": (
                f"excluded from the arm set: newest in the {label} tier, but no "
                f"model in that tier carries {policy['arm_enter_usage_pct']}% of "
                f"rankable census usage over the last "
                f"{policy['arm_enter_window_weeks']} weeks, so the tier qualifies "
                f"by usage for no seat and the newest-per-tier rule does not seat "
                f"it")})

    if deferred_defaults:
        seated_ids = {a["id"] for a in arms}
        for model in deferred_defaults:
            family = family_of(model["id"], rungs)
            seated_in_tier = sorted(
                (seat, p, p not in api_ids) for p in previous_arms
                if p != model["id"] and family_of(p, rungs) == family
                and (seat := _seated_form(
                    p, listed=listed, snapshots=snapshots,
                    available=available)) is not None
                and seat in seated_ids)
            seat, exclusion = default_rung_decision(
                model, seated_in_tier=seated_in_tier)
            if seat:
                arms.append({"id": model["id"], "reason": seat})
            elif exclusion:
                excluded.append({"id": model["id"], "reason": exclusion})
        order = {model_id: index for index, model_id in enumerate(live_order)}
        arms.sort(key=lambda entry: order[entry["id"]])
        excluded.sort(key=lambda entry: order[entry["id"]])

    for snapshot_id in sorted(snapshots):
        excluded.append({"id": snapshot_id, "reason": (
            f"dated snapshot of `{snapshots[snapshot_id]}`; collapsed onto that "
            f"alias for ranking, seating and usage, so it takes no seat of its own")})

    arm_ids = {a["id"] for a in arms}

    # --- judge -------------------------------------------------------------
    # Preference order, and the last rung is not decorative: with no census the
    # arm set is "newest per tier", which on a catalogue holding one current
    # model per tier is EVERY available model — the state of the very first run,
    # before any census has been published. Emitting a null judge there would
    # ship a roster with a hole in it, so the fallback names the strongest model
    # available and says out loud that it is also an arm, leaving the caller to
    # decide rather than leaving it to find out. `is_arm` carries that in a
    # field as well as in words: run_eval.py refuses such a judge for an
    # unpinned fixture, and it cannot read prose.
    strongest_arm_rung = max((rung_of(a, rungs) for a in arm_ids), default=None)
    non_arms = [m for m in available if m["id"] not in arm_ids]
    above = ([m for m in non_arms if rung_of(m["id"], rungs) > strongest_arm_rung]
             if strongest_arm_rung is not None else [])

    if above:
        pick = above[-1]
        judge = {"id": pick["id"],
                 "reason": (f"most capable available model at least one tier above "
                            f"the strongest arm model "
                            f"({rung_label(rungs, rung_of(pick['id'], rungs))} "
                            f"over {rung_label(rungs, strongest_arm_rung)}), and "
                            f"not itself an arm")}
    elif non_arms:
        pick = non_arms[-1]
        arm_tier_words = (f" — nothing available sits above the strongest arm's "
                          f"{rung_label(rungs, strongest_arm_rung)} tier"
                          if strongest_arm_rung is not None else "")
        judge = {"id": pick["id"],
                 "reason": f"strongest available model that is not an arm{arm_tier_words}"}
    elif available:
        pick = available[-1]
        judge = {"id": pick["id"],
                 "reason": ("strongest available model — every available model is "
                            "currently an arm, so no non-arm judge exists; do not "
                            "run this model as the arm and the judge of the same run")}
    else:
        judge = {"id": None,
                 "reason": "the Models API returned no model this policy can rank"}
    judge["is_arm"] = judge["id"] in arm_ids

    # --- preflight ---------------------------------------------------------
    # Cheapest = the lowest tier the API still returns. Within it, prefer a
    # model that has cleared the cooling-off: a day-old cheapest-tier model
    # is exactly the kind an old or narrowly-scoped bearer may not yet be
    # entitled to invoke, and eval.yml's preflight step this feeds is FATAL
    # to the whole job — it used to canary on "newest in tier" with no
    # regard for age at all. Falls back to the newest regardless of age only
    # when nothing in the tier has cleared cooling-off yet: there being no
    # older model in the tier to prefer over it.
    cheapest = None
    cheapest_reason = None
    if available:
        lowest_rung = rung_of(available[0]["id"], rungs)
        rung_models = [m for m in available if rung_of(m["id"], rungs) == lowest_rung]
        label = rung_label(rungs, lowest_rung)
        cooled = []
        for m in rung_models:  # ascending order -> newest last
            created = parse_ts(m.get("created_at"))
            age_days = (now - created).days if created else None
            if age_days is not None and age_days >= policy["cooling_off_days"]:
                cooled.append(m)
        if cooled:
            cheapest = cooled[-1]
            # A positive cooling-off keeps the wording every earlier roster
            # used, word for word (#203 round 1); only 0 reads differently.
            cleared = (f"that is past the {policy['cooling_off_days']}-day "
                       f"cooling-off" if policy["cooling_off_days"] > 0 else
                       f"({_cooling_off_cleared(0)})")
            cheapest_reason = (f"newest model in the {label} tier {cleared}: "
                               f"the lowest tier the Models API still returns, and "
                               f"this is its cheapest safely-invocable pick")
        else:
            cheapest = rung_models[-1]
            cheapest_reason = (f"newest model in the {label} tier: the lowest tier "
                               f"the Models API still returns (still within the "
                               f"{policy['cooling_off_days']}-day cooling-off — "
                               f"nothing older in this tier has cleared it yet)")
    preflight = ({"id": cheapest["id"], "reason": cheapest_reason}
                if cheapest else {"id": None, "reason": "the Models API returned no "
                                                        "model this policy can rank"})

    # --- what moved --------------------------------------------------------
    added: list[dict] = []
    retired: list[dict] = []
    if previous is not None:
        added = [a for a in arms if a["id"] not in previous_arms]
        # SORTED, not the previous roster's own insertion order (N-1, #129
        # review round 11). `retired_since_last` was the one part of the
        # published roster an untrusted input ordered: six shuffles of
        # `previous["arms"]` produced seven distinct `roster/latest.json`
        # digests differing in nothing but this list's order, and
        # `render_summary` prints retirements in it — so whoever writes
        # `persistent/eval-results` decided whether the one real retirement led the
        # step summary or sat on line 501 of it.
        for model_id in sorted(previous_arms):
            if model_id in arm_ids:
                continue
            if model_id not in api_ids:
                # Not literally returned this run — but its OTHER spelling
                # may be, if the Models API switched which form it lists
                # between runs (#203 probe round 9, R9-1): a dated previous
                # arm whose undated alias now holds the seat, or an undated
                # one whose sole dated snapshot does. `_seated_form` answers
                # both; say so, rather than "no longer returned", whenever
                # that answer is itself seated this run.
                seat = _seated_form(model_id, listed=listed, snapshots=snapshots,
                                    available=available)
                if seat is not None and seat != model_id and seat in arm_ids:
                    noun = ("dated snapshot" if SNAPSHOT_SUFFIX.match(seat)
                            else "undated alias")
                    why = f"collapsed onto its {noun} `{seat}`, which holds the seat"
                elif (seat is not None and seat != model_id
                      and seat not in arm_ids and seat in retire_notes):
                    # The renamed seat itself retired this run (e.g.
                    # superseded, `superseded_exit_weeks: 0`) — its real
                    # reason lives in `retire_notes` under the SEATED
                    # spelling, not the previous roster's old one (S2,
                    # adversarial round 10). Reuse it, and copy it onto
                    # `model_id` too so a later `retire_notes[model_id]`
                    # lookup (or a second renamed arm of the same seat)
                    # sees the same reason.
                    why = retire_notes[seat]
                    retire_notes[model_id] = why
                else:
                    why = "no longer returned by the Models API"
            elif model_id in unranked_ids:
                why = ("still returned by the Models API, but no family word from "
                       "the tier ladder appears in its id, so it can no longer be "
                       "ranked or seated")
            elif model_id in snapshots and snapshots[model_id] in arm_ids:
                why = (f"collapsed onto its undated alias `{snapshots[model_id]}`, "
                       f"which holds the seat")
            elif model_id in retire_notes:
                why = retire_notes[model_id]
            else:
                # Never `not usable` (nor `not exit_usable`) here: the arms
                # loop above gives every previous arm still in `available`
                # AND still in `previous_arms` an unconditional "no evidence
                # to retire it" reason in both of those cases, which keeps
                # it IN `arm_ids` — so a previous arm reaching this `else`
                # with `model_id not in arm_ids` has always been genuinely
                # measured against the exit bar and found below it. (A
                # prior revision carried a dead `elif not usable:` branch
                # here for exactly the case this comment rules out; it
                # could never execute.) What it has NOT been compared
                # against is a second, "anchored" denominator with the
                # previous roster's contribution removed — that whole
                # mechanism is deleted (#147), because `previous` is a
                # reviewed file on `main` now and the difference measured
                # doubt about a document that is no longer doubted.
                # NOR IS THERE A LIVE-ID EXEMPTION TO REASON ABOUT ANY
                # MORE. Rounds 8 to 12 argued here about whether a live
                # previous arm survived the cap that decided what was
                # carried forward; there is no cap, so every arm the
                # previous roster names is carried and none of that
                # reasoning has a subject (#147).
                held = usage_share(counts, model_id, exit_weeks, rungs, aliases,
                                  api_ids=api_ids, previous_arms=previous_arms,
                                  catalogue_seen=catalogue_seen)
                why = (f"below the {policy['arm_exit_usage_pct']}% exit bar for the last "
                       f"{policy['arm_exit_window_weeks']} weeks "
                       f"({_format_share(held, policy['arm_exit_usage_pct'], under=True)}% of "
                       f"rankable census usage)")
            retired.append({"id": model_id, "reason": why})

    # TWO STATES NOW, NOT THREE (#147). `unavailable` is gone because it
    # cannot happen and go unremarked any more: an unreadable committed
    # roster raises `TrustedRosterUnreadable` at the top of this function
    # and nothing is published at all. What is left is the honest pair —
    # `compared` when there was a roster with something in it to compare
    # against, and `none` when this run had nothing (a genuine first run,
    # or a committed roster naming neither an arm nor an observed model).
    previous_state = ("compared"
                      if previous is not None and (previous_arms or previous_seen)
                      else "none")

    # THE EVIDENCE EVERY PROPOSED SEAT CHANGE CARRIES, computed here
    # because it needs the windows and the alias map. A seat being ADDED
    # is argued over the ENTER window, a seat being RETIRED over the EXIT
    # window — the same two windows the decisions themselves were taken
    # over, so the numbers a reviewer reads are the numbers the policy
    # read.
    evidence: dict[str, str] = {}
    for model_id, weeks_for, bar in (
            [(a["id"], enter_weeks, policy["arm_enter_usage_pct"]) for a in arms]
            + [(i, retire_windows.get(i, exit_weeks), policy["arm_exit_usage_pct"])
               for i in previous_arms if i not in arm_ids]):
        mine, total = _combined_numbers(
            counts, retire_evidence_ids.get(model_id, [model_id]), weeks_for,
            rungs, aliases, api_ids=api_ids, previous_arms=previous_arms,
            catalogue_seen=catalogue_seen)
        evidence[model_id] = _usage_words(mine, total, bar)

    result = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "models_api_at": (models_doc or {}).get("fetched_at"),
            "census_at": census_at_published,
            "admin_report_at": (admin_doc or {}).get("fetched_at") if admin_doc else None,
        },
    }
    if defaults_info is not None and defaults_info["published"]:
        # Only when a probe resolved or tried to resolve an alias: without one
        # the roster is the one this module has always published, key for
        # key. Published for the reviewer only — the committed roster keeps
        # no `defaults` block (#203).
        result["defaults"] = {key: defaults_info[key] for key in
                              ("source", "probed_at", "resolved", "unresolved")}
    if frozen:
        # LOUD, and even when nothing resolved (#203 probe round 1): the
        # families frozen this run and why, read by `render_summary` and by
        # eval.yml's proposal step, which warns and keeps the tracking issue
        # open on it.
        result["defaults_failed"] = dict(sorted(frozen.items()))
    if defaults_info is not None and defaults_info["document_failed"]:
        result["defaults_document_failed"] = defaults_info["document_failed"]
    if mismatched:
        # The probe answered, but this run's catalogue does not match the
        # answer (#203 probe round 2, R2-1): said near the top of the
        # summary in its own words, and never as a probe failure.
        result["defaults_mismatched"] = {alias: dict(entry) for alias, entry
                                         in sorted(mismatched.items())}
    result.update({
        "arms": arms,
        "judge": judge,
        "preflight": preflight,
        "unranked": unranked,
        "excluded": excluded,
        "compared_to_previous": previous_state == "compared",
        "previous_state": previous_state,
        "retired_since_last": retired,
        "added_since_last": added,
        "catalogue_seen": catalogue_seen_entries,
        "proposal": _proposal(previous, previous_arms, previous_seen, arms,
                              judge, preflight, catalogue_seen_entries,
                              evidence, now=now,
                              max_age_days=policy["catalogue_seen_max_age_days"],
                              retire_notes=retire_notes),
    })
    return result


#: HOW MANY PROPOSED CHANGES THE STEP SUMMARY RENDERS. The JSON carries
#: every one of them; this bounds the MARKDOWN, because GitHub caps a step
#: summary at 1 MiB and a summary that is not rendered at all tells a
#: reader nothing. The list is as long as the committed roster's own
#: `arms` plus `catalogue_seen`, which is a reviewed length — so this is a
#: readability bound, not a defence, and it says out loud how many rows it
#: withheld rather than truncating silently.
PROPOSAL_SUMMARY_ROWS = 50


def _usage_words(mine: int, total: int, bar: float) -> str:
    """A seat's evidence IN WORDS: numerator, denominator, and the share
    they make — the three things a reviewer merging a proposal needs and a
    bare percentage does not give them (#147).

    A percentage on its own is unfalsifiable from the outside: `100.0%` of
    two turns and `100.0%` of nine thousand are the same string and are not
    the same claim. The counts are what separate them.
    """
    if total <= 0:
        return ("no rankable, attributable census turns in the window at all, "
                "so there is no share to quote")
    return (f"{mine} of the window's {total} rankable, attributable census "
            f"turns — {_format_share((100 * mine) / total, bar)}%")


def _change(kind: str, field: str, from_value, to_value, reason: str) -> dict:
    return {"kind": kind, "field": field, "from": from_value,
            "to": to_value, "reason": reason}


def _proposal(previous: dict | None, previous_arms: list[str],
              previous_seen: list[dict], arms: list[dict], judge: dict,
              preflight: dict, catalogue_seen_entries: list[dict],
              evidence: dict, *, now: datetime, max_age_days: int,
              retire_notes: dict | None = None) -> dict:
    """What this run would CHANGE about the committed roster, and why.

    THE OUTPUT OF THIS MODULE IS A PROPOSAL, NOT THE RUNNING SET (ADR
    0001, decision 2). `previous` is `evals/roster.yml`, committed on a
    ruleset-protected branch; the arms, judge, preflight and
    `catalogue_seen` computed above are what the Models API and the census
    say the roster SHOULD be. The difference is a diff for a human, and
    `eval.yml` pushes it to the bot-owned `roster/proposal` branch and
    files one tracking issue carrying it.

    WHY EVERY CHANGE CARRIES ITS EVIDENCE. A proposal is only as honest as
    the census, and the census stays attacker-writable — that is what ADR
    0001's last consequence says this design does NOT buy. The defence is
    that a reviewer sees the numbers the claim rests on beside the claim,
    in a diff on `main`'s history rather than silently on a branch nobody
    reads, and that reverting the merge undoes it.

    `status` is "same" or "differs"; on "same" `eval.yml` closes the
    tracking issue rather than leaving a stale one open.

    A `catalogue_seen.last_seen` refresh is ROUTINE — recorded in
    `changes` below like every other change, but not by itself enough to
    make `status` "differs" — when the COMMITTED date is younger than
    half of `max_age_days` (#200). Every live model's `last_seen` marches
    forward on every single run whether or not anything a reviewer needs
    to see changed, and unconditionally proposing on that alone made the
    weekly tracking issue nothing but date churn (issue #200: twelve rows,
    all of them `catalogue_seen.last_seen`, nothing a merge would change).
    A routine refresh still rides along in `changes` so it is applied the
    next time something material actually needs review. Once the
    committed date is at LEAST half the window old, though, the refresh
    IS material: `_update_catalogue_seen`'s eviction clock measures a
    departed model's remaining runway from that same committed date, so a
    refresh has to land at least every `max_age_days / 2` or a model that
    leaves the API keeps less than half its intended history before it
    ages out.
    """
    changes: list[dict] = []
    arm_ids = [a["id"] for a in arms]
    previous_arm_ids = list(previous_arms)
    for arm in arms:
        if arm["id"] not in previous_arm_ids:
            changes.append(_change(
                "seat", "arms", None, arm["id"],
                f"seat it: {arm['reason']}; {evidence.get(arm['id'], '')}".rstrip("; ")))
    for model_id in sorted(previous_arm_ids):
        if model_id not in arm_ids:
            note = (retire_notes or {}).get(model_id)
            changes.append(_change(
                "seat", "arms", model_id, None,
                f"retire it: {note + '; ' if note else ''}"
                f"{evidence.get(model_id, 'no evidence recorded')}"))
    for field, entry in (("judge", judge), ("preflight", preflight)):
        before = (previous or {}).get(field) if isinstance(previous, dict) else None
        before_id = before.get("id") if isinstance(before, dict) else None
        if before_id != entry["id"]:
            changes.append(_change("seat", field, before_id, entry["id"],
                                   entry["reason"]))
    seen_before_dates = {e["id"]: e["last_seen"] for e in previous_seen}
    seen_now_dates = {e["id"]: e["last_seen"]
                      for e in catalogue_seen_entries}
    seen_before = set(seen_before_dates)
    seen_now = set(seen_now_dates)
    for model_id in sorted(seen_now - seen_before):
        changes.append(_change(
            "catalogue_seen", "catalogue_seen", None, model_id,
            "the Models API listed it this run, so this harness has now "
            "observed it"))
    for model_id in sorted(seen_before - seen_now):
        changes.append(_change(
            "catalogue_seen", "catalogue_seen", model_id, None,
            "its last_seen is older than the policy's catalogue_seen window "
            "and the Models API did not list it this run"))
    for model_id in sorted(seen_before & seen_now):
        before = seen_before_dates[model_id]
        after = seen_now_dates[model_id]
        if before != after:
            changes.append(_change(
                "catalogue_seen", "catalogue_seen.last_seen", before, after,
                f"the Models API listed `{model_id}` this run, refreshing its "
                "reviewed observation date"))
    if previous_arm_ids != arm_ids and set(previous_arm_ids) == set(arm_ids):
        changes.append(_change(
            "seat", "arms.order", previous_arm_ids, arm_ids,
            "the ordered arm list changed; its first entry is the default "
            "unpinned model"))
    # STATUS, NOT JUST "any change at all" (#200). Every change kind above
    # is material except `catalogue_seen.last_seen`, which is material
    # only once the COMMITTED date (`change["from"]`) is at least half of
    # `max_age_days` old — see the docstring for why the half-window cut
    # exists. Falling back to `now` when the committed date does not
    # parse mirrors `_update_catalogue_seen`'s own `parse_ts(...) or now`,
    # so an unparseable date reads as "just seen" (routine) rather than
    # ancient, the same fallback the eviction check already trusts.
    half_window = timedelta(days=max_age_days / 2)
    material = any(
        change["field"] != "catalogue_seen.last_seen"
        or (now - (parse_ts(change["from"]) or now)) >= half_window
        for change in changes)
    return {"status": "differs" if material else "same", "changes": changes}


def render_summary(roster: dict, previous_state: str = "auto") -> str:
    """Markdown for $GITHUB_STEP_SUMMARY. A roster change leads.

    `previous_state` is "auto" (read the roster's own `previous_state`
    field — this is what every real caller does now that compute_roster
    fills it in, so the JSON and this Markdown cannot disagree) or an
    explicit override ("compared", "none") for exercising one state in
    isolation. The two are NOT interchangeable: printing "No change to the
    arm set since the last run" on a first run is a claim about a
    comparison nobody made.

    THE "unavailable" STATE IS GONE (#147). It said "a previous roster was
    published but could not be read this run", which was an ordinary fact
    about the unprotected branch `previous` used to come from. The
    committed roster cannot be unreadable and unremarkable at the same
    time: `compute_roster` raises and nothing is published.

    THE PROPOSAL SECTION is what this summary is now FOR. The table below
    it describes a roster that is not in force anywhere until a human
    merges it; the proposal says what merging it would change, with the
    numerator and denominator behind each seat.
    """
    lines = ["### Model roster", ""]
    # FIRST, when a probe failed (#203 probe round 1): a frozen family is a
    # roster that did not move for a reason the reviewer has to know about.
    failed = roster.get("defaults_failed") or {}
    if failed:
        named = ", ".join(f"`{alias}` ({cls})" for alias, cls in sorted(failed.items()))
        document = roster.get("defaults_document_failed")
        words = DOCUMENT_WORDS.get(document)
        whole = (f" (document: {document}{' — ' + words if words else ''})"
                 if document else "")
        lines += [f"**The vendor-default probe failed{whole}; frozen this run, "
                  f"seats held, none retired except models gone from the Models "
                  f"API, and none added unless the family holds no seat the "
                  f"Models API still lists:** {named}.", ""]
    # A catalogue mismatch is NOT a probe failure (#203 probe round 2): the
    # probe answered, so it keeps its own words — but it freezes exactly
    # like one (#203 probe round 7, R7-1), so a human reads the same "seats
    # held, none retired" sentence for either.
    mismatched = roster.get("defaults_mismatched") or {}
    if mismatched:
        said = "; ".join(mismatch_words(entry["id"], alias, entry["class"])
                         for alias, entry in sorted(mismatched.items()))
        lines += [f"**Vendor default not matched:** {said}. Frozen this run, "
                  f"not a probe failure: seats held, none retired except "
                  f"models gone from the Models API, and none added unless "
                  f"the family holds no seat the Models API still lists.", ""]
    changed = roster["added_since_last"] or roster["retired_since_last"]
    if previous_state == "auto":
        previous_state = (roster.get("previous_state") or
                          ("compared" if roster.get("compared_to_previous")
                           else "none"))
    if changed:
        lines.append("**Roster changed since the last run.**")
        for entry in roster["added_since_last"]:
            lines.append(f"- added `{entry['id']}` — {entry['reason']}")
        for entry in roster["retired_since_last"]:
            lines.append(f"- retired `{entry['id']}` — {entry['reason']}")
        lines.append("")
    elif previous_state == "compared":
        lines += ["No change to the arm set since the last run.", ""]
    else:
        lines += ["First published roster here — no previous roster to compare "
                  "against.", ""]

    lines += ["| Role | Model | Why |", "| --- | --- | --- |"]
    for arm in roster["arms"]:
        lines.append(f"| arm | `{arm['id']}` | {arm['reason']} |")
    for role in ("judge", "preflight"):
        entry = roster[role]
        lines.append(f"| {role} | `{entry['id']}` | {entry['reason']} |")
    for role, entries in (("unranked", roster.get("unranked") or []),
                          ("excluded", roster.get("excluded") or [])):
        for entry in entries:
            lines.append(f"| {role} | `{entry['id']}` | {entry['reason']} |")
    if roster["judge"].get("is_arm"):
        lines += ["", "> **The judge is also an arm this run.** Do not run it as "
                      "the arm and the judge of the same eval."]
    proposal = roster.get("proposal") or {}
    lines += ["", "### Proposal", ""]
    if proposal.get("status") == "differs":
        lines += ["**This run proposes a change to the committed roster** "
                  "(`evals/roster.yml`). Nothing changes until a human merges "
                  "it; the eval runs on the committed roster, as it always does.",
                  ""]
        lines += ["| Change | Field | From | To | Why |",
                  "| --- | --- | --- | --- | --- |"]
        changes = proposal.get("changes") or []
        for change in changes[:PROPOSAL_SUMMARY_ROWS]:
            before = f"`{change['from']}`" if change["from"] else "—"
            after = f"`{change['to']}`" if change["to"] else "—"
            lines.append(f"| {change['kind']} | {change['field']} | {before} "
                         f"| {after} | {change['reason']} |")
        lines.append("")
        if len(changes) > PROPOSAL_SUMMARY_ROWS:
            lines += [f"…and {len(changes) - PROPOSAL_SUMMARY_ROWS} further "
                      f"change(s), not rendered here. The published roster "
                      f"carries all of them, and so does the proposed file on "
                      f"`roster/proposal`.", ""]
    else:
        lines += ["The committed roster (`evals/roster.yml`) already says what "
                  "this run would compute. Nothing to propose.", ""]
        # ROUTINE REFRESHES STILL GET A LINE (#200), even though they did
        # not earn a proposal: a reviewer reading "nothing to propose" on
        # a week that actually moved a dozen `last_seen` dates should not
        # have to go read the JSON to learn that.
        refreshes = [c for c in (proposal.get("changes") or [])
                    if c.get("field") == "catalogue_seen.last_seen"]
        if refreshes:
            plural = len(refreshes) != 1
            lines += [f"{len(refreshes)} `catalogue_seen.last_seen` refresh"
                      f"{'es' if plural else ''} "
                      f"{'were' if plural else 'was'} left for the next "
                      f"material proposal (routine: the committed dates are "
                      f"within half the observation window).", ""]

    source = roster["source"]
    lines += [f"Models API `{source['models_api_at']}` · census "
                  f"`{source['census_at'] or 'none'}` · admin report "
                  f"`{source['admin_report_at'] or 'none'}`", ""]
    # Only when a defaults document resolved something to report (#202), so
    # a roster computed without one renders exactly as it always has.
    defaults = roster.get("defaults")
    if defaults:
        seated = ", ".join(f"`{alias}` → `{model_id}`" for alias, model_id
                           in sorted(defaults["resolved"].items())) or "none resolved"
        lines += [f"Vendor model defaults from {defaults['source']}, probed "
                  f"`{defaults['probed_at'] or 'unknown'}`: {seated}", ""]
        for entry in defaults["unresolved"]:
            lines += [f"- `{entry['alias']}` not resolved: {entry['reason']}"]
        if defaults["unresolved"]:
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, type=Path,
                        help="models document from scripts/refresh_models.py")
    parser.add_argument("--policy", type=Path,
                        default=Path(__file__).resolve().parent.parent / "evals" / "roster-policy.yml")
    parser.add_argument("--census", type=Path, default=None,
                        help="usage/latest.json from persistent/eval-results; optional")
    parser.add_argument("--admin-report", type=Path, default=None,
                        help="optional Admin API usage report; recorded as provenance")
    parser.add_argument("--previous", type=Path, default=None,
                        help="the COMMITTED roster (evals/roster.yml) this run "
                             "computes a proposal against; YAML or JSON")
    parser.add_argument("--defaults", type=Path, default=None,
                        help="vendor model defaults from "
                             "scripts/probe_model_defaults.py; optional (#202, #203)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    models_doc, problem = read_json(args.models)
    if models_doc is None:
        print(problem or f"no models document at {args.models}", file=sys.stderr)
        return 1

    policy = load_policy(args.policy)
    try:
        validate_policy(policy)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # THE BYTE BOUND, checked before the parser allocates anything
    # (`CENSUS_MAX_BYTES`). A key count cannot bound one key holding a
    # hundred-megabyte string, and by the time a key count could fire the
    # decoder has already built it.
    if args.census is not None and Path(args.census).is_file():
        size = Path(args.census).stat().st_size
        if size > CENSUS_MAX_BYTES:
            print(f"refusing to read the census: it is {size} bytes, past the "
                  f"{CENSUS_MAX_BYTES}-byte bound — an untrusted document may "
                  f"not decide how much work this run does", file=sys.stderr)
            return 4
    census_doc, census_problem = read_json(args.census)
    if census_problem:
        _stderr(census_problem)
    # YAML OR JSON, and a problem here is FATAL (#147). This is the
    # committed roster on `main`, not a document off an unprotected branch.
    previous_doc, previous_problem = read_trusted_roster(args.previous)
    # Absent or unreadable is not fatal. Absent changes nothing; unreadable
    # freezes every family for the run, and `compute_roster` says so in ONE
    # warning with the right cause (R2-5, #203 round 2; #203 probe round 1)
    # — so nothing is printed about it here.
    defaults_doc, defaults_problem = read_json(args.defaults)

    try:
        roster = compute_roster(
            models_doc=models_doc,
            census_doc=census_doc,
            policy=policy,
            previous=previous_doc,
            now=datetime.now(timezone.utc),
            admin_doc=load_json(args.admin_report),
            census_problem=census_problem,
            previous_problem=previous_problem,
            defaults_doc=defaults_doc,
            defaults_problem=defaults_problem,
        )
    except TrustedRosterUnreadable as exc:
        # A DISTINCT rc, so eval.yml can tell "somebody merged a broken
        # evals/roster.yml" apart from "an untrusted input was out of
        # bounds". Nothing is written either way.
        print(str(exc), file=sys.stderr)
        return 5
    except RosterRefusal as exc:
        # Named, one line, counts only — the same treatment every other
        # untrusted input gets, and a distinct rc from the "no arms"
        # refusal below so eval.yml can tell the two apart. Nothing is
        # written, so the last good roster stands.
        print(str(exc), file=sys.stderr)
        return 4

    if not roster["arms"]:
        # A roster with no arms is not a roster. It used to exit 0 and publish
        # an empty arm set — a run that silently evaluated nothing, which is
        # exactly the "went stale without anyone noticing" failure #67 exists
        # to remove. Nothing is written, so the last good roster stands.
        print("refusing to publish a roster with no arms: every ranked model is "
              "excluded (see the excluded/unranked reasons above)", file=sys.stderr)
        for entry in roster["excluded"] + roster["unranked"]:
            print(f"  {entry['id']}: {entry['reason']}", file=sys.stderr)
        return 3

    # Written via a temp file in the same directory and renamed: a partial
    # roster is worse than no roster, and the caller's "did the file appear?"
    # is the only signal the commit step has.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    staged = args.out.with_name(args.out.name + ".partial")
    with open(staged, "w", encoding="utf-8") as f:
        json.dump(roster, f, indent=2)
        f.write("\n")
    staged.replace(args.out)

    print(render_summary(roster))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
