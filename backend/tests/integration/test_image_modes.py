"""T-IMG-13 and the manual and character modes of T-IMG-03/T-IMG-07 on a migrated database: the detector decides between
a text reply and an `image_request` job, the explicit request and character mode queue image jobs, and contextual
proposals stay off by default and need a valid decision and an acceptance (docs/image-generation.md, Modes).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Connection, Engine, text

from integration.test_image_job import REQUEST, FakeComfy, Harness, conversation_of, files, scalar
from lyrveil.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from lyrveil.domain.image_proposals import ImageProposalResult
from lyrveil.domain.messages import Author, MessageStatus
from lyrveil.jobs.scheduler import read, write
from lyrveil.main import create_app
from lyrveil.media.store import MediaStore
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence import settings as settings_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.image_runtime import ImageEvent, ImageOutput, ImageProgress, ImageRequest, RenderFinished
from lyrveil.services.assets import CharacterAssetService
from lyrveil.services.image_proposals import ImageProposalService
from lyrveil.services.messages import MessageService
from lyrveil.settings import Settings

API = "/api/v1"
ASK = "Mandami una foto di te al porto"


class Clock:
    """A settable clock: `now` is what the services under test read."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def harness(engine: Engine, tmp_path: Path) -> Harness:
    upgrade_to_head(engine)
    return Harness(engine, MediaStore(tmp_path / "data" / "media"), [111, 222, 333])


@pytest.fixture
def chat(harness: Harness) -> str:
    return conversation_of(harness.engine)


def messages(harness: Harness) -> MessageService:
    return MessageService(harness.engine, harness.queue, images=harness.images)


def set_flag(engine: Engine, key: str, value: bool) -> None:
    write(engine, lambda connection: settings_repository.set_flag(connection, key, value, "2026-11-07T10:00:00.000Z"))


def job_kinds(engine: Engine) -> list[str]:
    def work(connection: Connection) -> list[str]:
        rows = connection.execute(text("SELECT kind FROM generation_jobs ORDER BY created_at, id"))
        return [row[0] for row in rows]

    return read(engine, work)


def character_of(engine: Engine, conversation_id: str) -> str:
    return str(scalar(engine, "SELECT character_id FROM conversations WHERE id = :id", id=conversation_id))


# Manual mode: the detector and the intents.


def test_a_message_that_asks_for_an_image_queues_an_image_job_instead_of_a_reply(harness: Harness, chat: str) -> None:
    sent = messages(harness).send(chat, ASK)

    assert (sent.message.author, sent.message.content, sent.message.status) == (
        Author.USER,
        ASK,
        MessageStatus.COMPLETE,
    )
    assert (sent.reply.kind, sent.reply.status, sent.job.kind) == ("image", MessageStatus.QUEUED, "image_request")
    assert job_kinds(harness.engine) == ["image_request"]
    payload = harness.job(sent.job.id).payload
    assert (payload["request"], payload["purpose"], payload["aspect"]) == (ASK, "chat", "auto")


def test_an_ordinary_message_still_gets_a_text_reply(harness: Harness, chat: str) -> None:
    sent = messages(harness).send(chat, "Ciao, come è andata oggi al porto?")

    assert (sent.reply.kind, sent.job.kind) == ("text", "chat_reply")


@pytest.mark.parametrize("text_sent", ["Non mandarmi foto", "Ti ho mandato una foto", "Mandami un messaggio"])
def test_negated_statements_and_requests_without_an_image_noun_get_a_text_reply(
    harness: Harness, chat: str, text_sent: str
) -> None:
    assert messages(harness).send(chat, text_sent).job.kind == "chat_reply"


def test_switching_the_detector_off_makes_every_auto_message_a_text_reply(harness: Harness, chat: str) -> None:
    set_flag(harness.engine, settings_repository.IMAGES_AUTO_DETECT_REQUESTS, False)

    assert messages(harness).send(chat, ASK).job.kind == "chat_reply"


def test_the_intents_override_the_detector(harness: Harness, chat: str) -> None:
    service = messages(harness)

    assert service.send(chat, ASK, intent="text").job.kind == "chat_reply"
    assert service.send(chat, "Il porto al tramonto", intent="image").job.kind == "image_request"


