# ADR 0042: Profile reload as a queued maintenance job, the tier read by the backend, name-only registration

Status: accepted (2026-11-07). Supersedes: none. Refines [ADR 0010](0010-job-queue-and-scheduling-policy.md),
[ADR 0024](0024-selectable-model-profiles.md) and [ADR 0027](0027-hardware-tiers-after-ram-upgrade.md).

## Context
- [Selecting a profile](../models.md#selecting-a-profile) asks **Applica ora** to wait for the running reply, set the
  desired mode to `none` and then to `llm`, so the text runtime stops, the release is verified and the new launch file
  loads. The scheduler is the only part of the backend that asks for modes, and it would ask for `llm` again as soon as
  a reply is queued.
- The orchestrator applies a mode once no lease is active. A `none` followed at once by `llm` would let the
  orchestrator keep the runtime that is already loaded.
- A profile above the machine's tier must be refused. Until now only `scripts\lyrveil.cmd models` knew the tier, from
  the memory Docker sees.
- [Model files](../security.md#model-files) lets a user register a file of the models volume. The backend must never
  read a file outside the two model folders.

## Decision
- **The reload is a job.** `POST /settings/models/apply` queues a `maintenance` job, `model_reload`, at priority 5,
  ahead of replies. Its stage needs no GPU and holds no lease. The worker runs one job at a time, so the reload starts
  after the running reply. It asks for `none`, long-polls the state until `IDLE`, which the orchestrator enters only
  after a verified release, and only then asks for `llm`. No other job runs meanwhile, so nothing asks for `llm` early.
- **The backend reads the tier.** `MemTotal` of `/proc/meminfo` in the backend container is the memory of the Docker VM,
  the same value `docker info` reports to the scripts. From 20 GiB the tier is `target`.
- **Registration takes a name.** The route takes a kind and a file name, never a path. The name must match the catalog's
  file pattern and the kind's extension. The file must be a regular file directly in its folder. A symbolic link is
  refused wherever it points, which is stricter than refusing only links out of the folder, and avoids a link that the
  runtimes, which mount the folders elsewhere, would resolve differently.
- **The hash is a job too.** `custom_model_hash` is a `maintenance` job at its default priority. The hash runs in a
  thread that a shutdown abandons.

## Alternatives
- **The route calls the orchestrator itself.** It would race the scheduler, which could ask for `llm` before the
  release, and it could not wait for the running reply without its own lock.
- **A new job kind.** It needs a migration of the kind constraint, while `maintenance` already has mode `none` and one
  attempt.
- **The tier in an application setting written by `setup.cmd`.** It goes stale when the `.wslconfig` memory changes, and
  setup does not write settings yet.
- **Hash in a separate worker.** The single worker keeps the jobs' state changes in one place (ADR 0010). A hash of a
  20 GB file delays the jobs behind it, which is acceptable for an action the user starts and sees in the queue.

## Consequences
- Jobs queued behind a reload or a hash wait for it. A reload waits at most 180 s for the release.
- A reload cut by a restart ends with `JOB_INTERRUPTED`, its only attempt. The next start's scheduler asks for `llm`
  again, so the runtime comes back with the launch file as it is.
- A model file that is a link, even inside its folder, cannot be registered. `import-model` copies a file, so it never
  creates one.
- The tier the backend reads can differ from the one the scripts used when the memory of the VM changed between them;
  the backend's view is the current one.
