# Contributing to Lyrveil

Lyrveil keeps private data on one machine and controls a GPU through Docker. These
rules keep it trustworthy. A change is finished only when it is verified,
documented and committed cleanly.

Implementation starts at roadmap phase P01: see [docs/roadmap.md](docs/roadmap.md).
Commands in this guide apply from that phase on.

## Engineering rules

- Read the requirement before changing behavior: [docs/requirements.md](docs/requirements.md).
- Call only the runtime endpoints and flags listed in
  [docs/runtime-contracts.md](docs/runtime-contracts.md). To use a new one, verify it
  at the pinned upstream tag and add it to that document in the same commit.
- Do not add a service, framework or dependency without a concrete need. A new
  infrastructure service, such as a database or a queue, needs an ADR.
- Do not add a cloud dependency or another AI model. Do not replace the two chosen
  models without the owner's authorization.
- Do not put mock data in a production path. Fake runtimes live under `tests/fakes/`
  and never enter a production image.
- Do not ship a control without behavior, and do not present a stub as working.
- Do not weaken persistence to simplify an implementation.
- Diagnose a problem concretely before adding a workaround.
- Prefer a simple, solid implementation. Record any limit you leave behind.

## Finish-a-change checklist

Complete it in order before committing:

```text
- [ ] 1. Run the real checks; note exact results (pass, fail, skipped)
- [ ] 2. Update the roadmap item; check it only if its exit checks passed
- [ ] 3. Update CHANGELOG.md for any user-visible change
- [ ] 4. Update every affected document; keep "delivered" and "planned" distinct
- [ ] 5. Review the diff: intended files only, no secrets, no unrelated edits
- [ ] 6. Write a neutral imperative commit message
- [ ] 7. Commit with your configured identity; never bypass hooks
```

If a check fails, fix it and run it again. Record partial work as partial, and
name what remains. Never document an unexecuted check as passed.

## Checks

```powershell
.\scripts\lyrveil.cmd test lint
.\scripts\lyrveil.cmd test unit
.\scripts\lyrveil.cmd test integration
.\scripts\lyrveil.cmd test e2e
```

From Git Bash, write `scripts/lyrveil.cmd test lint`. Run `lint` before every commit. It checks the documentation:
relative links, heading anchors, unfinished-work markers, requirement and acceptance criterion traceability, and the
test catalog against the documents and the roadmap exits. It also runs PSScriptAnalyzer on every PowerShell script,
and ruff, the format check and `mypy --strict` on the backend inside its Docker test stage, so Docker must be running.
`test unit -k name` passes the arguments to pytest and runs only the matching backend tests. The command fetches its pinned tools into the Git-ignored `.tools\` folder and installs nothing machine-wide. A level
with no tests yet exits 1 and never reports a pass. Details are in
[docs/testing.md](docs/testing.md#the-lint-level).

Run `.\scripts\lyrveil.cmd test gpu` when the change touches the runtimes, the orchestration
or the image pipeline and the hardware is available. See
[docs/testing.md](docs/testing.md).

## Code

- Write identifiers, comments and documentation in English. Product text is in
  Italian and lives in the template and translation catalogs.
- Start every module with a one-line statement of its responsibility.
- Comments state contracts and reasons: what a function promises and requires, what
  always holds, how it fails, and why a non-obvious decision was made. Do not narrate
  syntax.
- Python: Python 3.12, `ruff`, `mypy --strict`, `pytest`. Blocking code never runs
  inside `async def`.
- TypeScript: strict mode, standalone components, signals, zoneless. See
  [docs/frontend.md](docs/frontend.md).
- Windows scripts: compatible with Windows PowerShell 5.1, `Set-StrictMode -Version Latest`, idempotent, checked
  with PSScriptAnalyzer and tested with Pester.
- Shell scripts inside images: `set -euo pipefail`, checked with `shellcheck`.
- Never bind-mount source code or `node_modules` into a container. Builds read the Windows folder directly, while a
  bind mount from `C:` is slow ([ADR 0029](docs/decisions/0029-windows-operation-with-docker-volumes.md)).
- Never log message content, prompts or memory text at any default log level.

## Documentation

- Update the roadmap, the changelog and the affected documents in the same commit
  as the code they describe.
- Record each material decision as a short ADR in
  [docs/decisions/](docs/decisions/README.md).
- Use the present tense and short sentences. Give exact commands, versions and paths.
- Label planned work as planned.

## Commits

- Use a concise English imperative subject, ideally 72 characters or fewer, that
  describes the resulting behavior: `Keep queued replies across image generation`.
- Add a body only to explain the change, its reason, the checks or its limits.
- Keep authorship with the human committer: no tool or vendor references and no
  co-author or "generated by" trailers.
- Commit only coherent, verified steps. Never rewrite shared history, never
  force-add ignored files, and never use `--no-verify`.

## Privacy in the repository

Never commit model weights, databases, conversations, memories, user images, real
`.env` files, tokens, logs or backups. Tests, fixtures and screenshots use only
original demo characters and synthetic data. The full list and the pre-publication
audit are in [docs/publishing.md](docs/publishing.md).

## License

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE).
