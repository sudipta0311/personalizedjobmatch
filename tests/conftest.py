"""Pytest configuration and shared fixtures."""

import pytest


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Replace time.sleep with a no-op in all tests to avoid artificial delays."""
    monkeypatch.setattr("time.sleep", lambda _: None)
