# shellcheck shell=bash
#
# bump-pr-claims.sh — every sentence bump-consumer-locks.sh can put into a pull
# request title, a pull request body or a commit message, and the run state
# that makes each one true.
#
# WHY THIS FILE EXISTS, and it is one defect repeated five times rather than a
# taste for structure. A bump PR's whole design is that every line in it is
# checkable, and the way that design fails is always the same: a sentence
# written for one shape of run is left unbranched when a second shape becomes
# reachable, and then asserts something that run never established. Measured
# instances, all on this script: a degraded run's body said each federated
# source "answered that its bundles have not moved" from a generator that had
# no way to ask; the paragraph beside it said `--repin-source` "refuses that
# name outright" about a flag the same generator did not carry; a
# federated-only body's header said a source moved "and nothing else" further
# up the same body than a block saying one moved "too"; a body sent the reader
# to "the ref listed for it below" with nothing listed below; and a body called
# a command "the whole of it" while omitting `--repo`, `--source-repo` and
# `-o`, so it would not run.
#
# Every one of those was found by a reader noticing. So the fix is not five
# more branches — it is making an ungated sentence impossible to write.
#
# THE SHAPE. A CLAIM is a piece of text plus the run state that makes it true:
#
#   claim_text_<id>   renders the text (it may read the run's variables)
#   claim_holds <id>  exits 0 iff THIS run established what that text asserts
#
# `emit <id>` is the only way text reaches an artifact, and it refuses two
# things: a claim with no `claim_holds` arm (the `*)` default), and a claim
# whose arm is false. `glue` is the only other way to add anything, and it
# accepts whitespace alone. So an artifact is, structurally, a sequence of
# claims this run established — and a sentence someone forgets to gate is a
# CONSTRUCTION ERROR that fails the repo and opens no pull request, rather
# than a line a reviewer has to catch.
#
# THE STATE VOCABULARY is derived once, in `claims_state`, and `claim_holds`
# reads nothing else. That is deliberate: the conditions then speak one small
# language a reader can hold in their head, and a new claim has to say which
# of these facts it needs rather than reaching for whatever variable is in
# scope.
#
#   CLAIM_REASON            content | format | federated — what the PRIMARY
#                           half needed. `federated` means it needed nothing
#                           and a source is the only reason the PR exists.
#   CLAIM_PIN_HELD          the primary's own `ref` did not move (reason is
#                           not `content`).
#   CLAIM_FED_ADVANCED      at least one federated pin moves in this diff.
#   CLAIM_SCOPED_AVAILABLE  the generator had `--check-current --only` and
#                           `--repin-source`, so a per-source question could
#                           be PUT at all. False is the degraded run.
#   CLAIM_SOURCES_PRESENT   this lock names at least one `sources` entry.
#   CLAIM_LISTED_SOURCES    how many of those get a line in the body — i.e.
#                           sources other than the lock's own primary
#                           registry, which is never asked about.
#   CLAIM_SELF_NAMED        one `sources` entry names the primary registry.
#
# Sourced, not executed. It defines functions and nothing else, so the test
# suite can render an artifact for a run state without a fleet, a clone or a
# network.

# SCOPED_FLAG_PAIR — the two flags named as the ONE thing a degraded run has
# established the absence of, written once so no sentence can name half of it.
#
# WHY A CONSTANT AND NOT A PHRASE PER SENTENCE. `FED_ADVANCE_AVAILABLE` (and
# `CLAIM_SCOPED_AVAILABLE`, which is assigned straight from it) goes false when
# EITHER probe fails, so what a degraded run knows is that the generator did
# not carry BOTH. Every sentence that reports the shortfall was written as if
# it meant the `--check-current --only` probe alone — the two PR-body claims
# below, the self-named log line, and BOTH of the bumper's degraded per-repo
# annotations, which is the whole set that interpolates this variable now. On
# a generator that HAS `--only` and lacks only `--repin-source` (the shape the
# suite's own strip-scoped-flags.py builds in its `repin-source` mode) each of
# them says something the run did not establish, in a real PR body,
# contradicting the same run's own probe annotation, which named the other
# flag.
#
# Splitting the boolean in two would be the other repair and it is the wrong
# one: the probe block's "BOTH, OR NEITHER" note is a deliberate design — a
# scoped question this script cannot act on is one it does not ask — so the
# pair really is the unit, and the sentences were the half that drifted.
#
# Interpolated rather than re-typed so a needle can be DERIVED from it: the
# cross product reads this variable out of the library and requires it,
# whitespace-normalised, in every degraded sentence that names a scoped flag.
SCOPED_FLAG_PAIR='`--check-current --only <REGISTRY>` and `--repin --repin-source <REGISTRY>@`'

# failed_slice — the piece of a generator report a PR body quotes, read from
# stdin: from the first `FAILED:` headline to the end of the report, capped at
# 20 lines. THE ONE DEFINITION OF THAT PIPELINE, and it is a function rather
# than five copies of a one-liner for a reason that is about the TEST, not
# about repetition. What the cap keeps is a claim the PR body makes, so a test
# has to measure it — and a test that re-types the pipeline measures a
# pipeline nobody runs the moment the library changes its range. Both halves
# have to come from here.
failed_slice() { sed -n '/^FAILED:/,$p' | head -20; }

# lock_summary <file> <registry> — "registry@shortref" for one entry of a lock,
# used to describe in the PR body what moved and what did not. Lives here
# because the body is its only caller.
lock_summary() {
    python3 -c '
import json, sys

with open(sys.argv[1], encoding="utf-8") as handle:
    lock = json.load(handle)
wanted = sys.argv[2]
entries = [lock] + [s for s in (lock.get("sources") or []) if isinstance(s, dict)]
for entry in entries:
    if entry.get("registry") == wanted:
        print("%s@%s" % (entry.get("registry"), str(entry.get("ref", "?"))[:7]))
        break
' "$1" "$2"
}

# ── The machinery ─────────────────────────────────────────────────────────

