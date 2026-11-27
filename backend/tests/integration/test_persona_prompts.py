"""T-PSN-03 and the prompt half of T-PSN-04 on a migrated database: every prompt of a conversation names and describes
its own persona, and a persona or note edit reaches block 10 from the next reply on (ADR 0048).

The character Giulia has two conversations: one with the persona Marta and a note, one with the persona Elia and a note
of his own. A reply runs through ChatInferenceService under the real JobScheduler; the extraction, the summary and the
proactive message run their handlers directly. Each fake text runtime records its requests, and no text of Elia or of
his conversation may reach a prompt of Marta's. The reply and proactive prompts never hold the default name `Utente`
either; the extraction and summary templates do, in their fixed worked examples.
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from integration.test_context_builder import NOW, STAMP, Leases, add_message
from integration.test_memory_extraction import output
from integration.test_proactive_messages import ENABLED, add_running_event, decision, set_proactive
from integration.test_summaries import SETTINGS, summary_output
from lyrveil.catalog import TextTemplate
from lyrveil.context.builder import Assembly, ContextBuilder, ContextRequest
from lyrveil.domain.card import CharacterCard
from lyrveil.domain.identifiers import new_id
from lyrveil.domain.messages import Author
from lyrveil.events.hub import EventHub
from lyrveil.jobs.handlers import JobDone, RunContext
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import JobQueue
from lyrveil.jobs.scheduler import JobScheduler, read, write
from lyrveil.memory.window import ExtractionSettings
from lyrveil.persistence import characters as character_repository
from lyrveil.persistence import settings as settings_repository
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.prompts.formatter import formatter_for
from lyrveil.services.chat_inference import ChatInferenceService, load_context_request
from lyrveil.services.conversations import ConversationService
from lyrveil.services.memory_extraction import MemoryExtractionService, queue_extraction_if_due
from lyrveil.services.memory_retrieval import FtsMemoryRetriever
from lyrveil.services.messages import MessageService
from lyrveil.services.personas import PersonaService
from lyrveil.services.proactive_messages import ProactiveMessageService
from lyrveil.services.summaries import SummaryService, queue_summary_if_due

CARD = CharacterCard.model_validate(
    {
        "identity": {
            "first_name": "Giulia",
            "last_name": "Rossi",
            "age": 29,
            "grammatical_gender": "femminile",
            "narrative_identity": "Restaura mappe antiche a Genova e le mostra a {{user}}.",
        },
        "traits": [{"category": "main_trait", "description": "Curiosa e testarda."}],
        "communication": {
            "message_length": "medi",
            "examples": [{"user": "Che fai stasera?", "character": "Restauro una carta nautica."}],
        },
    }
)
MARTA: dict[str, Any] = {
    "name": "Marta",
    "grammatical_gender": "femminile",
    "age": 34,
    "appearance": "Capelli corti e neri, occhiali tondi.",
    "about": "Fa la cuoca in una trattoria di Sampierdarena e suona il contrabbasso.",
}
NOTE = "Vi siete conosciute a un corso di vela."
BLOCK_10 = (
    "Cosa sai di Marta:\nHa 34 anni.\nAspetto: Capelli corti e neri, occhiali tondi.\n"
    "Fa la cuoca in una trattoria di Sampierdarena e suona il contrabbasso.\n"
    "Tra voi: Vi siete conosciute a un corso di vela."
)
ELIA: dict[str, Any] = {
    "name": "Elia",
    "grammatical_gender": "maschile",
    "age": 51,
    "appearance": "Barba grigia e cappello di feltro.",
    "about": "Insegna astronomia a Pavia.",
}
ELIA_NOTE = "Siete vicini di casa da dieci anni."
# Every text of the other persona and its conversation, none of which may reach a prompt of Marta's chat.
ELIA_TEXTS = ("Elia", "51 anni", "Barba grigia", "astronomia", "vicini di casa", "maschile")


class RecordingRuntime:
    """A TextRuntime that streams `reply` one word per token, answers each structured generation with the next of
    `outputs`, and records every request of both kinds."""

    def __init__(self, reply: str = "Va bene.", outputs: Sequence[str] = ()) -> None:
        self.reply = reply
        self.outputs = list(outputs)
        self.streamed: list[TextRequest] = []
        self.generated: list[TextRequest] = []

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        self.streamed.append(request)
        words = self.reply.split(" ")
        tokens = [word if index == 0 else f" {word}" for index, word in enumerate(words)]
        for token in tokens:
            yield TextChunk(token)
        yield TextChunk("", "stop", "".join(tokens))

    async def generate(self, request: TextRequest) -> str:
        self.generated.append(request)
        return self.outputs.pop(0)

    async def abort(self, genkey: str) -> bool:
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


def start(engine: Engine, messages: int = 3) -> tuple[str, str]:
    """Returns Marta's conversation with Giulia and Marta's persona id. Elia has a conversation with Giulia too, with
    the same messages. Each chat gets `messages` messages, alternating user and character from the user."""
    character_id = new_id()

    def create(connection: Connection) -> None:
        character_repository.insert(connection, character_id=character_id, card=CARD, now=STAMP)

    write(engine, create)
    personas = PersonaService(engine)
    marta, elia = personas.create(MARTA), personas.create(ELIA)
    conversations = ConversationService(engine)
    chat = conversations.create(character_id, persona_id=marta.id, persona_note=NOTE).id
    other = conversations.create(character_id, persona_id=elia.id, persona_note=ELIA_NOTE).id
    for conversation_id in (chat, other):
        for number in range(1, messages + 1):
            author = Author.USER if number % 2 else Author.CHARACTER
            add_message(engine, conversation_id, author, f"Messaggio numero {number} della storia.")
    return chat, marta.id


def assert_only_marta(prompt: str) -> None:
    for other in ELIA_TEXTS:
        assert other not in prompt, other


async def assemble(engine: Engine, conversation_id: str, family: TextTemplate) -> Assembly:
    """Builds the prompt of the chat's next reply as ChatInferenceService loads it, in `family`."""

    def load(connection: Connection) -> ContextRequest | None:
        before: int = connection.execute(
            text("SELECT next_seq FROM conversations WHERE id = :id"), {"id": conversation_id}
        ).scalar_one()
        return load_context_request(connection, conversation_id, before, now=NOW)

    wanted = read(engine, load)
    assert wanted is not None
    runtime = RecordingRuntime()
    builder = ContextBuilder(runtime, FtsMemoryRetriever(engine))
    return await builder.build(wanted, formatter_for(family), 400)


