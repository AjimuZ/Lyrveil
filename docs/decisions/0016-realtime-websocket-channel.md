# ADR 0016: One event WebSocket, with REST as the source of truth

Status: accepted (2026-11-01). Supersedes: none.

## Context
The requirements name WebSocket for real-time events. The interface needs streaming tokens, typing, job status, GPU state and notifications across many conversations. FastAPI also offers SSE, but SSE does not match the stated stack.

## Decision
- One WebSocket per browser tab at `/ws` carries all server-to-client events.
- All client actions go through REST. The client sends only `hello` and `ping`.
- Events have a process-wide `id`. The server keeps the newest 2000 in memory and replays on reconnect, or sends `resync.required`.
- REST is authoritative: losing events loses no data.
- Streaming text is flushed to SQLite every 2 seconds, so a reload shows it.

## Alternatives
- A WebSocket per conversation: more connections, and the chat list needs events from all of them.
- Actions over the WebSocket: duplicates validation, errors and idempotency that REST already has.
- SSE: simple, but outside the stated stack.
- Durable event storage: more writes, with no benefit given that REST holds the truth.

## Consequences
One clear contract, easy to test. A single backend process is required, which [ADR 0004](0004-single-sqlite-writer.md) already needs. The client must handle `resync.required` everywhere it merges events.
