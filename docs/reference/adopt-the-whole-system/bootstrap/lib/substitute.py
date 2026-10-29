#!/usr/bin/env python3
"""substitute.py: give an exported starter tree your names, in place.

    substitute.py <dir> --owner LOGIN [--owner-two LOGIN] [--name NAME]
                  [--noreply ADDRESS]

The kit's exports name invented owners (export/DECISIONS.md C2, C10, C12) so
that each tree is a working repo whose own tests pass. This replaces every
form of them, in text files and in file and folder names, in one pass (so a
replacement is never replaced again):

  0+example-adopter@users.noreply.github.com  -> ADDRESS (--noreply)
  example-adopter-two / EXAMPLE_ADOPTER_TWO / example_adopter_two
                                              -> the second owner
  example-adopter / EXAMPLE_ADOPTER / example_adopter
                                              -> the owner
  Example Adopter, <your name>                -> NAME (default: the owner)

The lowercase hyphen form takes the login lowercased, because the export
folded every case of the source owner into it and the trees' own tests rely
on one spelling; GitHub logins are case-insensitive. The upper form is the
login uppercased with - and . as _ (the GH_TOKEN_<OWNER> rule sync.sh uses);
the underscore form is the login lowercased with - and . as _ (step ids).

A one-owner fleet passes --owner-two equal to --owner (GitHub logins compare
case-insensitively). Before the pass, every YAML file that names the second
owner is parsed (PyYAML's composer, by node position) and its second-owner
legs are dropped wherever they would otherwise become duplicates of the
owner's: a mapping key naming the second owner beside the same key naming
the owner (`GH_TOKEN_EXAMPLE_ADOPTER_TWO` beside `GH_TOKEN_EXAMPLE_ADOPTER`),
a list item whose `id` names the second owner beside one whose `id` names
the owner (the second owner's token and plan steps), a flow-list element
(`owner: [example-adopter, example-adopter-two]`), and the second owner's
word in a space-separated owner list (`SYNC_OWNERS`). Each file is checked
after the edit: it must load to exactly what dropping those nodes from the
original's data gives. Text inside block scalars (`run: |`) is left to the
pass. This step needs PyYAML; every other use is Python 3 standard library
only.

Refuses, changing nothing, when the tree names the second owner and
--owner-two is not given, or when a login or name is not plain. After the
pass, any form of the invented owner left over is an error. Binary files
(a NUL byte, or not UTF-8) are left alone. Prints each changed path.
"""
import argparse
import copy
import os
import re
import sys

LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
NAME = re.compile(r"[A-Za-z0-9 .'-]{1,80}")
NOREPLY = re.compile(r"[0-9]{1,12}\+[A-Za-z0-9-]{1,39}@users\.noreply\.github\.com")
TWO_FORMS = ("example-adopter-two", "EXAMPLE_ADOPTER_TWO", "example_adopter_two")
LEFTOVER = re.compile(r"(?i)example[-_ ]adopter")


def forms(login):
    low = login.lower()
    return low, re.sub(r"[-.]", "_", login.upper()), re.sub(r"[-.]", "_", low)


def table(owner, two, name, noreply):
    o_low, o_up, o_snake = forms(owner)
    t = {}
    if noreply:
        t["0+example-adopter@users.noreply.github.com"] = noreply
    if two:
        t_low, t_up, t_snake = forms(two)
        t.update({"example-adopter-two": t_low, "EXAMPLE_ADOPTER_TWO": t_up, "example_adopter_two": t_snake})
    t.update({"example-adopter": o_low, "EXAMPLE_ADOPTER": o_up, "example_adopter": o_snake,
              "Example Adopter": name, "<your name>": name})
    return t


ONE_OF = {"example-adopter-two": "example-adopter", "EXAMPLE_ADOPTER_TWO": "EXAMPLE_ADOPTER",
          "example_adopter_two": "example_adopter"}


def _to_one(s):
    for two, one in ONE_OF.items():
        s = s.replace(two, one)
    return s


def _names_two(s):
    return any(f in s for f in TWO_FORMS)


