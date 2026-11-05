# ADR 0029: Operation from Windows, with live data and models in Docker volumes

Status: accepted (2026-11-05). Supersedes: [ADR 0008](0008-persistence-on-wsl2-ext4-bind-mounts.md), [ADR 0022](0022-root-scripts-and-repository-layout.md).

## Context
On 2026-11-05 the owner decided not to work inside Linux: the repository stays on `C:` and Lyrveil is operated from Windows. Docker Desktop still runs Linux containers in its own WSL2 virtual machine, which the user never opens.

The same day the reference machine measured a folder on `C:` bind-mounted into a container against a Docker named volume. Each figure is the range of two runs, with 1 GiB files and 300 small files ([verification](../verification/2026-11-05-environment.md#storage-from-windows)):

| Operation | Folder on `C:` | Named volume |
| --- | --- | --- |
| Write 1 GiB with `fsync` | 320 to 352 MB/s | 2.3 to 2.6 GB/s |
| Read 1 GiB again | 373 to 429 MB/s, never cached | 14.7 GB/s from the page cache |
| Create 300 small files | 317 to 344 ms | 3 to 4 ms |

A Windows folder would add about 36 s to every reload of the 14.44 GB text model, and SQLite in WAL mode needs shared memory and locks that a translated filesystem does not guarantee. A named volume lives in Docker Desktop's disk image, which is itself on `C:`.

The brief asked for Bash scripts named like `start.sh`. The owner's decision replaces that request ([R-OPS-05](../requirements.md#16-docker-and-startup)).

## Decision
- The repository is a normal Windows folder, for example `%USERPROFILE%\Desktop\projects\lyrveil`. Git for Windows clones it. No Linux distribution is needed besides Docker Desktop's own.
- The root holds `setup.cmd`, `start.cmd`, `stop.cmd`, `restart.cmd`, `status.cmd` and `logs.cmd`. Each is one line that runs `scripts\lyrveil.ps1` with Windows PowerShell 5.1, `-NoProfile -ExecutionPolicy Bypass`, and passes its arguments. `scripts\lyrveil.cmd` does the same for the other commands: `models`, `import-model`, `backup`, `restore` and `test`. The bypass applies to that one process and never changes the machine's policy.
- Live data and model files are in two external named volumes, `lyrveil-data` and `lyrveil-models`, created by `setup.cmd` with the label `io.lyrveil.volume`. Compose declares them `external: true`, so `docker compose down -v` cannot remove them.
- Every service runs as UID and GID 10001. `setup.cmd` sets the owner of new volumes once, with a short container that has no network and only `CAP_CHOWN`.
- Model downloads run in a one-shot `lyrveil-models` service of the `tools` profile. It is the only container with a route out, it mounts only the models volume, and it reads URLs, sizes and hashes from the catalog. The runtimes stay on internal networks.
- Backups are exported to a Windows folder, by default `%USERPROFILE%\Documents\Lyrveil\backups`. The backend makes one backup a day while idle, and `start.cmd` and `stop.cmd` copy any backup missing from the Windows folder.
- `.gitattributes` keeps LF for everything that runs in containers and uses CRLF for `*.cmd` and `*.ps1`.

## Alternatives
- Clone and run inside Ubuntu on WSL2 ([ADR 0008](0008-persistence-on-wsl2-ext4-bind-mounts.md)): same speed as volumes, but the owner does not want to work in Linux.
- Data and models in folders on `C:`: files visible in Explorer, but the measured cost above, and WAL is unsafe.
- Ollama as a native Windows text server: reads models from NTFS at full speed and manages them easily. It offers no DRY sampler, no tokenize endpoint and no control over which expert weights stay in RAM, and it would run outside the orchestrator's control of the GPU. Its engine is the same llama.cpp, so it would not be faster.
- KoboldCpp as a Windows program: full speed and all features, but a container cannot start or stop a Windows process, so GPU exclusivity would need a privileged helper on the host.
- Bash scripts run with Git Bash: closer to the brief, but Docker path conversion in MSYS is fragile and the owner asked for Windows.

## Consequences
- Performance equals the Linux layout. Weights and the database are not browsable in Explorer. The app manages them, and backups are ordinary files.
- Resetting Docker Desktop's data, or uninstalling it, deletes the volumes. Daily backups in the Windows folder limit the loss to one day. The README says so.
- Custom models are imported with `scripts\lyrveil.cmd import-model <file>`, which copies a Windows file into the models volume.
- Script tests use Pester and PSScriptAnalyzer. Shell scripts that run inside images keep `shellcheck`.
- Development never bind-mounts source code. Python tests run in a test stage of the backend image, and frontend
  tests run with Node.js on Windows.
- Volume subpaths need Engine API 1.45, so preflight requires it.
- The Ubuntu distribution installed on the reference machine on 2026-11-05 is not needed.
