"""T-MEM-05, T-MEM-15, T-MEM-22, T-CON-01 and T-CON-02: FtsMemoryRetriever on a migrated database.

Checks the ranking and its score parts, the token budget, the maximum of items and the caps per kind, the subject and
conflict-group rules, the statuses each query may see, the episodes, the isolation of characters and conversations
with shared memory only on opt-in, the use counters, and recall at 8 on the synthetic set of
`tests/fixtures/memory_retrieval/evaluation.json`.
"""

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.jobs.scheduler import read, write
from lyrveil.memory.retrieval import MAX_EPISODES, MAX_PER_KIND, is_past_intent
from lyrveil.memory.text import fingerprint
from lyrveil.persistence.memory_index import index_episode, index_memory
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.memory_retriever import RetrievalQuery, RetrievalResult
from lyrveil.services.memory_retrieval import FtsMemoryRetriever

NOW = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
EVALUATION = Path(__file__).resolve().parents[1] / "fixtures" / "memory_retrieval" / "evaluation.json"
RECALL_TARGET = 0.8


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


def add_character(connection: Connection) -> str:
    character_id = new_id()
    connection.execute(
        text(
            "INSERT INTO characters (id, first_name, age, created_at, updated_at) VALUES (:id, 'Alba', 30, :now, :now)"
        ),
        {"id": character_id, "now": to_utc_text(NOW)},
    )
    return character_id


def add_chat(connection: Connection, character_id: str, *, shares_memory: bool = False) -> str:
    conversation_id = new_id()
    connection.execute(
        text(
            "INSERT INTO conversations (id, character_id, title, language, shares_memory, created_at, updated_at) "
            "VALUES (:id, :character_id, 'Chat', 'it', :shares, :now, :now)"
        ),
        {"id": conversation_id, "character_id": character_id, "shares": int(shares_memory), "now": to_utc_text(NOW)},
    )
    return conversation_id


def add_memory(
    connection: Connection,
    character_id: str,
    conversation_id: str | None,
    content: str,
    *,
    kind: str = "user_fact",
    subject: str | None = None,
    status: str = "active",
    importance: int = 3,
    provenance: str = "stated",
    durability: str = "lasting",
    expires_at: datetime | None = None,
    locked: bool = False,
    conflict_group_id: str | None = None,
    updated_at: datetime = NOW,
) -> str:
    """Inserts and indexes a memory (shared when `conversation_id` is None) and returns its id."""
    memory_id = new_id()
    connection.execute(
        text(
            "INSERT INTO memories (id, character_id, scope, conversation_id, kind, content, subject_key, provenance, "
            "durability, status, importance, confidence, locked, expires_at, conflict_group_id, fingerprint, "
            "language, created_at, updated_at) VALUES (:id, :character_id, :scope, :conversation_id, :kind, :content, "
            ":subject, :provenance, :durability, :status, :importance, :confidence, :locked, :expires_at, :group, "
            ":fingerprint, 'it', :updated, :updated)"
        ),
        {
            "id": memory_id,
            "character_id": character_id,
            "scope": "shared" if conversation_id is None else "conversation",
            "conversation_id": conversation_id,
            "kind": kind,
            "content": content,
            "subject": subject,
            "provenance": provenance,
            "durability": durability,
            "status": status,
            "importance": importance,
            "confidence": 0.6 if provenance == "inferred" else 0.95,
            "locked": int(locked),
            "expires_at": None if expires_at is None else to_utc_text(expires_at),
            "group": conflict_group_id,
            "fingerprint": fingerprint(content, "it"),
            "updated": to_utc_text(updated_at),
        },
    )
    index_memory(connection, memory_id, content, subject, "it")
    return memory_id


