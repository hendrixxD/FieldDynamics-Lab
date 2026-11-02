"""Throwaway: passes only if the runner cannot reach an outside host."""

import urllib.error
import urllib.request

import pytest


def test_outside_host_is_unreachable() -> None:
    with pytest.raises((urllib.error.URLError, TimeoutError)):
        urllib.request.urlopen("https://example.com", timeout=10)
