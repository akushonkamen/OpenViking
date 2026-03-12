# CLAUDE.md — OpenViking Codebase Guide

OpenViking is an **Agent-native context database** that unifies memories, resources, and skills into a virtual filesystem (`viking://`). It provides L0/L1/L2 tiered context loading, hierarchical semantic retrieval, and automatic session memory extraction.

---

## Repository Structure

```
OpenViking/
├── openviking/          # Core Python package (server + embedded SDK)
├── openviking_cli/      # Lightweight CLI/client package (HTTP client wrappers)
├── crates/ov_cli/       # Rust CLI binary (the `ov` command)
├── bot/vikingbot/       # VikingBot AI agent framework (optional extra)
├── src/                 # C++ extensions (vector index, scalar index, store)
├── third_party/agfs/    # AGFS (Agent Filesystem) Go component + Python bindings
├── tests/               # Test suite
├── examples/            # Usage examples
├── docs/                # English + Chinese documentation
└── bot/                 # VikingBot package root (separate from openviking/)
```

### `openviking/` — Core Package

| Subdirectory | Purpose |
|---|---|
| `server/` | FastAPI HTTP server (`openviking-server` entrypoint). Routers: resources, filesystem, search, sessions, admin, bot, content, debug, observer, pack, relations, tasks, system |
| `service/` | Business logic layer: `FSService`, `SearchService`, `SessionService`, `ResourceService`, `RelationService`, `PackService`, `DebugService` |
| `storage/` | Dual-layer storage: `VikingFS` (AGFS wrapper), `VikingDBManager` (vector index), `TransactionManager`, `QueueManager`, `LocalFS` |
| `retrieve/` | Context retrieval: `HierarchicalRetriever`, `IntentAnalyzer`, `MemoryLifecycle` |
| `session/` | Session management: `Session`, `SessionCompressor`, `MemoryExtractor`, `MemoryDeduplicator` |
| `parse/` | Document parsing: parsers for PDF/HTML/Markdown/code, `TreeBuilder`, `ResourceDetector` |
| `core/` | Foundations: `SkillLoader`, `DirectoryInitializer`, `BuildingTree`, `MCPConverter` |
| `utils/` | Utilities: embedding, summarizer, media processor, skill/resource processors |
| `prompts/` | Jinja2/YAML prompt templates for semantic processing and retrieval |
| `models/` | Pydantic model definitions (embedder, VLM) |
| `client/` | `LocalClient` (embedded mode), `Session` |
| `pyagfs/` | Python bindings for AGFS Go component |
| `console/` | Web UI console (FastAPI + static assets) |
| `async_client.py` | `AsyncOpenViking` — singleton async client for embedded mode |
| `sync_client.py` | `SyncOpenViking` — synchronous wrapper |

### `openviking_cli/` — CLI/Client Package

Minimal package to avoid heavy imports. Contains:
- `client/` — `AsyncHTTPClient`, `SyncHTTPClient`, `BaseClient` (abstract interface)
- `session/user_id.py` — `UserIdentifier`
- `rust_cli.py` — `ov` entry point (finds and `execv`s the Rust binary)
- `server_bootstrap.py` — `openviking-server` entry point
- `exceptions.py` — `OpenVikingError` and subclasses

### `crates/ov_cli/` — Rust CLI

The `ov` binary. Uses `clap` for argument parsing. Modules: `client`, `commands`, `config`, `error`, `output`, `tui`, `utils`.

### `bot/vikingbot/` — VikingBot

Optional AI agent framework built on OpenViking. Install with `pip install "openviking[bot]"`. Contains agent, channels, CLI, config, console, cron, hooks, integrations, providers, sandbox, and session modules.

### `src/` — C++ Extensions (pybind11)

Vector index, scalar index, KV store, and persistent store. Built via `setup.py build_ext --inplace` using CMake.

### `third_party/agfs/` — AGFS (Go)

