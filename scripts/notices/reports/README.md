# License reports

`pip-licenses --format=csv` output for the locked Python dependencies, read by `../generate.mjs`.
`<project>-all.csv` holds every group, `<project>-prod.csv` the production group only. The packages in `all` but not in `prod` are development-only.

Regenerate one file (Git Bash, Docker running). Replace `backend` with `orchestrator` as needed, and drop `--no-dev` for the `all` file:

```bash
docker run --rm -v "$PWD":/repo:ro ghcr.io/astral-sh/uv:0.13.6-python3.12-trixie-slim sh -c "uv export --frozen --no-emit-project --no-dev --no-hashes --project /repo/backend > /tmp/req.txt && uv venv /tmp/v -q && uv pip install -q --python /tmp/v/bin/python --no-deps -r /tmp/req.txt && uvx pip-licenses --python /tmp/v/bin/python --format=csv --with-system --ignore-packages pip"
```

Save the output over the CSV file, drop the `Installed ...` line, then run `node scripts/notices/generate.mjs`. After any lock change the generator fails until the reports match `uv.lock`.
