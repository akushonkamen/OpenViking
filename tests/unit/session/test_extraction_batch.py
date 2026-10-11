# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.session.extraction_batch import (
    DEFAULT_EXTRACTION_BATCH_MAX_MESSAGE_TOKENS,
    DEFAULT_EXTRACTION_BATCH_MAX_MESSAGES,
    ExtractionBatchLimits,
    plan_extraction_batches,
    resolve_extraction_batch_limits,
)
from openviking.session.session import Session


def _message(message_id: str, role: str = "user", text: str = "content") -> Message:
    return Message(id=message_id, role=role, parts=[TextPart(text)])


def test_auto_commit_policy_resolves_phase2_batch_limits():
    limits = resolve_extraction_batch_limits(
        {
            "pending_token_threshold": 8000,
            "message_count_threshold": 40,
        }
    )

    assert limits == ExtractionBatchLimits(max_message_tokens=8000, max_messages=40)


def test_missing_policy_resolves_to_default_chunking_limits():
    """#5894: without a policy, extraction still chunks oversized sessions.

    The old behavior returned disabled limits so the entire archived session
    went through one ExtractLoop pass and failed with parse_error on large
    sessions; the defaults keep small sessions in a single batch and split
    oversized ones at message boundaries.
    """
    limits = resolve_extraction_batch_limits(None)

    assert limits == ExtractionBatchLimits(
        max_message_tokens=DEFAULT_EXTRACTION_BATCH_MAX_MESSAGE_TOKENS,
        max_messages=DEFAULT_EXTRACTION_BATCH_MAX_MESSAGES,
    )
    assert resolve_extraction_batch_limits({}) == limits
    assert limits.enabled is True


def test_default_limits_keep_small_sessions_in_one_batch():
    messages = [_message(f"m{index}") for index in range(32)]

    batches = plan_extraction_batches(messages, resolve_extraction_batch_limits(None))

    assert len(batches) == 1
    assert [message.id for message in batches[0].messages] == [f"m{index}" for index in range(32)]
    assert batches[0].oversized is False


def test_default_limits_split_oversized_session_by_message_count():
    messages = [_message(f"m{index}") for index in range(600)]

    batches = plan_extraction_batches(messages, resolve_extraction_batch_limits(None))

    assert len(batches) == 3
    assert [len(batch.messages) for batch in batches] == [250, 250, 100]
    assert all(batch.oversized is False for batch in batches)


def test_default_limits_split_oversized_session_by_token_count():
    # ~9000 tokens per message: three of them already exceed the 30k default.
    messages = [_message(f"m{index}", text="字" * 6000) for index in range(6)]

    batches = plan_extraction_batches(messages, resolve_extraction_batch_limits(None))

    assert len(batches) > 1
    assert all(
        batch.estimated_tokens <= DEFAULT_EXTRACTION_BATCH_MAX_MESSAGE_TOKENS for batch in batches
    )


def test_phase2_batches_split_an_oversized_batch_by_message_count():
    messages = [_message(f"m{index}") for index in range(5)]

    batches = plan_extraction_batches(
        messages,
        ExtractionBatchLimits(max_messages=2),
    )

    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["m0", "m1"],
        ["m2", "m3"],
        ["m4"],
    ]


def test_phase2_batches_split_an_oversized_batch_by_token_count():
    messages = [_message(f"m{index}", text="word " * 80) for index in range(3)]
    one_message_tokens = messages[0].estimated_tokens

    batches = plan_extraction_batches(
        messages,
        ExtractionBatchLimits(max_message_tokens=one_message_tokens),
    )

    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["m0"],
        ["m1"],
        ["m2"],
    ]
    assert all(batch.estimated_tokens <= one_message_tokens for batch in batches)


def test_phase2_batches_keep_a_turn_together_when_it_fits():
    messages = [
        _message("u1", text="question"),
        _message("a1", role="assistant", text="answer"),
        _message("u2", text="next"),
        _message("a2", role="assistant", text="done"),
    ]

    batches = plan_extraction_batches(
        messages,
        ExtractionBatchLimits(max_messages=2),
    )

    assert [[message.id for message in batch.messages] for batch in batches] == [
        ["u1", "a1"],
        ["u2", "a2"],
    ]


