#!/usr/bin/env node
"use strict";
/**
 * base-md-merge-window.js — is automation allowed to merge this pull request
 * now? (ADR 0039.)
 *
 * Every edit to agents-md/base.md restarts the eval evidence of every
 * guidance section, so the owner batched those merges into one weekly window
 * (2026-11-07). The bounds live in scripts/base-md-merge-window.yml and only
 * there. A pull request is GATED when it changes agents-md/base.md or a file
 * under agents-md/sections/; a gated pull request may be merged, or have
 * auto-merge armed, by automation only inside the window. Any other pull
 * request is never gated. A person may merge any time; this script is for
 * automation that asks first, and it blocks nothing by itself.
 *
 * Usage:
 *   node scripts/base-md-merge-window.js --now <ISO instant> --files <path>...
 *   node scripts/base-md-merge-window.js --now <ISO instant> --files-json <file>
 * `--files-json` reads a JSON array of paths or of GitHub file objects
 * (`filename`, and `previous_filename` for a rename). `--window <file>`
 * names another bounds file (tests). `--now` is required: the clock is never
 * read implicitly, so a caller states the instant it judged.
 *
 * The bounds are wall-clock times in the file's IANA `timezone`, so a zone
 * with daylight saving time moves the window's UTC instants with the clocks
 * (America/New_York 12:00 is 17:00 UTC in EST, 16:00 UTC in EDT). Node's
 * built-in Intl time zone data does the conversion.
 *
 * Exit codes: 0 allowed (not gated, or inside the window); 2 refused (gated,
 * outside the window; stdout names the next window start); 1 bad input.
 */

const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");

const DEFAULT_WINDOW = path.join(__dirname, "base-md-merge-window.yml");
const BASE_MD = "agents-md/base.md";
const SECTIONS_PREFIX = "agents-md/sections/";
const KEYS = ["timezone", "weekday", "start", "end"];
const WEEKDAYS = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 };
const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

function minutes(hhmm) {
  const m = HHMM.exec(hhmm);
  return Number(m[1]) * 60 + Number(m[2]);
}

/** Validate a parsed bounds document. Throws on anything unexpected. */
function checkWindow(doc) {
  if (doc === null || typeof doc !== "object" || Array.isArray(doc)) {
    throw new Error("window file is not a mapping");
  }
  const extra = Object.keys(doc).filter((k) => !KEYS.includes(k));
  const missing = KEYS.filter((k) => !(k in doc));
  if (extra.length || missing.length) {
    throw new Error(`window file keys wrong (extra: ${extra.join(",") || "none"}; missing: ${missing.join(",") || "none"})`);
  }
  if (typeof doc.timezone !== "string" || !validZone(doc.timezone)) {
    throw new Error("timezone must be an IANA zone name such as America/New_York");
  }
  if (!Number.isInteger(doc.weekday) || doc.weekday < 1 || doc.weekday > 7) {
    throw new Error("weekday must be an integer 1 to 7");
  }
  for (const k of ["start", "end"]) {
    if (typeof doc[k] !== "string" || !HHMM.test(doc[k])) throw new Error(`${k} must be a "HH:MM" string`);
  }
  if (minutes(doc.start) >= minutes(doc.end)) throw new Error("end must be later than start");
  return { timezone: doc.timezone, weekday: doc.weekday, start: doc.start, end: doc.end };
}

function validZone(zone) {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

function loadWindow(file = DEFAULT_WINDOW) {
  return checkWindow(YAML.parse(fs.readFileSync(file, "utf8")));
}

/** Wall-clock calendar date, ISO weekday (1 Monday .. 7 Sunday) and minute
 * of the day of the instant `now` in `zone`. */
function zonedParts(now, zone) {
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) throw new Error("now is not a valid instant");
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: zone, hourCycle: "h23", weekday: "short",
    year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric",
  });
  const p = Object.fromEntries(fmt.formatToParts(now).map((x) => [x.type, x.value]));
  return {
    year: Number(p.year), month: Number(p.month), day: Number(p.day),
    weekday: WEEKDAYS[p.weekday], minute: Number(p.hour) * 60 + Number(p.minute),
  };
}