The Agent Filesystem Server. A Go binary that provides the content storage layer. Bundled as a pre-compiled binary in the wheel at `openviking/bin/agfs-server` and `openviking/lib/libagfsbinding.so`.

---

## Architecture

### Deployment Modes

**Embedded mode** (single-process):
```python
from openviking import OpenViking
client = OpenViking(path="./data")
# Auto-starts AGFS subprocess, uses local vector index
```

**HTTP server mode** (production/multi-client):
```bash
openviking-server          # starts FastAPI server on :1933
```
```python
from openviking import SyncHTTPClient
client = SyncHTTPClient(url="http://localhost:1933", api_key="...")
```

### Data Flow

**Adding context:**
```
Input → Parser → TreeBuilder → AGFS → SemanticQueue → Vector Index
```

**Retrieving context:**
```
Query → IntentAnalyzer → HierarchicalRetriever → Rerank → Results
```

**Session commit:**
```
Messages → Compress → Archive → MemoryExtractor → Storage (AGFS + Vector)
```

### Viking URI Scopes

All content is addressed via `viking://{scope}/{path}`:

| Scope | Contents |
|---|---|
| `viking://resources/` | Imported resources (docs, repos, web pages) |
| `viking://user/` | User preferences, memories |
| `viking://agent/` | Agent skills, instructions, task memories |
| `viking://session/{id}/` | Per-session messages, checkpoints, summaries |
| `viking://queue/` | Processing queue (internal) |

### L0/L1/L2 Context Layers

Every directory and file in the virtual filesystem has:
- **L0** (`.abstract.md`): One-sentence summary (~100 tokens)
- **L1** (`.overview.md`): Key information for Agent planning (~2k tokens)
- **L2**: Full original content, loaded on demand

---

## Development Setup

### Prerequisites

- Python 3.10+
- Go 1.22+ (for AGFS)
- C++ compiler: GCC 9+ or Clang 11+ (C++17 required)
- CMake 3.12+
- Rust 1.88+ (for `ov` CLI)

### Install for Development

```bash
# Install uv (recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync all extras and activate virtualenv
uv sync --all-extras
source .venv/bin/activate

# Build C++ extensions + AGFS + install editable
uv pip install -e . --force-reinstall
# Or using Make:
make build
```

### Build Commands

```bash
make build        # Build AGFS (Go), ov CLI (Rust), C++ extensions; install editable
make clean        # Remove all build artifacts
make check-deps   # Verify all toolchain versions
```

### Configuration

Create `~/.openviking/ov.conf`:
```json
{
  "storage": { "workspace": "/path/to/workspace" },
  "log": { "level": "INFO", "output": "stdout" },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "...",
      "model": "doubao-embedding-vision-250615",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "...",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

```bash
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf
```

---

## Testing

```bash
# Run all tests
pytest

# Run with coverage (default via pyproject.toml addopts)
pytest --cov=openviking --cov-report=term-missing

# Run a specific test file
pytest tests/unit/test_uri_short_format.py

# Run integration tests
pytest tests/integration/
```

Test layout:
```
tests/
├── unit/         # Unit tests (fast, no external deps)
├── integration/  # Integration tests (requires running server/config)
├── engine/       # Engine-level tests
├── cli/          # CLI tests
├── session/      # Session-related tests
├── retrieve/     # Retrieval tests
├── storage/      # Storage tests
├── parse/        # Parse tests
├── eval/         # Evaluation tests
└── conftest.py   # Shared fixtures
```

Top-level test files cover memory lifecycle, session commit, upload utils, config loading, edge cases, and task tracking.

---

## Code Style & Linting

This project uses **ruff** for linting and formatting.

```bash
# Lint and auto-fix
ruff check --fix .

# Format
ruff format .