def collapse_yaml(text):
    """Drop the second owner's legs from one YAML text (see the module docstring).

    Returns the new text; raises ValueError when the edit does not give the
    expected data."""
    import yaml
    edits, plan = [], []   # (start, end, replacement); (doc index, path, action, value)

    def line_start(i):
        return text.rfind("\n", 0, i) + 1

    def leaf_end(node):
        while isinstance(node, (yaml.MappingNode, yaml.SequenceNode)) and node.value:
            node = node.value[-1][1] if isinstance(node, yaml.MappingNode) else node.value[-1]
        m = node.end_mark
        if m.column == 0:
            return m.index
        nl = text.find("\n", m.index)
        return len(text) if nl < 0 else nl + 1

    def drop_lines(start_node, end_node):
        start, end = line_start(start_node.start_mark.index), leaf_end(end_node)
        # one blank line goes with the block when blank lines surround it
        if text[end:end + 1] == "\n" and (start == 0 or text[start - 2:start] == "\n\n"):
            end += 1
        edits.append((start, end, ""))

    def scalar_id(item):
        if isinstance(item, yaml.MappingNode):
            for k, v in item.value:
                if isinstance(k, yaml.ScalarNode) and k.value == "id" and isinstance(v, yaml.ScalarNode):
                    return v.value
        return None

    def walk(node, doc, path):
        if isinstance(node, yaml.MappingNode):
            keys = {k.value for k, _ in node.value if isinstance(k, yaml.ScalarNode)}
            for k, v in node.value:
                if (isinstance(k, yaml.ScalarNode) and _names_two(k.value) and _to_one(k.value) in keys
                        and not node.flow_style):
                    drop_lines(k, v)
                    plan.append((doc, path, "del", k.value))
                else:
                    walk(v, doc, path + [k.value if isinstance(k, yaml.ScalarNode) else None])
        elif isinstance(node, yaml.SequenceNode):
            ids = {scalar_id(i) for i in node.value} - {None}
            scalars = {i.value for i in node.value if isinstance(i, yaml.ScalarNode)}
            gone = []
            for n, item in enumerate(node.value):
                sid = scalar_id(item)
                if not node.flow_style and sid and _names_two(sid) and _to_one(sid) in ids:
                    drop_lines(item, item)
                    gone.append(n)
                elif (node.flow_style and isinstance(item, yaml.ScalarNode) and item.value in ONE_OF
                      and ONE_OF[item.value] in scalars):
                    prev = node.value[n - 1] if n else None
                    if prev is None:
                        edits.append((item.start_mark.index, node.value[n + 1].start_mark.index, ""))
                    else:
                        edits.append((prev.end_mark.index, item.end_mark.index, ""))
                    gone.append(n)
                else:
                    walk(item, doc, path + [n])
            for n in reversed(gone):
                plan.append((doc, path, "pop", n))
        elif isinstance(node, yaml.ScalarNode) and node.style in (None, "", '"', "'"):
            words = node.value.split(" ")
            if len(words) > 1 and all(w and not _names_two(w) or w in ONE_OF for w in words) \
                    and any(w in ONE_OF and ONE_OF[w] in words for w in words):
                kept = [w for w in words if not (w in ONE_OF and ONE_OF[w] in words)]
                new = " ".join(kept)
                q = node.style or ""
                edits.append((node.start_mark.index, node.end_mark.index, q + new + q))
                plan.append((doc, path, "set", new))

    docs = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    for d, root in enumerate(docs):
        if root is not None:
            walk(root, d, [])
    if not edits:
        return text
    out = text
    for start, end, new in sorted(edits, reverse=True):
        out = out[:start] + new + out[end:]

    want = [copy.deepcopy(x) for x in yaml.safe_load_all(text)]
    for d, path, action, value in plan:
        if action == "set":
            *head, last = path
            target = want[d]
            for key in head:
                target = target[key]
            target[last] = value
            continue
        target = want[d]
        for key in path:
            target = target[key]
        if action == "del":
            del target[value]
        else:
            target.pop(value)
    got = list(yaml.safe_load_all(out))
    if got != want:
        raise ValueError("the edited YAML does not load to the expected data")
    return out


