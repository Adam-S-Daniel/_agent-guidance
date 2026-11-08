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
 * Exit codes: 0 allowed (not gated, or inside the window); 2 refused (gated,
 * outside the window; stdout names the next window start); 1 bad input.
 */

const fs = require("node:fs");
const path = require("node:path");
const YAML = require("yaml");

const DEFAULT_WINDOW = path.join(__dirname, "base-md-merge-window.yml");
const BASE_MD = "agents-md/base.md";
const SECTIONS_PREFIX = "agents-md/sections/";
const KEYS = ["weekday", "start", "end"];
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
  if (!Number.isInteger(doc.weekday) || doc.weekday < 1 || doc.weekday > 7) {
    throw new Error("weekday must be an integer 1 to 7");
  }
  for (const k of ["start", "end"]) {
    if (typeof doc[k] !== "string" || !HHMM.test(doc[k])) throw new Error(`${k} must be a "HH:MM" string`);
  }
  if (minutes(doc.start) >= minutes(doc.end)) throw new Error("end must be later than start");
  return { weekday: doc.weekday, start: doc.start, end: doc.end };
}

function loadWindow(file = DEFAULT_WINDOW) {
  return checkWindow(YAML.parse(fs.readFileSync(file, "utf8")));
}

/** ISO weekday (1 Monday .. 7 Sunday) and minute of the day, both UTC. */
function utcParts(now) {
  if (!(now instanceof Date) || Number.isNaN(now.getTime())) throw new Error("now is not a valid instant");
  return { weekday: now.getUTCDay() === 0 ? 7 : now.getUTCDay(), minute: now.getUTCHours() * 60 + now.getUTCMinutes() };
}

/** True from `start` (inclusive) to `end` (exclusive) on the window's day. */
function inWindow(now, win) {
  const { weekday, minute } = utcParts(now);
  return weekday === win.weekday && minute >= minutes(win.start) && minute < minutes(win.end);
}

/** The first window start strictly after `now`, as an ISO instant. */
function nextWindowStart(now, win) {
  utcParts(now);
  const [h, m] = win.start.split(":").map(Number);
  for (let d = 0; d <= 7; d += 1) {
    const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + d, h, m));
    const wd = day.getUTCDay() === 0 ? 7 : day.getUTCDay();
    if (wd === win.weekday && day.getTime() > now.getTime()) return day.toISOString();
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