def test_a_message_too_long_for_an_image_request_is_text_under_auto_and_refused_as_an_image(
    harness: Harness, chat: str
) -> None:
    long_text = ASK + " " + "porto " * 200

    assert messages(harness).send(chat, long_text).job.kind == "chat_reply"
    with pytest.raises(ValidationFailedError):
        messages(harness).send(chat, long_text, intent="image")


def test_without_an_image_service_the_image_intent_is_refused_and_nothing_is_detected(
    harness: Harness, chat: str
) -> None:
    service = MessageService(harness.engine, harness.queue)

    assert service.send(chat, ASK).job.kind == "chat_reply"
    with pytest.raises(ConflictError):
        service.send(chat, ASK, intent="image")


def test_sending_the_same_client_message_id_again_returns_the_first_image_request(harness: Harness, chat: str) -> None:
    first = messages(harness).send(chat, ASK, client_message_id="client-1")
    again = messages(harness).send(chat, ASK, client_message_id="client-1")

    assert (again.message.id, again.reply.id, again.job.id) == (first.message.id, first.reply.id, first.job.id)
    assert scalar(harness.engine, "SELECT count(*) FROM messages") == 2
    assert job_kinds(harness.engine) == ["image_request"]


def test_a_client_message_id_that_was_sent_as_text_is_not_turned_into_an_image(harness: Harness, chat: str) -> None:
    messages(harness).send(chat, ASK, client_message_id="client-1", intent="text")

    with pytest.raises(ConflictError):
        messages(harness).send(chat, ASK, client_message_id="client-1")


def test_the_explicit_request_keeps_its_options_and_checks_their_ranges(harness: Harness, chat: str) -> None:
    sent = harness.images.request(chat, REQUEST, aspect="wide", quality="rapida", seed=7, steps=20, cfg=4.5)

    payload = harness.job(sent.job.id).payload
    assert (payload["aspect"], payload["quality"], payload["seed"], payload["steps"], payload["cfg"]) == (
        "wide",
        "rapida",
        7,
        20,
        4.5,
    )
    refused: list[dict[str, Any]] = [{"steps": 9}, {"steps": 51}, {"cfg": 0.5}, {"cfg": 12.5}, {"aspect": "huge"}]
    refused.append({"seed": -1})
    for bad in refused:
        with pytest.raises(ValidationFailedError):
            harness.images.request(chat, REQUEST, **bad)


@pytest.mark.anyio
async def test_the_steps_and_cfg_of_a_request_reach_the_workflow_and_the_image_row(harness: Harness, chat: str) -> None:
    harness.images.request(chat, REQUEST, steps=20, cfg=4.5)

    await harness.run_all()

    sampler = harness.comfy.workflows[0]["6"]["inputs"]
    assert (sampler["steps"], sampler["cfg"]) == (20, 4.5)
    assert scalar(harness.engine, "SELECT steps || '/' || cfg FROM generated_images") == "20/4.5"


# Character mode.


def test_character_mode_queues_a_job_with_no_message_and_checks_its_input(harness: Harness, chat: str) -> None:
    character_id = character_of(harness.engine, chat)

    job = harness.images.request_asset(character_id, "avatar")

    stored = harness.job(job.id)
    assert (stored.kind.value, stored.conversation_id, stored.message_id, stored.character_id) == (
        "image_request",
        None,
        None,
        character_id,
    )
    assert (stored.payload["purpose"], stored.payload["aspect"]) == ("avatar", "square")
    assert "ritratto" in stored.payload["request"]
    assert scalar(harness.engine, "SELECT count(*) FROM messages") == 0
    with pytest.raises(ValidationFailedError):
        harness.images.request_asset(character_id, "chat")
    with pytest.raises(ValidationFailedError):
        harness.images.request_asset(character_id, "scene", "  ")
    with pytest.raises(ValidationFailedError):
        harness.images.request_asset(character_id, "avatar", outfit="nowhere")
    with pytest.raises(NotFoundError):
        harness.images.request_asset("missing", "avatar")


