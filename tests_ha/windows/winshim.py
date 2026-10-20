"""Local pytest plugin for Windows only (not part of the repository): the asyncio loop of Windows opens a socket pair that
pytest-socket would refuse. CI runs on Linux without it."""
import pytest_socket


def pytest_configure(config):
    pytest_socket.disable_socket = lambda *args, **kwargs: None
