# ADR 0009: Synchronous SQLAlchemy behind a thread boundary

Status: accepted (2026-11-01). Supersedes: none.

## Context
The stack requires SQLAlchemy and SQLite, as well as asynchronous operations. Queries on a local file take very little time. Streaming and runtime calls are the slow, truly asynchronous parts. Async SQLAlchemy over `aiosqlite` adds a helper thread per connection, implicit I/O rules and a further dependency, with no gain for one local writer.

## Decision
- Use synchronous SQLAlchemy 2.1 sessions.
- REST handlers that touch the database are plain `def`, so FastAPI runs them in its thread pool.
- Async job handlers reach the database through `anyio.to_thread.run_sync`, in short units of work.
- Writes begin with `BEGIN IMMEDIATE`. `busy_timeout` is 5000 ms.
- A streaming reply never keeps a transaction open.

## Alternatives
- Async SQLAlchemy with `aiosqlite`: uniform `await`, more complexity, and a known class of lazy-loading errors.
- SQLModel: FastAPI's tooling guidance suggests it, but the requirements name SQLAlchemy, and this schema has many non-CRUD rules.
- Asyncer instead of AnyIO: a nice wrapper, but one more dependency, where AnyIO already comes with Starlette.

## Consequences
Code stays simple and easy to type. Blocking database calls must not appear inside `async def`, and tests with asyncio's debug mode check for it. Moving to PostgreSQL later would keep this model working.
