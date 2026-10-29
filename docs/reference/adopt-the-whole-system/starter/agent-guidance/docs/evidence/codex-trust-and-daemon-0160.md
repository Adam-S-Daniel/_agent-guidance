# Codex project trust and daemon refresh: source evidence

For [issue 181](https://github.com/Adam-S-Daniel/_agent-guidance/issues/181)
and [issue 182](https://github.com/Adam-S-Daniel/_agent-guidance/issues/182),
supporting the addendum to
[ADR 0012](../decisions/0012-codex-gets-the-guidance-as-user-instructions.md).

## Observation boundary (2026-10-04; codex-cli 0.160.0)

Two kinds of evidence appear here, and each section says which it uses:

- **Source reads** of the `rust-v0.160.0` tag, for the sections up to
  "Refresh limits". Nothing in them was executed.
- **Local runs** of `codex-cli 0.160.0` in throwaway profiles, for the two
  experiments at the end. Each uses its own `CODEX_HOME`, holds no
  credentials, and ends in a `401 Unauthorized` from the model endpoint, so
  no model response was ever produced.

```text
$ codex --version
WARNING: proceeding, even though we could not create PATH aliases: Read-only file system (os error 30)
codex-cli 0.160.0

$ git ls-remote https://github.com/openai/codex.git 'refs/tags/rust-v0.160.0' 'refs/tags/rust-v0.160.0^{}'
79b1b666f2e8551f8abbbca34957227f67f3f553 refs/tags/rust-v0.160.0
a956835d020762cb2b570053af06f643a11c0ecc refs/tags/rust-v0.160.0^{}
```

Both commands exited 0 on 2026-10-04. The last line dereferences the annotated
tag to the source commit. Cited files were downloaded from
`https://raw.githubusercontent.com/openai/codex/rust-v0.160.0/<path>` and
read with `sed` or `nl`. Upstream tests were read, not run. The installed CLI
version does not establish a running daemon's version; the daemon experiment
prints that version itself.

The repository's test suite (`test/run-tests.sh`) exercises synthetic hook
and config fixtures. Those tests are not observations of Codex's trust or
daemon behavior.

## Trust gates (source read 2026-10-04; CLI 0.160.0; rust-v0.160.0)

- **Project instructions:**
  [`load_project_instructions`, lines 55–67](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/agents_md.rs#L55-L67)
  constructs the user-instruction result first, then returns it without
  project discovery when `active_project.is_untrusted()` is true.
  [`ProjectConfig`, lines 602–615](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/config_toml.rs#L602-L615)
  defines that predicate as explicit `trust_level = "untrusted"`. Missing
  trust does not trigger this guard. This does not establish whether the
  interactive UI lets an unfamiliar project proceed.
- **Project configuration:**
  [`disabled_reason_for_decision`, lines 1087–1107](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/loader/mod.rs#L1087-L1107)
  gates project-local config, hooks, and exec policies unless trusted. Missing
  trust and explicit untrusted status therefore differ for instruction
  discovery, but both disable this project config layer. The
  [official configuration documentation](https://learn.chatgpt.com/docs/config-file/config-basic#configuration-precedence),
  read on this date, also says untrusted project `.codex/` layers are skipped
  while user and system layers still load.
- **Hook definition trust is another gate:** discovery iterates
  [enabled config layers](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L129-L186),
  whose [iterator excludes disabled layers](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/state.rs#L544-L552).
  A handler also needs to be enabled and satisfy
  [hook trust](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L677-L717);
  [the hash comparison](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L794-L821)
  determines whether a non-managed definition is trusted. Managed policy can
  [restrict discovery to managed hooks](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L108-L145).
  User-layer discovery does not guarantee execution.

**Repository implication, inferred:** the user-level
[registrar](../../scripts/register-codex-hook.sh) selects a checkout's own
`.claude/hooks/fleet-memory.sh` in its command (lines 178–180). Project-layer
gating alone does not remove that user-layer definition. If enabled, trusted,
and allowed by managed policy, it remains eligible for discovery. This is
not a live demonstration that the fleet hook executes in an untrusted
project. The [hash input is normalized configuration](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L766-L792),
so trusting the definition does not establish the trustworthiness of the
checkout script's current contents.

## Prompt debugging (source read 2026-10-04; CLI 0.160.0; rust-v0.160.0)

The CLI's
[`run_debug_prompt_input_command`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/cli/src/main.rs#L1970-L2060)
constructs its own global-instruction provider and calls the in-process
`codex_core::build_prompt_input` with no state database.
[`build_prompt_input_from_session`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/prompt_debug.rs#L83-L110)
captures a step without entering `run_turn`; the
[`SessionStart` call](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/turn.rs#L322-L324)
is in `run_turn`.

Consequently, this command cannot by itself demonstrate SessionStart hook
execution or what an already-running daemon served. Absence of the hook's
emitted `fleet-guidance:` verdict is not a delivery-failure signal for this
command. A managed block in its global instruction fragment demonstrates
what this fresh diagnostic process loaded from disk, not that the current
session's hook just installed it. The trust experiment below uses the command
for exactly that purpose, to read project instructions from disk.

## Refresh limits (source read 2026-10-04; CLI 0.160.0; rust-v0.160.0)

1. The app-server creates a
   [`CodexHomeUserInstructionsProvider`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/message_processor.rs#L334-L357)
   for its thread manager; root threads
   [receive a clone](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/thread_manager.rs#L1800-L1834).
   The provider
   [checks disk on every load](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/codex-home/src/instructions/mod.rs#L40-L109),
   preferring nonempty `AGENTS.override.md` over `AGENTS.md`. When no
   instructions load and warnings exist, it retains the last successful
   value; absence or empty files without warnings clear it.
2. Refresh runs at
   [session initialization](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/session.rs#L1446-L1474)
   and during
   [step-context capture](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/mod.rs#L3841-L3851).
   [`AgentsMdManager::refresh`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/agents_md_manager.rs#L98-L140)
   invokes the provider before deciding whether to reuse a snapshot. The
   upstream [same-turn refresh test](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/tests/suite/agents_md_refresh.rs#L322-L395)
   expects a replacement fragment after a file change between requests. Its
   [failed-refresh test](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/codex-home/src/instructions/tests.rs#L151-L196)
   expects retention until recovery or removal. Neither upstream test was run.
3. `run_turn`
   [captures and records its first context](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/turn.rs#L257-L324)
   before pending SessionStart hooks run, then ordinarily
   [reuses it for the first request](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/turn.rs#L424-L473).
   **Inference:** if a hook rewrites global instructions after that capture,
   the first request can use the prior copy (or none on first install), with
   a later capture eligible to read the replacement. This is core turn
   ordering, not proof of daemon-specific caching. The daemon experiment below
does not exercise this path.
4. This does **not** settle general configuration freshness. The
   [config manager](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/config_manager.rs#L42-L56)
   retains process state, including CLI overrides. Its
   [reload methods](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/config_manager.rs#L189-L258)
   combine refreshed layers with retained session layers. Global AGENTS
   rereads do not establish immediate refresh of every setting, hook
   definition, provider, or project instruction in every loaded thread.

**Source-only conclusion:** this implementation has a disk-refresh path for
global instructions, so daemon staleness of those instructions is not
inherent. Override precedence, failed reads, hook ordering, and retained
configuration need separate consideration.

## Experiment for issue 181: trust, project instructions, and the user hook

Reproduces the trust behavior for
[issue 181](https://github.com/Adam-S-Daniel/_agent-guidance/issues/181) in a
throwaway profile. It takes about three minutes (six `codex exec` runs, each
ending in a `401` after Codex's reconnect attempts) and needs no credentials.
The script unsets the three variables Codex reads for credentials
(`CODEX_API_KEY`, `OPENAI_API_KEY`, `CODEX_ACCESS_TOKEN`, per
[`login/src/auth/manager.rs`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/login/src/auth/manager.rs#L953-L969)),
so it cannot send an authenticated request. It leaves nothing behind and does
not touch `~/.codex`; the subshell keeps `CODEX_HOME` out of your terminal.

```bash
(
unset CODEX_API_KEY OPENAI_API_KEY CODEX_ACCESS_TOKEN   # the three variables Codex reads for credentials
EXP=$(mktemp -d) && export CODEX_HOME="$EXP/home" && mkdir -p "$CODEX_HOME" "$EXP/proj" && cd "$EXP/proj" && git init -q
printf 'GLOBAL_MARKER_181\n' > "$CODEX_HOME/AGENTS.md"
printf 'PROJECT_MARKER_181\n' > AGENTS.md
printf '{"hooks":{"SessionStart":[{"matcher":"startup|resume","hooks":[{"type":"command","command":"touch \\"%s/hook-ran\\"","timeout":30}]}]}}\n' "$EXP" > "$CODEX_HOME/hooks.json"
for level in trusted untrusted none; do
  : > "$CODEX_HOME/config.toml"
  [ "$level" = none ] || printf '[projects."%s"]\ntrust_level = "%s"\n' "$(pwd -P)" "$level" > "$CODEX_HOME/config.toml"
  echo "== project trust: $level"
  codex debug prompt-input 2>/dev/null | grep -o 'GLOBAL_MARKER_181\|PROJECT_MARKER_181' | sort | uniq -c
  for flag in "" --dangerously-bypass-hook-trust; do
    rm -f "$EXP/hook-ran"
    timeout 120 codex exec $flag --skip-git-repo-check OK </dev/null >/dev/null 2>&1 || true
    if [ -f "$EXP/hook-ran" ]; then echo "hook ran      (flag: ${flag:-none})"; else echo "hook did not run (flag: ${flag:-none})"; fi
  done
done
rm -rf "$EXP"
)
```

Output on 2026-10-04 (`codex-cli 0.160.0`, Linux under WSL2). Compare yours
line for line:

```text
== project trust: trusted
      1 GLOBAL_MARKER_181
      1 PROJECT_MARKER_181
hook did not run (flag: none)
hook ran      (flag: --dangerously-bypass-hook-trust)
== project trust: untrusted
      1 GLOBAL_MARKER_181
hook did not run (flag: none)
hook ran      (flag: --dangerously-bypass-hook-trust)
== project trust: none
      1 GLOBAL_MARKER_181
      1 PROJECT_MARKER_181
hook did not run (flag: none)
hook ran      (flag: --dangerously-bypass-hook-trust)
```

What it shows:

- An explicit `trust_level = "untrusted"` drops the project `AGENTS.md`; the
  global `AGENTS.md` still loads. A project with no trust entry still
  supplies its `AGENTS.md`, matching the source read above.
- A user-level `hooks.json` hook did not run without the bypass flag (the
  rows marked `flag: none`) and ran with `--dangerously-bypass-hook-trust`,
  including in an explicitly untrusted project. The experiment never trusted
  the definition, so it does not show that a trusted hook runs; the source
  read above is what ties the no-run result to hook-definition trust.

What it does not show: a hook whose definition was trusted through `/hooks`
(interactive, so not scripted here), the fleet's own
`.claude/hooks/fleet-memory.sh`, or any trust state other than the three
above. The prompt-input command reads disk in a fresh process, so the
`GLOBAL`/`PROJECT` marker lines describe what a new process loads, not what a
long-lived session holds.

## Experiment for issue 182: same daemon, changed instruction file

Reproduces the refresh behavior for
[issue 182](https://github.com/Adam-S-Daniel/_agent-guidance/issues/182)
against the managed background server, in a throwaway profile with no
credentials (it unsets the same three credential variables as the issue 181
script). It starts a daemon under that profile, opens two sessions through
its control socket (a WebSocket on a Unix socket, speaking the app-server
JSON-RPC protocol), rewrites the global `AGENTS.md` between them, and reads
each session's rollout file, which records the instructions the session
loaded. Each session's model request fails with a `401`; the rollout is
written first. It stops the daemon and removes its own files at the end, and
takes about 30 seconds. Requires `python3`.

```bash
(
unset CODEX_API_KEY OPENAI_API_KEY CODEX_ACCESS_TOKEN   # the three variables Codex reads for credentials
EXP=$(mktemp -d) && export CODEX_HOME="$EXP/home" && mkdir -p "$CODEX_HOME" "$EXP/proj"
printf 'MARKER_ONE_182\n' > "$CODEX_HOME/AGENTS.md"
codex app-server daemon start >/dev/null 2>&1
codex app-server daemon version 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print("daemon:", d["status"], d["appServerVersion"])'
cat > "$EXP/probe.py" <<'EOF'
import base64, json, os, socket, struct, sys, time

home, proj, text = sys.argv[1], sys.argv[2], sys.argv[3]
os.chdir(os.path.join(home, "app-server-control"))      # keeps the socket path short
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(30)
sock.connect("app-server-control.sock")
sock.sendall(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
              "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
              % base64.b64encode(os.urandom(16)).decode()).encode())
buf = b""
while b"\r\n\r\n" not in buf:
    buf += sock.recv(4096)
buf = buf.split(b"\r\n\r\n", 1)[1]

def need(n):
    global buf
    while len(buf) < n:
        buf += sock.recv(65536)
    out, buf = buf[:n], buf[n:]
    return out

def send(obj):
    data = json.dumps(obj).encode()
    n = len(data)
    head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
    mask = os.urandom(4)
    sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

def recv():
    message = b""
    while True:
        b0, b1 = need(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", need(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", need(8))[0]
        payload = need(n)
        message += payload
        if b0 & 0x80 and (b0 & 0x0F) in (0, 1):
            return json.loads(message)
        if b0 & 0x0F not in (0, 1):
            message = b""

def call(id_, method, params):
    send({"id": id_, "method": method, "params": params})
    while True:
        reply = recv()
        if reply.get("id") == id_:
            return reply["result"]

call(1, "initialize", {"clientInfo": {"name": "probe", "title": "probe", "version": "0"}})
send({"method": "initialized"})
thread = call(2, "thread/start", {"cwd": proj})["thread"]["id"]
call(3, "turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}]})
time.sleep(10)   # the model request fails with 401; the rollout file is written first
print(thread)
EOF
# The daemon records its own pid files; read those, never search by process name.
pid_of() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pid"])' "$CODEX_HOME/app-server-daemon/$1.pid" 2>/dev/null; }
# Kill a recorded pid only if its command line names this scratch profile.
kill_scratch() { p=$(pid_of "$1"); [ -n "$p" ] && [[ "$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)" == *"$CODEX_HOME/"* ]] && kill "$p"; }
for n in ONE TWO; do
  printf 'MARKER_%s_182\n' "$n" > "$CODEX_HOME/AGENTS.md"
  python3 "$EXP/probe.py" "$CODEX_HOME" "$EXP/proj" OK >/dev/null
  echo "after writing MARKER_${n}_182 (daemon pid $(pid_of daemon)); each rollout lists the markers it contains:"
  i=0
  for f in $(ls "$CODEX_HOME"/sessions/*/*/*/rollout-*.jsonl); do
    i=$((i + 1)); echo "  session $i: $(grep -o 'MARKER_[A-Z]*_182' "$f" | sort -u | tr '\n' ' ')"
  done
done
codex app-server daemon stop >/dev/null 2>&1
kill_scratch daemon; kill_scratch daemon-updater
rm -rf "$EXP"
)
```

Output on 2026-10-04 (`codex-cli 0.160.0`, Linux under WSL2). The pid differs
on every run; it must be the same in both lines:

```text
daemon: running 0.160.0
after writing MARKER_ONE_182 (daemon pid 2552048); each rollout lists the markers it contains:
  session 1: MARKER_ONE_182 
after writing MARKER_TWO_182 (daemon pid 2552048); each rollout lists the markers it contains:
  session 1: MARKER_ONE_182 
  session 2: MARKER_TWO_182 
```

What it shows: a session opened on the same running daemon after the file
changed loaded the new content, and the earlier session's record still holds
the old one. What it does not show: the next step of an already-open session,
a SessionStart hook that rewrites the file during the first turn (the
first-request ordering inferred above), a changed `AGENTS.override.md`, or any
configuration or hook-definition change. Windows-native daemons are also
untested.

Neither experiment closes its issue. Issue 181 still needs the fleet's own
hook checked after `/hooks` trust; issue 182 still needs the open-session and
SessionStart-rewrite cases above.

## Live fleet-hook and open-thread requests (2026-10-08 UTC / 2026-10-07 EDT)

This follow-up supplies the remaining fleet-hook observation for
[issue 181](https://github.com/Adam-S-Daniel/_agent-guidance/issues/181) and
part of the remaining refresh evidence for
[issue 182](https://github.com/Adam-S-Daniel/_agent-guidance/issues/182).
It uses actual HTTP request bodies from one long-lived **stdio app-server**,
not `debug prompt-input` or a managed daemon. The installed CLI reported
`codex-cli 0.161.0`; the running server's `initialize` response independently
reported `disposable-probe/0.161.0`, platform `unix` / `linux`.

### Disposable setup and persisted hook trust

One standalone Git project, initialized without a remote, was exercised with
explicit `trusted` and `untrusted` statuses in two separate threads. Its
[`fleet-memory.sh`](../../.claude/hooks/fleet-memory.sh) was byte-identical
to the tracked script; the existing
[`codex-session-start.sh`](../../.claude/hooks/codex-session-start.sh)
launcher was copied individually too. The adjacent synthetic payload was
exactly 29 bytes: `FLEET_ACTUAL_PAYLOAD_181_NEW` followed by a newline.
The project instructions contained `PROJECT_CANARY_181`; the disposable
global instructions initially contained `GLOBAL_BEFORE_HOOK_182` outside
any managed block.

The [registrar](../../scripts/register-codex-hook.sh) exited 0 using its
documented `CODEX_HOOK_COMMAND` override to run `bash` with the copied
launcher. The invocation shape was:

```text
CODEX_HOOK_COMMAND="bash <project>/.claude/hooks/codex-session-start.sh" bash scripts/register-codex-hook.sh
```

Angle-bracket paths here describe the disposable setup, not a runnable
command or an exact transcript. The default inline `bash -c` registrar
command was **not exercised**: this run prohibited those wrappers. The
existing launcher still selected the Git root's actual fleet hook.

An actual TUI session (`--no-daemon --no-alt-screen`) first selected
**Continue without trusting** at the startup review. A submitted
`PRE_TRUST_CONTROL` turn failed to connect to the configured local provider
and was interrupted through the UI; the global file retained only the old
marker. Then `/hooks` → `SessionStart` → enter displayed a new user-config
hook, matcher `startup|resume`, synchronous execution, and a 30-second
timeout. Pressing `t` changed its state to **Trusted**; the event list showed
one installed and one active hook. Codex persisted this state itself:

```text
[hooks.state."<disposable-CODEX_HOME>/hooks.json:session_start:0:0"]
trusted_hash = "sha256:f3bf1533a7967835fe239a84116cc24320a379000f411ac94646fad1abe84394"
```

The path is sanitized; it was not entered as a literal placeholder. Neither
a trust-bypass flag nor a hand-written trust-store entry was used. The TUI
exited 0.

### Six real requests and the latest instruction fragment

A local HTTP endpoint at `127.0.0.1:4379` saved request JSON and returned
HTTP 400 with a fabricated `invalid_request_error`; it produced no model
response. The disposable provider configuration was:

```toml
model = "local-probe"
model_provider = "probe"
[model_providers.probe]
name = "local credential-free probe"
base_url = "http://127.0.0.1:4379/v1"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
```

The scratch harness launched `codex app-server --stdio`, sent `initialize`
with client name/title `disposable-probe` and version `1`, then `initialized`.
For each trust status it wrote that explicit project status and restored the
old global marker before `thread/start` with the disposable project as
`cwd`. Each `turn/start` supplied `threadId` and a synthetic text input;
the harness waited for `turn/completed` before the next turn. The same
app-server process (PID 3 inside its namespace) served all six requests.

The following indexes are zero-based positions in each actual HTTP request's
`input` array. Every listed item had `type: "message"`.

| Project trust | Turn | Latest user instruction item | Developer item 2 | Project marker in latest instructions |
| --- | --- | --- | --- | --- |
| trusted | 1 | 1: old global marker; no fleet payload | installed verdict | present |
| trusted | 2 | 4: replacement containing installed fleet payload | retained verdict | present |
| trusted | 3 | 6: replacement containing manual new global marker | retained verdict | present |
| untrusted | 1 | 1: old global marker; no fleet payload | installed verdict | absent |
| untrusted | 2 | 4: replacement containing installed fleet payload | retained verdict | absent |
| untrusted | 3 | 6: replacement containing manual new global marker | retained verdict | absent |

Sanitized text excerpts from the trusted thread's real request items:

```text
Turn 1, item 1, role=user, inside <INSTRUCTIONS>:
GLOBAL_BEFORE_HOOK_182
--- project-doc ---
PROJECT_CANARY_181

Turn 1, item 2, role=developer:
fleet-guidance: installed (v5cef0915, 29 bytes) -> ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md

Turn 2, item 4, role=user, inside <INSTRUCTIONS>:
These AGENTS.md instructions replace all previously provided AGENTS.md instructions.
GLOBAL_BEFORE_HOOK_182
<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->
<!-- fleet-guidance-version: 5cef0915 -->
[delivery timestamp omitted]
FLEET_ACTUAL_PAYLOAD_181_NEW
<!-- END FLEET GUIDANCE -->
--- project-doc ---
PROJECT_CANARY_181

Turn 3, item 6, role=user, inside <INSTRUCTIONS>:
These AGENTS.md instructions replace all previously provided AGENTS.md instructions.
GLOBAL_NEXT_TURN_182
--- project-doc ---
PROJECT_CANARY_181
```

Blank lines and the timestamp were condensed in these excerpts. At the first
request capture, the global file already held the fleet payload, although
the initial user instruction item still held the prior content. Nothing
rewrote the file between turns 1 and 2: turn 2 read the actual hook's output.
Before turn 3, the harness replaced the global file with
`GLOBAL_NEXT_TURN_182`. Earlier instruction items remained in request history;
the later replacement message identifies the current fragment. Searching
for a marker anywhere in the request would conflate history with refresh.

### Execution boundary, validation, and remaining blocker

Every runtime and checker ran inside
`unshare --user --map-current-user --pid --fork --mount-proc --`.
Subprocess environments contained only disposable `HOME`, `CODEX_HOME`,
`CLAUDE_CONFIG_DIR`, sentinel `PATH`/log, `GIT_CONFIG_NOSYSTEM=1`,
`GIT_CONFIG_GLOBAL=/dev/null`, and `TERM`; no credentials were read or copied.
A `claude` sentinel first on `PATH` exited 97 if called: its log recorded
zero calls. The local endpoint and app-server were owned by the harness;
closing app-server stdin and waiting yielded exit 0, and exiting the PID
namespace reaped remaining descendants.

The scratch checker parsed actual JSON message roles and TOML trust state:
**exit 0, 22 assertions, six requests, two trust cases**. Removing the turn-2
fleet replacement message from a separate receipt copy made it exit 1 at
`next turn actual fleet payload replacement`; checking the intact receipts
again exited 0 with 22 assertions. The scratch harness and checker are not
committed. Their invocation shape was the namespace prefix above, followed
by `env -i` with those disposable variables and `python3 <scratch-checker>`;
this is an observation record, not a checked-in reproduction script.

**Managed-daemon coverage is blocked.** In the same sanitized namespace,
`codex app-server daemon start` spawned its own child but exited 1 after:

```text
app-server socket directory must be a user-owned directory with mode 0700
failed to connect .../app-server-control.sock: No such file or directory
```

The socket directory's mode 0700 and owner were verified. A shorter profile
with a 105-byte full socket path still failed; path length is not an
established cause. A process-path alias canonicalized back to the original
path, and a private bind-mount attempt failed without modifying the host.
No test or daemon failure was escalated out of its sandbox. No successful
managed-daemon turn was captured, and Windows-native behavior remains
untested.

For [issue 181](https://github.com/Adam-S-Daniel/_agent-guidance/issues/181),
the requested fleet-hook observation after actual `/hooks` trust is now
complete within the stated launcher override. For
[issue 182](https://github.com/Adam-S-Daniel/_agent-guidance/issues/182),
first-context ordering and open-thread global instruction refresh are now
observed in the core stdio app-server, but managed-daemon and Windows-native
coverage remain partial. These observations narrow the consequence in
[ADR 0012](../decisions/0012-codex-gets-the-guidance-as-user-instructions.md);
they do not establish general configuration freshness.