@pytest.mark.anyio
async def test_a_generated_avatar_and_scene_join_the_assets_and_keep_their_files(
    harness: Harness, chat: str, tmp_path: Path
) -> None:
    character_id = character_of(harness.engine, chat)
    harness.images.request_asset(character_id, "avatar")
    await harness.run_all()
    harness.images.request_asset(character_id, "scene", "il porto di notte", aspect="landscape")
    await harness.run_all()

    assets = CharacterAssetService(harness.engine, harness.media).list_assets(character_id)
    by_kind = {asset.kind: asset for asset in assets}
    assert sorted(by_kind) == ["avatar", "scene"]
    assert (by_kind["avatar"].source, by_kind["avatar"].is_primary) == ("generated", True)
    assert (by_kind["scene"].source, by_kind["scene"].is_primary) == ("generated", False)
    scene_image = scalar(
        harness.engine, "SELECT id FROM generated_images WHERE purpose = 'scene' AND message_id IS NULL"
    )
    assert by_kind["scene"].generated_image_id == scene_image
    completed = [data for kind, data in harness.events.sent if kind == "image.completed"]
    assert [data["message_id"] for data in completed] == [None, None]

    # Deleting the asset leaves the image its files, and the gallery can still serve them.
    CharacterAssetService(harness.engine, harness.media).delete(character_id, by_kind["scene"].id)
    assert harness.media.resolve(by_kind["scene"].file_path).is_file()
    assert len(files(tmp_path / "data" / "media", "images")) == 2


@pytest.mark.anyio
async def test_persisting_a_character_image_twice_adds_one_asset(harness: Harness, chat: str) -> None:
    character_id = character_of(harness.engine, chat)
    job = harness.images.request_asset(character_id, "profile")
    await harness.run_all()

    assert scalar(harness.engine, "SELECT count(*) FROM character_assets WHERE source = 'generated'") == 1
    assert harness.job(job.id).status.value == "completed"
    assert scalar(harness.engine, "SELECT count(*) FROM generated_images") == 1


async def two_pass_render(self: FakeComfy, request: ImageRequest) -> AsyncIterator[ImageEvent]:
    """A render of the **Alta** preset: the first pass samples, the second refines."""
    self.workflows.append(request.workflow)
    yield ImageProgress("loading")
    yield ImageProgress("sampling", 14, 28)
    yield ImageProgress("refining", 5, 20)
    yield RenderFinished("success", (ImageOutput("lyrveil_00001_.png", "", "output"),))