CLAIM_ERRORS=""
CLAIM_BUF=""
EMITTED_CLAIMS=()
EMITTED_TEXT=()

claim_error() { CLAIM_ERRORS="${CLAIM_ERRORS}${CLAIM_ERRORS:+; }$1"; }

claim_begin() { CLAIM_BUF=""; }
claim_take()  { printf '%s' "$CLAIM_BUF"; }

# emit <id> [args...] — append one established claim to the artifact.
emit() {
    local id="$1"; shift
    if ! declare -F "claim_text_$id" >/dev/null; then
        claim_error "claim '$id' has no text"
        return 0
    fi
    if ! claim_holds "$id" "$@"; then
        claim_error "claim '$id' was emitted by a run that did not establish it"
        return 0
    fi
    local text
    text="$("claim_text_$id" "$@")"
    EMITTED_CLAIMS+=("$id")
    EMITTED_TEXT+=("$text")
    CLAIM_BUF="${CLAIM_BUF}${text}"
}

# glue <whitespace> — the ONLY thing that may go between two claims. A
# paragraph break is not a claim; a sentence is. Refusing anything else is
# what makes "the artifact is exactly its claims" a structural property rather
# than a convention, and it is the check that catches a sentence typed
# straight into the composer.
glue() {
    if [[ -n "${1//[$' \t\n']/}" ]]; then
        claim_error "glue between claims must be whitespace, got '$1'"
        return 0
    fi
    CLAIM_BUF="${CLAIM_BUF}$1"
}

# ── The state vocabulary ──────────────────────────────────────────────────
#
# Derived once from the run's own variables, so every condition below is
# written against one small set of names. `CLAIM_LISTED_SOURCES` is the one
# that is not simply a rename: a `sources` entry naming the lock's own primary
# registry gets no line in the body — the scoped question has two answers
# under one name, so it is never put — and a sentence that points the reader
# at a list has to be conditioned on that list being non-empty rather than on
# the lock having sources at all. That distinction is exactly what sent a
# self-federating lock's body to "the ref listed for it below" with nothing
# listed below.
claims_state() {
    CLAIM_REASON="$repin_reason"
    CLAIM_PIN_HELD=true
    [[ "$repin_reason" == "content" ]] && CLAIM_PIN_HELD=false
    CLAIM_FED_ADVANCED=false
    [[ ${#fed_drifted_regs[@]} -gt 0 ]] && CLAIM_FED_ADVANCED=true
    CLAIM_SCOPED_AVAILABLE=$FED_ADVANCE_AVAILABLE
    CLAIM_SOURCES_PRESENT=false
    [[ ${#source_registries[@]} -gt 0 ]] && CLAIM_SOURCES_PRESENT=true
    CLAIM_LISTED_SOURCES=0
    CLAIM_SELF_NAMED=false
    local reg
    for reg in ${source_registries[@]+"${source_registries[@]}"}; do
        if [[ "$reg" == "$primary_registry" ]]; then
            CLAIM_SELF_NAMED=true
        else
            CLAIM_LISTED_SOURCES=$((CLAIM_LISTED_SOURCES + 1))
        fi
    done

    # THREE COMBINATIONS THAT CANNOT HAPPEN, refused in the state rather than
    # in each claim that would otherwise have to remember them. Every one is a
    # body that would read as coherent — which is exactly why a per-claim
    # condition misses it: each sentence is true of the state it was given,
    # and the state itself is the thing that cannot occur.
    #
    #   * A pin advanced with no flag to advance it. `fed_drifted_regs` is
    #     filled only by the scoped loop, which does not run without
    #     `--check-current --only`; a body built from that pair would mark a
    #     source **advanced** and label it "not asked" in the same list.
    #   * `federated` as a re-pin reason MEANS a source moved and the primary
    #     did not, so it is the one reason that is not free of the other axis.
    #     Without an advance it names a PR that has no reason to exist.
    #   * A reason this vocabulary does not have. The unanswerable primary is
    #     the live case: that run reports why and re-pins nothing, and if it
    #     ever reached here every reason-keyed sentence would be false at once.
    if $CLAIM_FED_ADVANCED && ! $CLAIM_SCOPED_AVAILABLE; then
        claim_error "a federated pin advanced on a run whose generator has no --repin-source"
    fi
    if [[ "$CLAIM_REASON" == federated ]] && ! $CLAIM_FED_ADVANCED; then
        claim_error "the re-pin reason is 'federated' but no federated pin moved"
    fi
    case "$CLAIM_REASON" in
        content|format|federated) ;;
        *) claim_error "'$CLAIM_REASON' is not a re-pin reason any sentence here is written for" ;;
    esac
}

claim_drifted() {   # <registry> — did this run's scoped question say it moved?
    local reg
    for reg in ${fed_drifted_regs[@]+"${fed_drifted_regs[@]}"}; do
        [[ "$reg" == "$1" ]] && return 0
    done
    return 1
}

# ── What each claim needs the run to have established ─────────────────────
#
# One arm per claim, and NO default-true: an id with no arm is a construction
# error, which is what makes adding a sentence without saying when it is true
# fail loudly instead of shipping.
claim_holds() {
    local id="$1"; shift
    case "$id" in
        # Skeleton. True of every bump PR there is: what opened it, that it
        # merges itself, and that its diff is the generator's output.
        body_opening|body_self_merge|body_generated_lead|body_generated_tail) return 0 ;;

        # Titles and commit subjects — one per (reason, federated) cell.
        title_format_fed|moved_format_fed|repro_shape_half)
            [[ "$CLAIM_REASON" == format ]] && $CLAIM_FED_ADVANCED ;;
        title_format|moved_format|repro_whole)
            [[ "$CLAIM_REASON" == format ]] && ! $CLAIM_FED_ADVANCED ;;
        title_federated|moved_federated|why_federated|commit_subject_federated|commit_body_federated)
            [[ "$CLAIM_REASON" == federated ]] ;;
        title_content_fed|moved_content_fed|why_content_scoped|commit_body_content_with_source)
            [[ "$CLAIM_REASON" == content ]] && $CLAIM_FED_ADVANCED ;;
        title_content|moved_content|why_content_plain|commit_body_content_only_ref)
            [[ "$CLAIM_REASON" == content ]] && ! $CLAIM_FED_ADVANCED ;;
        commit_subject_format|commit_body_format|why_format|why_format_tail)
            [[ "$CLAIM_REASON" == format ]] ;;
        commit_subject_content)
            [[ "$CLAIM_REASON" == content ]] ;;

        # The federated half of an artifact whose PRIMARY half had its own
        # reason. Said as an addition ("too"), which is only true when
        # something else moved as well.
        commit_subject_fed_clause|commit_body_fed_clause|why_fed_evidence_also)
            $CLAIM_FED_ADVANCED && [[ "$CLAIM_REASON" != federated ]] ;;
        why_fed_evidence_only)
            $CLAIM_FED_ADVANCED && [[ "$CLAIM_REASON" == federated ]] ;;
        why_fed_one_question)
            $CLAIM_FED_ADVANCED ;;

        # Where the digests came from. On the federation axis first: a lock
        # with sources holds digests published in more than one repository, so
        # no single commit is where "every digest here" came from.
        derived_multi_repo)
            [[ $CLAIM_LISTED_SOURCES -gt 0 ]] ;;
        derived_self_named_extra)
            [[ $CLAIM_LISTED_SOURCES -gt 0 ]] && $CLAIM_SELF_NAMED ;;
        derived_self_named_only)
            $CLAIM_SOURCES_PRESENT && [[ $CLAIM_LISTED_SOURCES -eq 0 ]] && $CLAIM_SELF_NAMED ;;
        derived_content)
            ! $CLAIM_SOURCES_PRESENT && [[ "$CLAIM_REASON" == content ]] ;;
        derived_held)
            ! $CLAIM_SOURCES_PRESENT && [[ "$CLAIM_REASON" != content ]] ;;

        # What --repin was and was not told to change.
        generated_merged_advanced)
            $CLAIM_FED_ADVANCED && [[ "$CLAIM_REASON" == content ]] ;;
        generated_merged_held)
            $CLAIM_FED_ADVANCED && [[ "$CLAIM_REASON" != content ]] ;;
        generated_inherits_content)
            ! $CLAIM_FED_ADVANCED && [[ "$CLAIM_REASON" == content ]] ;;
        generated_inherits_held)
            ! $CLAIM_FED_ADVANCED && [[ "$CLAIM_REASON" != content ]] ;;

        # The per-source disclosure. THREE states, not two: a source can be
        # unchanged because it was asked and said so, or carry its pin because
        # nothing could ask. Only the first is a verdict.
        federated_none)
            ! $CLAIM_SOURCES_PRESENT ;;
        federated_none_askable)
            $CLAIM_SOURCES_PRESENT && [[ $CLAIM_LISTED_SOURCES -eq 0 ]] ;;
        federated_degraded)
            [[ $CLAIM_LISTED_SOURCES -gt 0 ]] && ! $CLAIM_SCOPED_AVAILABLE ;;
        federated_advanced)
            [[ $CLAIM_LISTED_SOURCES -gt 0 ]] && $CLAIM_SCOPED_AVAILABLE && $CLAIM_FED_ADVANCED ;;
        federated_unchanged)
            [[ $CLAIM_LISTED_SOURCES -gt 0 ]] && $CLAIM_SCOPED_AVAILABLE && ! $CLAIM_FED_ADVANCED ;;
        federated_line_advanced)
            $CLAIM_SCOPED_AVAILABLE && claim_drifted "$1" ;;
        federated_line_unchanged)
            $CLAIM_SCOPED_AVAILABLE && ! claim_drifted "$1" ;;
        federated_line_not_asked)
            ! $CLAIM_SCOPED_AVAILABLE ;;

        # THE PARAGRAPH THAT SHIPPED THE DEFECT TWICE. It describes what
        # `--check-current --only` and `--repin-source` do with a name that
        # means two things — and a degraded run has neither flag, so on that
        # run it explains a refusal by a generator that could not have made
        # it. Two claims, because the FACT (the entry's pin is carried
        # through) survives the degrade and the REASON does not.
        self_named_scoped)
            $CLAIM_SELF_NAMED && $CLAIM_SCOPED_AVAILABLE ;;
        self_named_degraded)
            $CLAIM_SELF_NAMED && ! $CLAIM_SCOPED_AVAILABLE ;;

        *)  claim_error "claim '$id' has no condition — say when it is true"
            return 1 ;;
    esac
}

