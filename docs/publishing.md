# Publishing

Status: accepted (2026-11-01). The application release is phase P09. This guide also covers publishing the documentation before any code exists.

The public repository is named `Lyrveil`. It contains only code, example configuration, documentation, tests and reusable original assets.

## Never commit

| Category | Examples | Guard |
| --- | --- | --- |
| Weights | `*.gguf`, `*.safetensors`, `*.ckpt`, `*.pt`, `*.pth`, `*.onnx`, `models/*` | `.gitignore`, `.dockerignore` |
| User data | `data/*`, `*.sqlite3`, conversations, memories | `.gitignore`, `.dockerignore` |
| Images | Generated images, personal reference images | Under `data/`. Only reviewed demo assets elsewhere |
| Secrets | `.env`, tokens, passwords, keys | `.gitignore`, gitleaks |
| Noise | Caches, logs, backups, temporary files, Docker volumes | `.gitignore` |

Private data never enters the history, not even temporarily. If it happens before a push, rewrite the local unpublished commit. If it reaches a remote, treat a token as compromised and rotate it first, then purge the history. A personal text in public history is public, so assess that with the owner.

## Commit rules

See [CONTRIBUTING](../CONTRIBUTING.md#commits). Commits are progressive and readable, and unverified code is not pushed.

**Commit identity.** Git records the configured `user.name` and `user.email` in each commit, and a public repository shows them. Before the first push to a public remote, the owner should confirm that identity. GitHub offers a private `noreply` address for that purpose. This is the owner's decision, and the project's tools never change it.

## First publication

The steps below publish whatever the main branch holds at that moment. For the documentation phase that means documents only.

### With the GitHub CLI

Install and authenticate once:

```powershell
winget install --id GitHub.cli
```

```bash
gh auth login
```

Then, from the repository:

```bash
gh repo create AjimuZ/Lyrveil --public --source=. --remote=origin --push --description "Local simulated messenger with AI characters, persistent memory and generated images"
```

If `origin` already exists, create the repository and push separately:

```bash
gh repo create AjimuZ/Lyrveil --public --description "Local simulated messenger with AI characters, persistent memory and generated images"
```

```bash
git push -u origin main
```

### Without the GitHub CLI

1. Open `https://github.com/new`.
2. Name the repository `Lyrveil`, choose **Public**, and add no README, license or `.gitignore`. They already exist.
3. Create it, and then push:

```bash
git remote add origin https://github.com/AjimuZ/Lyrveil.git
```

```bash
git push -u origin main
```

Skip the first command when `origin` exists.

### After the first push

- Enable **Private vulnerability reporting** in the repository's security settings. [SECURITY.md](../SECURITY.md) relies on it.
- Enable secret scanning and push protection if available.
- Protect the main branch from force pushes.

## Pre-publication audit

Required before the release in P09. Each result goes to `docs/verification/YYYY-MM-DD-release.md`.

| # | Check | Command |
| --- | --- | --- |
| 1 | Tests | `.\scripts\lyrveil.cmd test all`, plus `gpu` and `quality` on the reference machine |
| 2 | Python dependencies | `uv run pip-audit` in `backend/` and `orchestrator/` |
| 3 | npm dependencies | `npm audit` and `npm audit signatures` in `frontend/` |
| 4 | Secrets, whole history | `gitleaks detect --source . --log-opts="--all"` |
| 5 | Large files, whole history | See below. Nothing over 5 MB without a reviewed reason |
| 6 | Personal data | Search the tree and history for real names, addresses, home paths and tokens. Review fixtures and screenshots |
| 7 | History | `git log --all --stat`. Check messages, authors and each added file |
| 8 | Licenses | Generate notices, apply the [policy](licensing.md#policy) |
| 9 | Clean build | Fresh clone, `.\setup.cmd`, `.\start.cmd`, acceptance run |
| 10 | Ignore rules | `git check-ignore` on sample paths. `git ls-files` lists no forbidden pattern |

Large files in history:

```bash
git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | awk '$1 == "blob" && $3 > 5242880'
```

Ignore rules:

```bash
git check-ignore models/x.gguf data/db/lyrveil.sqlite3 .env backups/a.tar
```

```bash
git ls-files | grep -Ei '\.(gguf|safetensors|ckpt|pt|pth|onnx|sqlite3?|db|env)$'
```

The second command must print nothing.

## Documentation phase checks

The checks for the documentation-only commit are recorded in [the documentation verification](verification/2026-11-01-documentation.md). A gitleaks run is not part of them, because the tool is not installed on the reference machine. An equivalent pattern scan ran instead, and P09 runs gitleaks on the whole history.

## Release

1. Complete the audit and the [acceptance criteria](testing.md#acceptance).
2. Move `Unreleased` to `1.0.0` with the date in [CHANGELOG](../CHANGELOG.md).
3. Tag and publish:

```bash
git tag -a v1.0.0 -m "Lyrveil 1.0.0"
```

```bash
git push origin v1.0.0
```

```bash
gh release create v1.0.0 --title "Lyrveil 1.0.0" --notes-file docs/verification/release-notes-1.0.0.md
```

No prebuilt images and no weights are attached to a release ([Licensing](licensing.md#ai-runtimes)).
