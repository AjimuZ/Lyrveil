# ADR 0008: Bind mounts on the WSL2 Linux filesystem

Status: accepted (2026-11-01). Supersedes: none.

## Context
The requirements prefer the WSL2 Linux filesystem for sources, models and data, and they sketch `data/` and `models/` in the repository. SQLite in WAL mode needs shared memory and reliable locks, which network or translated filesystems such as `/mnt/c` do not guarantee. Models are 14.42 GB and must be reachable by the download script. Data must survive container and image removal.

## Decision
- `data/` and `models/` are bind mounts, by default inside the clone on the Ubuntu ext4 disk. The variables `LYRVEIL_DATA_DIR` and `LYRVEIL_MODELS_DIR` can move them.
- `start.sh` refuses a data directory under `/mnt/*`, and warns for models.
- Containers run as the host user's UID and GID, so files stay accessible and backups work without root.

## Alternatives
- Named Docker volumes: good performance, but tied to Docker Desktop's storage, at risk from `down -v` or a factory reset, and harder to back up and inspect. General Compose guidance prefers them, but this project's requirements and data ownership point the other way.
- Project on `C:`: easy to browse from Windows, but slow and unsafe for WAL.

## Consequences
The current checkout on `C:` is suitable for documentation only. From P01 on, development needs a clone in Ubuntu. Files are visible from Windows at `\\wsl$`. Removing the Ubuntu distribution deletes the data, so the documentation recommends backups.
