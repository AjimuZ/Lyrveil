# Import, backup and restore, 2026-11-07

Task P01.6d, from Git Bash on Windows 11 with Docker Desktop, on 2026-11-07. Only what was run is listed as run.

## Written

- `backend/src/lyrveil/tools/import_model.py`, run as `python -m lyrveil.tools.import_model`: standard library only,
  refuses another extension, a name outside the pattern and an existing name, and copies through a temporary name and
  a hard link, so no file is ever replaced.
- `backend/src/lyrveil/cli.py`, run as `python -m lyrveil.cli`: `backup`, `export-backups`, `restore-check` and
  `restore`. `known_revisions()` in `persistence/migrations.py` lists the revisions the installed app knows.
- `scripts/lib/DataFiles.psm1` and the commands `import-model`, `backup` and `restore` in `scripts/lyrveil.ps1`. Every
  container runs with `docker run --network none --read-only --cap-drop ALL` as `10001:10001`. `Get-EnvFileValue` of
  `scripts/lib/Setup.psm1` is now exported, for `LYRVEIL_BACKUP_DIR`.
- `backend/tests/unit/test_cli.py` (26 tests) and `backend/tests/unit/test_import_model_tool.py` (16 tests), on
  temporary folders.
- `tests/scripts/DataFiles.Tests.ps1`, at the `unit` level, and `tests/docker/Data.Tests.ps1` with
  `tests/docker/compose.data-test.yaml`, the override of the test project `lyrveil-data-test`, at the `docker` level.

## Run

- `scripts/lyrveil.cmd test unit -k "cli or import_model"`: 43 backend tests passed (the 42 new ones and the Docker
  socket scan, whose name matched), and the 3 orchestrator tests that the expression matched. Run again after the
  lint fixes, with the same result.
- `scripts/lyrveil.cmd test lint`: the first run found one PSScriptAnalyzer finding in `Data.Tests.ps1` and three mypy
  errors in the new Python code. They were fixed, and the level then passed.

## Not run

- `tests/scripts/DataFiles.Tests.ps1` and `tests/docker/Data.Tests.ps1`: written, not run in this task. The orchestrator
  runs the `unit` and `docker` levels at the end of the plan. So these are unverified: a container of user `10001`
  writing into a Windows bind mount (the export to the backup folder), the `--mount` syntax with a Windows source, a
  backup taken while the backend runs, and the restore followed by `start.cmd`.
- The commands against the real project `lyrveil`, its volumes and `%USERPROFILE%\Documents\Lyrveil\backups`.
- The free-space refusals of `import-model` (exit 2) and `restore`, a restore from an older schema that migrates at
  start, and an interactive yes typed at the console: the tests answer through mocks.