@pytest.mark.anyio
async def test_avatar_generation_runs_the_shared_image_job_and_reports_both_passes(
    harness: Harness, chat: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-CHR-12: the avatar is drawn by the same three stages as a chat image, publishes the same progress for the
    first and the second pass, and the new image becomes the primary avatar of a character that has none."""
    monkeypatch.setattr(FakeComfy, "render", two_pass_render)
    character_id = character_of(harness.engine, chat)
    job = harness.images.request_asset(character_id, "avatar")

    done = await harness.run_all()

    assert (done.id, done.kind.value, done.stage, done.status.value) == (
        job.id,
        "image_request",
        "persist",
        "completed",
    )
    progress = [data for kind, data in harness.events.sent if kind == "image.progress"]
    assert [(data["phase"], data["value"], data["max"]) for data in progress] == [
        ("prompt", None, None),
        ("loading", None, None),
        ("sampling", 14, 28),
        ("refining", 5, 20),
        ("saving", None, None),
    ]
    assert {data["job_id"] for data in progress} == {job.id}
    assert {data["message_id"] for data in progress} == {None}
    avatars = CharacterAssetService(harness.engine, harness.media).list_assets(character_id, "avatar")
    assert [(asset.source, asset.is_primary) for asset in avatars] == [("generated", True)]


# The routes.


@pytest.fixture
def client(settings: Settings, harness: Harness) -> TestClient:
    return TestClient(create_app(settings, harness.engine, media_store=harness.media), base_url="http://localhost:4200")


def test_the_explicit_request_route_queues_the_image_and_checks_the_body(client: TestClient, chat: str) -> None:
    response = client.post(f"{API}/conversations/{chat}/images", json={"request": REQUEST, "aspect": "square"})

    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["job"]["kind"], body["reply"]["kind"], body["message"]["content"]) == (
        "image_request",
        "image",
        REQUEST,
    )
    url = f"{API}/conversations/{chat}/images"
    assert client.post(url, json={"request": REQUEST, "steps": 5}).status_code == 422
    assert client.post(url, json={"request": "  "}).status_code == 422
    assert client.post(url, json={"request": REQUEST, "x": 1}).status_code == 422
    assert client.post(f"{API}/conversations/missing/images", json={"request": REQUEST}).status_code == 404


def test_the_message_route_runs_the_detector(client: TestClient, chat: str) -> None:
    sent = client.post(f"{API}/conversations/{chat}/messages", json={"content": ASK})
    text_sent = client.post(f"{API}/conversations/{chat}/messages", json={"content": "Buongiorno"})

    assert sent.json()["job"]["kind"] == "image_request"
    assert text_sent.json()["job"]["kind"] == "chat_reply"


def test_the_generate_route_queues_a_character_image(client: TestClient, harness: Harness, chat: str) -> None:
    character_id = character_of(harness.engine, chat)
    url = f"{API}/characters/{character_id}/assets/generate"

    accepted = client.post(url, json={"purpose": "profile", "scene": "in biblioteca"})

    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["job"]["kind"] == "image_request"
    assert client.post(url, json={}).status_code == 202
    assert client.post(url, json={"purpose": "scene"}).status_code == 422
    assert client.post(url, json={"purpose": "reference"}).status_code == 422
    assert client.post(f"{API}/characters/missing/assets/generate", json={}).status_code == 404


@pytest.mark.anyio
async def test_the_gallery_pages_filters_serves_thumbnails_and_deletes(
    client: TestClient, harness: Harness, chat: str, tmp_path: Path
) -> None:
    """The gallery routes: newest first with a cursor, the filters, files that load with no ComfyUI running, and a
    delete that removes the files, hides the image and keeps the primary avatar."""
    character_id = character_of(harness.engine, chat)
    for purpose in ("avatar", "scene", "profile"):
        harness.images.request_asset(character_id, purpose, "il porto" if purpose == "scene" else "")
        await harness.run_all()

    first = client.get(f"{API}/images", params={"limit": 2}).json()
    assert (len(first["items"]), first["next_cursor"] is not None) == (2, True)
    second = client.get(f"{API}/images", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert (len(second["items"]), second["next_cursor"]) == (1, None)
    listed = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(listed)) == 3
    scene = client.get(f"{API}/images", params={"purpose": "scene", "character_id": character_id}).json()["items"]
    assert [item["purpose"] for item in scene] == ["scene"]
    assert client.get(f"{API}/images", params={"character_id": "missing"}).json()["items"] == []
    assert client.get(f"{API}/images", params={"purpose": "nope"}).status_code == 422
    assert client.get(f"{API}/images", params={"cursor": "!!"}).status_code == 422

    thumbnail = client.get(scene[0]["thumbnail_url"])
    assert (thumbnail.status_code, thumbnail.headers["content-type"]) == (200, "image/webp")

    avatar = client.get(f"{API}/images", params={"purpose": "avatar"}).json()["items"][0]
    assert client.delete(f"{API}/images/{avatar['id']}").status_code == 409

    before = len(files(tmp_path / "data" / "media", "images"))
    assert client.delete(f"{API}/images/{scene[0]['id']}").status_code == 204
    assert client.get(f"{API}/images/{scene[0]['id']}").status_code == 404
    assert client.get(scene[0]["thumbnail_url"]).status_code == 404
    assert client.delete(f"{API}/images/{scene[0]['id']}").status_code == 404
    assert len(client.get(f"{API}/images").json()["items"]) == 2
    assert len(files(tmp_path / "data" / "media", "images")) < before
    kinds = [asset.kind for asset in CharacterAssetService(harness.engine, harness.media).list_assets(character_id)]
    assert sorted(kinds) == ["avatar", "profile"]


def test_regenerating_an_image_with_no_chat_is_refused(client: TestClient) -> None:
    assert client.post(f"{API}/images/missing/regenerate", json={"keep_seed": True}).status_code == 404
    assert client.post(f"{API}/images/missing/regenerate", json={"seed": 1}).status_code == 422


# Contextual mode.


def decision(**changes: Any) -> ImageProposalResult:
    values: dict[str, Any] = {
        "schema_version": 1,
        "propose": True,
        "scene": "la mappa aperta sul tavolo del porto",
        "reason": "Ne avete appena parlato",
    }
    return ImageProposalResult.model_validate({**values, **changes})


class Proposals:
    """The proposal service on the harness, with a settable clock and the harness's event recorder."""

    def __init__(self, harness: Harness) -> None:
        self.harness = harness
        self.clock = Clock()
        images = harness.images
        self.service = ImageProposalService(
            harness.engine, images, harness.events, queue=harness.queue, min_messages=5, clock=self.clock
        )

    def last_message(self, chat: str) -> str:
        sql = "SELECT id FROM messages WHERE conversation_id = :c ORDER BY seq DESC LIMIT 1"
        return str(scalar(self.harness.engine, sql, c=chat))

    def fill(self, chat: str, count: int) -> None:
        """Adds `count` text exchanges (two messages each) whose reply jobs the image-only scheduler never takes."""
        service = messages(self.harness)
        for number in range(count):
            service.send(chat, f"Messaggio {number}", intent="text")

    def queue(self, chat: str) -> Any:
        return write(self.harness.engine, lambda connection: self.service.queue_if_due(connection, chat))


@pytest.fixture
def proposals(harness: Harness) -> Proposals:
    return Proposals(harness)


def test_contextual_mode_is_off_by_default_and_queues_nothing(proposals: Proposals, chat: str) -> None:
    proposals.fill(chat, 6)

    assert proposals.queue(chat) is None
    assert "image_proposal" not in job_kinds(proposals.harness.engine)


def test_a_proposal_is_due_after_enough_messages_and_not_twice_in_a_row(proposals: Proposals, chat: str) -> None:
    engine = proposals.harness.engine
    set_flag(engine, settings_repository.IMAGES_CONTEXTUAL_ENABLED, True)
    proposals.fill(chat, 2)
    assert proposals.queue(chat) is None  # Four messages: below the minimum of five.
    proposals.fill(chat, 1)

    job = proposals.queue(chat)

    assert job is not None
    assert job.kind == "image_proposal"
    assert proposals.queue(chat) is None  # A job is already waiting.


@pytest.mark.anyio
async def test_only_a_valid_decision_makes_a_proposal_and_it_is_announced(proposals: Proposals, chat: str) -> None:
    engine = proposals.harness.engine
    proposals.fill(chat, 1)
    message_id = proposals.last_message(chat)
    service = proposals.service

    # Off: nothing is stored, whatever the model said.
    assert await service.propose(chat, message_id, decision()) is None
    set_flag(engine, settings_repository.IMAGES_CONTEXTUAL_ENABLED, True)
    assert await service.propose(chat, message_id, decision(propose=False)) is None
    assert await service.propose(chat, message_id, decision(scene="  ")) is None
    assert await service.propose(chat, message_id, decision(scene="una ragazzina timida sul molo")) is None
    assert await service.propose("missing", message_id, decision()) is None
    assert await service.propose(chat, "missing", decision()) is None
    assert scalar(engine, "SELECT count(*) FROM image_proposals") == 0
    assert "image.proposed" not in proposals.harness.events.types()

    stored = await service.propose(chat, message_id, decision())

    assert stored is not None
    assert (stored["status"], stored["scene"], stored["message_id"]) == (
        "pending",
        "la mappa aperta sul tavolo del porto",
        message_id,
    )
    assert ("image.proposed", {"proposal": stored}) in proposals.harness.events.sent
    # One proposal waits at a time.
    assert await service.propose(chat, message_id, decision()) is None
    assert scalar(engine, "SELECT count(*) FROM image_proposals") == 1


def test_a_decision_must_match_the_schema() -> None:
    for bad in (
        {"schema_version": 2, "propose": True, "scene": "x", "reason": "y"},
        {"schema_version": 1, "propose": True, "scene": "x" * 301, "reason": "y"},
        {"schema_version": 1, "propose": True, "scene": "x", "reason": "y" * 201},
        {"schema_version": 1, "propose": "forse", "scene": "x", "reason": "y"},
        {"schema_version": 1, "propose": True, "scene": "x"},
        {"schema_version": 1, "propose": True, "scene": "x", "reason": "y", "extra": 1},
    ):
        with pytest.raises(ValidationError):
            ImageProposalResult.model_validate(bad)


@pytest.mark.anyio
async def test_only_an_acceptance_queues_an_image_and_only_once(proposals: Proposals, chat: str) -> None:
    engine = proposals.harness.engine
    set_flag(engine, settings_repository.IMAGES_CONTEXTUAL_ENABLED, True)
    proposals.fill(chat, 1)
    stored = await proposals.service.propose(chat, proposals.last_message(chat), decision())
    assert stored is not None
    before = job_kinds(engine)

    assert job_kinds(engine) == before  # The proposal alone queued no image.

    sent = proposals.service.accept(stored["id"])

    assert (sent.job.kind, sent.reply.kind, sent.reply.status) == ("image_request", "image", MessageStatus.QUEUED)
    payload = proposals.harness.job(sent.job.id).payload
    assert (payload["request"], payload["purpose"]) == ("la mappa aperta sul tavolo del porto", "chat")
    assert proposals.service.get(stored["id"])["status"] == "accepted"
    assert proposals.service.get(stored["id"])["job_id"] == sent.job.id
    with pytest.raises(ConflictError):
        proposals.service.accept(stored["id"])
    with pytest.raises(ConflictError):
        proposals.service.reject(stored["id"])
    assert job_kinds(engine).count("image_request") == before.count("image_request") + 1

    # The accepted image is drawn like any other and completes the placeholder.
    done = await proposals.harness.run_all()
    assert done.id == sent.job.id
    assert done.status.value == "completed"
    assert scalar(engine, "SELECT status FROM messages WHERE id = :id", id=sent.reply.id) == "complete"
    assert scalar(engine, "SELECT count(*) FROM generated_images WHERE message_id = :id", id=sent.reply.id) == 1


@pytest.mark.anyio
async def test_purging_a_character_removes_its_proposals(proposals: Proposals, chat: str) -> None:
    engine = proposals.harness.engine
    set_flag(engine, settings_repository.IMAGES_CONTEXTUAL_ENABLED, True)
    proposals.fill(chat, 1)
    stored = await proposals.service.propose(chat, proposals.last_message(chat), decision())
    assert stored is not None
    character_id = character_of(engine, chat)

    write(engine, lambda connection: character_repository.purge(connection, character_id))

    assert scalar(engine, "SELECT count(*) FROM image_proposals") == 0


@pytest.mark.anyio
async def test_a_rejection_queues_nothing_and_an_unanswered_proposal_expires(proposals: Proposals, chat: str) -> None:
    engine = proposals.harness.engine
    set_flag(engine, settings_repository.IMAGES_CONTEXTUAL_ENABLED, True)
    proposals.fill(chat, 1)
    message_id = proposals.last_message(chat)
    first = await proposals.service.propose(chat, message_id, decision())
    assert first is not None
    jobs = job_kinds(engine)

    rejected = proposals.service.reject(first["id"])

    assert rejected["status"] == "rejected"
    assert rejected["decided_at"] is not None
    assert job_kinds(engine) == jobs
    with pytest.raises(ConflictError):
        proposals.service.accept(first["id"])

    second = await proposals.service.propose(chat, message_id, decision())
    assert second is not None
    proposals.clock.now += timedelta(hours=25)
    with pytest.raises(ConflictError):
        proposals.service.accept(second["id"])
    assert proposals.service.get(second["id"])["status"] == "expired"
    assert job_kinds(engine) == jobs
    with pytest.raises(NotFoundError):
        proposals.service.accept("missing")


@pytest.mark.anyio
async def test_unanswered_proposals_expire_after_a_day_and_the_cooldown_is_an_hour(
    proposals: Proposals, chat: str
) -> None:
    engine = proposals.harness.engine
    set_flag(engine, settings_repository.IMAGES_CONTEXTUAL_ENABLED, True)
    proposals.fill(chat, 6)
    stored = await proposals.service.propose(chat, proposals.last_message(chat), decision())
    assert stored is not None
    proposals.service.reject(stored["id"])
    proposals.fill(chat, 6)

    assert proposals.queue(chat) is None  # The proposal is less than an hour old.
    proposals.clock.now += timedelta(minutes=61)
    assert proposals.queue(chat) is not None

    third = await proposals.service.propose(chat, proposals.last_message(chat), decision())
    assert third is not None
    assert proposals.service.expire_stale() == 0
    proposals.clock.now += timedelta(hours=24, minutes=1)
    assert proposals.service.expire_stale() == 1
    assert proposals.service.get(third["id"])["status"] == "expired"