/** The instant whose wall-clock time in `zone` is the given date and time.
 * Two passes of the zone's offset settle it on either side of a DST change;
 * the window's hours are never in a skipped or repeated hour. */
function zonedInstant(year, month, day, hour, minute, zone) {
  const asUtc = Date.UTC(year, month - 1, day, hour, minute);
  const offsetAt = (t) => {
    const p = zonedParts(new Date(t), zone);
    return Date.UTC(p.year, p.month - 1, p.day, Math.floor(p.minute / 60), p.minute % 60) - t;
  };
  const first = asUtc - offsetAt(asUtc);
  return new Date(asUtc - offsetAt(first));
}

/** True from `start` (inclusive) to `end` (exclusive) on the window's day,
 * in the window's zone. */
function inWindow(now, win) {
  const { weekday, minute } = zonedParts(now, win.timezone);
  return weekday === win.weekday && minute >= minutes(win.start) && minute < minutes(win.end);
}

/** The first window start strictly after `now`, as an ISO instant. */
function nextWindowStart(now, win) {
  const today = zonedParts(now, win.timezone);
  const [h, m] = win.start.split(":").map(Number);
  for (let d = 0; d <= 7; d += 1) {
    const cal = new Date(Date.UTC(today.year, today.month - 1, today.day + d));
    const wd = cal.getUTCDay() === 0 ? 7 : cal.getUTCDay();
    if (wd !== win.weekday) continue;
    const start = zonedInstant(cal.getUTCFullYear(), cal.getUTCMonth() + 1, cal.getUTCDate(), h, m, win.timezone);
    if (start.getTime() > now.getTime()) return start.toISOString();
  }
  throw new Error("no window start found");
}

/** True when a changed path is part of the fleet guidance. */
function isGuidancePath(p) {
  return p === BASE_MD || p.startsWith(SECTIONS_PREFIX);
}

function decide({ files, now, window }) {
  const gated = files.some(isGuidancePath);
  const inside = inWindow(now, window);
  return { gated, inWindow: inside, allowed: !gated || inside, nextWindowStart: nextWindowStart(now, window) };
}

/** Paths from a JSON array of strings or GitHub file objects. */
function pathsFromJson(value) {
  if (!Array.isArray(value)) throw new Error("files JSON is not an array");
  const out = [];
  for (const item of value) {
    if (typeof item === "string") out.push(item);
    else if (item && typeof item.filename === "string") {
      out.push(item.filename);
      if (typeof item.previous_filename === "string") out.push(item.previous_filename);
    } else throw new Error("files JSON holds an entry that is neither a path nor a file object");
  }
  return out;
}

function main(argv) {
  let now = null;
  let windowFile = DEFAULT_WINDOW;
  const files = [];
  try {
    for (let i = 0; i < argv.length; i += 1) {
      const a = argv[i];
      if (a === "--now") now = new Date(argv[++i]);
      else if (a === "--window") windowFile = argv[++i];
      else if (a === "--files-json") files.push(...pathsFromJson(JSON.parse(fs.readFileSync(argv[++i], "utf8"))));
      else if (a === "--files") {
        while (i + 1 < argv.length && !argv[i + 1].startsWith("--")) files.push(argv[++i]);
      } else throw new Error(`unknown argument ${a}`);
    }
    if (now === null) throw new Error("--now is required");
    const result = decide({ files, now, window: loadWindow(windowFile) });
    if (result.allowed) {
      console.log(`allowed: ${result.gated ? "inside the merge window" : "not a guidance change"}`);
      return 0;
    }
    console.log(`refused: changes the fleet guidance outside the weekly merge window; next window starts ${result.nextWindowStart}`);
    return 2;
  } catch (err) {
    console.error(`bad input: ${err.message}`);
    return 1;
  }
}

if (require.main === module) process.exit(main(process.argv.slice(2)));

module.exports = { loadWindow, checkWindow, inWindow, nextWindowStart, isGuidancePath, decide, pathsFromJson, main };
