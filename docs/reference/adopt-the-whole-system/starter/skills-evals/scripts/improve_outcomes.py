#!/usr/bin/env python3
"""Improve outcomes, derived from evidence that already exists (G5).

The thresholds' stopping rules (rule C: a cooldown after a rejection, a stop
after three in a row) need a record of every improve attempt. The routine's
improve mode pushes nothing on a rejection (ADR 0005), and its saved prompt
may only change with the owner's OK, so this module derives each outcome from
what GitHub already holds:

  * THE ATTEMPT: a run of routine-eval-fire.yml whose title is
    `Fire eval routine: improve <skill>` (the workflow's `run-name`, which is
    what makes a dispatch's mode and skill readable from the runs API). A
    fire run that concluded `success` started a routine session; any other
    conclusion is `fire-failed`: refused before any spend (the allowance
    guard, the fixture check) or broken, so infrastructure, never a measured
    rejection.
  * ACCEPTED: a `claude/eval-improve-<run id>` branch in skills-evals whose
    run id's timestamp falls inside that fire run's span (the fire workflow
    stamps the run id in its validate step, between the run's start and its
    last update), and whose `summary.json` names the same skill when it
    could be read. The routine pushes the branch on an accepted candidate
    only.
  * REJECTED: no such branch `OUTCOME_WINDOW` after the fire. That is a
    measured rejection, a refusal or a run that never finished; the evidence
    cannot tell them apart, so every one counts toward the cooldown and the
    stop rule, the conservative direction.
  * PENDING: younger than that, with no branch yet.

A guidance section's improve run (routine-eval-fire.yml's `guidance-improve`
mode, ADR 0005's routine guidance improve mode addendum) is derived the same
way, keyed by `section` instead of `skill`: its title is `Fire eval routine:
guidance-improve <section>` and an accepted candidate is a
`claude/eval-guidance-improve-<run id>` branch whose `summary.json` names the
section. `KINDS` holds the two shapes; every function below takes `mode`,
`improve` by default, so a caller that names none reads skills only.

`outcomes` is pure: tests pass recorded API rows and `now`. The fetchers
below it make the only network calls (GitHub REST, `GH_TOKEN`), page with a
fixed cap and raise `EvidenceError` with at most a status code, never a
response body.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import urllib.error
import urllib.parse
import urllib.request

FIRE_WORKFLOW = "routine-eval-fire.yml"
#: routine-eval-fire.yml's `run-name`, as GitHub renders it in
#: `display_title`: the mode, ` bare` after it for an eval fire with
#: `arms: bare` (ADR 0012, bare baseline addendum), then the one target input
#: of that mode (fixture, candidate, skill or section), then the model when
#: one was given.
TITLE_RE = re.compile(r"Fire eval routine: (?P<mode>eval|scaffold|improve|guidance-improve)"
                      r"(?P<bare> bare)? "
                      r"(?P<target>[A-Za-z0-9][A-Za-z0-9/_.-]{0,255})"
                      r"(?: (?P<model>[A-Za-z0-9][A-Za-z0-9._-]{0,127}))? ?")
SKILL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
#: A guidance section id the fire workflow accepts (its `section` input).
SECTION_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
RUN_ID_RE = re.compile(r"(?P<stamp>[0-9]{8}T[0-9]{6}Z)-[0-9a-f]{6}")
BRANCH_PREFIX = "claude/eval-improve-"
GUIDANCE_BRANCH_PREFIX = "claude/eval-guidance-improve-"
#: The two improve-run shapes: the record key naming the subject, the
#: routine's branch prefix, the one directory that branch adds to, and the
#: subject's name pattern.
KINDS = {
    "improve": {"key": "skill", "prefix": BRANCH_PREFIX, "root": "eval-improve",
                "name_re": SKILL_RE},
    "guidance-improve": {"key": "section", "prefix": GUIDANCE_BRANCH_PREFIX,
                         "root": "eval-guidance-improve", "name_re": SECTION_RE},
}
#: An improve run that pushed nothing this long after its fire is over: the
#: longest healthy improve run (about 24 agent trials) takes a few hours.
OUTCOME_WINDOW = timedelta(hours=12)
#: The run id is stamped a few seconds into the fire job; allow clock skew
#: between the runner and the API's timestamps.
STAMP_SLACK = timedelta(minutes=2)
MAX_PAGES = 10
SUMMARY_MAX_BYTES = 256 * 1024
OUTCOMES = ("accepted", "rejected", "pending", "fire-failed")


class EvidenceError(Exception):
    """The evidence could not be read. The message is fixed text and, for an
    HTTP failure, a status code: never a response body."""


def parse_time(value) -> datetime | None:
    """An ISO 8601 time with its offset (GitHub's `...Z`), or None."""
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def parse_title(title) -> dict | None:
    """{mode, target, model, bare} of a fire run's title, or None for any
    other title (a run from before the `run-name`, one that does not match,
    or ` bare` after a mode other than eval, which the workflow never
    renders). `bare` is True for an eval fire with `arms: bare`: one
    isolation arm, never a pair (ADR 0012, bare baseline addendum)."""
    match = TITLE_RE.fullmatch(title) if isinstance(title, str) else None
    if match is None:
        return None
    bare = match["bare"] is not None
    if bare and match["mode"] != "eval":
        return None
    return {"mode": match["mode"], "target": match["target"], "model": match["model"],
            "bare": bare}


def run_id_time(run_id) -> datetime | None:
    match = RUN_ID_RE.fullmatch(run_id) if isinstance(run_id, str) else None
    if match is None:
        return None
    return datetime.strptime(match["stamp"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def improve_fires(runs: list, mode: str = "improve") -> list:
    """The `mode` fire runs among `runs` (the runs API's rows), each as
    {id, <key>, started, updated, conclusion, status}, oldest first, where
    <key> is KINDS[mode]["key"] (`skill`, or `section` for guidance-improve).
    A row with an unreadable time or a target that is not a name of that
    kind is left out."""
    kind = KINDS[mode]
    fires = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        title = parse_title(run.get("display_title"))
        if title is None or title["mode"] != mode or title["model"] \
                or not kind["name_re"].fullmatch(title["target"]):
            continue
        started = parse_time(run.get("run_started_at")) or parse_time(run.get("created_at"))
        updated = parse_time(run.get("updated_at")) or started
        if started is None or not isinstance(run.get("id"), int):
            continue
        fires.append({"id": run["id"], kind["key"]: title["target"], "started": started,
                      "updated": max(updated, started), "status": run.get("status"),
                      "conclusion": run.get("conclusion")})
    return sorted(fires, key=lambda f: (f["started"], f["id"]))


def charged_fires(runs: list) -> list:
    """Every fire the allowance guard charges an improve run's cost: the
    improve fires and the guidance-improve fires, oldest first. Both run the
    same measure-baseline-and-candidate loop at three trials per fixture."""
    fires = [f for mode in KINDS for f in improve_fires(runs, mode)]
    return sorted(fires, key=lambda f: (f["started"], f["id"]))


def outcomes(runs: list, branches: list, now: datetime, mode: str = "improve") -> list:
    """One record per `mode` fire, oldest first:
    {<key>, fire_run, at, outcome, run_id}, <key> `skill` (or `section` for
    guidance-improve). `branches` are {run_id, <key>} for each of that
    mode's branches (`claude/eval-improve-<run id>`, or
    `claude/eval-guidance-improve-<run id>`), <key> None when its summary
    could not be read. A branch is matched to at most one fire: the fire
    whose span holds its stamp and whose subject agrees (an unreadable one
    matches only when one fire's span holds it)."""
    key = KINDS[mode]["key"]
    fires = improve_fires(runs, mode)
    claimed: dict = {}
    for branch in sorted(branches, key=lambda b: str(b.get("run_id"))):
        stamp = run_id_time(branch.get("run_id"))
        if stamp is None:
            continue
        spans = [f for f in fires if f["conclusion"] == "success"
                 and f["started"] - STAMP_SLACK <= stamp <= f["updated"] + STAMP_SLACK
                 and f["id"] not in claimed]
        skill = branch.get(key)
        matches = [f for f in spans if skill is None or f[key] == skill]
        if len(matches) == 1 or (skill is not None and matches):
            claimed[matches[0]["id"]] = branch["run_id"]
    records = []
    for fire in fires:
        run_id = claimed.get(fire["id"])
        if fire["status"] not in (None, "completed"):
            outcome = "pending"
        elif fire["conclusion"] != "success":
            outcome = "fire-failed"
        elif run_id is not None:
            outcome = "accepted"
        elif now - fire["started"] >= OUTCOME_WINDOW:
            outcome = "rejected"
        else:
            outcome = "pending"
        records.append({key: fire[key], "fire_run": fire["id"],
                        "at": fire["started"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "outcome": outcome, "run_id": run_id})
    return records


# ---------------------------------------------------------------------------
# The evidence, read from GitHub. Nothing above this line touches the network.

def github_get(path: str, token: str, api: str = "https://api.github.com",
               opener=urllib.request.urlopen, raw: bool = False,
               cap: int = 4 * 1024 * 1024):
    """GET `api + path`: the parsed JSON (or the bytes, with `raw`), or
    EvidenceError carrying at most the status code. A 404 is None: a ref or
    file that is not there (or not visible to this token)."""
    accept = "application/vnd.github.raw" if raw else "application/vnd.github+json"
    request = urllib.request.Request(api + path, headers={
        "Authorization": f"Bearer {token}", "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with opener(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            body = response.read(cap + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise EvidenceError(f"the GitHub API answered {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise EvidenceError("the GitHub API could not be reached") from None
    if status != 200:
        raise EvidenceError(f"the GitHub API answered {status}")
    if len(body) > cap:
        raise EvidenceError("the GitHub API answered with an oversize body")
    if raw:
        return body
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise EvidenceError("the GitHub API answered 200 with an unreadable body") from None


def _repo(repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repo or ""):
        raise EvidenceError("repository name is missing or malformed")
    return repo


def fetch_fire_runs(repo: str, token: str, since: datetime, get=github_get) -> list:
    """Every run of routine-eval-fire.yml created at or after `since`, any
    status, up to MAX_PAGES pages of 100 (more is an error, fail closed)."""
    rows = []
    for page in range(1, MAX_PAGES + 1):
        query = urllib.parse.urlencode({
            "created": ">=" + since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "workflow_dispatch", "per_page": "100", "page": str(page)})
        doc = get(f"/repos/{_repo(repo)}/actions/workflows/{FIRE_WORKFLOW}/runs?{query}", token)
        found = doc.get("workflow_runs") if isinstance(doc, dict) else None
        if not isinstance(found, list):
            raise EvidenceError("the workflow runs API answered without a run list")
        rows.extend(found)
        if len(found) < 100:
            return rows
    raise EvidenceError(f"more than {MAX_PAGES * 100} fire runs in the window")


def fetch_improve_branches(repo: str, token: str, get=github_get,
                           mode: str = "improve") -> list:
    """{run_id, <key>} for each of `mode`'s branches
    (`claude/eval-improve-<run id>`, or `claude/eval-guidance-improve-<run
    id>`). The branch is untrusted (a model run wrote it): only its
    summary's `skill` (or `section`) is read, as data, and only when it is a
    name of that kind; otherwise None."""
    kind = KINDS[mode]
    prefix, key = kind["prefix"], kind["key"]
    refs = get(f"/repos/{_repo(repo)}/git/matching-refs/heads/{prefix}", token) or []
    if not isinstance(refs, list):
        raise EvidenceError("the refs API answered without a ref list")
    branches = []
    for ref in refs:
        name = ref.get("ref") if isinstance(ref, dict) else None
        run_id = name[len("refs/heads/" + prefix):] if isinstance(name, str) \
            and name.startswith("refs/heads/" + prefix) else None
        if run_id_time(run_id) is None:
            continue
        query = urllib.parse.urlencode({"ref": prefix + run_id})
        body = get(f"/repos/{repo}/contents/{kind['root']}/{run_id}/summary.json?{query}",
                   token, raw=True, cap=SUMMARY_MAX_BYTES)
        subject = None
        try:
            doc = json.loads(body) if body else None
            value = doc.get(key) if isinstance(doc, dict) else None
            subject = value if isinstance(value, str) and kind["name_re"].fullmatch(value) \
                else None
        except (ValueError, UnicodeDecodeError, RecursionError):
            subject = None
        branches.append({"run_id": run_id, key: subject})
    return branches
