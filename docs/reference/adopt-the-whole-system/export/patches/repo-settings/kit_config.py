"""DECISIONS C11 and C12 for repo-settings.

C11: fleet.yml declares only the kit's repos. The source declares the
owner's other repos (personal tools, scratch repos, forks, archived repos);
each element is cut whole with the comment that explains it. The comments
about the list as a whole stay, as history.

C12: the two GitHub App ids fleet.yml grants a ruleset bypass (the guidance
sync App on every default branch, the roster App on skills-evals'
roster/proposal branch) are the source account's. fleet.yml now names each
as "${VARIABLE}"; the engine reads that environment variable when it renders
the ruleset and refuses to render one whose variable is unset or not a
positive integer, so a missing id can never drop or invent a bypass. The
workflow maps the repository variables of the same names, which the kit's
bootstrap sets. The tests use invented ids.

Runs on the substituted tree. After the cuts it checks that every repo
fleet.yml declares is in `kit_repos` (manifests/_common.yml).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


def _cut(text: str, start: str, end: str, where: str) -> str:
    if text.count(start) != 1:
        raise SystemExit(f"kit_config: {where}: expected 1 of {start[:60]!r}, found {text.count(start)}")
    i = text.index(start)
    j = text.find(end, i + len(start))
    if j < 0 or text.count(end) != 1:
        raise SystemExit(f"kit_config: {where}: expected 1 of {end[:60]!r} after the start")
    return text[:i] + text[j:]


def _sub(text: str, pairs, where: str, ids=None) -> str:
    """Replace each old text (exactly once) with its new one.

    `@SYNC_ID@` and `@ROSTER_ID@` in an old text stand for the source's two
    App ids, which `_app_ids` found by shape, so this file spells neither.
    """
    for old, new in pairs:
        for token, value in (ids or {}).items():
            old = old.replace(token, value)
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"kit_config: {where}: expected 1 of {old[:70]!r}, found {n}")
        text = text.replace(old, new)
    return text


FLEET_CUTS = [
    ("  # RETIRED: superseded by example-adopter/acsi-agentskills below.",
     "  # acsi-agentskills: the new public skills registry"),
    ("  - name: example-adopter/claude-memory-map\n",
     "  # --- onboarded 2026-07-21"),
    ("  - name: example-adopter/fastmail-actions\n",
     "  # ai-usage-dashboard: public, onboarded"),
    ("  # ai-usage-dashboard: public, onboarded",
     "  # --- _agent-guidance: ruleset enabled."),
    ("  # Public (read 2026-10-27; listed with",
     "  # --- private: only SHA pinning applies."),
    ("  - name: example-adopter/jc\n",
     "  # Renamed from example-adopter/retired-agentskills-private"),
    ("  - name: example-adopter/4A\n",
     "  # --- this repo, managing itself."),
    ("  # --- scratch/experimental: Actions hardening only, no ruleset ---\n",
     "  # --- example-site-platform + consumers"),
]

FLEET_EDITS = [
    ("  # acsi-agentskills: the new public skills registry replacing agentskills\n  # above. Same policy shape",
     "  # acsi-agentskills: the public skills registry. Same policy shape"),
    ("      # agents-md-sync GitHub App (App id @SYNC_ID@): pushes the managed\n",
     "      # agents-md-sync GitHub App (its App id is the GUIDANCE_SYNC_APP_ID\n"
     "      # variable; the adoption kit's DECISIONS C12): pushes the managed\n"),
    ("        actor_id: @SYNC_ID@\n",
     '        actor_id: "${GUIDANCE_SYNC_APP_ID}"\n'),
    ("      # (App id @ROSTER_ID@). GitHub checks --match-head-commit only when\n",
     "      # (its App id is the ROSTER_WRITER_APP_ID variable). GitHub checks\n"
     "      # --match-head-commit only when\n"),
    ('            - {actor_type: Integration, actor_id: @ROSTER_ID@, bypass_mode: always}\n',
     '            - {actor_type: Integration, actor_id: "${ROSTER_WRITER_APP_ID}", bypass_mode: always}\n'),
]

ENGINE_EDITS = [
    ("import json\nimport re\n", "import json\nimport os\nimport re\n"),
    ('''def _bypass_entries(actors: list | None) -> list[dict]:''',
     '''# An account-specific id (a GitHub App's) is written in fleet.yml as
# "${NAME}" and read from the environment variable NAME when the ruleset is
# rendered (the adoption kit's DECISIONS C12). The workflow maps NAME from the
# repository variable of the same name. Unset, empty or not a positive
# integer is refused: a bypass actor is never dropped or guessed.
_ENV_ID = re.compile(r"\\$\\{([A-Z][A-Z0-9_]*)\\}")


def _actor_id(value) -> int:
    if isinstance(value, str):
        m = _ENV_ID.fullmatch(value.strip())
        if m:
            raw = os.environ.get(m.group(1), "").strip()
            if not raw.isdigit() or int(raw) <= 0:
                raise SystemExit(
                    f"error: bypass_actors actor_id {value} needs the environment "
                    f"variable {m.group(1)} set to the App's numeric id (got "
                    f"{'nothing' if not raw else 'a non-integer value'})"
                )
            return int(raw)
    return int(value)


def _bypass_entries(actors: list | None) -> list[dict]:'''),
    ('''            "actor_id": int(actor["actor_id"]),\n''',
     '''            "actor_id": _actor_id(actor["actor_id"]),\n'''),
]

WORKFLOW_EDITS = [
    ('''permissions:
  contents: read

jobs:''',
     '''permissions:
  contents: read

# Account-specific App ids that fleet.yml names as "${NAME}" (the adoption
# kit's DECISIONS C12): repository variables the kit's bootstrap sets. The
# engine refuses to render a ruleset whose id is unset, so a missing variable
# fails the plan instead of dropping a bypass.
env:
  GUIDANCE_SYNC_APP_ID: ${{ vars.GUIDANCE_SYNC_APP_ID }}
  ROSTER_WRITER_APP_ID: ${{ vars.ROSTER_WRITER_APP_ID }}

jobs:'''),
]

SCHEMA_EDITS = [
    ("  #   actor_id:    numeric id (for Integration, the GitHub App's id)\n",
     "  #   actor_id:    numeric id (for Integration, the GitHub App's id), or\n"
     "  #                \"${NAME}\" to read it from the environment variable NAME\n"
     "  #                when the ruleset is rendered (an id that differs per\n"
     "  #                account; unset or not a positive integer is refused)\n"),
    ('      - {actor_type: Integration, actor_id: @ROSTER_ID@, bypass_mode: always}\n',
     '      - {actor_type: Integration, actor_id: "${ROSTER_WRITER_APP_ID}", bypass_mode: always}\n'),
]


MODULE_ENV = '''

# The adoption kit's DECISIONS C12: fleet.yml names its App ids as
# "${NAME}", read from the environment. These invented ids stand in for an
# account's while this module's tests run.
KIT_APP_IDS = {"GUIDANCE_SYNC_APP_ID": "900001", "ROSTER_WRITER_APP_ID": "900003"}


def setUpModule():
    env = patch.dict(os.environ, KIT_APP_IDS)
    env.start()
    unittest.addModuleCleanup(env.stop)
'''

ENV_ID_TESTS = '''

class EnvAppIdTests(unittest.TestCase):
    """fleet.yml's "${NAME}" actor ids (the adoption kit's DECISIONS C12)."""

    ACTOR = {"actor_type": "Integration", "actor_id": "${EXAMPLE_APP_ID}", "bypass_mode": "always"}

    def test_the_variable_supplies_the_id(self):
        with patch.dict(os.environ, {"EXAMPLE_APP_ID": "123456"}):
            self.assertEqual(rs._bypass_entries([self.ACTOR]),
                             [{"actor_id": 123456, "actor_type": "Integration", "bypass_mode": "always"}])

    def test_an_unset_variable_is_refused(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXAMPLE_APP_ID", None)
            with self.assertRaises(SystemExit) as cm:
                rs._bypass_entries([self.ACTOR])
        self.assertIn("EXAMPLE_APP_ID", str(cm.exception))

    def test_an_empty_or_non_integer_variable_is_refused(self):
        for value in ("", "  ", "12a", "-5", "0"):
            with patch.dict(os.environ, {"EXAMPLE_APP_ID": value}):
                with self.assertRaises(SystemExit, msg=repr(value)):
                    rs._bypass_entries([self.ACTOR])

    def test_a_literal_id_still_works(self):
        actor = dict(self.ACTOR, actor_id=42)
        self.assertEqual(rs._bypass_entries([actor])[0]["actor_id"], 42)

    def test_the_real_fleet_names_both_app_ids_by_variable(self):
        text = (Path(__file__).resolve().parent.parent / "repo-settings" / "fleet.yml").read_text(encoding="utf-8")
        self.assertIn(\'actor_id: "${GUIDANCE_SYNC_APP_ID}"\', text)
        self.assertIn(\'actor_id: "${ROSTER_WRITER_APP_ID}"\', text)
'''

# C11: tests that pin fleet.yml's repo list follow the kit's list. Each
# removal and change is listed in DECISIONS.md (C11, "Tests").
TEST_CUTS = [
    # Lock the entries of repos the kit does not have.
    ("    def test_security_alerts_declares_deletion_only_persistent_ruleset(self):",
     "    def test_agent_guidance_declares_deletion_only_persistent_ruleset(self):"),
    ("    def test_gha_bench_declares_deletion_only_persistent_ruleset(self):",
     "    def test_every_declared_extra_builds(self):"),
]

TEST_EDITS = {
    "tests/test_repo_settings.py": [
        ('        "example-adopter/claude-memory-map": ["test", "agent-checks"],\n', ""),
        ('        "example-adopter/fastmail-actions": ["pester", "agent-checks"],\n        "example-adopter/GHA-bench": ["test"],\n', ""),
        ('        "example-adopter/ai-usage-dashboard": ["node-test", "pester", "agent-checks"],\n', ""),
        ('        "example-adopter/verify-actions-lock": ["test", "self-verify", "agent-checks"],\n        "example-adopter/security-alerts": ["agent-checks", "unit"],\n', ""),
        ('        "example-adopter/retired-agentskills": ["pytest-windows"],\n', ""),
        ('                "example-adopter/_agent-guidance",\n                "example-adopter/GHA-bench",\n                "example-adopter/security-alerts",\n',
         '                "example-adopter/_agent-guidance",\n'),
        ("        self.assertEqual(checked, 22)\n",
         "        self.assertEqual(checked, 8)  # the kit's fleet (DECISIONS C11)\n"),
        ('        self.assertEqual(t["declared"]["extra_rulesets"][0], EXTRA_CFG)\n',
         '        # fleet.yml names the App id by variable (DECISIONS C12); rendered,\n'
         '        # it is the fixture\'s.\n'
         '        self.assertEqual(rs.build_extra_ruleset_body(t["declared"]["extra_rulesets"][0]),\n'
         '                         rs.build_extra_ruleset_body(EXTRA_CFG))\n'),
        ("""        excluded = [
            t for t in self.targets if not any(rs.manages(t, x) for x in rs.SURFACES)
        ]""",
         """        # The kit's fleet excludes no repo (DECISIONS C11), so the test adds
        # one of its own.
        cfg = copy.deepcopy(self.cfg)
        cfg["repos"].append({"name": "example-adopter/excluded-fixture", "manage": []})
        excluded = [
            t for t in rs.resolve_targets(cfg, None) if not any(rs.manages(t, x) for x in rs.SURFACES)
        ]"""),
        ('''    WERE_FALSE = {
        "example-adopter/retired-agentskills", "example-adopter/GHA-bench",
        "example-adopter/OctopusDeploy-Api", "example-adopter/SonosAmpJuicePi",
    }''',
         '''    # None in the kit's fleet (DECISIONS C11 dropped them).
    WERE_FALSE: set = set()'''),
        ('''        self.assertEqual(set(by_value), {("all",), (), ("labels",)})
        self.assertEqual(by_value[()], self.WERE_FALSE)
        self.assertEqual(by_value[("labels",)], self.LABELS_ONLY)
        self.assertEqual(len(by_value[("all",)]), 19)''',
         '''        self.assertEqual(set(by_value), {("all",), ("labels",)})
        self.assertEqual(by_value.get((), set()), self.WERE_FALSE)
        self.assertEqual(by_value[("labels",)], self.LABELS_ONLY)
        self.assertEqual(len(by_value[("all",)]), 6)  # the kit's fleet (DECISIONS C11)'''),
        ("import repo_settings as rs\n\n\nADMIN = ",
         "import repo_settings as rs\n" + MODULE_ENV + "\n\nADMIN = "),
        ("ROSTER_APP_ID = @ROSTER_ID@\n", "ROSTER_APP_ID = 900003  # KIT_APP_IDS above\n"),
    ],
    "tests/test_required_checks.py": [
        ("import repo_settings as rs  # noqa: E402\n",
         "import repo_settings as rs  # noqa: E402\nimport os  # noqa: E402\nfrom unittest.mock import patch  # noqa: E402\n" + MODULE_ENV),
    ],
}


def _app_ids(fleet_text: str) -> dict:
    """The source's two App ids, found by their shape in fleet.yml.

    The guidance sync App's is the id in "agents-md-sync GitHub App (App id N)"
    and its `actor_id`; the roster App's is the id in "(App id N). GitHub
    checks --match-head-commit". Neither number is written in this file.
    """
    found = {}
    for token, rx in (("@SYNC_ID@", r"agents-md-sync GitHub App \(App id ([1-9][0-9]{4,11})\)"),
                      ("@ROSTER_ID@", r"\(App id ([1-9][0-9]{4,11})\)\. GitHub checks --match-head-commit")):
        m = re.findall(rx, fleet_text)
        if len(m) != 1:
            raise SystemExit(f"kit_config: fleet.yml: expected 1 App id after {token}'s comment, found {len(m)}")
        found[token] = m[0]
    return found


def apply(root: Path, ctx) -> None:
    kit = set(ctx["common"]["kit_repos"])
    fleet = root / "repo-settings" / "fleet.yml"
    text = fleet.read_text(encoding="utf-8")
    ids = _app_ids(text)
    for start, end in FLEET_CUTS:
        text = _cut(text, start, end, "fleet.yml")
    text = _sub(text, FLEET_EDITS, "fleet.yml", ids)
    fleet.write_text(text, encoding="utf-8")
    names = [e["name"] for e in yaml.safe_load(text)["repos"]]
    stray = sorted(n for n in names if n.split("/", 1)[1] not in kit)
    if stray:
        raise SystemExit(f"kit_config: fleet.yml still declares repos outside the kit: {stray}")

    tests = root / "tests" / "test_repo_settings.py"
    text = tests.read_text(encoding="utf-8")
    if text.count(ids["@SYNC_ID@"]) == 0:
        raise SystemExit("kit_config: test_repo_settings.py no longer names the sync App id")
    tests.write_text(text.replace(ids["@SYNC_ID@"], "900001") + ENV_ID_TESTS, encoding="utf-8")
    text = tests.read_text(encoding="utf-8")
    for start, end in TEST_CUTS:
        text = _cut(text, start, end, "tests/test_repo_settings.py")
    tests.write_text(text, encoding="utf-8")
    for rel, pairs in TEST_EDITS.items():
        p = root / rel
        p.write_text(_sub(p.read_text(encoding="utf-8"), pairs, rel, ids), encoding="utf-8")

    for rel, pairs in (("scripts/repo_settings.py", ENGINE_EDITS),
                       (".github/workflows/repo-settings.yml", WORKFLOW_EDITS),
                       ("repo-settings/schema.md", SCHEMA_EDITS)):
        p = root / rel
        p.write_text(_sub(p.read_text(encoding="utf-8"), pairs, rel, ids), encoding="utf-8")
