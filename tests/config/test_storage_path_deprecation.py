# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for the self-generated ``agfs.path``/``vectordb.path`` warnings.

``StorageConfig.resolve_paths`` checks the deprecated ``agfs.path``/``vectordb.path``
fields *before* assigning them from ``workspace`` in the same validator. A config that
never contained those keys therefore stayed silent on first validation but re-warned
about the validator-written values on every re-validation, with the "instead of" value
identical to the workspace value, and no user config could silence the noise (#5843).

The module logger does not propagate (see ``openviking_cli.utils.logger``), so these
tests attach a recording handler to it directly instead of using ``caplog``.
"""

from __future__ import annotations

import logging

import pytest

import openviking_cli.utils.config.storage_config as storage_config_module
from openviking_cli.utils.config.storage_config import StorageConfig


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def storage_path_warnings():
    handler = _RecordingHandler()
    logger = storage_config_module.logger
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _path_deprecation_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [
        record
        for record in records
        if "'agfs.path' is deprecated" in record.getMessage()
        or "'vectordb.path' is deprecated" in record.getMessage()
    ]


def test_no_path_config_never_warns(storage_path_warnings, tmp_path):
    """A config without agfs.path/vectordb.path stays silent across re-validations."""
    config = StorageConfig(workspace=str(tmp_path / "ws"))
    assert _path_deprecation_records(storage_path_warnings) == []

    # The server validates the config more than once at startup; each re-validation
    # of the validator-written values used to emit two spurious warnings (#5843).
    for _ in range(2):
        StorageConfig.model_validate(config.model_dump())
    assert _path_deprecation_records(storage_path_warnings) == []


def test_real_deprecation_still_warns(storage_path_warnings, tmp_path):
    """A user-configured path that differs from workspace still warns, once per validation."""
    raw = {
        "workspace": str(tmp_path / "ws"),
        "agfs": {"path": str(tmp_path / "legacy-agfs")},
        "vectordb": {"path": str(tmp_path / "legacy-vdb")},
    }

    StorageConfig.model_validate(raw)
    records = _path_deprecation_records(storage_path_warnings)
    assert len(records) == 2
    messages = [record.getMessage() for record in records]
    assert any("'agfs.path' is deprecated" in message for message in messages)
    assert any("'vectordb.path' is deprecated" in message for message in messages)
    # The "instead of" value must differ from the workspace value; the bug report was
    # precisely that the two were identical.
    vectordb_message = next(message for message in messages if "vectordb.path" in message)
    assert str(tmp_path / "ws") in vectordb_message
    assert str(tmp_path / "legacy-vdb") in vectordb_message

    # Re-validating the validator-normalized config must not warn again.
    config = StorageConfig.model_validate(raw)
    before = len(storage_path_warnings)
    StorageConfig.model_validate(config.model_dump())
    assert len(_path_deprecation_records(storage_path_warnings)) == before

    # A fresh validation of the same user input keeps warning about the real deprecation.
    StorageConfig.model_validate(raw)
    assert len(_path_deprecation_records(storage_path_warnings)) == before + 2
