# ADR 0011: FTS5 with Snowball stemming, no embeddings

Status: accepted (2026-11-01). Supersedes: none.

## Context
The requirements fix SQLite FTS5 and BM25, forbid an embedding model in the first version, and ask for a `MemoryRetriever` seam. FTS5 has no Italian stemmer, so `lavoravo` would not match `lavoro`. On 2026-11-01, `bm25()` returned `-1e-06` on a one-row table, so absolute scores are unusable on small tables.

## Decision
- Stem text in Python with `snowballstemmer` and store the stems in the FTS columns, using `unicode61 remove_diacritics 2`.
- Build queries from quoted stems only, so user text cannot inject FTS syntax.
- Normalize BM25 within each candidate set.
- Combine it with importance, recency, topic, kind, confidence, status and hard scope filters.
- Add standing candidates that apply regardless of words.
- `retrieve_relevant_memories` is the Python name of the required `retrieveRelevantMemories`.

## Alternatives
- FTS5 `porter`: English only.
- FTS5 `trigram`: matches fragments, noisy for retrieval and bigger on disk.
- `sqlite-vec` or embeddings: excluded by the requirements for this version.
- A Python-only ranking of all memories: simple, but slow, and it would drop the required FTS5.

## Consequences
Retrieval remains deterministic and offline. Synonyms and paraphrases will be missed, and that is the method's known limit. P04 measures recall on a synthetic fixture with a target of 80 % at 8. A semantic retriever can replace this implementation without touching callers, after a new ADR.
