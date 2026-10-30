"""Start and stop the background worker."""
import subprocess


def start(cmd: list[str]) -> int:
    """Start the worker in its own process group and return its pid."""
    proc = subprocess.Popen(cmd, start_new_session=True)
    return proc.pid


def stop(pid):
    """Stop the worker's whole process group with SIGTERM."""
    raise NotImplementedError
