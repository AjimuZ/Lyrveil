"""T-IMG-18 on a migrated database: the image caption is written through the chat path before the scene, with the
normalizer and the reply checks, and a failed caption leaves the image without text.

The chat is loaded with load_context_request as a reply's is, and the memories come from FtsMemoryRetriever. The text
runtime is a fake that streams the scripted caption, answers `generate` with the scripted scene, and records the
order of the calls.
"""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Connection, Engine, text

from integration.test_context_builder import STAMP, add_message
from lyrveil.context.builder import ContextRequest
from lyrveil.context.minimal import UserProfile
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.identifiers import new_id
from lyrveil.domain.messages import Author
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.prompts.image_scene import EXAMPLE_RESULT
from lyrveil.services.chat_inference import load_context_request
from lyrveil.services.conversations import ConversationService
from lyrveil.services.image_prompt import CAPTION_MAX_LENGTH, EMPTY, ImagePromptRequest, ImagePromptStage
from lyrveil.services.memory_retrieval import FtsMemoryRetriever

NOW = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
REQUEST = "una foto di te seduta al porto"
LINE = f"Stai per mandare a Utente un'immagine: {REQUEST}. Scrivi solo il messaggio che la accompagna."
CARD = CharacterCard.model_validate(
    {
        "identity": {"first_name": "Nerea", "age": 27, "grammatical_gender": "femminile", "setting": "Genova, oggi"},
        "traits": [{"category": "main_trait", "description": "Curiosa e diretta."}],
        "communication": {"message_length": "medi", "narration": "nessuna"},
        "visual": {
            "subject_tag": "1girl",
            "identity_en": {"hair_color": "red hair", "eye_color": "green eyes"},
            "usual_outfit_en": ["canvas jacket"],
        },
    }
)


class ImageRuntime:
    """A TextRuntime that streams `caption` word by word and returns `scene` from `generate`. `calls` records
    ("stream" | "generate", request) in order, and `aborted` each genkey the caller aborted."""

    def __init__(self, caption: str, scene: str = json.dumps(EXAMPLE_RESULT)) -> None:
        self.caption = caption
        self.scene = scene
        self.calls: list[tuple[str, TextRequest]] = []
        self.aborted: list[str] = []

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        self.calls.append(("stream", request))
        words = self.caption.split(" ")
        tokens = [word if index == 0 else f" {word}" for index, word in enumerate(words)]
        for token in tokens:
            yield TextChunk(token)
        yield TextChunk("", "stop", "".join(tokens))

    async def generate(self, request: TextRequest) -> str:
        self.calls.append(("generate", request))
        return self.scene

    async def abort(self, genkey: str) -> bool:
        self.aborted.append(genkey)
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=8192)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


def chat_of(engine: Engine) -> ContextRequest:
    """Seeds a chat of Nerea that ends with the user's image request and loads it as the image's reply context."""
    character_id = new_id()

    def create(connection: Connection) -> None:
        character_repository.insert(connection, character_id=character_id, card=CARD, now=STAMP)

    write(engine, create)
    conversation_id = ConversationService(engine).create(character_id).id
    add_message(engine, conversation_id, Author.USER, "Dove sei adesso?")
    add_message(engine, conversation_id, Author.CHARACTER, "Al porto, a guardare le barche.")
    add_message(engine, conversation_id, Author.USER, f"Mandami {REQUEST}!")

    def load(connection: Connection) -> ContextRequest | None:
        before: int = connection.execute(
            text("SELECT next_seq FROM conversations WHERE id = :id"), {"id": conversation_id}
        ).scalar_one()
        return load_context_request(connection, conversation_id, before, user=UserProfile(), now=NOW)

    chat = read(engine, load)
    assert chat is not None
    return chat


def image() -> ImagePromptRequest:
    return ImagePromptRequest(card=CARD, request=REQUEST, preset="illustrious", quality="alta")


@pytest.mark.anyio
async def test_the_caption_takes_the_chat_path_before_the_scene_and_is_normalized(migrated: Engine) -> None:
    runtime = ImageRuntime("Nerea: Eccola, il porto al tramonto. *sorride mentre scatta la foto*")
    prepared = await ImagePromptStage(runtime, FtsMemoryRetriever(migrated)).run(image(), chat_of(migrated), "job-1")

    assert [kind for kind, _ in runtime.calls] == ["stream", "generate"]
    caption_request, scene_request = runtime.calls[0][1], runtime.calls[1][1]
    prompt = caption_request.prompt
    # The chat's reply prompt: stable text, history, anchor, then the extra line, then the user's message.
    assert "Stai interpretando Nerea in una chat privata con Utente." in prompt
    assert "Al porto, a guardare le barche." in prompt
    assert prompt.index("Ricorda: sei Nerea e hai 27 anni.") < prompt.index(LINE)
    assert prompt.index(LINE) < prompt.index(f"[Messaggio di Utente]\nMandami {REQUEST}!")
    assert caption_request.max_length == CAPTION_MAX_LENGTH  # The band "medi" is 240, capped at 120.
    assert caption_request.grammar is None
    assert caption_request.stop_sequence
    assert scene_request.grammar is not None
    assert "Richiesta: " + REQUEST in scene_request.prompt

    # N1 removed the label and N3 the narration, which the card does not allow.
    assert prepared.caption.text == "Eccola, il porto al tramonto."
    assert prepared.caption.failure is None
    normalizer = prepared.caption.checks["normalizer"]
    assert isinstance(normalizer, dict)
    assert (normalizer.get("N1"), normalizer.get("N3")) == (1, 1)
    assert not prepared.scene.fallback
    assert (prepared.aspect, prepared.width, prepared.height) == ("portrait", 1248, 1824)
    assert prepared.prompt.positive.startswith("masterpiece, best quality, amazing quality, absurdres, general, 1girl")


@pytest.mark.anyio
async def test_a_caption_that_a_check_refuses_sends_the_image_without_text(migrated: Engine) -> None:
    # The sentence ends while the caption streams, so the generation is aborted at once.
    runtime = ImageRuntime("Eccola! Ho 41 anni e questo è il mio porto. Ti piace?")
    prepared = await ImagePromptStage(runtime, FtsMemoryRetriever(migrated)).run(image(), chat_of(migrated), "job-2")

    assert (prepared.caption.text, prepared.caption.failure) == (None, "V10")
    assert runtime.aborted == ["job-2"]
    assert [kind for kind, _ in runtime.calls] == ["stream", "generate"]  # The scene and the image still follow.
    assert prepared.prompt.positive


@pytest.mark.anyio
async def test_an_empty_caption_or_no_chat_leaves_the_image_without_text(migrated: Engine) -> None:
    runtime = ImageRuntime("*sorride e scatta una foto al porto*")
    prepared = await ImagePromptStage(runtime, FtsMemoryRetriever(migrated)).run(image(), chat_of(migrated), "job-3")
    assert (prepared.caption.text, prepared.caption.failure) == (None, EMPTY)

    alone = ImageRuntime("Non usata.")
    outside = await ImagePromptStage(alone, FtsMemoryRetriever(migrated)).run(image(), None, "job-4")
    assert outside.caption.text is None
    assert [kind for kind, _ in alone.calls] == ["generate"]