def collapse_two(root):
    try:
        import yaml  # noqa: F401
    except ImportError:
        sys.exit("ERROR: a one-owner fleet (OWNER_TWO equal to OWNER) needs PyYAML to drop the second owner's "
                 "workflow legs: python3 -m pip install pyyaml")
    for p in walk_files(root):
        if not p.endswith((".yml", ".yaml")):
            continue
        text = text_of(p)
        if not text or not _names_two(text):
            continue
        try:
            new = collapse_yaml(text)
        except ValueError as e:
            sys.exit(f"ERROR: {os.path.relpath(p, root)}: {e}")
        except Exception as e:  # a YAML file the parser refuses: name it, change nothing
            sys.exit(f"ERROR: {os.path.relpath(p, root)} does not parse as YAML ({type(e).__name__})")
        if new != text:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            print(f"collapsed: {os.path.relpath(p, root)}")


def text_of(path):
    data = open(path, "rb").read()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def walk_files(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in sorted(files):
            p = os.path.join(d, f)
            if not os.path.islink(p):
                yield p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--owner-two", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--noreply", default="")
    a = ap.parse_args(argv)
    if not LOGIN.fullmatch(a.owner) or (a.owner_two and not LOGIN.fullmatch(a.owner_two)):
        sys.exit("ERROR: an owner must be a plain GitHub login")
    name = a.name or a.owner
    if not NAME.fullmatch(name):
        sys.exit("ERROR: the name may hold only letters, digits, spaces and . ' -")
    if a.noreply and not NOREPLY.fullmatch(a.noreply):
        sys.exit("ERROR: --noreply must be <id>+<login>@users.noreply.github.com")
    for login in (a.owner, a.owner_two):
        if login and LEFTOVER.search(login):
            sys.exit("ERROR: a login that contains the kit's invented owner would not be told apart from it")

    if not a.owner_two:
        named = []
        for p in walk_files(a.root):
            text = text_of(p)
            if (text and any(f in text for f in TWO_FORMS)) or any(f in p for f in TWO_FORMS):
                named.append(os.path.relpath(p, a.root))
        if named:
            for rel in named[:20]:
                print(f"names the second owner: {rel}", file=sys.stderr)
            sys.exit(f"ERROR: {len(named)} file(s) name the second owner (example-adopter-two); set OWNER_TWO in "
                     "fleet.env to your second account, or to your OWNER for a one-owner fleet")

    if a.owner_two and a.owner_two.lower() == a.owner.lower():
        collapse_two(a.root)

    t = table(a.owner, a.owner_two, name, a.noreply)
    rx = re.compile("|".join(re.escape(k) for k in sorted(t, key=len, reverse=True)))

    def sub(s):
        return rx.sub(lambda m: t[m.group(0)], s)

    # Names first, deepest first, so a renamed folder's children are done.
    for d, dirs, files in os.walk(a.root, topdown=False):
        if os.sep + ".git" in d + os.sep:
            continue
        for n in sorted(dirs + files):
            new = sub(n)
            if new == n:
                continue
            src, dst = os.path.join(d, n), os.path.join(d, new)
            if os.path.lexists(dst):
                sys.exit(f"ERROR: renaming {os.path.relpath(src, a.root)} would replace {os.path.relpath(dst, a.root)}")
            os.rename(src, dst)
            print(f"renamed: {os.path.relpath(src, a.root)} -> {os.path.relpath(dst, a.root)}")

    left = []
    for p in walk_files(a.root):
        text = text_of(p)
        if text is None:
            continue
        new = sub(text)
        if new != text:
            mode = os.stat(p).st_mode
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            os.chmod(p, mode & 0o7777)
            print(f"filled: {os.path.relpath(p, a.root)}")
        if LEFTOVER.search(new):
            left.append(os.path.relpath(p, a.root))
        if not a.noreply and f"0+{a.owner.lower()}@users.noreply.github.com" in new:
            print(f"WARNING: {os.path.relpath(p, a.root)} has the commit identity 0+{a.owner.lower()}@users."
                  "noreply.github.com; pass --noreply with your own (<id>+<login>@users.noreply.github.com)",
                  file=sys.stderr)
    if left:
        for rel in left:
            print(f"left over: {rel}", file=sys.stderr)
        sys.exit(f"ERROR: {len(left)} file(s) still name the invented owner in a form this does not know")
    return 0


if __name__ == "__main__":
    sys.exit(main())