# ── The sentences ─────────────────────────────────────────────────────────
#
# One function per claim, and the WHOLE sentence per branch rather than a
# fragment plus a shared tail: a shared tail names a ref, and which ref that
# is differs per branch — the newly pinned commit, the commit already pinned,
# or one ref per source. A shared tail is how the header and the
# why-paragraph came to contradict their own diffs.

# Titles. A PR list shows the title alone, so a federated advance that goes
# unnamed here is a pin moving under a heading that says nothing moved.
claim_text_title_format_fed() { printf '%s' "chore: relabel skills.lock digests as ${LOCK_DIGEST_SHAPE} and advance its federated pin for ${fed_drifted_regs[*]} (primary pin unchanged)"; }
claim_text_title_format()     { printf '%s' "chore: relabel skills.lock digests as ${LOCK_DIGEST_SHAPE} (pin unchanged)"; }
claim_text_title_federated()  { printf '%s' "chore: advance skills.lock's federated pin for ${fed_drifted_regs[*]} (primary pin unchanged)"; }
claim_text_title_content_fed() { printf '%s' "chore: re-pin skills.lock onto ${primary_registry}@${new_ref:0:7} and advance its federated pin for ${fed_drifted_regs[*]}"; }
claim_text_title_content()    { printf '%s' "chore: re-pin skills.lock onto ${primary_registry}@${new_ref:0:7}"; }