# Type check
mypy openviking/
```

**Pre-commit hooks** (ruff lint + ruff format) run automatically on commit:
```bash
pre-commit install   # first-time setup
pre-commit run --all-files
```

### Style Rules (from `pyproject.toml`)

- Line length: **100** characters
- Quote style: **double quotes**
- Indent: **spaces**
- `__init__.py` unused imports (`F401`) are allowed
- Long lines (`E501`), mutable defaults (`B006`), and a few others are ignored
- `third_party/` is excluded from linting

### Type Checking (`mypy`)

- `ignore_missing_imports = true`
- `disallow_untyped_defs = false` — type annotations are encouraged but not required
- Check with `mypy openviking/`

---

## Key Conventions

### Error Handling

Use exceptions from `openviking_cli.exceptions`:
- `OpenVikingError` — base
- `NotFoundError` — resource not found
- `NotInitializedError` — service not initialized

Server maps error codes to HTTP status via `ERROR_CODE_TO_HTTP_STATUS` in `openviking/server/models.py`.

### Logging

Use the project logger factory, never `print()`:
```python
from openviking_cli.utils.logger import get_logger
logger = get_logger(__name__)
```

Uses **loguru** under the hood.

### Async vs Sync

- All service layer methods and `LocalClient` are **async**.
- `SyncOpenViking` / `SyncHTTPClient` wrap async methods for synchronous use.
- `AsyncOpenViking` is a **singleton** (thread-safe double-checked locking).
- Use `await service.initialize()` before calling any service methods.

### Pydantic Models

All request/response data uses **Pydantic v2** models. Define models in `openviking/server/models.py` or relevant subpackages.

### Adding a New API Endpoint

1. Add router in `openviking/server/routers/`
2. Register router in `openviking/server/app.py`
3. Add service method in `openviking/service/`
4. Add client method to `BaseClient` in `openviking_cli/client/base.py` and both `AsyncHTTPClient` / `LocalClient`

### Viking URI Usage

```python
from openviking_cli.utils.uri import VikingURI
uri = VikingURI("viking://resources/my_project/docs/api.md")
```

Always use `VikingURI` for URI manipulation — never raw string concatenation.

### Prompt Templates

Prompt templates are YAML files under `openviking/prompts/templates/`. Managed via `openviking/prompts/manager.py`. Use Jinja2 syntax for variable substitution.

---

## VikingBot Development

VikingBot lives in `bot/` and is a separate package (`vikingbot`). Install with:
```bash
uv pip install -e ".[bot]"
openviking-server --with-bot
```

When modifying VikingBot, also run its own test suite:
```bash
pytest bot/tests/
```

---

## Release & Versioning

- Versioning uses `setuptools_scm` — version is derived from Git tags.
- Tag format: `vX.Y.Z` (e.g., `v0.2.0`). Non-tag builds get `X.Y.Z.devN`.
- Builds target Python 3.10–3.13, Linux x86_64, macOS x86_64/arm64, Windows x86_64.
- Published to PyPI via GitHub Actions workflow `03. Release`.

---

## Important Files

| File | Purpose |
|---|---|
| `openviking/__init__.py` | Public API exports |
| `openviking/server/app.py` | FastAPI app factory (`create_app`) |
| `openviking/service/core.py` | `OpenVikingService` — composes all sub-services |
| `openviking/storage/viking_fs.py` | `VikingFS` — AGFS wrapper + URI translation |
| `openviking/retrieve/hierarchical_retriever.py` | Core retrieval algorithm |
| `openviking/session/compressor.py` | Session compression + memory extraction |
| `openviking_cli/client/base.py` | `BaseClient` abstract interface |
| `openviking_cli/rust_cli.py` | `ov` CLI entry point (finds Rust binary) |
| `openviking_cli/server_bootstrap.py` | `openviking-server` entry point |
| `pyproject.toml` | Package metadata, dependencies, tool config |
| `setup.py` | C++ extension + AGFS + Rust CLI build logic |
| `Makefile` | Developer convenience targets |
| `.pre-commit-config.yaml` | Pre-commit hooks (ruff) |
| `docs/en/concepts/01-architecture.md` | Architecture reference |
