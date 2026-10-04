## Python

- Use type hints on all function signatures.
- Format with the project's configured formatter (black, ruff format, etc.) — do not mix styles.
- Prefer `pathlib.Path` over `os.path` for filesystem operations.
- Use context managers (`with`) for files, locks, and database connections.
- Raise specific exceptions; never use bare `except:` or `except Exception`.
- Use `logging` instead of `print()` for any output that is not user-facing CLI output.
- Pin dependencies in `requirements.txt` or lock files; do not add unpinned deps.
- `MagicMock` (3.8+) converts to `1`: a mock reaching `os.kill`, `os.killpg`, `os.waitpid` or `os.close` acts on `1`, and `killpg(1, sig)` is `kill(-1, sig)`. Patch them when mocking `subprocess`, or use `spec=subprocess.Popen` plus a `pid`.
