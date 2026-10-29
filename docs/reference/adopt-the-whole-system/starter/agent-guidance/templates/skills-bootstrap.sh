#!/usr/bin/env bash
# skills-bootstrap.sh: SessionStart hook for cloud sessions. Installs the skills
# that the attached repos' skills.lock files pin, at the pinned commit, after
# checking each skill's digest, into ~/.claude/skills. Durable machines use the
# plugin marketplace instead. Never fails the session.
set -u
say() {  # JSON-escape the message: it can hold paths
  local m=${1//\\/\\\\}; m=${m//\"/\\\"}; m=$(printf '%s' "$m" | tr '\n\t' '  ')
  printf '{"systemMessage":"skills: %s","hookSpecificOutput":{"hookEventName":"SessionStart","reloadSkills":true}}\n' "$m"; exit 0
}
[ "${CLAUDE_CODE_REMOTE:-}" = true ] || exit 0
command -v python3 >/dev/null || say "DEGRADED - python3 missing"
here=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd) || say "DEGRADED - cannot find the repo"
msg=$(python3 -I - "${CLAUDE_PROJECT_DIR:-$here}" "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" "$here/scripts/skill-digest.py" <<'PY'
import glob, json, os, re, shutil, subprocess, sys, tempfile
project, dest, digester = sys.argv[1:]
ALLOWED_REGISTRY = "<you>/agent-skills"      # the one registry this hook may install from
KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,63}/[a-z0-9][a-z0-9-]{0,63}")
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# A single-repo session reads that repo's lock; a multi-repo session (the
# project directory is the parent of the repos) reads the union of every
# attached repo's lock. Two locks that pin one skill differently install neither.
if os.path.exists(os.path.join(project, ".git")):   # a single repo: its own lock only
    locks = glob.glob(os.path.join(project, "skills.lock"))
else:
    locks = glob.glob(os.path.join(project, "*", "skills.lock"))
wanted, conflicts = {}, set()
for lock in sorted(locks):
    try:
        d = json.load(open(lock))
        reg, ref, skills = d["registry"], d["ref"], d["skills"]
    except Exception:
        print(f"DEGRADED - unreadable {lock}"); sys.exit()
    if reg != ALLOWED_REGISTRY or not SHA.fullmatch(ref):
        print(f"DEGRADED - {lock} names another registry or a ref that is not a commit sha"); sys.exit()
    for key, dig in skills.items():
        if not KEY.fullmatch(key) or not DIGEST.fullmatch(dig):
            print(f"DEGRADED - {lock} has a bad entry"); sys.exit()
        skill = key.split("/")[1]
        if skill in wanted:
            # Same plugin and digest is the same skill, whatever commit pins it.
            if wanted[skill][1:] != (key, dig):
                conflicts.add(skill)
            continue
        wanted[skill] = (ref, key, dig)
os.makedirs(dest, exist_ok=True)
record = os.path.join(dest, ".skills-bootstrap.json")   # what this hook installed last time
try:
    previous = set(json.load(open(record)))
except Exception:
    previous = set()
installed, failed = [], sorted(conflicts)
def extract(repo, ref):
    # Install from git archive, exactly what skill-digest.py lock digested
    # (export-ignore and all), not from a checkout.
    import io, tarfile
    p = subprocess.run(["git", "-C", repo, "archive", "--format=tar", ref, "plugins"], capture_output=True)
    if p.returncode:
        return False
    archive = tarfile.open(fileobj=io.BytesIO(p.stdout))
    try:
        archive.extractall(repo, filter="data")
    except TypeError:  # Python without extraction filters
        archive.extractall(repo)
    return True
with tempfile.TemporaryDirectory() as work:
    for ref in sorted({r for r, _, _ in wanted.values()}):
        repo = os.path.join(work, ref)
        url = f"https://github.com/{ALLOWED_REGISTRY}.git"
        # The pinned commit must be on the registry's main branch: GitHub serves
        # commits from a whole fork network through the parent's URL.
        git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True).returncode == 0
        ok = subprocess.run(["git", "init", "-q", repo]).returncode == 0 and \
             git("fetch", "-q", url, "+refs/heads/main:refs/remotes/registry/main") and \
             git("merge-base", "--is-ancestor", ref, "refs/remotes/registry/main") and \
             extract(repo, ref)
        for skill, (r, key, dig) in sorted(wanted.items()):
            if r != ref or skill in conflicts:
                continue
            plugin = key.split("/")[0]
            src = os.path.join(repo, "plugins", plugin, "skills", skill)
            got = subprocess.run([sys.executable, "-I", digester, "digest", src], capture_output=True, text=True).stdout.strip() if ok else ""
            if got != dig:
                failed.append(skill); continue
            target = os.path.join(dest, skill)
            if os.path.dirname(os.path.realpath(target)) != os.path.realpath(dest):
                failed.append(skill); continue
            if os.path.lexists(target) and skill not in previous:
                failed.append(skill + " (a skill of that name exists and was not installed by this hook)"); continue
            tmp = target + ".new"
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(src, tmp)
            shutil.rmtree(target, ignore_errors=True)
            os.rename(tmp, target)
            installed.append(skill)
# Remove skills this hook installed earlier that no lock pins any more, or
# that two locks now pin differently.
for skill in sorted(previous - (set(wanted) - conflicts)):
    if KEY.fullmatch("x/" + skill):
        shutil.rmtree(os.path.join(dest, skill), ignore_errors=True)
json.dump(sorted(set(installed) | (previous & (set(wanted) - conflicts))), open(record, "w"))
if failed:
    print(f"DEGRADED - installed {len(installed)}; not installed (digest mismatch, fetch failure or conflicting locks): {' '.join(sorted(failed))}")
else:
    print(f"installed {len(installed)} skills from {ALLOWED_REGISTRY}")
PY
) || say "DEGRADED - the installer crashed"
[ -n "$msg" ] && say "$msg"
exit 0
