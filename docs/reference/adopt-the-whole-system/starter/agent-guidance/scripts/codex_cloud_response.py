"""Shared validation for saved Codex Cloud task responses."""

import json
from pathlib import Path


class CheckFailure(Exception):
    pass


def load_response(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CheckFailure("saved task response is not readable JSON") from None
    if not isinstance(value, dict):
        raise CheckFailure("saved task response is not a JSON object")
    return value


def validated_turn(response):
    turn = response.get("current_assistant_turn")
    if not isinstance(turn, dict):
        raise CheckFailure("saved task response has no current assistant turn")
    if turn.get("type") != "assistant" or turn.get("role") != "assistant":
        raise CheckFailure("current assistant turn has an invalid identity")
    if turn.get("turn_status") != "completed" or turn.get("error") is not None:
        raise CheckFailure("assistant turn is incomplete or failed")
    return turn
