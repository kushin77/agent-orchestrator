"""Shared pytest fixtures for the guardrails/honesty package.

The ``guardrails/honesty`` directory IS the ``honesty`` package, so its
parent (the ``guardrails`` directory) must be importable for
``import honesty`` to resolve.  This file inserts that parent on ``sys.path``
and exposes convenient absolute paths to the fixtures/corpus/manifest.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # guardrails/honesty == the honesty package
_PARENT = os.path.dirname(_PKG_ROOT)  # guardrails/
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import pytest  # noqa: E402


@pytest.fixture()
def honesty_root() -> str:
    return _PKG_ROOT


@pytest.fixture()
def fixtures_dir(honesty_root: str) -> str:
    return os.path.join(honesty_root, "fixtures")


@pytest.fixture()
def corpus_dir(honesty_root: str) -> str:
    return os.path.join(honesty_root, "corpus")


@pytest.fixture()
def manifest_path(honesty_root: str) -> str:
    return os.path.join(honesty_root, "manifest.negative.yaml")