def add_episode(
    connection: Connection,
    character_id: str,
    conversation_id: str,
    title: str,
    description: str,
    *,
    importance: int = 3,
    updated_at: datetime = NOW,
) -> str:
    episode_id = new_id()
    connection.execute(
        text(
            "INSERT INTO episodic_memories (id, character_id, conversation_id, title, description, occurred_at, "
            "importance, fingerprint, created_at, updated_at) VALUES (:id, :character_id, :conversation_id, :title, "
            ":description, :updated, :importance, :fingerprint, :updated, :updated)"
        ),
        {
            "id": episode_id,
            "character_id": character_id,
            "conversation_id": conversation_id,
            "title": title,
            "description": description,
            "importance": importance,
            "fingerprint": fingerprint(f"{title} {description}", "it"),
            "updated": to_utc_text(updated_at),
        },
    )
    index_episode(connection, episode_id, title, description, "it")
    return episode_id


def query(character_id: str, conversation_id: str, words: str, **overrides: Any) -> RetrievalQuery:
    fields: dict[str, Any] = {
        "character_id": character_id,
        "conversation_id": conversation_id,
        "text": words,
        "topic": None,
        "language": "it",
        "token_budget": 900,
        "now": NOW,
        "include_shared": False,
        "past_intent": False,
        "previous_ids": frozenset(),
        **overrides,
    }
    return RetrievalQuery(**fields)


def ids(result: RetrievalResult) -> list[str]:
    return [item.id for item in result.items]


def flat_cost(line: str) -> int:
    return 10


def test_ranking_follows_the_weighted_score_and_keeps_its_parts(migrated: Engine) -> None:
    """T-MEM-05, ranking: a closer, more important and more recent match comes first, a standing memory joins without
    a match, and an unrelated memory is no candidate."""

    def seed(connection: Connection) -> tuple[str, str, list[str]]:
        character = add_character(connection)
        chat = add_chat(connection, character)
        made = [
            add_memory(connection, character, chat, "Lavora come infermiera all'ospedale San Carlo.", importance=4),
            add_memory(
                connection,
                character,
                chat,
                "Ha visitato l'ospedale da bambina.",
                importance=1,
                updated_at=NOW - timedelta(days=200),
            ),
            add_memory(connection, character, chat, "Si chiama Giulia.", locked=True),
            add_memory(connection, character, chat, "Ama il mare d'inverno."),
        ]
        return character, chat, made

    character, chat, (nurse, visit, name, sea) = write(migrated, seed)
    retriever = FtsMemoryRetriever(migrated)
    result = retriever.retrieve_relevant_memories(query(character, chat, "Oggi in ospedale, che fatica"))
    assert ids(result) == [nurse, visit, name]
    assert sea not in ids(result)
    assert result.candidate_count == 3
    first, second, third = result.items
    assert first.score > second.score > third.score
    # Two matches: the shorter text has the higher BM25, so the parts are 0.5 and 1.0; the standing one has none.
    assert (first.parts.fts, second.parts.fts, third.parts.fts) == (0.5, 1.0, 0.0)
    assert second.parts.recency == pytest.approx(math.exp(-math.log(2) * 200 / 30))
    assert third.parts.recency == 1.0
    assert third.parts.locked_bonus == 0.10
    for item in result.items:
        parts = item.parts
        weighted = 0.40 * parts.fts + 0.20 * parts.importance + 0.15 * parts.recency
        weighted += 0.10 * parts.topic + 0.10 * parts.kind + 0.05 * parts.confidence
        expected = weighted * parts.status_factor + parts.locked_bonus + parts.previous_bonus
        assert item.score == pytest.approx(min(1.0, expected))
    assert first.label == "[sicuro]"
    assert first.line == "- [sicuro] Lavora come infermiera all'ospedale San Carlo."
    assert result.token_cost == sum(item.token_cost for item in result.items)