@pytest.mark.anyio
async def test_a_reply_names_and_describes_only_the_conversations_persona(migrated: Engine) -> None:
    """T-PSN-03 for a reply: block 1, block 3, block 10, the anchor, the frame, the placeholders, the stop sequences and
    the reply checks all read Marta."""
    chat, _ = start(migrated, messages=2)
    runtime = RecordingRuntime("Sei stanco oggi?")
    service = ChatInferenceService(migrated, runtime, EventHub("boot"), clock=lambda: NOW)
    scheduler = JobScheduler(migrated, JobQueue(), {JobKind.CHAT_REPLY: service}, Leases(), lambda: RequiredMode.LLM)
    MessageService(migrated).send(chat, "Ciao, come va?")
    finished = await scheduler.run_once()
    assert finished is not None
    assert finished.status is JobStatus.COMPLETED

    (request,) = runtime.streamed
    prompt = request.prompt
    assert "Stai interpretando Giulia Rossi in una chat privata con Marta." in prompt
    assert "- Quando ti rivolgi a Marta, usa il femminile." in prompt
    assert "Restaura mappe antiche a Genova e le mostra a Marta." in prompt
    assert prompt.count("Dai del tu a Marta.") == 2, "block 3 and the anchor"
    assert "Marta: Che fai stasera?\nGiulia: Restauro una carta nautica." in prompt
    assert BLOCK_10 in prompt
    assert prompt.index("Come scrivi in chat:") < prompt.index("Cosa sai di Marta:"), "block 10 follows block 3"
    assert "[Contesto per Giulia, non scritto da Marta]" in prompt
    assert "[Messaggio di Marta]\nCiao, come va?" in prompt
    assert request.stop_sequence is not None
    assert request.stop_sequence[-1] == "\nMarta:"
    assert "Utente" not in prompt
    assert_only_marta(prompt)
    checks = read(
        migrated,
        lambda connection: connection.execute(
            text(
                "SELECT v.checks_json FROM messages AS m JOIN message_variants AS v ON v.id = m.active_variant_id "
                "WHERE m.conversation_id = :id AND m.author = 'character' ORDER BY m.seq DESC LIMIT 1"
            ),
            {"id": chat},
        ).scalar_one(),
    )
    # V12: "stanco" addresses a feminine persona in the masculine.
    assert json.loads(checks)["checks"].get("V12") == 1


