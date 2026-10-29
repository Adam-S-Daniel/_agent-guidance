#!/usr/bin/env python3
"""Build an isolated, deterministic Codex 0.160.0 memory store without credentials."""

import argparse
import hashlib
from pathlib import Path
import sqlite3


# Exact DDL, including whitespace, from the pinned Codex migrations:
# https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/memory_migrations/0001_memories.sql#L1-L35
# https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/memory_migrations/0002_consolidation_progress.sql#L1-L6
MIGRATIONS = (
    (1, "memories", """CREATE TABLE stage1_outputs (
    thread_id TEXT PRIMARY KEY,
    source_updated_at INTEGER NOT NULL,
    raw_memory TEXT NOT NULL,
    rollout_summary TEXT NOT NULL,
    rollout_slug TEXT,
    generated_at INTEGER NOT NULL,
    usage_count INTEGER,
    last_usage INTEGER,
    selected_for_phase2 INTEGER NOT NULL DEFAULT 0,
    selected_for_phase2_source_updated_at INTEGER
);

CREATE INDEX idx_stage1_outputs_source_updated_at
    ON stage1_outputs(source_updated_at DESC, thread_id DESC);

CREATE TABLE jobs (
    kind TEXT NOT NULL,
    job_key TEXT NOT NULL,
    status TEXT NOT NULL,
    worker_id TEXT,
    ownership_token TEXT,
    started_at INTEGER,
    finished_at INTEGER,
    lease_until INTEGER,
    retry_at INTEGER,
    retry_remaining INTEGER NOT NULL,
    last_error TEXT,
    input_watermark INTEGER,
    last_success_watermark INTEGER,
    PRIMARY KEY (kind, job_key)
);

CREATE INDEX idx_jobs_kind_status_retry_lease
    ON jobs(kind, status, retry_at, lease_until);
"""),
    (2, "consolidation progress", """-- A successful consolidation is the readiness boundary for the version experiment.
CREATE TABLE consolidation_progress (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    max_thread_count INTEGER NOT NULL DEFAULT 0
);
INSERT INTO consolidation_progress (singleton) VALUES (1);
"""),
)


def build(home, namespace="memories", seed="empty"):
    home.mkdir(parents=True, exist_ok=True)
    root = home / namespace
    root.mkdir(exist_ok=True)
    database = home / (namespace + "_1.sqlite")
    with sqlite3.connect(database) as connection:
        # SQLx migration bookkeeping. Fixed installed_on avoids wall-clock input;
        # checksums are the real SHA-384 of the exact migration bytes above.
        connection.execute("""CREATE TABLE _sqlx_migrations (
            version BIGINT PRIMARY KEY,
            description TEXT NOT NULL,
            installed_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            success BOOLEAN NOT NULL,
            checksum BLOB NOT NULL,
            execution_time BIGINT NOT NULL
        )""")
        for version, description, ddl in MIGRATIONS:
            connection.executescript(ddl)
            connection.execute(
                "INSERT INTO _sqlx_migrations VALUES (?, ?, ?, ?, ?, ?)",
                (version, description, "2026-10-02 00:00:00", 1,
                 hashlib.sha384(ddl.encode()).digest(), 0),
            )
        if seed == "phase1":
            connection.execute(
                "INSERT INTO stage1_outputs "
                "(thread_id, source_updated_at, raw_memory, rollout_summary, rollout_slug, generated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                # v2 parses raw_memory as None and stores its empty-string default:
                # https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/phase1_output.rs#L44-L47
                # https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/phase1.rs#L243-L251
                ("fixture-thread", 100,
                 "" if namespace == "memories_v2" else "PRIVATE_FIXTURE_RAW_CONTENT",
                 "PRIVATE_FIXTURE_ROLLOUT_CONTENT",
                 "fixture-slug" if namespace == "memories_v2" else None, 101),
            )
    if seed == "ad-hoc":
        note = root / "extensions/ad_hoc/notes/fixture.md"
        note.parent.mkdir(parents=True)
        note.write_text("PRIVATE_FIXTURE_AD_HOC_CONTENT\n", encoding="utf-8")
    if seed == "consolidated":
        (root / "MEMORY.md").write_text(
            "PRIVATE_FIXTURE_PREAMBLE\n# PRIVATE_FIXTURE_HEADING\n"
            "PRIVATE_FIXTURE_PARENT\n## Duplicate\nPRIVATE_FIXTURE_FIRST\n"
            "```md\n# Fenced heading\n```\n"
            "## Duplicate\nPRIVATE_FIXTURE_SECOND\n", encoding="utf-8")
        (root / "memory_summary.md").write_text("# Summary\nPRIVATE_FIXTURE_SUMMARY\n", encoding="utf-8")
        skill = root / "skills/example/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# Skill\nPRIVATE_FIXTURE_SKILL\n", encoding="utf-8")
    if seed == "skill-binary":
        resource = root / "skills/example/resource.bin"
        resource.parent.mkdir(parents=True)
        resource.write_bytes(b"\x00\xffPRIVATE_FIXTURE_BINARY_CONTENT\r\n")
    if seed == "rollout":
        summary = root / "rollout_summaries/fixture-thread.md"
        summary.parent.mkdir(parents=True)
        summary.write_text("PRIVATE_FIXTURE_ROLLOUT_FILE\n", encoding="utf-8")
    return database


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("home", type=Path)
    parser.add_argument("--namespace", choices=("memories", "memories_v2"), default="memories")
    parser.add_argument("--seed", choices=("empty", "phase1", "ad-hoc", "consolidated", "rollout", "skill-binary"), default="empty")
    args = parser.parse_args()
    build(args.home, args.namespace, args.seed)
