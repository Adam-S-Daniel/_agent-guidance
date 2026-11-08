#!/usr/bin/env python3
"""Fire a routine once for each new feed article whose title says "agent".

Run by .github/workflows/article-watch.yml every hour. It reads an RSS 2.0
feed that the workflow downloaded and a JSON state file of the items already
seen, fires the routine's API trigger for each NEW item whose title contains
"agent" (any case), and writes the state back. The routine's prompt is
"See how much lift you can get out of <article url>"
(docs/routines/article-lift.md).

Rules, each pinned by test/test_article_watch.py:

* No state file means first run: every item now in the feed is recorded as
  seen and nothing fires, so setting this up never fires for old articles.
  `--reseed` does the same on demand.
* An item is seen when its guid OR its link is in the state, so a change of
  guid scheme alone cannot refire an article.
* More than MAX_FIRES_PER_RUN new matching items in one run fires nothing and
  fails: that is a feed or state fault, not a news day. Recover by checking
  the feed, then dispatching the workflow with `reseed`.
* A matching item is recorded as seen only after its fire returned HTTP 200,
  so a failed fire is retried next run.
* A link that is not an anthropic.com https URL is recorded as skipped and
  never fired: the link is the only feed text that reaches the prompt.
* The fire URL must be an https://api.anthropic.com/ URL, so the bearer is
  never sent anywhere else.
* A missing feed URL, fire URL or bearer fails the run with a message naming
  the variable or secret to set. The state is still written first, so items
  that do not match are not re-examined and matching ones fire once it is set.
* The bearer travels only in a request header; no response body is printed.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable

KEYWORD = "agent"
MAX_FIRES_PER_RUN = 3
PROMPT = "See how much lift you can get out of {url}"
LINK_RE = re.compile(r"^https://(www\.)?anthropic\.com/[A-Za-z0-9][A-Za-z0-9/_.-]*$")
FIRE_URL_PREFIX = "https://api.anthropic.com/"
STATE_VERSION = 1

FEED_VAR = "ARTICLE_WATCH_FEED_URL"
FIRE_URL_VAR = "ARTICLE_WATCH_FIRE_URL"
BEARER_SETTING = "ARTICLE_WATCH_FIRE_BEARER"

FireFn = Callable[[str, str, str], "tuple[str, str]"]


def parse_feed(text: str) -> list[dict]:
    """Return the feed's items as {id, title, link}, in feed order."""
    root = ET.fromstring(text)
    items = []
    for item in root.iter("item"):
        title = " ".join((item.findtext("title") or "").split())
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip() or link
        if guid:
            items.append({"id": guid, "title": title, "link": link})
    return items


def matches(title: str) -> bool:
    return KEYWORD in title.casefold()


