# Documentation checks, 2026-11-01

Scope: the documentation-only state of the repository. No application code exists, so no application test could run.

The checks below ran from a temporary script outside the repository. Roadmap item P01.8 adds equivalent checks to CI.

## Results

| Check | Result |
| --- | --- |
| Relative links and heading anchors in all Markdown files | 340 checked, 0 broken |
| Unfinished-work markers in any document | 0 found |
| Requirements traced to a design, phase and test | 172 of 172 |
| Acceptance criteria traced | 20 of 20 |
| Tests in the catalog that belong to a roadmap exit | 128 of 128 |
| Test identifiers used anywhere but missing from the catalog | 0 |
| Required tables named in the data model | 20 of 20 |
| Required services named in the backend document | 20 of 20 |
| Required GPU states | 11 of 11 |
| Palette tokens | 9 of 9 |
| Required scripts in the operations guide | 6 of 6 |
| Required README sections | 14 of 14 |
| Required screens in the frontend document | 10 of 10 |
| JSON samples that parse | 7 of 7 |
| ADRs with all sections and listed in the index | 23 of 23 |
| CRLF line endings | 0 files |
| Forbidden file types, real `.env`, files over 1 MB | 0 found |
| Pattern scan for tokens, private keys, home paths and the owner's private identifiers | 0 found |

## Other verified items

| Item | How | Result |
| --- | --- | --- |
| `LICENSE` | `diff` against `https://www.apache.org/licenses/LICENSE-2.0.txt` | Identical |
| Reference Compose file | Extracted from [Operations](../operations.md#reference-compose-file), then `docker compose --profile ai config --quiet` with Compose v5.6.1 | Exit 0. Five services with the profile, three without |
| Ignore rules | `git check-ignore` on `models/x.gguf`, `data/db/lyrveil.sqlite3`, `.env`, `backups/a.tar` | All ignored |
| Files that must stay tracked | `git check-ignore` on `.env.example`, `models/.gitkeep`, `data/.gitkeep` | None ignored |
| Designed SQLite constraints | In-memory test with SQLite 3.53.4 | The partial unique index on `memories` refused a duplicate and allowed a superseded copy. The scope `CHECK` refused a conversation-scoped row with no conversation |
| FTS5 weights | `bm25(memory_fts, 0.0, 2.0, 1.0)` with an unindexed first column | Works, ranks the two-term match first |
| Palette contrast | WCAG formula, computed | Values in [Design system](../design-system.md#contrast) |
| Mermaid diagrams | Opened on GitHub after the first push, on 2026-11-02, in `README.md`, `architecture.md`, `data-model.md`, `gpu-orchestration.md` and `memory-engine.md` | All 7 render. P01.8 adds a parser to CI |

## Not executed

| Item | Reason | Follow-up |
| --- | --- | --- |
| External links | Not requested from each host. Main upstream pages were read during the research | P09 audit |
| gitleaks | Not installed on the reference machine. The pattern scan above is narrower | P09 runs it on the whole history |
| `.dockerignore` | No image exists yet | T-DCK-10 in P01 |
| Everything else in [Testing](../testing.md) | No code exists | Assigned phases |
