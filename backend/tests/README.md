# Backend Tests

## Setup

Install dependencies from the project root:

```bash
pip install -r backend/requirements.txt
```

## Running Tests

Run the full backend test suite:

```bash
python -m pytest backend/tests/ -v
```

Run a specific test file:

```bash
python -m pytest backend/tests/test_auth.py -v
```

Run tests matching a keyword:

```bash
python -m pytest backend/tests/ -k "timing" -v
```

## Test Structure

Tests live in `backend/tests/` and are organized by component:

| File | Covers |
|---|---|
| `test_auth.py` | Token validation (401/403), ownership checks |
| `test_models.py` | Pydantic model validation (subtitle timing) |
| `test_project_service.py` | Project CRUD, state round-trips, ID uniqueness |

Property-based tests (using [Hypothesis](https://hypothesis.readthedocs.io/)) and unit tests coexist in the same files. Property tests run 100+ iterations with randomly generated inputs to verify correctness invariants.

## Environment

Tests don't require any environment variables. The test helpers create isolated `Settings` instances and patch them in directly — no `.env` file needed.

For the auth tests specifically, a fake `API_SECRET_KEY` is injected per test class so token validation is exercised without touching real config.

## Conventions

- Async tests are auto-detected via `pytest-asyncio` (configured in `conftest.py` with `asyncio_mode = "auto"`)
- Property-based tests are tagged with `Feature: story-video-editor, Property N: [title]`
- Temporary files (e.g., project state round-trip tests) use `tmp_path` fixtures and are cleaned up automatically
