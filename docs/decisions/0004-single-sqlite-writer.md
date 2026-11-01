# ADR 0004: Backend is the only SQLite writer

Status: accepted (2026-11-01). Supersedes: none.

## Context
The requirements list `generation_jobs` and `model_lifecycle_events` in one schema, and they require a persistent job queue and logged transitions. Backend and orchestrator are separate containers. Two processes on one SQLite file would share locks and the WAL shared memory across containers, and would couple the most privileged service to the data layer.

## Decision
- Only the backend opens SQLite, and it owns the job queue.
- The orchestrator holds no durable business state. Its truth is the real state of containers and the GPU, which it re-reads at startup in `RECOVERING`.
- The orchestrator buffers its newest 5000 events in memory with `boot_id` and `seq`. The backend fetches them and stores them. A unique key removes duplicates.
- The orchestrator also logs every transition to stdout.

## Alternatives
- Both services write to one database: WAL can work on one kernel, but it spreads schema knowledge and puts user data within reach of the socket holder.
- A separate SQLite file for the orchestrator: it adds a second migration path and backup, with little value.
- A durable queue inside the orchestrator: it would need user-related payloads there, which the control API deliberately excludes.

## Consequences
A compromise of the orchestrator does not directly expose the database through its normal code. Reconciliation must handle every combination of container states. Events that occur while the backend is down longer than the buffer can hold survive only in Docker logs.
