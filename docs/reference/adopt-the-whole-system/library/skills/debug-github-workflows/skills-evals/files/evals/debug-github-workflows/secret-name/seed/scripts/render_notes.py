"""Print the CHANGES.md section for one tag."""

import sys
from pathlib import Path


def section(text, tag):
    lines, keep = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            keep = line[3:].strip() == tag
            continue
        if keep:
            lines.append(line)
    return "\n".join(lines).strip() + "\n"


if __name__ == "__main__":
    print(section(Path("CHANGES.md").read_text(), sys.argv[1]), end="")