def test_the_topic_and_the_previous_selection_raise_the_score(migrated: Engine) -> None:
    """T-MEM-05, ranking: of two equal matches, the one that shares the topic, then the one used for the previous
    reply, comes first; the bonus for the previous reply is exactly 0.05."""

    def seed(connection: Connection) -> tuple[str, str, str, str]:
        character = add_character(connection)
        chat = add_chat(connection, character)
        sicily = add_memory(connection, character, chat, "Ama il mare di Sicilia.")
        sardinia = add_memory(connection, character, chat, "Ama il mare di Sardegna.")
        return character, chat, sicily, sardinia

    character, chat, sicily, sardinia = write(migrated, seed)
    retriever = FtsMemoryRetriever(migrated)
    plain = retriever.retrieve_relevant_memories(query(character, chat, "il mare"))
    assert plain.items[0].score == plain.items[1].score
    by_topic = retriever.retrieve_relevant_memories(query(character, chat, "il mare", topic="vacanze in Sardegna"))
    assert ids(by_topic)[0] == sardinia
    assert by_topic.items[0].parts.topic == pytest.approx(0.5)
    again = retriever.retrieve_relevant_memories(query(character, chat, "il mare", previous_ids=frozenset({sicily})))
    assert ids(again) == [sicily, sardinia]
    assert again.items[0].score - again.items[1].score == pytest.approx(0.05)


