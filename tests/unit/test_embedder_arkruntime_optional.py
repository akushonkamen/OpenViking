# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests: importing the embedder package must not require volcenginesdkarkruntime.

``volcengine-python-sdk`` 5.0.51.post1 split the single wheel and stopped
shipping ``volcenginesdkarkruntime``. The embedder package is imported during
server startup for every user, so the Ark runtime client may only be imported
lazily, when a Volcengine embedder is actually instantiated (same pattern as
``openviking/models/vlm/backends/volcengine_vlm.py``).
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_BLOCKED_IMPORT_CODE = (
    "import sys; sys.modules['volcenginesdkarkruntime'] = None; import openviking.models.embedder"
)


def test_embedder_package_imports_without_arkruntime():
    """The exact crash from issue #5906: package import with the module absent."""
    result = subprocess.run(
        [sys.executable, "-c", _BLOCKED_IMPORT_CODE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"openviking.models.embedder failed to import without "
        f"volcenginesdkarkruntime:\n{result.stderr}"
    )


def test_sync_ark_client_missing_arkruntime_raises_friendly_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "volcenginesdkarkruntime", None)
    from openviking.models.embedder import volcengine_embedders

    with pytest.raises(ImportError, match="pip install volcenginesdkarkruntime"):
        volcengine_embedders._create_sync_ark({"api_key": "test-key"})


def test_async_ark_client_missing_arkruntime_raises_friendly_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "volcenginesdkarkruntime", None)
    from openviking.models.embedder import volcengine_embedders

    with pytest.raises(ImportError, match="pip install volcenginesdkarkruntime"):
        volcengine_embedders._create_async_ark({"api_key": "test-key"})


def test_dense_embedder_missing_arkruntime_raises_friendly_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "volcenginesdkarkruntime", None)
    from openviking.models.embedder.volcengine_embedders import VolcengineDenseEmbedder

    with pytest.raises(ImportError, match="pip install volcenginesdkarkruntime"):
        VolcengineDenseEmbedder(model_name="test", api_key="test-key")
