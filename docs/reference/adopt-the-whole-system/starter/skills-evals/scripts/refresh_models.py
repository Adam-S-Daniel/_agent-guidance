#!/usr/bin/env python3
"""Refresh model AVAILABILITY from the Anthropic Models API.

This is the only network call in the roster feature; `harness/roster.py` is a
pure function over the files this script writes.

Auth is the SAME credential `.github/workflows/eval.yml` already mints — the
short-lived WIF-derived bearer, read from ANTHROPIC_AUTH_TOKEN. No new
credential shape is introduced, and none is created here. A plain
ANTHROPIC_API_KEY is accepted as the local-development path.

Two outputs:

  --out            {"fetched_at", "models": [{id, display_name, created_at,
                   max_input_tokens, max_tokens, capabilities}, ...]} — every
                   Claude model the API returns. Required; a failure here is
                   loud, because a roster computed from a half-read API would
                   retire models that never went anywhere.

  --admin-report   optional, and SOFT. The Admin API usage report needs
                   ANTHROPIC_ADMIN_KEY, which nobody has provisioned yet; when
                   it is absent this prints a `::notice::` naming that exact
                   variable and moves on, per the fleet's credential
                   convention. Never required, never created here.

No model id appears in this file — it writes down whatever the API returns.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
# Fields the roster policy and the explorer read. `created_at` drives the
# cooling-off; the token limits and capabilities are what a future fixture
# would select on.
MODEL_FIELDS = ("id", "display_name", "created_at", "max_input_tokens",
                "max_tokens", "capabilities")
#: Page ceiling. Reaching it raises — see build_models_document.
MAX_PAGES = 20


def _auth_headers() -> dict:
    """Bearer for the WIF/OAuth token, x-api-key for a plain key.

    The exchanged token is an OAuth access token, so it goes on
    `Authorization: Bearer` with the oauth beta header — not on `x-api-key`.
    Converting between the two is a header change, not a value swap.
    """
    headers = {"anthropic-version": ANTHROPIC_VERSION,
               "accept": "application/json"}
    bearer = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if bearer:
        headers["authorization"] = f"Bearer {bearer}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
        return headers
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        headers["x-api-key"] = key
        return headers
    raise RuntimeError(
        "no Anthropic credential in the environment: expected "
        "ANTHROPIC_AUTH_TOKEN (the bearer eval.yml mints) or ANTHROPIC_API_KEY")


def http_json(url: str, headers: dict, timeout: int = 30) -> dict:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def build_models_document(fetch, now: datetime, headers: dict | None = None,
                          page_size: int = 100) -> dict:
    """Page through GET /v1/models and normalize what comes back.

    `fetch(url, headers) -> dict` and `headers` are both injected — the caller
    owns the credential, so the tests hand over a canned payload and an empty
    header map and this function needs no credential to be exercised. Nothing
    here reaches the network on its own.

    Only Claude models are written. The roster ranks by the family word in the
    id, so a non-Claude entry would be unranked noise in a file whose whole
    purpose is to be ranked.
    """
    headers = headers or {}
    models: list[dict] = []
    after: str | None = None
    seen: set[str] = set()
    # Bounded: a server that keeps answering `has_more` with the same cursor
    # would otherwise spin forever inside a step that holds a credential. The
    # catalogue is tens of models, so MAX_PAGES is far past any real answer —
    # and reaching it is a FAILURE, not a stopping condition. This file refuses
    # a half-read API because a partial read is indistinguishable from a model
    # retiring; writing the first 20 pages and calling it the catalogue is
    # exactly that half read. See the `for ... else` below.
    for _ in range(MAX_PAGES):
        params = {"limit": page_size}
        if after:
            params["after_id"] = after
        page = fetch(f"{API_BASE}/v1/models?{urllib.parse.urlencode(params)}", headers)
        entries = page.get("data") or []
        for entry in entries:
            model_id = entry.get("id")
            if not model_id or not str(model_id).startswith("claude") or model_id in seen:
                continue
            seen.add(model_id)
            models.append({field: entry.get(field) for field in MODEL_FIELDS})
        if not page.get("has_more"):
            break
        # `has_more` is true: the API promises another page exists. Any
        # reason we cannot actually reach it — an empty page with nothing to
        # read a cursor from, no `last_id` and no entry carrying an `id`, or
        # a cursor that has not advanced (a stuck cursor, page returns the
        # same last_id() forever) — is the SAME failure as running out of
        # MAX_PAGES: a partial read that is indistinguishable from some
        # models retiring. It must never be a silent `break`.
        next_after = page.get("last_id") or (entries[-1].get("id") if entries else None)
        if not next_after or next_after == after:
            raise RuntimeError(
                "the Models API reports more pages but returned no usable "
                "cursor to continue from; refusing to publish a truncated "
                "catalogue")
        after = next_after
    else:
        raise RuntimeError(
            f"the Models API still reports more pages after {MAX_PAGES} pages; "
            f"refusing to publish a truncated catalogue")

    models.sort(key=lambda m: m["id"])
    return {"fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "models": models}


def fetch_admin_usage_report(now: datetime, fetch, days: int = 30) -> tuple[dict | None, str | None]:
    """The optional Admin API usage report, grouped by model.

    Returns `(document, notice)`. Absent key -> `(None, "::notice::...")`
    naming ANTHROPIC_ADMIN_KEY exactly, so the missing knob is legible in the
    run log instead of showing up as a mystery gap in the roster's provenance.
    A request that fails for any other reason degrades the same way: this input
    is a nice-to-have next to the local census, and it must never fail a run.
    """
    admin_key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not admin_key:
        return None, ("::notice::ANTHROPIC_ADMIN_KEY is not provisioned, so the "
                      "org-wide Admin API usage report was skipped; the roster "
                      "falls back to the local usage census alone. Provision "
                      "ANTHROPIC_ADMIN_KEY to enable it.")
    started = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = [("starting_at", started), ("bucket_width", "1d"),
              ("group_by[]", "model"), ("limit", "31")]
    url = f"{API_BASE}/v1/organizations/usage_report/messages?{urllib.parse.urlencode(params)}"
    headers = {"anthropic-version": ANTHROPIC_VERSION, "x-api-key": admin_key,
               "accept": "application/json"}
    try:
        payload = fetch(url, headers)
    except Exception as exc:  # noqa: BLE001 — optional input, never fatal
        return None, (f"::notice::ANTHROPIC_ADMIN_KEY is set but the Admin API "
                      f"usage report could not be read ({type(exc).__name__}); "
                      f"continuing on the local usage census alone.")
    return {"fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_days": days, "report": payload}, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True,
                        help="where to write the availability document")
    parser.add_argument("--admin-report", type=Path, default=None,
                        help="optional: where to write the Admin API usage report")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    # Loud on purpose: a partial read looks exactly like a retirement. What is
    # PRINTED is a status code or an exception class name and nothing else —
    # never a response body. This runs in a public CI log, and an error body
    # from a billing or auth endpoint is the kind of thing that carries an
    # account id or an org name. The two RuntimeErrors raised here are our own
    # sentences (no credential; truncated catalogue), so they print in full.
    #
    # OSError covers URLError, HTTPError and TimeoutError; ValueError covers
    # JSONDecodeError and UnicodeDecodeError. Naming URLError and
    # JSONDecodeError separately was redundant, and the two it left out —
    # a socket timeout and a mis-encoded body — both exited by traceback.
    try:
        document = build_models_document(http_json, now, _auth_headers())
    except urllib.error.HTTPError as exc:
        print(f"Models API read failed: HTTP {exc.code}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Models API read failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Models API read failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)
        f.write("\n")
    print(f"models: {len(document['models'])} written to {args.out}")

    if args.admin_report:
        report, notice = fetch_admin_usage_report(now, http_json)
        if notice:
            print(notice)
        if report is not None:
            args.admin_report.parent.mkdir(parents=True, exist_ok=True)
            with open(args.admin_report, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
                f.write("\n")
            print(f"admin usage report written to {args.admin_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
