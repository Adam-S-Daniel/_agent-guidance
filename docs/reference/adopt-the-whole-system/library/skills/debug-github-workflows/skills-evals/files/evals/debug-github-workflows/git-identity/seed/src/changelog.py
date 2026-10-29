"""Group a repository's conventional commit subjects into changelog sections."""

import subprocess

SECTIONS = {"feat": "Features", "fix": "Fixes"}


def subjects(repo):
    """Commit subjects of `repo`, oldest first."""
    result = subprocess.run(["git", "-C", repo, "log", "--reverse", "--format=%s"],
                            check=True, capture_output=True, text=True)
    return result.stdout.splitlines()


def render(lines):
    groups = {title: [] for title in SECTIONS.values()}
    for line in lines:
        prefix, _, rest = line.partition(": ")
        if prefix in SECTIONS and rest:
            groups[SECTIONS[prefix]].append(rest)
    parts = []
    for title, items in groups.items():
        if items:
            parts.append(f"## {title}\n" + "".join(f"- {item}\n" for item in items))
    return "\n".join(parts)
