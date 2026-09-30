# tests/conftest.py
"""Keep tests offline: no LangSmith trace uploads from fixture runs."""

import pytest


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