@pytest.mark.anyio
@pytest.mark.parametrize("family", ["gemma4", "chatml"])
async def test_a_persona_or_note_edit_changes_block_10_from_the_next_reply_only(
    migrated: Engine, family: TextTemplate
) -> None:
    """The prompt half of T-PSN-04, and T-MOD-05 with block 10: two replies with nothing edited share the stable text
    byte for byte; after a persona edit and a note edit, the next reply's block 10 holds the new texts and nothing
    else of the stable text changed."""
    chat, marta_id = start(migrated)
    first = await assemble(migrated, chat, family)
    again = await assemble(migrated, chat, family)
    assert BLOCK_10 in first.prompt.prefix
    assert again.prompt.prefix == first.prompt.prefix

    PersonaService(migrated).update(marta_id, {**MARTA, "about": "Ora fa la pasticcera a Nervi."})
    ConversationService(migrated).update(chat, {"persona_note": "Vi siete conosciute a Lisbona."})
    edited = await assemble(migrated, chat, family)

    new_block = (
        "Cosa sai di Marta:\nHa 34 anni.\nAspetto: Capelli corti e neri, occhiali tondi.\nOra fa la pasticcera a Nervi."
        "\nTra voi: Vi siete conosciute a Lisbona."
    )
    assert new_block in edited.prompt.prefix
    assert "Sampierdarena" not in edited.prompt.text
    assert "corso di vela" not in edited.prompt.text
    assert edited.prompt.prefix.replace(new_block, BLOCK_10) == first.prompt.prefix, "only block 10 changed"
    assert_only_marta(edited.prompt.text)


@pytest.mark.anyio
async def test_the_extraction_and_summary_prompts_name_the_conversations_persona(migrated: Engine) -> None:
    """T-PSN-03 for the structured tasks that read the chat: the rules and the author of every user message name
    Marta."""
    chat, _ = start(migrated, messages=30)
    queue = JobQueue(clock=lambda: NOW)
    extraction = write(
        migrated, lambda connection: queue_extraction_if_due(connection, queue, chat, ExtractionSettings())
    )
    assert extraction is not None
    extractor_runtime = RecordingRuntime(outputs=[output([])])
    extractor = MemoryExtractionService(migrated, extractor_runtime, queue=queue, clock=lambda: NOW)
    done = await extractor(extraction, RunContext(lease=None, genkey="genkey-extraction"))
    assert isinstance(done, JobDone)
    assert done.result is not None
    assert done.result["status"] == "completed"

    summary = write(migrated, lambda connection: queue_summary_if_due(connection, queue, chat, SETTINGS))
    assert summary is not None
    summary_runtime = RecordingRuntime(outputs=[summary_output("A")])
    summarizer = SummaryService(migrated, summary_runtime, queue=queue, settings=SETTINGS, clock=lambda: NOW)
    done = await summarizer(summary, RunContext(lease=None, genkey="genkey-summary"))
    assert isinstance(done, JobDone)

    (extraction_request,) = extractor_runtime.generated
    assert "Analizzi una chat tra Marta e il personaggio Giulia Rossi." in extraction_request.prompt
    assert "] Marta: Messaggio numero 1 della storia." in extraction_request.prompt
    (summary_request,) = summary_runtime.generated
    assert "tra Marta e il personaggio Giulia Rossi." in summary_request.prompt
    assert "] Marta: Messaggio numero 1 della storia." in summary_request.prompt
    for prompt in (extraction_request.prompt, summary_request.prompt):
        assert_only_marta(prompt)


@pytest.mark.anyio
async def test_the_proactive_decision_and_message_name_and_describe_the_conversations_persona(
    migrated: Engine,
) -> None:
    """T-PSN-03 for a proactive message: the decision prompt, the generation prompt with block 10, its instruction and
    its stop sequences read Marta."""
    chat, _ = start(migrated)

    def switch_on(connection: Connection) -> None:
        settings_repository.set_value(connection, settings_repository.PROACTIVE_ENABLED, True, STAMP)

    write(migrated, switch_on)
    set_proactive(migrated, chat, ENABLED)
    _, job = add_running_event(migrated, chat)
    runtime = RecordingRuntime("Ti penso, come stai?", outputs=[decision(True)])
    handler = ProactiveMessageService(migrated, runtime, EventHub("boot"), clock=lambda: NOW)

    outcome = await handler(job, RunContext(lease=None, genkey="genkey-proactive"))

    assert isinstance(outcome, JobDone)
    assert outcome.result is not None
    assert outcome.result["status"] == "delivered"
    (decided,) = runtime.generated
    assert "Decidi se Giulia deve scrivere per primo a Marta, adesso." in decided.prompt
    assert "\nMarta: Messaggio numero 1 della storia." in decided.prompt
    (written,) = runtime.streamed
    assert BLOCK_10 in written.prompt
    assert "Sei tu a scrivere a Marta" in written.prompt
    assert written.stop_sequence is not None
    assert written.stop_sequence[-1] == "\nMarta:"
    for prompt in (decided.prompt, written.prompt):
        assert "Utente" not in prompt
        assert_only_marta(prompt)
