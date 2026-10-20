"""Local stub for Windows only (not part of the repository): Home Assistant imports resource to raise the file
descriptor limit; tests do not need it. CI runs on Linux with the real module."""
RLIMIT_NOFILE = 7


def getrlimit(which):  # noqa: ARG001
    return (1024, 1024)


def setrlimit(which, limits):  # noqa: ARG001
    return None
