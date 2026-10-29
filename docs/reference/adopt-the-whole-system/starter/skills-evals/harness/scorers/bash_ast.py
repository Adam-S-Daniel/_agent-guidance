"""Small shared entry point for the pinned Tree-sitter Bash parser."""

from __future__ import annotations


#: The exact pins CI installs (ci.yml, eval.yml). One place in code; a test
#: holds every workflow `pip install` line to it.
PARSER_REQUIREMENTS = ("tree-sitter==0.26.0", "tree-sitter-bash==0.25.1")

PARSER_UNAVAILABLE = "parser_unavailable"


class BashParseError(ValueError):
    """A fixed parser failure reason, without source text or parser output."""


class ScorerUnavailableError(RuntimeError):
    """A check could not run because a scoring dependency is missing here.

    Deliberately not a `ValueError`: the checks turn those into a failed
    check, and a missing parser says nothing about the agent's work. The
    runner records the trial as an error, outside every check's denominator.
    """


def require_parser(check: str) -> None:
    """Raise `ScorerUnavailableError` unless the pinned parser can be imported.

    Called at the top of every parser-backed check, before it looks at
    anything the agent wrote, so the outcome never depends on that content.
    """
    if not parser_importable():
        raise ScorerUnavailableError(
            f"{check}: the Bash parser is not installed; {install_command()}")


def parser_importable() -> bool:
    """True when the pinned parser wheels can be imported by this interpreter."""
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_bash  # noqa: F401
    except ImportError:
        return False
    return True


def install_command(python: str = "python3") -> str:
    """The pip command that installs the pinned parser wheels."""
    return f"{python} -m pip install {' '.join(PARSER_REQUIREMENTS)}"


def parse_bash(source: str | bytes):
    """Return a valid Bash root node; import parser wheels only when called.

    Consumers can walk Tree-sitter fields and children directly. Syntax recovery
    is never accepted as a successful parse.
    """
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_bash
    except ImportError:
        raise BashParseError(PARSER_UNAVAILABLE) from None
    root = Parser(Language(tree_sitter_bash.language())).parse(
        source.encode("utf-8") if isinstance(source, str) else source).root_node
    if root.has_error:
        raise BashParseError("invalid_bash")
    return root