def fire_http(fire_url: str, bearer: str, text: str) -> tuple[str, str]:
    """POST one fire. Returns (status, session_url); status "200" on success."""
    body = json.dumps({"text": text}).encode()
    request = urllib.request.Request(
        fire_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer}",
            "anthropic-beta": "experimental-cc-routine-2026-04-01",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = str(response.status)
            try:
                session = json.loads(response.read() or b"{}").get("claude_code_session_url") or ""
            except (ValueError, AttributeError):
                session = ""
            return status, session if isinstance(session, str) else ""
    except urllib.error.HTTPError as err:
        return str(err.code), ""
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return f"network error ({type(err).__name__})", ""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path and lines:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def run(feed_text: str, state: dict | None, *, reseed: bool, fire_url: str,
        bearer: str, fire: FireFn, now: str) -> tuple[dict, int, list[str]]:
    """Decide and fire. Returns (new_state, exit_code, log_lines)."""
    log: list[str] = []
    items = parse_feed(feed_text)
    if not items:
        log.append("::error::the feed has no items; state left unchanged")
        return state or {}, 1, log

    if state is None or reseed:
        seen = dict((state or {}).get("items", {}))
        for item in items:
            seen.setdefault(item["id"], {"title": item["title"], "link": item["link"], "seen_at": now, "seeded": True})
        log.append(f"::notice::recorded {len(items)} current feed items as seen; nothing fired")
        new_state = {"version": STATE_VERSION, "items": seen}
        return new_state, _config_check(fire_url, bearer, log), log

    seen = dict(state.get("items", {}))
    seen_links = {v.get("link") for v in seen.values() if v.get("link")}
    new = [i for i in items if i["id"] not in seen and i["link"] not in seen_links]
    hits = [i for i in new if matches(i["title"])]

    if len(hits) > MAX_FIRES_PER_RUN:
        log.append(
            f"::error::{len(hits)} new matching items at once (limit {MAX_FIRES_PER_RUN}); "
            "fired none. Check the feed, then run the workflow by hand with reseed."
        )
        return state, 1, log

    for item in new:
        if not matches(item["title"]):
            seen[item["id"]] = {"title": item["title"], "link": item["link"], "seen_at": now}

    rc = _config_check(fire_url, bearer, log)
    if rc:
        if hits:
            log.append(f"::error::{len(hits)} matching article(s) wait until that is set")
        return {"version": STATE_VERSION, "items": seen}, rc, log

    for item in hits:
        if not LINK_RE.match(item["link"]):
            seen[item["id"]] = {"title": item["title"], "link": item["link"], "seen_at": now,
                                "skipped": "link is not an https anthropic.com URL"}
            log.append(f"::warning::skipped {item['title']!r}: its link is not an https anthropic.com URL")
            continue
        status, session = fire(fire_url, bearer, PROMPT.format(url=item["link"]))
        if status == "200":
            seen[item["id"]] = {"title": item["title"], "link": item["link"], "seen_at": now,
                                "fired_at": now, "session": session}
            log.append(f"::notice::fired for {item['title']!r} ({item['link']}): {session or 'no session URL returned'}")
        else:
            rc = 1
            log.append(f"::error::fire failed for {item['title']!r} (HTTP {status}); it will be retried next run")
    if not hits:
        log.append(f"::notice::no new matching articles ({len(new)} new item(s) seen)")
    return {"version": STATE_VERSION, "items": seen}, rc, log


def _config_check(fire_url: str, bearer: str, log: list[str]) -> int:
    rc = 0
    if not fire_url:
        log.append(f"::error::repository variable {FIRE_URL_VAR} is not set; set it to the routine's API trigger URL")
        rc = 1
    elif not fire_url.startswith(FIRE_URL_PREFIX):
        log.append(f"::error::repository variable {FIRE_URL_VAR} must start with {FIRE_URL_PREFIX}")
        rc = 1
    if not bearer:
        log.append(f"::error::repository secret {BEARER_SETTING} is not set; set it to the routine's API trigger token")
        rc = 1
    return rc


def main(argv: list[str] | None = None, fire: FireFn = fire_http) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--feed", required=True, help="downloaded RSS file")
    parser.add_argument("--state", required=True, help="JSON state file; written back")
    parser.add_argument("--reseed", action="store_true", help="record every current item as seen, fire none")
    args = parser.parse_args(argv)

    with open(args.feed, encoding="utf-8") as fh:
        feed_text = fh.read()
    state = None
    if os.path.exists(args.state):
        with open(args.state, encoding="utf-8") as fh:
            state = json.load(fh)
        if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
            print(f"::error::{args.state} is not a version {STATE_VERSION} state file")
            return 1

    try:
        new_state, rc, log = run(
            feed_text, state, reseed=args.reseed,
            fire_url=os.environ.get("FIRE_URL", "").strip(),
            bearer=os.environ.get("FIRE_BEARER", "").strip(),
            fire=fire, now=_now(),
        )
    except ET.ParseError as err:
        print(f"::error::the feed is not valid XML ({err.__class__.__name__}); state left unchanged")
        return 1

    with open(args.state, "w", encoding="utf-8") as fh:
        json.dump(new_state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    for line in log:
        print(line)
    _summary([line.split("::", 2)[-1] for line in log])
    return rc


if __name__ == "__main__":
    sys.exit(main())
