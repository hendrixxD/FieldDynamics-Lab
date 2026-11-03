"""Throwaway: fails on purpose to time the stop-on-failure job."""


def test_fails_on_purpose() -> None:
    assert False, "deliberate failure"