def test_the_token_budget_the_maximum_and_the_caps_per_kind_bound_the_selection(migrated: Engine) -> None:
    """T-MEM-05, budget and maximum: items enter while their cost fits; never more than `max_items`, six of one kind
    or three episodes."""

    def seed(connection: Connection) -> tuple[str, str]:
        character = add_character(connection)
        chat = add_chat(connection, character)
        for number in range(10):
            add_memory(connection, character, chat, f"Il gatto numero {number} dorme sul divano.")
        for number in range(3):
            add_memory(connection, character, chat, f"Il gatto {number} ama la pioggia.", kind="user_preference")
        for number in range(5):
            add_episode(connection, character, chat, f"Il gatto in fuga {number}", f"È scappato {number} volte.")
        return character, chat

    character, chat = write(migrated, seed)
    retriever = FtsMemoryRetriever(migrated, token_cost=flat_cost)
    wide = retriever.retrieve_relevant_memories(query(character, chat, "gatto", token_budget=10_000))
    kinds = [item.kind for item in wide.items]
    assert kinds.count("user_fact") == MAX_PER_KIND
    assert kinds.count("user_preference") == 3
    assert kinds.count("episode") == MAX_EPISODES
    assert len(wide.items) == 12
    assert wide.candidate_count == 18
    assert [item.score for item in wide.items] == sorted((item.score for item in wide.items), reverse=True)

    tight = retriever.retrieve_relevant_memories(query(character, chat, "gatto", token_budget=35))
    assert len(tight.items) == 3
    assert tight.token_cost == 30
    assert ids(tight) == ids(wide)[:3]
    assert len(retriever.retrieve_relevant_memories(query(character, chat, "gatto", max_items=4)).items) == 4
    assert retriever.retrieve_relevant_memories(query(character, chat, "gatto", token_budget=0)).items == ()
    # The default cost prices each rendered line from its length.
    estimating = FtsMemoryRetriever(migrated)
    estimated = estimating.retrieve_relevant_memories(query(character, chat, "gatto", token_budget=40))
    assert 0 < estimated.token_cost <= 40
    assert all(item.token_cost == -(-len(item.line) // 3) + 1 for item in estimated.items)


def test_one_memory_per_subject_and_a_conflict_group_stays_whole(migrated: Engine) -> None:
    """T-MEM-05, selection: the best memory of a subject wins; both sides of a conflict enter together, labeled
    uncertain, even when the query reaches one of them, or neither enters."""

    def seed(connection: Connection) -> tuple[str, str, list[str]]:
        character = add_character(connection)
        chat = add_chat(connection, character)
        group = new_id()
        made = [
            add_memory(
                connection,
                character,
                chat,
                "Il suo gatto si chiama Briciola.",
                subject="user.pet",
                importance=4,
            ),
            add_memory(
                connection,
                character,
                chat,
                "Il suo gatto è nero.",
                subject="user.pet",
                importance=2,
                updated_at=NOW - timedelta(days=300),
            ),
            add_memory(
                connection,
                character,
                chat,
                "Vive a Roma.",
                subject="user.city",
                status="uncertain",
                conflict_group_id=group,
            ),
            add_memory(
                connection,
                character,
                chat,
                "Vive a Milano.",
                subject="user.city",
                status="uncertain",
                conflict_group_id=group,
            ),
        ]
        return character, chat, made

    character, chat, (briciola, black, rome, milan) = write(migrated, seed)
    retriever = FtsMemoryRetriever(migrated, token_cost=flat_cost)
    pets = retriever.retrieve_relevant_memories(query(character, chat, "il gatto"))
    assert ids(pets) == [briciola]
    assert black not in ids(pets)

    cities = retriever.retrieve_relevant_memories(query(character, chat, "Roma"))
    assert ids(cities)[0] == rome
    assert set(ids(cities)[:2]) == {rome, milan}
    assert [item.label for item in cities.items[:2]] == ["[incerto]", "[incerto]"]
    assert cities.items[1].parts.fts == 0.0

    # The group costs 20, so it does not fit a budget of 15, and neither side is shown alone.
    small = retriever.retrieve_relevant_memories(query(character, chat, "Roma gatto", token_budget=15))
    assert ids(small) == [briciola]


def test_statuses_expiry_and_identity_candidates_are_filtered_before_ranking(migrated: Engine) -> None:
    """T-MEM-05, candidates: only active and uncertain memories in use are seen; superseded ones join under past
    intent, labeled `[passato]`, beside the current fact of their subject."""

    def seed(connection: Connection) -> tuple[str, str, dict[str, str]]:
        character = add_character(connection)
        chat = add_chat(connection, character)
        made = {
            "current": add_memory(connection, character, chat, "Lavora da Verdi & Co.", subject="user.employer"),
            "past": add_memory(
                connection, character, chat, "Lavorava allo Studio Rossi.", subject="user.employer", status="superseded"
            ),
            "temporary": add_memory(
                connection,
                character,
                chat,
                "Questa settimana lavora da casa.",
                durability="temporary",
                expires_at=NOW + timedelta(days=2),
            ),
            "inferred": add_memory(connection, character, chat, "Forse lavora troppo.", provenance="inferred"),
            "story": add_memory(
                connection,
                character,
                chat,
                "Avete trovato un lavoro da pirati.",
                kind="narrative_event",
                provenance="fictional",
            ),
            "elapsed": add_memory(
                connection,
                character,
                chat,
                "Oggi lavora fino a tardi.",
                durability="temporary",
                expires_at=NOW - timedelta(minutes=1),
            ),
            "contradicted": add_memory(connection, character, chat, "Lavora in banca.", status="contradicted"),
            "invalidated": add_memory(connection, character, chat, "Lavora al porto.", status="invalidated"),
            "expired": add_memory(connection, character, chat, "Lavora di notte.", status="expired"),
            "candidate": add_memory(
                connection, character, chat, "Lavora come pittore.", kind="identity_candidate", importance=5
            ),
        }
        return character, chat, made

    character, chat, made = write(migrated, seed)
    retriever = FtsMemoryRetriever(migrated)
    now_result = retriever.retrieve_relevant_memories(query(character, chat, "il lavoro"))
    labels = {item.id: item.label for item in now_result.items}
    assert labels == {
        made["current"]: "[sicuro]",
        made["temporary"]: "[per ora]",
        made["inferred"]: "[incerto]",
        made["story"]: "[storia]",
    }

    past_text = "Ti ricordi quando lavoravo?"
    assert is_past_intent(past_text, "it")
    past_result = retriever.retrieve_relevant_memories(query(character, chat, past_text, past_intent=True))
    past_labels = {item.id: item.label for item in past_result.items}
    assert past_labels[made["past"]] == "[passato]"
    assert past_labels[made["current"]] == "[sicuro]"
    past_item = next(item for item in past_result.items if item.id == made["past"])
    assert past_item.parts.status_factor == 0.7
    for hidden in ("elapsed", "contradicted", "invalidated", "expired", "candidate"):
        assert made[hidden] not in past_labels, hidden


def test_episodes_are_labeled_and_text_without_words_finds_standing_memories(migrated: Engine) -> None:
    """T-MEM-05: an episode shows its title and description; a query with only stop words or FTS syntax is
    harmless and leaves the standing memories."""

    def seed(connection: Connection) -> tuple[str, str, str, str]:
        character = add_character(connection)
        chat = add_chat(connection, character)
        episode = add_episode(
            connection, character, chat, "La notte al faro", "Siete rimasti svegli a parlare del suo trasloco."
        )
        boundary = add_memory(connection, character, chat, "Non vuole parlare del suo ex.", kind="boundary")
        return character, chat, episode, boundary

    character, chat, episode, boundary = write(migrated, seed)
    retriever = FtsMemoryRetriever(migrated)
    result = retriever.retrieve_relevant_memories(query(character, chat, "Ti ricordi il faro?"))
    item = next(item for item in result.items if item.id == episode)
    assert item.item_type == "episode"
    assert item.line == "- [episodio] La notte al faro: Siete rimasti svegli a parlare del suo trasloco."
    assert boundary in ids(result)
    for words in ("", "il la di", 'faro" OR NOT *', "content_stem:faro"):
        found = retriever.retrieve_relevant_memories(query(character, chat, words))
        assert boundary in ids(found), words
    assert ids(retriever.retrieve_relevant_memories(query(character, chat, "il la di"))) == [boundary]


def test_conversations_are_isolated_and_shared_memory_needs_the_opt_in(migrated: Engine) -> None:
    """T-MEM-15 and T-CON-02: two chats of one character never see each other's memories, standing or matched; the
    shared scope is read only when the query asks for it and the conversation has `shares_memory = 1`."""

    def seed(connection: Connection) -> dict[str, str]:
        character = add_character(connection)
        made = {
            "character": character,
            "first": add_chat(connection, character),
            "second": add_chat(connection, character),
            "sharing": add_chat(connection, character, shares_memory=True),
        }
        made["own"] = add_memory(connection, character, made["first"], "Ha un cane di nome Fido.")
        made["standing"] = add_memory(connection, character, made["first"], "Odia le sorprese.", importance=5)
        made["shared"] = add_memory(connection, character, None, "Il cane Fido ha dieci anni.")
        return made

    made = write(migrated, seed)
    retriever = FtsMemoryRetriever(migrated)

    def found(chat: str, *, include_shared: bool) -> list[str]:
        result = retriever.retrieve_relevant_memories(
            query(made["character"], made[chat], "Come sta Fido? Una sorpresa!", include_shared=include_shared)
        )
        return ids(result)

    assert set(found("first", include_shared=False)) == {made["own"], made["standing"]}
    assert found("second", include_shared=False) == []
    # The query's flag alone is not enough: the second chat has not opted in.
    assert found("second", include_shared=True) == []
    assert found("sharing", include_shared=False) == []
    assert found("sharing", include_shared=True) == [made["shared"]]


def test_a_fact_told_to_one_character_never_reaches_another(migrated: Engine) -> None:
    """T-CON-01: character B's retrieval finds nothing of character A, even with A's conversation id, A's shared
    scope, a standing memory and the exact words."""

    def seed(connection: Connection) -> dict[str, str]:
        first, second = add_character(connection), add_character(connection)
        made = {
            "a": first,
            "b": second,
            "a_chat": add_chat(connection, first, shares_memory=True),
            "b_chat": add_chat(connection, second, shares_memory=True),
        }
        add_memory(connection, first, made["a_chat"], "Si è trasferita a Lisbona.", importance=5, locked=True)
        add_memory(connection, first, None, "Ama Lisbona e il fado.")
        add_episode(connection, first, made["a_chat"], "Lisbona", "Il primo giorno a Lisbona.")
        return made

    made = write(migrated, seed)
    retriever = FtsMemoryRetriever(migrated)
    words = "Lisbona fado trasferita"
    for conversation in ("b_chat", "a_chat"):
        result = retriever.retrieve_relevant_memories(
            query(made["b"], made[conversation], words, include_shared=True, past_intent=True)
        )
        assert result.items == ()
        assert result.candidate_count == 0
    own = retriever.retrieve_relevant_memories(query(made["a"], made["a_chat"], words, include_shared=True))
    assert len(own.items) == 3


def use_counters(connection: Connection) -> tuple[Any, ...]:
    return tuple(connection.execute(text("SELECT use_count, last_used_at, updated_at FROM memories")).one())


def test_recording_use_counts_each_memory_once_and_keeps_its_recency(migrated: Engine) -> None:
    """Selection step 5: after the reply, `use_count` and `last_used_at` change and `updated_at` does not."""

    def seed(connection: Connection) -> tuple[str, str]:
        character = add_character(connection)
        chat = add_chat(connection, character)
        add_memory(connection, character, chat, "Ama il mare.", updated_at=NOW - timedelta(days=3))
        add_episode(connection, character, chat, "Il mare", "Una giornata al mare.")
        return character, chat

    character, chat = write(migrated, seed)
    retriever = FtsMemoryRetriever(migrated)
    result = retriever.retrieve_relevant_memories(query(character, chat, "mare"))
    assert len(result.items) == 2
    later = NOW + timedelta(minutes=5)
    assert retriever.record_use([*ids(result), *ids(result)], later) == 1
    row = read(migrated, use_counters)
    assert row == (1, to_utc_text(later), to_utc_text(NOW - timedelta(days=3)))
    assert retriever.record_use([], later) == 0


Loaded = list[tuple[str, str, list[dict[str, Any]], dict[str, str]]]


def load_evaluation(connection: Connection) -> Loaded:
    """Stores the evaluation set, one character and conversation each, and returns for each conversation its
    character, its id, its questions and the stored id of each fixture key."""
    data = json.loads(EVALUATION.read_text(encoding="utf-8"))
    now = datetime.fromisoformat(data["now"])
    loaded: Loaded = []
    for conversation in data["conversations"]:
        character = add_character(connection)
        chat = add_chat(connection, character)
        keys: dict[str, str] = {}
        for memory in conversation["memories"]:
            keys[memory["key"]] = add_memory(
                connection,
                character,
                chat,
                memory["content"],
                kind=memory["kind"],
                subject=memory.get("subject"),
                status=memory.get("status", "active"),
                importance=memory["importance"],
                updated_at=now - timedelta(days=memory["days_ago"]),
            )
        for episode in conversation["episodes"]:
            keys[episode["key"]] = add_episode(
                connection,
                character,
                chat,
                episode["title"],
                episode["description"],
                importance=episode["importance"],
                updated_at=now - timedelta(days=episode["days_ago"]),
            )
        loaded.append((character, chat, conversation["questions"], keys))
    return loaded


def test_recall_at_eight_reaches_the_target_on_the_synthetic_set(migrated: Engine) -> None:
    """T-MEM-22: on 3 conversations and 60 questions, at least 80 % of the relevant items are among the first 8
    selected, with past intent read from each message as a reply would."""
    loaded = write(migrated, load_evaluation)
    retriever = FtsMemoryRetriever(migrated)
    relevant = found = questions = 0
    missed: list[str] = []
    for character, chat, items, keys in loaded:
        for question in items:
            questions += 1
            result = retriever.retrieve_relevant_memories(
                query(
                    character,
                    chat,
                    question["text"],
                    past_intent=is_past_intent(question["text"], "it"),
                    max_items=8,
                )
            )
            top = set(ids(result)[:8])
            for key in question["relevant"]:
                relevant += 1
                if keys[key] in top:
                    found += 1
                else:
                    missed.append(f"{question['text']} -> {key}")
    recall = found / relevant
    print(f"T-MEM-22 recall at 8: {found}/{relevant} = {recall:.3f}; missed: {missed}")
    assert questions == 60
    assert recall >= RECALL_TARGET, missed
