# type: ignore
"""Shared fixtures."""

import pytest
from loguru import logger

logger.remove()  # Remove default logger


@pytest.fixture(autouse=True)
def _change_test_dir(monkeypatch, tmp_path):
    """All tests should run in a temporary directory."""
    monkeypatch.chdir(tmp_path)