# Commit subjects and bodies.
claim_text_commit_subject_format()    { printf '%s' "chore: relabel $LOCK_REL_PATH's digests as ${LOCK_DIGEST_SHAPE}"; }
claim_text_commit_subject_federated() { printf '%s' "chore: advance $LOCK_REL_PATH's federated pin for ${fed_drifted_regs[*]}"; }
claim_text_commit_subject_content()   { printf '%s' "chore: re-pin $LOCK_REL_PATH onto ${primary_registry}@${new_ref:0:7}"; }
claim_text_commit_subject_fed_clause() { printf '%s' " and advance its federated pin for ${fed_drifted_regs[*]}"; }

claim_text_commit_body_format() {
    printf '%s' "The bundle content at ${old_ref:0:7} has NOT moved; what is wrong is the
shape of the digests stored here, which are bare hex. Re-pinned at that same
commit with generate_skills_lock.py --repin --ref, so the primary's pin does not
move and every digest of its own bundles is recomputed from the ref this lock
already attests."
}
claim_text_commit_body_federated() {
    printf '%s' "The primary bundle at ${old_ref:0:7} has NOT moved and its pin is held with
generate_skills_lock.py --repin --ref. What moved is a FEDERATED source, whose own
pin is advanced with --repin-source; that flag merges by registry key into the
inherited sources array, so no source is added, dropped or reordered."
}
# Two, because "re-resolves ONLY the primary ref" stops being true the moment a
# --repin-source rides in the same command.
claim_text_commit_body_content_only_ref() {
    printf '%s' "The bundle content this lock installs has moved since ${old_ref:0:7}, so
nothing added or changed since then reached an ephemeral session. Generated by
generate_skills_lock.py --repin, which inherits this repo's own registry,
bundles and sources and re-resolves only the primary ref."
}
claim_text_commit_body_content_with_source() {
    printf '%s' "The bundle content this lock installs has moved since ${old_ref:0:7}, so
nothing added or changed since then reached an ephemeral session. Generated by
generate_skills_lock.py --repin, which inherits this repo's own registry,
bundles and sources and re-resolves the primary ref."
}
claim_text_commit_body_fed_clause() {
    printf '%s' "A FEDERATED source moved too: ${fed_drifted_regs[*]}. Its pin is advanced with
--repin-source, which merges by registry key into the inherited sources array,
so no source is added, dropped or reordered. That source's digests are
re-derived at its OWN new pin, in its own repository."
}

