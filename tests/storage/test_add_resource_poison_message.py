# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Poison-message delivery budget for the add_resource queue (issue #5879).

A native parser crash (pdfium.dll BEX64 on Windows) kills the whole server
process. On the next startup RecoverStale resets the in-flight message back to
pending with its payload unchanged, so the identical message_id is re-delivered
and crashes the server again — an unbounded crash loop that survives deleting
the task JSON files (the poison lives in the queue store).

These tests construct that processing → recover → re-deliver loop and assert
that the replay is capped and the poison message is dead-lettered with the
task marked failed, so the server can start while the message exists.

Two crash emulations are used:

* :class:`EmulatedNativeCrash` — a ``BaseException`` that no ``except
  Exception`` clause in the queue or processor can catch. It leaves exactly
  the durable state a hard crash leaves: the task stuck non-terminal and the
  message never acked.
* a real ``os._exit(1)`` in a child process for one full crash round-trip.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.service.task_store import _task_to_payload
from openviking.service.task_tracker import TaskStatus, TaskTracker, set_task_tracker
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking.storage.queuefs.named_queue import NamedQueue

# Keep in sync with the processor's delivery budget (asserted on the green run).
MAX_DELIVERY_ATTEMPTS = 3

ACCOUNT_ID = "account-1"
USER_ID = "user-1"
TASK_ID = "task-crash-1"
MESSAGE_ID = "a483bd5a-thesis"

REPO_ROOT = Path(__file__).resolve().parents[2]


class EmulatedNativeCrash(BaseException):
    """Stand-in for a segfault / os._exit: bypasses every ``except Exception``."""


class MemoryTaskStore:
    """Task records survive 'restarts', like the persistent task store."""

    def __init__(self):
        self.tasks = {}

    async def create(self, task):
        self.tasks[task.task_id] = _task_to_payload(task)

    async def update(self, task):
        self.tasks[task.task_id] = _task_to_payload(task)

    async def get(self, task_id, *, account_id=None, user_id=None):
        return self.tasks.get(task_id)

    async def list(self, account_id, *, user_id=None):
        return [task for task in self.tasks.values() if task.get("account_id") == account_id]

    async def delete(self, task_id, *, account_id, user_id=None):
        self.tasks.pop(task_id, None)


class CrashingParserService:
    """The PDF parse that takes the server process down mid-job."""

    def __init__(self):
        self.parse_attempts = 0

    async def execute_add_resource_job(self, msg, **kwargs):
        self.parse_attempts += 1
        raise EmulatedNativeCrash("pdfium.dll BEX64")


def make_envelope():
    msg = AddResourceMsg(
        task_id=TASK_ID,
        root_uri="viking://resources/thesis.pdf",
        path="/tmp/thesis.pdf",
        source_path="/tmp/thesis.pdf",
        account_id=ACCOUNT_ID,
        user_id=USER_ID,
        role="root",
    )
    return {"id": MESSAGE_ID, "data": json.dumps(msg.to_dict())}


class RecoveringTransport:
    """Serve the same message on every dequeue until it is acked.

    Mirrors RecoverStale: a crashed (processing) message is reset to pending
    on restart with its payload unchanged, so the replay loop re-delivers the
    identical message_id unless the consumer removes it (dead-letter).
    """

    def __init__(self, envelope):
        self.envelope = envelope
        self.acked = []

    async def read(self, path, *args, **kwargs):
        if path.endswith("/dequeue"):
            if self.acked:
                return b"{}"
            return json.dumps(self.envelope).encode("utf-8")
        if path.endswith("/status"):
            return b'{"pending":0,"processing":0}'
        return b"{}"

    async def write(self, path, data, *args, **kwargs):
        if path.endswith("/ack"):
            self.acked.append(data.decode("utf-8"))
        return "ok"

    async def mkdir(self, path, *args, **kwargs):
        raise FileExistsError


@pytest.fixture
def transport(monkeypatch):
    fake = RecoveringTransport(make_envelope())
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda _client: fake
    )
    return fake


def make_processor(service):
    return AddResourceProcessor(
        resource_service=service,
        queue_name="AddResource",
        viking_fs=SimpleNamespace(_async_agfs=None),
    )


async def run_one_server_lifetime(store, transport, service):
    """One boot: fresh tracker cache + queue over the durable stores."""
    tracker = TaskTracker(store)
    set_task_tracker(tracker)
    try:
        queue = NamedQueue(
            object(),
            "/queue",
            "AddResource",
            dequeue_handler=make_processor(service),
        )
        return await asyncio.wait_for(queue.dequeue(), timeout=10)
    finally:
        set_task_tracker(None)


