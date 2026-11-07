"""The Memory Studio routes (P04.9a) over memories, episodes, summaries and the relationship state that the real
extraction pipeline wrote, on a real database: the list with its filters and pages, one memory with its sources and
history, lock, confirm, edit (T-MEM-08), delete, a labeled contradiction (T-MEM-11), a manual memory, and the episodes,
checkpoints, state and change log with their sources.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from integration.test_memory_extraction import VALID, Chat, Clock, memory, output, start_chat
from lyrveil.domain.identifiers import new_id
from lyrveil.domain.messages import Author
from lyrveil.jobs.scheduler import read, write
from lyrveil.main import create_app
from lyrveil.persistence import summaries
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

API = "/api/v1"
EMPLOYER = "Lavora allo Studio Rossi."
CAT = "Ha un gatto di nome Briciola."


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


@pytest.fixture
def clock() -> Clock:
    return Clock()


def client_for(settings: Settings, engine: Engine) -> TestClient:
    return TestClient(create_app(settings, engine), base_url="http://localhost:4200")


def character_of(engine: Engine, conversation_id: str) -> str:
    return read(
        engine,
        lambda connection: str(
            connection.execute(
                text("SELECT character_id FROM conversations WHERE id = :id"), {"id": conversation_id}
            ).scalar_one()
        ),
    )


def memories_of(client: TestClient, character_id: str, **query: Any) -> list[dict[str, Any]]:
    response = client.get(f"{API}/memories", params={"character_id": character_id, **query})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def by_content(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["content"]: item for item in items}


@pytest.mark.anyio
async def test_the_list_filters_pages_and_shows_one_memory_with_its_source_and_history(
    migrated: Engine, clock: Clock, settings: Settings
) -> None:
    chat = Chat(migrated, clock)
    await chat.extract(VALID)
    client = client_for(settings, migrated)
    character_id = character_of(migrated, chat.conversation_id)

    found = by_content(memories_of(client, character_id))
    assert set(found) == {EMPLOYER, CAT, "Questa settimana è molto stanca."}
    employer, cat = found[EMPLOYER], found[CAT]
    assert (employer["provenance"], employer["confidence"], employer["source_count"]) == ("stated", 0.95, 1)
    assert (cat["provenance"], cat["confidence"], cat["source_count"]) == ("inferred", 0.6, 2)
    assert by_content(memories_of(client, character_id, provenance="inferred")).keys() == {CAT}
    assert by_content(memories_of(client, character_id, q="Briciola")).keys() == {CAT}
    assert memories_of(client, character_id, q="e") == [], "text with no searchable word matches nothing"
    assert memories_of(client, character_id, status="superseded") == []
    assert memories_of(client, character_id, conversation_id=new_id()) == []
    first = client.get(f"{API}/memories", params={"character_id": character_id, "limit": 2}).json()
    rest = client.get(f"{API}/memories", params={"character_id": character_id, "cursor": first["next_cursor"]}).json()
    assert (len(first["items"]), len(rest["items"]), rest["next_cursor"]) == (2, 1, None)
    assert {item["id"] for item in first["items"] + rest["items"]} == {item["id"] for item in found.values()}
    assert client.get(f"{API}/memories", params={"character_id": character_id, "cursor": "x"}).status_code == 422
    assert client.get(f"{API}/memories", params={"character_id": new_id()}).status_code == 404

    detail = client.get(f"{API}/memories/{employer['id']}").json()
    [source] = detail["sources"]
    assert (source["message_id"], source["seq"], source["author"], source["valid"]) == (
        chat.message_ids[0],
        1,
        "user",
        True,
    )
    assert source["excerpt"].startswith("Ciao! Oggi ho iniziato")
    assert detail["conflict"] == []

    locked = client.post(f"{API}/memories/{employer['id']}/lock").json()
    assert (locked["locked"], locked["version"]) == (True, 1), "a lock writes no revision"
    assert by_content(memories_of(client, character_id, locked=True)).keys() == {EMPLOYER}
    assert client.post(f"{API}/memories/{employer['id']}/unlock").json()["locked"] is False

    edited = client.patch(
        f"{API}/memories/{employer['id']}", json={"content": "Lavora da Verdi & Co.", "importance": 5}
    ).json()
    assert (edited["content"], edited["version"], edited["importance"]) == ("Lavora da Verdi & Co.", 2, 5)
    [revision] = client.get(f"{API}/memories/{employer['id']}/revisions").json()["items"]
    assert (revision["version"], revision["actor"], revision["reason"], revision["content"]) == (
        1,
        "user",
        "edit",
        EMPLOYER,
    ), "T-MEM-08: an edit shows a new revision with the text it replaced"
    assert client.patch(f"{API}/memories/{employer['id']}", json={"content": CAT}).status_code == 409
    assert client.patch(f"{API}/memories/{employer['id']}", json={"importance": 9}).status_code == 422

    confirmed = client.post(f"{API}/memories/{cat['id']}/confirm").json()
    assert (confirmed["provenance"], confirmed["confidence"], confirmed["version"]) == ("user_confirmed", 1.0, 2)
    [confirm] = client.get(f"{API}/memories/{cat['id']}/revisions").json()["items"]
    assert (confirm["reason"], confirm["provenance"], confirm["confidence"]) == ("confirm", "inferred", 0.6)
    assert client.post(f"{API}/memories/{cat['id']}/confirm").json()["version"] == 2, "confirming twice changes nothing"

    assert client.delete(f"{API}/memories/{cat['id']}").status_code == 204
    assert client.get(f"{API}/memories/{cat['id']}").status_code == 404
    assert client.delete(f"{API}/memories/{cat['id']}").status_code == 404
    assert CAT not in by_content(memories_of(client, character_id))


@pytest.mark.anyio
async def test_an_unresolved_contradiction_is_labeled_in_both_memories(
    migrated: Engine, clock: Clock, settings: Settings
) -> None:
    """T-MEM-11: both memories are `uncertain` in one conflict group, each lists the other, and the history says why."""
    chat = Chat(migrated, clock)
    await chat.extract(output([memory()]))
    await chat.extract(
        output(
            [
                memory(
                    relation="contradiction",
                    target_ref="E1",
                    content="Lavora allo Studio Bianchi.",
                    value="Studio Bianchi",
                    source_seqs=[11],
                )
            ]
        ),
        (Author.USER, "Mi ha preso lo Studio Bianchi, credo. O era Rossi?"),
        (Author.CHARACTER, "Non ti seguo più."),
    )
    client = client_for(settings, migrated)
    character_id = character_of(migrated, chat.conversation_id)

    doubtful = by_content(memories_of(client, character_id, status="uncertain"))
    rossi, bianchi = doubtful[EMPLOYER], doubtful["Lavora allo Studio Bianchi."]
    assert rossi["conflict_group_id"] is not None
    assert rossi["conflict_group_id"] == bianchi["conflict_group_id"]
    assert [peer["id"] for peer in client.get(f"{API}/memories/{rossi['id']}").json()["conflict"]] == [bianchi["id"]]
    assert [peer["id"] for peer in client.get(f"{API}/memories/{bianchi['id']}").json()["conflict"]] == [rossi["id"]]
    [revision] = client.get(f"{API}/memories/{rossi['id']}/revisions").json()["items"]
    assert (revision["reason"], revision["actor"], revision["status"]) == ("contradiction", "extraction", "active")


@pytest.mark.anyio
async def test_episodes_the_relationship_and_summaries_show_their_sources(
    migrated: Engine, clock: Clock, settings: Settings
) -> None:
    chat = Chat(migrated, clock)
    await chat.extract(VALID)
    client = client_for(settings, migrated)
    character_id = character_of(migrated, chat.conversation_id)

    episodes = client.get(f"{API}/episodes", params={"character_id": character_id}).json()["items"]
    [episode] = episodes
    assert (episode["title"], episode["participants"], episode["importance"]) == (
        "Primo giorno allo Studio Rossi",
        ["Utente", "Dario"],
        3,
    )
    assert [source["seq"] for source in episode["sources"]] == [1, 2]
    found = client.get(f"{API}/episodes", params={"character_id": character_id, "q": "giorno"}).json()["items"]
    assert [item["title"] for item in found] == [episode["title"]]
    assert client.get(f"{API}/episodes", params={"character_id": character_id, "q": "gatto"}).json()["items"] == []
    assert client.get(f"{API}/episodes", params={"character_id": new_id()}).status_code == 404

    state = client.get(f"{API}/conversations/{chat.conversation_id}/relationship").json()
    assert state["written"] is True
    assert (state["values"]["trust"], state["bands"]["trust"]) == (22, "è prudente con te")
    assert state["description"] == ("vi siete appena conosciuti, è prudente con te, parla poco di sé, nessuna tensione")
    assert [event["title"] for event in state["recent_events"]] == [episode["title"]]
    assert set(state["values"]) == {"familiarity", "trust", "confidence", "tension"}
    [change] = client.get(f"{API}/conversations/{chat.conversation_id}/relationship/history").json()["items"]
    assert (change["event_kind"], change["dimension"], change["reason"], change["status"]) == (
        "relationship_change",
        "trust",
        "Si confida.",
        "applied",
    )
    assert [source["seq"] for source in change["sources"]] == [5]

    checkpoint = summaries.NewCheckpoint(
        id=new_id(),
        conversation_id=chat.conversation_id,
        level=0,
        from_seq=1,
        to_seq=10,
        version=1,
        content={"events": ["Ha iniziato un nuovo lavoro."], "source_seqs": [1, 3]},
        text="Dario e l'utente parlano del nuovo lavoro.",
        source_hash="hash",
        prompt_version="summary.v1",
        token_count=12,
    )
    write(migrated, lambda connection: summaries.insert_checkpoint(connection, checkpoint, "2026-11-07T10:00:00.000Z"))
    [summary] = client.get(f"{API}/conversations/{chat.conversation_id}/summaries").json()["items"]
    assert (summary["from_seq"], summary["to_seq"], summary["version"], summary["status"]) == (1, 10, 1, "active")
    assert summary["content"]["source_seqs"] == [1, 3]

    for path in ("summaries", "relationship", "relationship/history"):
        assert client.get(f"{API}/conversations/{new_id()}/{path}").status_code == 404


def test_a_conversation_with_no_run_has_an_empty_state_and_a_manual_memory_can_be_written(
    migrated: Engine, settings: Settings
) -> None:
    conversation_id = start_chat(migrated)
    client = client_for(settings, migrated)
    character_id = character_of(migrated, conversation_id)

    state = client.get(f"{API}/conversations/{conversation_id}/relationship").json()
    assert (state["written"], state["description"], state["recent_events"]) == (False, "", [])
    assert client.get(f"{API}/conversations/{conversation_id}/relationship/history").json() == {"items": []}

    body = {"conversation_id": conversation_id, "content": "Ama il caffè amaro.", "kind": "user_preference"}
    created = client.post(f"{API}/memories", json=body)
    assert created.status_code == 201, created.text
    made = created.json()
    assert (made["provenance"], made["confidence"], made["status"], made["sources"]) == (
        "user_confirmed",
        1.0,
        "active",
        [],
    )
    assert [item["id"] for item in memories_of(client, character_id, q="caffè")] == [made["id"]]
    assert client.post(f"{API}/memories", json=body).status_code == 409
    assert client.post(f"{API}/memories", json={**body, "conversation_id": new_id()}).status_code == 404
    assert client.post(f"{API}/memories", json={**body, "content": "  "}).status_code == 422
    assert client.post(f"{API}/memories", json={**body, "content": "x" * 281}).status_code == 422
