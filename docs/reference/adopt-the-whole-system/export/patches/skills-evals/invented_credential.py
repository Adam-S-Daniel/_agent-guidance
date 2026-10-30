"""Names: the fixture scan's Basic-auth test credential is an invented one.

`test/run_tests.py` lists credential shapes a public fixture must never
carry, among them an `Authorization: Basic <base64>` header. The source's
base64 decodes to the source owner's first name and a stock password. It
becomes `alex:hunter2` (the kit's invented name), still a valid Basic
credential of the same shape, so the test checks the same thing. The value
is found by decoding, so this file spells neither the name nor its base64.
Runs on the substituted tree.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

REL = "test/run_tests.py"
BASIC = re.compile(r'"Authorization: Basic ([A-Za-z0-9+/]+={0,2})"')
INVENTED = "alex:hunter2"


def apply(root: Path, ctx) -> None:
    path = root / REL
    text = path.read_text(encoding="utf-8")
    hits = []
    for m in BASIC.finditer(text):
        user, _, password = base64.b64decode(m.group(1)).decode("utf-8").partition(":")
        if password == "hunter2" and user != INVENTED.split(":")[0]:
            hits.append(m)
    if len(hits) != 1:
        raise RuntimeError(f"{REL}: expected one Basic credential to replace, found {len(hits)}")
    m = hits[0]
    new = base64.b64encode(INVENTED.encode()).decode()
    text = text[:m.start(1)] + new + text[m.end(1):]
    path.write_text(text, encoding="utf-8")