# The PR body's skeleton.
claim_text_body_opening() {
    printf '%s' "Automated re-pin of this repo's \`$LOCK_REL_PATH\`, opened by
\`scripts/bump-consumer-locks.sh\` in ${BUMPER_SOURCE}."
}
claim_text_body_self_merge() {
    printf '%s' "**This pull request merges itself.** A later run of the same bumper sweeps it
and merges it with a merge commit once every check on it has concluded green —
or straight away where this repo runs no checks at all. It is not waiting for a
reviewer, so review it now if you mean to. It refuses to merge itself if
anything but \`$LOCK_REL_PATH\` appears in the diff: push a second file onto this
branch and it becomes yours. Closing it is not a way to say no — the next run
proposes the same change again. See \`docs/decisions/0006\`."
}
claim_text_body_generated_lead() {
    printf '%s' "**Generated, never hand-edited.** The whole change is
\`generate_skills_lock.py --repin\`'s output."
}
claim_text_body_generated_tail() {
    printf '%s' "Nothing
in \`_agent-guidance\` composes a lock of its own — see its
\`docs/decisions/0005\` and \`docs/decisions/0009\`."
}

# The header a reviewer reads first.
claim_text_moved_format_fed() {
    printf '%s' "**What changed:** the digest SHAPE stored in \`$LOCK_REL_PATH\`, and the
federated pins listed below. \`${primary_registry}\`'s pin stays at \`${old_ref:0:7}\` —
this re-pin is anchored to it with \`--ref\`, so \`ref\` and \`generated_from\` are
untouched and every digest of ITS OWN bundles is the same hex it was, wearing its
label. An advanced source's digests are re-derived at its new pin, so those do
change."
}
claim_text_moved_format() {
    printf '%s' "**What changed:** the digest SHAPE stored in \`$LOCK_REL_PATH\`, and nothing
else. \`${primary_registry}\`'s pin stays at \`${old_ref:0:7}\` — this re-pin is
anchored to it with \`--ref\`, so \`ref\` and \`generated_from\` are untouched and
every digest below is the same hex it was, wearing its label."
}
claim_text_moved_federated() {
    printf '%s' "**What moved:** a FEDERATED source, and nothing else. \`${primary_registry}\`'s
pin stays at \`${old_ref:0:7}\` — this re-pin is anchored to it with \`--ref\` — while
the source pins listed below advance."
}
claim_text_moved_content_fed() {
    printf '%s' "**What moved:** \`${primary_registry}\` —
\`${old_ref:0:7}\` → \`${new_ref:0:7}\`, and the federated pins listed below."
}
claim_text_moved_content() {
    printf '%s' "**What moved:** \`${primary_registry}\` —
\`${old_ref:0:7}\` → \`${new_ref:0:7}\`."
}

# WHY THIS PR EXISTS, in the words of the question that actually forced it.
# Each branch quotes the verdict of the flag that fired, and only that one.
claim_text_why_format() {
    printf '%s' "The bundle content at the pinned ref is UNCHANGED — nothing has diverged, and
this is not a currency bump. What is wrong is the SHAPE of the digests stored
here: they are bare hex where the canonical form is \`${LOCK_DIGEST_SHAPE}\`.
\`--check-format\` reads this file alone and says so:

\`\`\`
$(failed_slice <<< "$format_out" | sed "s#$lock_file#$LOCK_REL_PATH#g")
\`\`\`"
}
# THE COMMAND THE BODY QUOTES, and what it is honest to call it. The
# `--check-format` remediation line carries no `--repin-source`, so on a run
# that advanced a source it is not the whole command that produced this diff.
# The first fix for that said "the whole of it was <command>" and printed a
# command missing `--repo`, `--source-repo` and `-o` — a claim weaker than the
# line it was correcting, because the quoted remediation line does carry those
# placeholders and this one would not run. So the addition is named as an
# addition: what this run put on the end of that line, which is checkable
# against the diff, rather than a whole invocation that is not.
#
# BOTH flags, because there are two and the first cut named one. The
# invocation adds a `--repin-source` per DRIFTED source and a `--source-repo`
# per source the lock NAMES; the second is how the generator is pointed at a
# clone that is not the sibling `../<repo-name>` it would otherwise look for,
# which this script cannot assume its own are. A reader who took the quoted
# line and appended only what the first half named got a command that can
# stop at "no checkout at ..." before it writes. Incomplete rather than false, which
# is a weaker defect than calling half a command the whole of it and costs
# the reader the same afternoon.
#
# "APPENDED" IS SAID OF ONE AND NOT THE OTHER, and the asymmetry is not
# stylistic. The quoted line never carries a `--repin-source` — agentskills'
# `_addressing` builds no such flag — so naming that one as an addition holds
# on every run. It CAN carry a `--source-repo`, but only for a source whose
# default sibling clone is missing: read on ag58-generator (e118b8e),
# `_addressing` skips the flag whenever `source_checkout(repo, source,
# {}).is_dir()`. The fleet workflow checks both registries out as siblings
# under `registries/`, so there the quoted line has none and this run really
# does add it — but that is a property of the machine's layout and not of the
# claim. Saying the run PASSED it is true under either layout, which is the
# only kind of sentence this file is allowed to print.
claim_text_repro_shape_half() {
    printf '%s' "**That remediation line is the SHAPE half of the command this PR ran**,
\`--ref\` included. This run appended \`${repin_source_flags_shown}\` to it, because a
federated source moved too, and it passed \`${repin_source_repo_shown}\` to point the
generator at a clone of each source this lock names; the quoted line alone
reproduces the relabelling and not those pin advances."
}
claim_text_repro_whole() {
    printf '%s' "**That remediation line is the command this PR ran**, \`--ref\` included, so you
can reproduce this diff from it."
}
claim_text_why_format_tail() {
    printf '%s' "It was not always: without \`--ref\` a re-pin
falls through to the registry checkout's HEAD, which would advance the pin here
while the quoted command named the old one — a body that could not reproduce
its own diff, and a shape repair doing a content advance's work across every
consumer in one sweep.

\`--check-current\` cannot see this: it digests the pinned tree and the working
tree afresh and never reads the values stored here, so it reported \`OK\` at
exit 0 on this lock every night while the defect stood. That is why the fix
arrives as a re-pin rather than an edit — \`--repin\` recomputes every digest
from the pinned commit and labels it on the way out, where a hand-edited label
would be an attestation over bytes nobody re-derived. It happens once: after
this lands the shape is right and the anti-churn gate goes back to proposing
nothing until the bundle actually moves. If the bundle later does move, that
is a separate question asked afresh every night and arrives as its own PR,
headed by the move."
}
claim_text_why_federated() {
    printf '%s' "The primary's bundle at the pinned ref is UNCHANGED — this is not a currency
bump for \`${primary_registry}\`. What has moved is a federated source, whose bundles
this lock also installs and whose pin only \`--repin-source\` advances."
}
claim_text_why_content_scoped() {
    printf '%s' "The bundle content at the old ref no longer matches the registry's tree, which
is why this PR exists: a lock is not wrong for being old, but a lock pinned
before a skill changed delivers the older skill to every ephemeral session and
reports \`OK\` while doing it. \`--check-current\`, scoped to the primary, says the two
have diverged:

\`\`\`
$(failed_slice <<< "$check_out" | sed -e "s#$primary_lock#$LOCK_REL_PATH#g" -e "s#$lock_file#$LOCK_REL_PATH#g")
\`\`\`"
}
claim_text_why_content_plain() {
    printf '%s' "The bundle content at the old ref no longer matches the registry's tree, which
is why this PR exists: a lock is not wrong for being old, but a lock pinned
before a skill changed delivers the older skill to every ephemeral session and
reports \`OK\` while doing it. \`--check-current\` says the two have diverged:

\`\`\`
$(failed_slice <<< "$check_out" | sed -e "s#$primary_lock#$LOCK_REL_PATH#g" -e "s#$lock_file#$LOCK_REL_PATH#g")
\`\`\`"
}

# WHAT THE 20-LINE CAP DOES AND DOES NOT GUARANTEE HERE, stated because this
# slice inherits nothing from the unscoped ones above it. `$fed_check_out` is a
# CONCATENATION of one scoped report per drifted source, so it carries no
# primary block at all. What holds: the FIRST block's headline is line 1 of the
# slice and its command, when it has one, is line 2, so the pair a reader needs
# most cannot be split. What does NOT hold: a later block can lose its command
# to the cap — a first source with 17 difference lines puts the second source's
# headline on line 20 and its command on line 21. A block the generator refused
# a command for cannot be orphaned at all, because its answer is inside its
# headline. Measured against agentskills@ag58-generator, which carries the same
# arithmetic beside its own report and names the tests that hold it — and
# states it there as an absolute, about a stream that does have the property.
# Do not carry that wording across to this one.
#
# THE FEDERATED EVIDENCE, appended to whichever primary-side paragraph fired.
# TWO HEADINGS, because "moved too" asserts that something ELSE moved as well
# — true of a content or shape re-pin that carries a source advance, and
# flatly denied by the federated-only header higher up the same body, which
# says a source moved "and nothing else".
#
# AND BOTH QUANTIFIERS ARE RESTRICTED TO THE LIST, for the reason the two
# per-source disclosure headers below already are: a `sources` entry naming
# this lock's own primary registry is never scoped a question, so "once per
# source" over the lock's sources is denied further down the same body by the
# block that says one was not asked. That block and these paragraphs render
# into one artifact on the self-named-plus shape, which is a lock this fleet
# can hold today.
claim_text_why_fed_evidence_also() {
    printf '%s' "**A FEDERATED source moved too, and its pin advances with this PR.** Its bundles
are installed by this lock and only \`--repin-source\` advances its pin.
\`--check-current --only <registry>\` was asked once per source listed below, and
these are the ones that answered:

\`\`\`
$(failed_slice <<< "$fed_check_out" | sed -e "s#$primary_lock#$LOCK_REL_PATH#g" -e "s#$lock_file#$LOCK_REL_PATH#g")
\`\`\`"
}
claim_text_why_fed_evidence_only() {
    printf '%s' "**The scoped question that says so.** \`--check-current --only <registry>\` was
asked once per source listed below, and these are the ones that answered:

\`\`\`
$(failed_slice <<< "$fed_check_out" | sed -e "s#$primary_lock#$LOCK_REL_PATH#g" -e "s#$lock_file#$LOCK_REL_PATH#g")
\`\`\`"
}
claim_text_why_fed_one_question() {
    printf '%s' "**One scoped question per source listed below, never one combined verdict.** A single
\`--check-current\` over the whole lock reports one \`FAILED:\` anchored on the
PRIMARY's ref, so a drift in the primary alone reads as federated drift — and
acting on that would advance every federated pin in the fleet on any night the
registry had a commit. Attribution here is a property of which question was
asked. See \`docs/decisions/0009\`."
}

# WHERE THE DIGESTS CAME FROM. On the federation axis first: a lock with
# sources holds digests published in more than one repository, so no single
# commit is where "every digest here" came from.
claim_text_derived_multi_repo() {
    printf '%s' "**Every digest here is re-derived from a pinned commit**, materialized with
\`git archive\` — never from anyone's working tree — and each from the pin of the
half it belongs to: \`${primary_registry}\`'s own bundles at \`${new_ref:0:7}\`, and each
federated source's at the ref listed for it below, in the repository that
publishes it."
}
# The entry that is not listed below, said where the sentence above would
# otherwise send a reader looking for it.
claim_text_derived_self_named_extra() {
    printf '%s' "The \`sources\` entry naming \`${primary_registry}\` again gets no line in that list
and does not move: its digests come from the pin this lock already carried for
it."
}
# A lock whose ONLY source names its own primary registry has sources and no
# list — the case "each federated source's at the ref listed for it below"
# described with nothing below it.
claim_text_derived_self_named_only() {
    printf '%s' "**Every digest here is re-derived from a pinned commit**, materialized with
\`git archive\` — never from anyone's working tree. This lock's only \`sources\`
entry names \`${primary_registry}\`, its own primary registry: that entry is not
listed below, its pin is carried through exactly as this lock had it, and its
digests come from that pin. Everything else here comes from
\`${primary_registry}\`'s own bundles at \`${new_ref:0:7}\`."
}
claim_text_derived_content() {
    printf '%s' "**Every digest here is re-derived from the newly pinned commit**, materialized
with \`git archive\` — never from anyone's working tree — so the lock describes
bytes that are actually published at \`${new_ref:0:7}\`."
}
claim_text_derived_held() {
    printf '%s' "**Every digest here is re-derived from the commit this lock already pinned**, materialized
with \`git archive\` — never from anyone's working tree — so the lock describes
bytes that are actually published at \`${new_ref:0:7}\`."
}

# WHAT --repin WAS AND WAS NOT TOLD TO CHANGE. "It cannot be told to change any
# of them" was true of `sources` only for as long as inheritance was the one
# thing carrying a source forward; `--repin-source` merges one pin into that
# inherited array, so a run where it fired has to say what actually happened.
claim_text_generated_merged_advanced() {
    printf '%s' "That command inherits this repo's own \`registry\` and \`bundles\` from the lock
already committed here, and it takes \`sources\` from there too: \`--repin-source\`
merged the advanced pins listed above INTO that inherited array by registry key, so
it could not add, drop or reorder a source. \`--source\`, which REPLACES the array
outright, stays refused alongside \`--repin\`. The primary's own \`ref\` advanced to \`${new_ref:0:7}\`."
}
claim_text_generated_merged_held() {
    printf '%s' "That command inherits this repo's own \`registry\` and \`bundles\` from the lock
already committed here, and it takes \`sources\` from there too: \`--repin-source\`
merged the advanced pins listed above INTO that inherited array by registry key, so
it could not add, drop or reorder a source. \`--source\`, which REPLACES the array
outright, stays refused alongside \`--repin\`. The primary's own \`ref\` was held at \`${old_ref:0:7}\` with \`--ref\`."
}
claim_text_generated_inherits_content() {
    printf '%s' "That command inherits this repo's own \`registry\`, \`bundles\` and \`sources\` from
the lock already committed here and re-resolves only \`ref\`; it cannot be told to change any of
them."
}
claim_text_generated_inherits_held() {
    printf '%s' "That command inherits this repo's own \`registry\`, \`bundles\` and \`sources\` from
the lock already committed here and here it was given \`--ref\`, so even \`ref\` is unchanged; it cannot be told to change any of
them."
}

# THE PER-SOURCE DISCLOSURE. Three states, not two: `advanced` and `unchanged`
# are both verdicts a scoped question returned, and `not asked` is what a
# degraded run actually knows — the pin the lock already carried, and that
# nothing here verified it.
claim_text_federated_none() { printf '%s' "This lock has no federated sources."; }
claim_text_federated_none_askable() { printf '%s' "**No federated source in this lock could be asked about.**"; }
claim_text_federated_degraded() {
    printf '%s' "**Federated sources keep their pins, and this run could not ask whether they
should.** The generator this run used has no ${SCOPED_FLAG_PAIR} pair, and this
script puts a scoped question only where it can also advance the pin that answer
names. So no per-source question was put and no pin below was checked against its
own registry — each is carried through exactly as this lock already had it, which
is what \`--repin\` does with a source nothing names. \`--source\` is refused
outright, because that flag REPLACES the inherited \`sources\` array and would
silently de-federate the lock:"
}
# THE QUANTIFIER IS RESTRICTED TO THE LIST, and "listed below" is doing real
# work in both of these. A `sources` entry naming this lock's own primary
# registry is a source that gets no line in that list and no scoped question —
# so an unrestricted "each source was asked" is denied two paragraphs down its
# own body, by the block that says one was not. That is structurally the
# "and nothing else" / "moved too" defect one axis over, and the cross product
# now prohibits it directly.
claim_text_federated_advanced() {
    printf '%s' "**Federated sources advance one at a time, and only when asked.** Each source
listed below was put its OWN \`--check-current --only <registry>\` question, and only
the ones that answered \`FAILED\` were named to \`--repin-source\`. \`--source\` stays
refused outright, because that flag REPLACES the inherited \`sources\` array and
would silently de-federate the lock:"
}
claim_text_federated_unchanged() {
    printf '%s' "**Federated sources keep their pins.** Each one listed below was put its own
\`--check-current --only <registry>\` question and answered that its bundles have
not moved, so none was named to \`--repin-source\`; \`--source\` is refused outright,
because that flag REPLACES the inherited \`sources\` array and would silently
de-federate the lock:"
}
# The OLD pin comes from $lock_file, this run's copy of the lock as it stands
# on the default branch, and the NEW one from the re-pinned working copy. Two
# files, so the arrow cannot show the same value twice.
claim_text_federated_line_advanced() {
    printf '%s' "- \`$(lock_summary "$lock_file" "$1")\` → \`$(lock_summary "$LOCK_REL_PATH" "$1")\` — **advanced**"
}
claim_text_federated_line_unchanged() {
    printf '%s' "- \`$(lock_summary "$LOCK_REL_PATH" "$1")\` — **unchanged**"
}
claim_text_federated_line_not_asked() {
    printf '%s' "- \`$(lock_summary "$LOCK_REL_PATH" "$1")\` — **not asked**"
}
claim_text_self_named_scoped() {
    printf '%s' "**One \`sources\` entry names \`${primary_registry}\`, this lock's own primary
registry.** A \`--check-current --only ${primary_registry}\` has two answers under
that one name, so the question was not put; \`--repin-source\` refuses that name
outright for the same reason. That entry's pin is carried through exactly as this
lock had it. Give the two halves two names if either is meant to move on its own."
}
# The same fact with the reason this run can actually stand behind. "The
# question was not put because the name has two answers" describes a refusal
# nothing here could have made — and "this generator has no flag that could
# scope one", the first repair, is false of the half-capability generator that
# carries `--only` and not `--repin-source`. What is true on every degraded run
# is the absence of the PAIR, so that is what this says.
claim_text_self_named_degraded() {
    printf '%s' "**One \`sources\` entry names \`${primary_registry}\`, this lock's own primary
registry.** No per-source question was put about any source in this run: this
generator has no ${SCOPED_FLAG_PAIR} pair, and this script asks nothing it could
not act on. That entry's pin is carried through exactly as this lock had it. Give
the two halves two names if either is meant to move on its own."
}

# THE LOG'S HALF OF THE SAME FACT, and it lives here for the reason the two
# claims above do. `log()` is not composed by this machinery — it fires from
# the per-repo loop BEFORE that run knows why it is re-pinning, so
# `claims_state` has not run, `CLAIM_REASON` does not exist yet and `emit` is
# not available. What log() is NOT is out of scope: it is the artifact a human
# reads at 3am in a red nightly, which is exactly when a wrong reason costs
# the most, and round 2 filed this line in the same defect as the body's.
#
# So the sentence sits beside the two body claims that say the same thing,
# picks its branch on the SAME condition, and is drawn into the cross
# product's prose — where "a run with no scoped flags never names one" governs
# it as it governs every other sentence a run can print.
self_named_log_line() {   # <scoped-available true|false> <lock path> <registry>
    if [[ "$1" == true ]]; then
        printf '%s' "$2 names $3 as both its primary registry and a federated source; a scoped question under that one name has two answers, so it is not put and that entry's pin is carried through untouched."
    else
        printf '%s' "$2 names $3 as both its primary registry and a federated source; this run's generator has no ${SCOPED_FLAG_PAIR} pair, so no per-source question was put about any source and that entry's pin is carried through untouched."
    fi
}

# THE DEGRADED PER-REPO ANNOTATION'S REMEDY, here for the reason
# self_named_log_line above is: it is a sentence a run PRINTS about the scoped
# flags, and a guard on it has to be able to read it from the same place the
# body's claims are read from. Left in the script, the two arms could only be
# guarded by re-typing them in the test — and a re-typed NEGATIVE needle is
# the shape this whole branch exists to remove: reword the sentence and the
# assertion that forbids it goes quietly green.
#
# TWO ARMS, because "update the checkout" is advice whose result is ZERO
# scoped questions on a lock every one of whose `sources` entries names its
# own primary registry: this script never scopes a question to that name and
# `--repin-source` refuses it, so the limitation is a permanent property of
# the LOCK, not of the generator's age.
degraded_fed_remedy() {   # <count of sources a question could be put about> <primary registry>
    if [[ "$1" -gt 0 ]]; then
        printf '%s' "Update the registry checkout to ask one scoped question per source."
    else
        printf '%s' "No scoped question is available for this lock even with that checkout updated: every 'sources' entry here names $2, its own primary registry, which has two answers under one name. Give the two halves two names if either is meant to move on its own."
    fi
}

# ── The composer ──────────────────────────────────────────────────────────
#
# The ONLY place a bump artifact is built. It reads the run's state, sets
# PR_TITLE, PR_BODY, COMMIT_SUBJECT and COMMIT_BODY, and returns non-zero if
# any claim it emitted was ungated, unregistered or false for this run — a
# CONSTRUCTION ERROR, which the caller treats like any other per-repo failure:
# count it, write nothing, open no pull request. A body that asserts what the
# run did not establish is worse than no body, because it is read and
# believed.
#
# Inputs, all globals the per-repo loop already holds:
#   LOCK_REL_PATH LOCK_DIGEST_SHAPE BUMPER_SOURCE
#   primary_registry old_ref new_ref repin_reason
#   fed_drifted_regs[] source_registries[] FED_ADVANCE_AVAILABLE
#   check_out format_out fed_check_out primary_lock lock_file
#   repin_source_flags_shown repin_source_repo_shown
compose_bump_artifacts() {
    CLAIM_ERRORS=""
    EMITTED_CLAIMS=()
    EMITTED_TEXT=()
    claims_state
    # An impossible state produces no artifact at all: composing one would
    # mean choosing which of several false sentences to print.
    if [[ -n "$CLAIM_ERRORS" ]]; then
        PR_TITLE=""; PR_BODY=""; COMMIT_SUBJECT=""; COMMIT_BODY=""
        return 1
    fi

    local reg

    # ── The title ─────────────────────────────────────────────────────
    claim_begin
    if [[ "$CLAIM_REASON" == format ]] && $CLAIM_FED_ADVANCED; then
        emit title_format_fed
    elif [[ "$CLAIM_REASON" == format ]]; then
        emit title_format
    elif [[ "$CLAIM_REASON" == federated ]]; then
        emit title_federated
    elif $CLAIM_FED_ADVANCED; then
        emit title_content_fed
    else
        emit title_content
    fi
    PR_TITLE="$(claim_take)"

    # ── The commit subject ────────────────────────────────────────────
    claim_begin
    case "$CLAIM_REASON" in
        format)    emit commit_subject_format ;;
        federated) emit commit_subject_federated ;;
        *)         emit commit_subject_content ;;
    esac
    if $CLAIM_FED_ADVANCED && [[ "$CLAIM_REASON" != federated ]]; then
        emit commit_subject_fed_clause
    fi
    COMMIT_SUBJECT="$(claim_take)"

    # ── The commit body ───────────────────────────────────────────────
    claim_begin
    case "$CLAIM_REASON" in
        format)    emit commit_body_format ;;
        federated) emit commit_body_federated ;;
        *)         if $CLAIM_FED_ADVANCED; then
                       emit commit_body_content_with_source
                   else
                       emit commit_body_content_only_ref
                   fi ;;
    esac
    if $CLAIM_FED_ADVANCED && [[ "$CLAIM_REASON" != federated ]]; then
        glue $'\n\n'
        emit commit_body_fed_clause
    fi
    COMMIT_BODY="$(claim_take)"

    # ── The pull request body ─────────────────────────────────────────
    claim_begin
    emit body_opening
    glue $'\n\n'

    if [[ "$CLAIM_REASON" == format ]] && $CLAIM_FED_ADVANCED; then
        emit moved_format_fed
    elif [[ "$CLAIM_REASON" == format ]]; then
        emit moved_format
    elif [[ "$CLAIM_REASON" == federated ]]; then
        emit moved_federated
    elif $CLAIM_FED_ADVANCED; then
        emit moved_content_fed
    else
        emit moved_content
    fi
    glue $'\n\n'
    emit body_self_merge
    glue $'\n\n'

    case "$CLAIM_REASON" in
        format)
            emit why_format
            glue $'\n\n'
            if $CLAIM_FED_ADVANCED; then emit repro_shape_half; else emit repro_whole; fi
            glue ' '
            emit why_format_tail
            ;;
        federated)
            emit why_federated
            ;;
        *)
            if $CLAIM_FED_ADVANCED; then emit why_content_scoped; else emit why_content_plain; fi
            ;;
    esac
    if $CLAIM_FED_ADVANCED; then
        glue $'\n\n'
        if [[ "$CLAIM_REASON" == federated ]]; then
            emit why_fed_evidence_only
        else
            emit why_fed_evidence_also
        fi
        glue $'\n\n'
        emit why_fed_one_question
    fi
    glue $'\n\n'

    if [[ $CLAIM_LISTED_SOURCES -gt 0 ]]; then
        emit derived_multi_repo
        if $CLAIM_SELF_NAMED; then
            glue ' '
            emit derived_self_named_extra
        fi
    elif $CLAIM_SOURCES_PRESENT; then
        emit derived_self_named_only
    elif [[ "$CLAIM_REASON" == content ]]; then
        emit derived_content
    else
        emit derived_held
    fi
    glue $'\n\n'

    if ! $CLAIM_SOURCES_PRESENT; then
        emit federated_none
    elif [[ $CLAIM_LISTED_SOURCES -eq 0 ]]; then
        emit federated_none_askable
        glue $'\n\n'
        if $CLAIM_SCOPED_AVAILABLE; then emit self_named_scoped; else emit self_named_degraded; fi
    else
        if ! $CLAIM_SCOPED_AVAILABLE; then
            emit federated_degraded
        elif $CLAIM_FED_ADVANCED; then
            emit federated_advanced
        else
            emit federated_unchanged
        fi
        for reg in "${source_registries[@]}"; do
            [[ "$reg" == "$primary_registry" ]] && continue
            glue $'\n'
            if ! $CLAIM_SCOPED_AVAILABLE; then
                emit federated_line_not_asked "$reg"
            elif claim_drifted "$reg"; then
                emit federated_line_advanced "$reg"
            else
                emit federated_line_unchanged "$reg"
            fi
        done
        if $CLAIM_SELF_NAMED; then
            glue $'\n\n'
            if $CLAIM_SCOPED_AVAILABLE; then emit self_named_scoped; else emit self_named_degraded; fi
        fi
    fi
    glue $'\n\n'

    emit body_generated_lead
    glue ' '
    if $CLAIM_FED_ADVANCED; then
        if [[ "$CLAIM_REASON" == content ]]; then
            emit generated_merged_advanced
        else
            emit generated_merged_held
        fi
    else
        if [[ "$CLAIM_REASON" == content ]]; then
            emit generated_inherits_content
        else
            emit generated_inherits_held
        fi
    fi
    glue ' '
    emit body_generated_tail
    PR_BODY="$(claim_take)"

    [[ -z "$CLAIM_ERRORS" ]]
}
