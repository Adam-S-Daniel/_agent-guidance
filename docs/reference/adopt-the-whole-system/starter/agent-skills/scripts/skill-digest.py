#!/usr/bin/env python3
"""skill-digest.py: a stable sha256 over one skill directory, and a lock writer.

  skill-digest.py digest <skill-dir>
  skill-digest.py lock <registry-checkout> <owner/repo> <plugin>... > skills.lock

`lock` digests the committed tree at HEAD (via git archive), never the working
tree, so an uncommitted edit or a stray untracked file cannot end up pinned.
"""
import hashlib, io, json, os, re, subprocess, sys, tarfile, tempfile

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

def digest(root):
    if not os.path.isdir(root) or os.path.islink(root):
        raise SystemExit(f"not a skill directory: {root}")
    h, files = hashlib.sha256(), 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]):
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                raise SystemExit(f"refusing symlink in a skill: {path}")
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, "rb") as f:
                h.update(rel.encode() + b"\0" + hashlib.sha256(f.read()).hexdigest().encode() + b"\n")
            files += 1
    if not files:
        raise SystemExit(f"empty skill directory: {root}")
    return "sha256:" + h.hexdigest()

def main(argv):
    if len(argv) >= 2 and argv[0] == "digest":
        print(digest(argv[1]))
    elif len(argv) >= 4 and argv[0] == "lock":
        checkout, registry, plugins = argv[1], argv[2], argv[3:]
        ref = subprocess.check_output(["git", "-C", checkout, "rev-parse", "HEAD"], text=True).strip()
        skills = {}
        with tempfile.TemporaryDirectory() as tmp:
            for plugin in plugins:
                if not NAME.fullmatch(plugin):
                    raise SystemExit(f"bad plugin name: {plugin}")
                tar = subprocess.check_output(["git", "-C", checkout, "archive", "--format=tar", ref, f"plugins/{plugin}/skills"])
                archive = tarfile.open(fileobj=io.BytesIO(tar))
                try:
                    archive.extractall(tmp, filter="data")
                except TypeError:  # Python without extraction filters
                    archive.extractall(tmp)
                base = os.path.join(tmp, "plugins", plugin, "skills")
                for skill in sorted(os.listdir(base)):
                    if not os.path.isdir(os.path.join(base, skill)):
                        continue
                    if not NAME.fullmatch(skill):
                        raise SystemExit(f"skill folder name not allowed (lowercase, digits, hyphens): {plugin}/{skill}")
                    skills[f"{plugin}/{skill}"] = digest(os.path.join(base, skill))
        print(json.dumps({"registry": registry, "ref": ref, "skills": skills}, indent=2))
    else:
        raise SystemExit(__doc__)

if __name__ == "__main__":
    main(sys.argv[1:])
