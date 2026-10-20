"""Local stub for Windows only (not part of the repository): Home Assistant imports fcntl for a lock file it does not
need in tests. CI runs on Linux with the real module."""
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def flock(fd, operation):  # noqa: ARG001
    return None
