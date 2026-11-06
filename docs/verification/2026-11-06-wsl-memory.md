# WSL2 memory check, 2026-11-06

Confirms that the 32 GB `.wslconfig` applied on 2026-11-05 ([record](2026-11-05-environment.md#after-the-changes)) is still in effect. Nothing was changed.

Command, run from Git Bash:

```text
docker info --format "{{.MemTotal}}"
```

Result: `25199247360` bytes, which is 23.47 GiB (25199247360 / 1073741824).

The pass threshold of the preflight table in [operations](../operations.md) is at least 20 GiB for the memory Docker sees. 23.47 GiB meets it.