@pytest.mark.asyncio
async def test_working_memory_batches_carry_the_previous_summary_forward():
    vlm = type("VLM", (), {"is_available": lambda self: True})()
    session = Session(
        viking_fs=None,
        vlm_resolver=type(
            "Resolver",
            (),
            {"get_vlm": AsyncMock(return_value=vlm)},
        )(),
    )
    messages = [_message("u1"), _message("u2"), _message("u3")]
    calls = []

    async def generate(batch, latest_archive_overview="", checkpoint_requests=None):
        calls.append(
            (
                [message.id for message in batch],
                latest_archive_overview,
                list(checkpoint_requests or []),
            )
        )
        return f"{latest_archive_overview}|{batch[0].id}"

    session._generate_archive_summary_async = AsyncMock(side_effect=generate)

    limits = ExtractionBatchLimits(max_messages=1)
    result = await session._generate_archive_summary_with_batching(
        plan_extraction_batches(messages, limits),
        latest_archive_overview="previous",
        limits=limits,
    )

    assert result == "previous|u1|u2|u3"
    assert [(ids, previous) for ids, previous, _ in calls] == [
        (["u1"], "previous"),
        (["u2"], "previous|u1"),
        (["u3"], "previous|u1|u2"),
    ]


@pytest.mark.asyncio
async def test_working_memory_no_vlm_fallback_uses_all_messages(monkeypatch):
    messages = [_message("u1"), _message("u2"), _message("u3")]
    config = type("Config", (), {"vlm": None})()
    monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)
    session = Session(
        viking_fs=None,
        vlm_resolver=type(
            "Resolver",
            (),
            {"get_vlm": AsyncMock(return_value=None)},
        )(),
    )

    limits = ExtractionBatchLimits(max_messages=1)
    result = await session._generate_archive_summary_with_batching(
        plan_extraction_batches(messages, limits),
        latest_archive_overview="",
        limits=limits,
    )

    assert result == "# Session Summary\n\n**Overview**: 3 turns, 3 messages"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["create", "update", "fallback"])
@pytest.mark.parametrize(
    "message",
    [
        "Error code: 400 - InvalidParameter: Total tokens of multi-modal content "
        "and text exceed max message tokens.",
        "503 Service Unavailable",
    ],
)
async def test_working_memory_propagates_model_errors(monkeypatch, path, message):
    error = RuntimeError(message)
    completion = AsyncMock(side_effect=["no tool call", error] if path == "fallback" else error)
    vlm = SimpleNamespace(is_available=lambda: True, get_completion_async=completion)
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(output_language_override="en"),
    )
    session = Session(
        viking_fs=None,
        vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=vlm)),
    )

    async def summarize():
        return await session._generate_archive_summary_async(
            [_message("u1")],
            latest_archive_overview="" if path == "create" else "## Current State\nPrevious work",
        )

    with pytest.raises(RuntimeError) as raised:
        await summarize()

    assert raised.value is error
    assert completion.await_count == (2 if path == "fallback" else 1)


@pytest.mark.asyncio
async def test_long_term_memory_batches_follow_the_planned_limit():
    session = Session(viking_fs=None)
    messages = [_message("u1"), _message("u2"), _message("u3")]
    recorded_batches = []

    async def extract(batch):
        return [batch[0].id]

    async def record(operation_name, step, batch, fn):
        result = await fn()
        recorded_batches.append((operation_name, step, [message.id for message in batch]))
        return result

    result = await session._extract_long_term_memories_with_batching(
        messages=messages,
        limits=ExtractionBatchLimits(max_messages=1),
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        extract_batch=extract,
        record_batch=record,
    )

    assert result == ["u1", "u2", "u3"]
    assert [message_ids for _, _, message_ids in recorded_batches] == [
        ["u1"],
        ["u2"],
        ["u3"],
    ]
    assert all(step == "long_term" for _, step, _ in recorded_batches)


@pytest.mark.asyncio
async def test_long_term_memory_extraction_chunks_by_default_without_policy():
    """#5894: an oversized session commits chunked even without a policy.

    600 messages exceed the default 250-message batch cap, so the extractor
    runs once per chunk and merges the per-chunk results instead of feeding
    the whole conversation to a single ExtractLoop pass.
    """
    session = Session(viking_fs=None)
    messages = [_message(f"m{index}") for index in range(600)]
    extracted_batches = []

    async def extract(batch):
        extracted_batches.append([message.id for message in batch])
        return [f"memory-from-{batch[0].id}"]

    async def record(operation_name, step, batch, fn):
        return await fn()

    result = await session._extract_long_term_memories_with_batching(
        messages=messages,
        limits=resolve_extraction_batch_limits(None),
        archive_uri="viking://user/default/sessions/s1/history/archive_001",
        extract_batch=extract,
        record_batch=record,
    )

    assert len(extracted_batches) == 3
    assert all(len(batch) <= DEFAULT_EXTRACTION_BATCH_MAX_MESSAGES for batch in extracted_batches)
    assert extracted_batches[0][0] == "m0"
    assert extracted_batches[-1][-1] == "m599"
    assert result == [f"memory-from-m{index}" for index in (0, 250, 500)]
