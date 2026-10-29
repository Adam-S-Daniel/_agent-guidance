"""One module per objective check type. Add a type here and in the fixture
schema (harness/fixture.py) together."""
from . import command_succeeds, file_unchanged, yaml_parses
from .common import ScorerError

SCORERS = {
    "yaml_parses": yaml_parses.score,
    "command_succeeds": command_succeeds.score,
    "file_unchanged": file_unchanged.score,
}


def run_check(check: dict, workspace) -> dict:
    """Score one check. Any error becomes passed=False, error=True."""
    try:
        passed, detail = SCORERS[check["type"]](check, workspace)
        return {"id": check["id"], "passed": bool(passed), "error": False, "detail": detail}
    except ScorerError as exc:
        return {"id": check["id"], "passed": False, "error": True, "detail": str(exc)[:200]}
    except Exception as exc:  # a scorer bug must not skip the check
        return {"id": check["id"], "passed": False, "error": True,
                "detail": f"scorer crashed: {type(exc).__name__}"}