async def get_persisted_task(store):
    tracker = TaskTracker(store)
    return await tracker.get(TASK_ID, account_id=ACCOUNT_ID, user_id=USER_ID)


async def test_crash_replay_is_capped_and_poison_message_dead_lettered(transport):
    store = MemoryTaskStore()
    service = CrashingParserService()

    # One server lifetime per crash: RecoverStale re-delivers the identical
    # message and the native crash kills the process again.
    for _ in range(MAX_DELIVERY_ATTEMPTS):
        with pytest.raises(EmulatedNativeCrash):
            await run_one_server_lifetime(store, transport, service)
        task = await get_persisted_task(store)
        assert task is not None
        assert task.status is TaskStatus.RUNNING, "crash leaves the task non-terminal"

    # Budget exhausted: the next delivery must NOT re-run the crashing parse.
    await run_one_server_lifetime(store, transport, service)

    assert service.parse_attempts == MAX_DELIVERY_ATTEMPTS
    assert transport.acked == [MESSAGE_ID], "poison message must be dead-lettered (acked)"
    task = await get_persisted_task(store)
    assert task.status is TaskStatus.FAILED
    assert "delivery budget" in task.error
    # The message is gone: further restarts deliver nothing and parse nothing.
    assert await run_one_server_lifetime(store, transport, service) is None
    assert service.parse_attempts == MAX_DELIVERY_ATTEMPTS


CHILD_CRASH_SCRIPT = """\
import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, {repo_root!r})

import openviking.storage.queuefs.named_queue as named_queue_module
from openviking.service.task_store import _task_to_payload
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking.storage.queuefs.named_queue import NamedQueue

STATE_PATH = sys.argv[1]


def update_state(fn):
    with open(STATE_PATH) as handle:
        state = json.load(handle)
    fn(state)
    with open(STATE_PATH, "w") as handle:
        json.dump(state, handle)


class FileTaskStore:
    async def create(self, task):
        update_state(lambda state: state.update(task=_task_to_payload(task)))

    async def update(self, task):
        update_state(lambda state: state.update(task=_task_to_payload(task)))

    async def get(self, task_id, *, account_id=None, user_id=None):
        with open(STATE_PATH) as handle:
            return json.load(handle).get("task")

    async def list(self, account_id, *, user_id=None):
        return []

    async def delete(self, task_id, *, account_id, user_id=None):
        pass


class FileTransport:
    async def read(self, path, *args, **kwargs):
        with open(STATE_PATH) as handle:
            state = json.load(handle)
        if path.endswith("/dequeue"):
            if state.get("acked"):
                return b"{{}}"
            return json.dumps(state["message"]).encode("utf-8")
        return b'{{"pending":0,"processing":0}}'

    async def write(self, path, data, *args, **kwargs):
        if path.endswith("/ack"):
            update_state(lambda state: state.update(acked=True))
        return "ok"

    async def mkdir(self, path, *args, **kwargs):
        raise FileExistsError


class HardKillParser:
    async def execute_add_resource_job(self, msg, **kwargs):
        os._exit(1)  # nothing after this line ever runs — like pdfium.dll


async def main():
    named_queue_module.AsyncAGFSClient = lambda _client: FileTransport()
    tracker = TaskTracker(FileTaskStore())
    set_task_tracker(tracker)
    queue = NamedQueue(
        object(),
        "/queue",
        "AddResource",
        dequeue_handler=AddResourceProcessor(
            resource_service=HardKillParser(),
            queue_name="AddResource",
            viking_fs=SimpleNamespace(_async_agfs=None),
        ),
    )
    await asyncio.wait_for(queue.dequeue(), timeout=30)


asyncio.run(main())
"""


async def test_real_process_kill_replay_is_capped_and_dead_lettered(transport, tmp_path):
    """Full crash round-trip with a genuine os._exit(1) in a child process."""
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"message": make_envelope(), "task": None, "acked": False}))
    script_path = tmp_path / "crash_child.py"
    script_path.write_text(CHILD_CRASH_SCRIPT.format(repo_root=str(REPO_ROOT)))

    crashes = 0
    for _ in range(MAX_DELIVERY_ATTEMPTS + 1):
        proc = subprocess.run(
            [sys.executable, str(script_path), str(state_path)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            crashes += 1
            state = json.loads(state_path.read_text())
            assert state["acked"] is False, "crashed delivery must not ack"
            assert state["task"]["status"] == "running", "crash leaves the task non-terminal"
        else:
            break

    assert crashes == MAX_DELIVERY_ATTEMPTS
    final = json.loads(state_path.read_text())
    assert final["acked"] is True, "poison message must be dead-lettered (acked)"
    assert final["task"]["status"] == "failed"
    assert "delivery budget" in final["task"]["error"]
