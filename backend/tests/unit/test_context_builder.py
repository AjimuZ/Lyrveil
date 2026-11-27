"""Tests of lyrveil/context/blocks.py and lyrveil/context/builder.py without a database: T-CON-11 (length bands, the
anchor, the examples of block 3), T-MOD-05 with the full context (two replies share the stable text and the history),
the prefix of a scripted conversation (P08.4), and the unit half of T-PSN-03 (block 10, its budget and its cut order).

The runtime is a fake whose tokenizer counts whitespace-separated words; the retriever is a fake that packs fixed items
by their estimated cost.
"""

from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest

from lyrveil.catalog import TextTemplate
from lyrveil.context.blocks import (
    LENGTH_BANDS,
    OPTIONAL_IDENTITY,
    anchor,
    canonical_identity,
    communication_profile,
    persona_block,
    reply_length,
)
from lyrveil.context.builder import (
    COMMUNICATION_MAX_TOKENS,
    CURRENT_MAX_TOKENS,
    EXAMPLES,
    IDENTITY,
    PERSONA,
    PERSONA_MAX_TOKENS,
    REDUCTION_ORDER,
    STATE,
    Assembly,
    ContextBuilder,
    ContextRequest,
    split_messages,
)
from lyrveil.domain.card import CharacterCard, MessageLength
from lyrveil.domain.messages import Author, Message, MessageStatus
from lyrveil.domain.personas import ChatPersona
from lyrveil.memory.retrieval import estimate_tokens
from lyrveil.ports.memory_retriever import RetrievalQuery, RetrievalResult, RetrievedMemory, ScoreParts
from lyrveil.ports.text_runtime import TextChunk, TextRequest, TextRuntimeInfo
from lyrveil.prompts.formatter import formatter_for

FAMILIES: tuple[TextTemplate, ...] = get_args(TextTemplate)
BANDS: tuple[MessageLength, ...] = get_args(MessageLength)
NOW = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
USER = ChatPersona()
FRAME = "[Contesto per Giulia, non scritto da Utente]"
APPEARANCE = "Capelli corti e neri, occhiali tondi."
ABOUT = "Fa la cuoca in una trattoria di Sampierdarena e suona il contrabbasso."
NOTE = "Vi siete conosciute a un corso di vela."
MARTA = ChatPersona("Marta", "femminile", 34, APPEARANCE, ABOUT, NOTE)


class WordRuntime:
    """A TextRuntime whose tokenizer counts whitespace-separated words, plus one start token with `special`, and that
    reports a window of `window` tokens."""

    def __init__(self, window: int = 8192) -> None:
        self.window = window

    async def count_tokens(self, text: str, *, special: bool = False) -> int:
        return len(text.split()) + int(special)

    async def stream(self, request: TextRequest) -> AsyncIterator[TextChunk]:
        yield TextChunk("", "stop", "")

    async def generate(self, request: TextRequest) -> str:
        return ""

    async def abort(self, genkey: str) -> bool:
        return True

    async def info(self) -> TextRuntimeInfo:
        return TextRuntimeInfo(model="fake-model.gguf", version="1.124.2", max_context_length=self.window)


class Retriever:
    """A MemoryRetriever that packs its fixed items, in order, while their estimated cost fits the budget."""

    def __init__(self, items: Sequence[RetrievedMemory] = ()) -> None:
        self.items = list(items)
        self.queries: list[RetrievalQuery] = []

    def retrieve_relevant_memories(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        chosen: list[RetrievedMemory] = []
        cost = 0
        for item in self.items:
            if cost + item.token_cost <= query.token_budget:
                chosen.append(item)
                cost += item.token_cost
        return RetrievalResult(items=tuple(chosen), token_cost=cost, candidate_count=len(self.items))


def memory(number: int, content: str) -> RetrievedMemory:
    line = f"- [sicuro] {content}"
    return RetrievedMemory(
        id=f"memory-{number}",
        item_type="memory",
        kind="user_fact",
        content=content,
        status="active",
        label="[sicuro]",
        line=line,
        score=1.0 - number / 100,
        parts=ScoreParts(0, 0, 0, 0, 0, 0, 1, 0, 0),
        token_cost=estimate_tokens(line),
        subject_key=None,
        conflict_group_id=None,
    )


def message(seq: int, author: Author, content: str) -> Message:
    return Message(
        id=f"message-{seq}",
        conversation_id="chat",
        seq=seq,
        client_message_id=None,
        author=author,
        kind="text",
        status=MessageStatus.COMPLETE,
        read_state=None,
        active_variant_id=f"variant-{seq}",
        content=content,
        variant_count=1,
        edited_at=None,
        deleted_at=None,
        created_at="2026-11-07T10:00:00.000Z",
    )


def chat(count: int) -> list[Message]:
    """Returns `count` messages, alternating user and character from seq 1, so an odd count ends with the user."""
    return [
        message(seq, Author.USER if seq % 2 else Author.CHARACTER, f"Messaggio numero {seq} della chat.")
        for seq in range(1, count + 1)
    ]


def card(**communication: Any) -> CharacterCard:
    return CharacterCard.model_validate(
        {
            "identity": {
                "first_name": "Giulia",
                "last_name": "Rossi",
                "age": 29,
                "grammatical_gender": "femminile",
                "narrative_identity": "Restaura mappe antiche a Genova.",
                "occupation": "restauratrice",
                "interests": ["mappe antiche", "gatti di porto"],
                "background": "Ha lasciato la gilda dei cartografi.",
            },
            "traits": [
                {"category": "main_trait", "description": "Curiosa e testarda. Fa domande su tutto."},
                {"category": "main_trait", "description": "Ironica, mai crudele"},
                {"category": "main_trait", "description": "Paziente con chi impara."},
                {"category": "value", "description": "La lealtà"},
                {"category": "fear", "description": "Il mare in tempesta", "locked": True},
            ],
            "communication": {"message_length": "medi", **communication},
        }
    )


def request(character: CharacterCard, messages: Sequence[Message], **overrides: Any) -> ContextRequest:
    split = split_messages(messages)
    assert split is not None
    history, current = split
    fields: dict[str, Any] = {
        "character_id": "character",
        "conversation_id": "chat",
        "card": character,
        "persona": USER,
        "language": "it",
        "shares_memory": False,
        "history": history,
        "current": current,
        "summaries": (),
        "now": NOW,
        **overrides,
    }
    return ContextRequest(**fields)


async def build(
    character: CharacterCard,
    messages: Sequence[Message],
    *,
    window: int = 8192,
    llm_max_length: int = 400,
    retriever: Retriever | None = None,
    family: TextTemplate = "gemma4",
    **overrides: Any,
) -> Assembly:
    builder = ContextBuilder(WordRuntime(window), retriever or Retriever())
    return await builder.build(request(character, messages, **overrides), formatter_for(family), llm_max_length)


def example(number: int) -> dict[str, str]:
    """An example exchange whose character line is 60 words long, so seven fit within block 3's 500 words."""
    return {"character": " ".join([f"esempio{number}"] + ["parola"] * 59)}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("band", BANDS)
def test_length_bands_set_max_length_capped_by_llm_max_length(band: MessageLength) -> None:
    assert reply_length(band, 400) == {"brevissimi": 60, "brevi": 120, "medi": 240, "lunghi": 400}[band]
    assert reply_length(band, 100) == min(100, LENGTH_BANDS[band].max_length)


@pytest.mark.anyio
@pytest.mark.parametrize("band", BANDS)
async def test_the_reply_reserve_is_the_band_capped_by_llm_max_length(band: MessageLength) -> None:
    """T-CON-11: the band sets `max_length` and the reserve that the window keeps free, under `llm.max_length`."""
    character = card(message_length=band)
    for cap in (400, 100):
        assembly = await build(character, chat(5), llm_max_length=cap)
        expected = min(LENGTH_BANDS[band].max_length, cap)
        assert assembly.max_length == expected
        assert assembly.available == 8192 - expected - 64


@pytest.mark.anyio
@pytest.mark.parametrize("band", BANDS)
async def test_the_anchor_holds_the_band_text_and_closes_the_dynamic_context(band: MessageLength) -> None:
    """T-CON-11: the anchor holds the length text of the band, and it is the last block before the current message."""
    character = card(message_length=band, narration="leggera", emoji="mai", address_form="lei")
    text = anchor(character, USER, ["Nota uno.", "Nota due.", "Nota tre."])

    assert text.splitlines() == [
        "Ricorda: sei Giulia Rossi e hai 29 anni. Parli di te al femminile.",
        "Curiosa e testarda. Ironica, mai crudele.",
        f"Dai del lei a Utente. {LENGTH_BANDS[band].text} "
        "Al massimo un breve gesto tra asterischi, e non in ogni messaggio. Non usare emoji.",
        "Nota uno.",
        "Nota due.",
    ]
    assembly = await build(character, chat(5), notes=("Nota uno.",))
    last_turn = assembly.prompt.text[len(assembly.prompt.prefix) :]
    frame = last_turn.split(f"{FRAME}\n", 1)[1]
    assert frame.startswith(anchor(character, USER, ["Nota uno."]) + "\n[Messaggio di Utente]\nMessaggio numero 5")


@pytest.mark.anyio
async def test_block_3_renders_examples_in_saved_order_and_stops_before_its_maximum() -> None:
    """T-CON-11: examples are shown in saved order, stopping before the first that would take block 3 over 500 tokens;
    under budget pressure they are the first thing cut, from the last one."""
    character = card(examples=[example(number) for number in range(1, 9)])
    runtime = WordRuntime()

    async def size(shown: int) -> int:
        return await runtime.count_tokens(communication_profile(character, USER, shown))

    assembly = await build(character, chat(5))
    shown = assembly.example_count
    assert 0 < shown < 8, "the fixture is meant to fit some examples, not all"
    assert await size(shown) <= COMMUNICATION_MAX_TOKENS < await size(shown + 1)
    assert assembly.blocks["communication"] == await size(shown)
    stable = assembly.prompt.prefix
    positions = [stable.index(f"Giulia: esempio{number} ") for number in range(1, shown + 1)]
    assert positions == sorted(positions)
    assert f"esempio{shown + 1} " not in assembly.prompt.text

    # A window 30 tokens short of that prompt: the history is below its floor, so the first reduction pays.
    squeezed = await build(character, chat(5), window=assembly.token_total - 30 + assembly.max_length + 64)
    assert squeezed.fits
    assert squeezed.reductions == (EXAMPLES,)
    assert squeezed.example_count == shown - 1
    kept = [f"esempio{number} " in squeezed.prompt.text for number in range(1, shown + 1)]
    assert kept == [True] * squeezed.example_count + [False] * (shown - squeezed.example_count)


@pytest.mark.anyio
async def test_a_current_message_over_the_cap_is_cut_in_the_prompt_only() -> None:
    long_message = message(5, Author.USER, " ".join(f"parola{number}" for number in range(3000)))
    assembly = await build(card(), [*chat(4), long_message], window=16384)

    assert assembly.current_truncated
    assert assembly.blocks["current"] <= CURRENT_MAX_TOKENS
    assert "\nparola0 parola1 " in assembly.prompt.text
    assert "parola2999" not in assembly.prompt.text
    assert long_message.content.endswith("parola2999"), "the stored message is not changed"


@pytest.mark.anyio
@pytest.mark.parametrize("family", FAMILIES)
async def test_consecutive_replies_share_the_stable_text_and_the_history(family: TextTemplate) -> None:
    """T-MOD-05 with the full context: block 5 and the anchor change between two replies, the stable text and the
    history do not, and no old turn carries the frame."""
    character = card(examples=[example(1)])
    first = await build(character, chat(5), retriever=Retriever([memory(1, "Anna vive a Torino.")]), family=family)
    second = await build(
        character,
        chat(7),
        retriever=Retriever([memory(2, "Anna ama il mare.")]),
        family=family,
        notes=("Hai 29 anni.",),
    )

    assert second.prompt.text.startswith(first.prompt.prefix)
    assert second.prompt.prefix.startswith(first.prompt.prefix)
    assert "Messaggio numero 5 della chat." in second.prompt.prefix
    for assembly, line in ((first, "- [sicuro] Anna vive a Torino."), (second, "- [sicuro] Anna ama il mare.")):
        prompt = assembly.prompt
        last_turn = prompt.text[len(prompt.prefix) :]
        stable_part = prompt.prefix if family != "mistral_v3_tekken" else last_turn
        assert "Stai interpretando Giulia Rossi" in stable_part
        assert "Come scrivi in chat:" in stable_part
        assert last_turn.count(FRAME) == 1
        assert f"Ricordi pertinenti:\n{line}\nRicorda: sei Giulia Rossi" in last_turn
        assert "[Contesto per" not in prompt.prefix
        assert "Ricordi pertinenti" not in prompt.prefix
        assert "Ricorda: sei" not in prompt.prefix


@pytest.mark.anyio
@pytest.mark.parametrize("family", FAMILIES)
async def test_the_stable_prefix_stays_byte_identical_across_a_scripted_conversation(
    family: TextTemplate, record_property: Callable[[str, object], None]
) -> None:
    """P08.4: over twelve replies of one chat, with different memories, notes and clock at every turn, the prefix of
    each reply (the stable text and the history) starts with the prefix of the one before, byte for byte, so KoboldCpp
    reuses its cache. Reports the sizes per turn on the test's output and as the `prefix_stability` property."""
    character = card(examples=[example(1)])
    previous = b""
    sizes: list[int] = []
    for turn in range(1, 13):
        assembly = await build(
            character,
            chat(2 * turn + 1),
            retriever=Retriever([memory(turn, f"Anna ricorda il fatto numero {turn}.")]),
            family=family,
            notes=(f"Nota del turno {turn}.",),
            now=NOW + timedelta(hours=turn),
        )
        prompt = assembly.prompt
        prefix = prompt.prefix.encode("utf-8")
        assert prompt.text.encode("utf-8").startswith(prefix)
        assert prefix.startswith(previous), f"turn {turn}: the prefix of turn {turn - 1} is no longer its start"
        assert len(prefix) > len(previous), f"turn {turn}: the history did not grow the prefix"
        # What changes between replies sits after the prefix, in the dynamic context and the anchor.
        dynamic = prompt.text[len(prompt.prefix) :]
        assert f"fatto numero {turn}." in dynamic
        assert f"Nota del turno {turn}." in dynamic
        assert "fatto numero" not in prompt.prefix
        assert "Nota del turno" not in prompt.prefix
        sizes.append(len(prefix))
        previous = prefix

    report = f"{family}: {len(sizes)} replies, prefix bytes per reply {sizes}, every prefix extends the one before"
    record_property("prefix_stability", report)
    print(f"prefix stability, {report}")


def test_block_10_holds_the_age_appearance_about_and_note_and_an_empty_one_adds_nothing() -> None:
    assert persona_block(MARTA).splitlines() == [
        "Cosa sai di Marta:",
        "Ha 34 anni.",
        f"Aspetto: {APPEARANCE}",
        ABOUT,
        f"Tra voi: {NOTE}",
    ]
    assert persona_block(ChatPersona("Marta", age=34)) == "Cosa sai di Marta:\nHa 34 anni."
    assert persona_block(ChatPersona("Marta", "femminile")) == "", "the name and the gender alone are block 1's"
    cut = persona_block(MARTA, frozenset({"appearance", "about"}))
    assert cut == f"Cosa sai di Marta:\nHa 34 anni.\nTra voi: {NOTE}"


@pytest.mark.anyio
@pytest.mark.parametrize("family", FAMILIES)
async def test_the_persona_names_every_block_and_block_10_follows_block_3(family: TextTemplate) -> None:
    """T-PSN-03, unit half: block 1, block 3, the anchor and the frame name the persona, and block 10 sits in the stable
    text between block 3 and the scenario."""
    character = card(examples=[{"user": "Che fai stasera?", "character": "Restauro una mappa."}])
    scenario = {"title": "Il porto", "description": "Vi incontrate al porto vecchio.", "setting": "Genova"}
    assembly = await build(character, chat(5), family=family, persona=MARTA, scenario=scenario)

    text = assembly.prompt.text
    assert "Stai interpretando Giulia Rossi in una chat privata con Marta." in text
    assert "- Quando ti rivolgi a Marta, usa il femminile." in text
    assert text.count("Dai del tu a Marta.") == 2, "block 3 and the anchor"
    block = persona_block(MARTA)
    assert f"{communication_profile(character, MARTA, 1)}\n\n{block}\n\nScenario: Il porto" in text
    assert "[Contesto per Giulia, non scritto da Marta]" in text
    assert "[Messaggio di Marta]\nMessaggio numero 5" in text
    assert "Utente" not in text
    assert assembly.blocks["persona"] == len(block.split())


@pytest.mark.anyio
async def test_a_persona_with_only_a_name_and_a_gender_adds_no_block_10() -> None:
    assembly = await build(card(), chat(5), persona=ChatPersona("Marta", "femminile"))
    assert "Cosa sai di" not in assembly.prompt.text
    assert assembly.blocks["persona"] == 0


@pytest.mark.anyio
async def test_block_10_leaves_out_appearance_then_about_to_stay_within_its_maximum() -> None:
    """Block 10 is rendered whole up to PERSONA_MAX_TOKENS. Over it, `appearance` goes first, then `about`; the age and
    the note always stay. The cut is part of rendering, like block 3's examples, not a reduction."""
    tall = " ".join(["alta"] * 120)
    cook = " ".join(["cuoca"] * 200)
    first = await build(card(), chat(5), persona=ChatPersona("Marta", "femminile", 34, tall, cook, NOTE))
    assert "alta alta" not in first.prompt.text
    assert cook in first.prompt.text
    assert first.blocks["persona"] <= PERSONA_MAX_TOKENS
    assert first.reductions == ()

    longer = " ".join(["cuoca"] * 320)
    second = await build(card(), chat(5), persona=ChatPersona("Marta", "femminile", 34, tall, longer, NOTE))
    assert "Aspetto:" not in second.prompt.text
    assert "cuoca" not in second.prompt.text
    assert f"Cosa sai di Marta:\nHa 34 anni.\nTra voi: {NOTE}" in second.prompt.text


@pytest.mark.anyio
async def test_under_pressure_block_10_loses_appearance_then_about_after_the_identity_parts() -> None:
    """docs/memory-engine.md (Planned for version 1.1): after the optional identity parts and before the state details,
    block 10 loses `appearance`, then `about`. The name and the gender in block 1, the age and the note stay."""
    assert REDUCTION_ORDER.index(IDENTITY) < REDUCTION_ORDER.index(PERSONA) < REDUCTION_ORDER.index(STATE)
    character = card()
    roomy = await build(character, chat(3), persona=MARTA)
    assert roomy.reductions == ()
    full_identity = len(canonical_identity(character).split())
    identity_parts = full_identity - len(canonical_identity(character, frozenset(OPTIONAL_IDENTITY)).split())
    appearance = len(f"Aspetto: {APPEARANCE}".split())

    def window(short: int) -> int:
        """A window `short` tokens too small for the roomy prompt."""
        return roomy.token_total - short + roomy.max_length + 64

    no_appearance = await build(character, chat(3), persona=MARTA, window=window(identity_parts + 1))
    assert no_appearance.fits
    assert no_appearance.reductions == (IDENTITY, PERSONA)
    assert "Aspetto:" not in no_appearance.prompt.text
    assert ABOUT in no_appearance.prompt.text

    neither = await build(character, chat(3), persona=MARTA, window=window(identity_parts + appearance + 1))
    assert neither.fits
    assert neither.reductions == (IDENTITY, PERSONA)
    kept = neither.prompt.text
    assert "Aspetto:" not in kept
    assert ABOUT not in kept
    assert "Stai interpretando Giulia Rossi in una chat privata con Marta." in kept
    assert "- Quando ti rivolgi a Marta, usa il femminile." in kept
    assert f"Cosa sai di Marta:\nHa 34 anni.\nTra voi: {NOTE}" in kept


@pytest.mark.anyio
@pytest.mark.parametrize("family", FAMILIES)
async def test_consecutive_replies_share_block_10_in_the_stable_text(family: TextTemplate) -> None:
    """T-MOD-05 with block 10: two replies of a chat with a persona share the stable text, block 10 included, and the
    history byte for byte, while the memories and the notes change in the dynamic context."""
    first = await build(
        card(), chat(5), family=family, persona=MARTA, retriever=Retriever([memory(1, "Vive a Genova.")])
    )
    second = await build(
        card(),
        chat(7),
        family=family,
        persona=MARTA,
        retriever=Retriever([memory(2, "Ama il mare.")]),
        notes=("Hai 29 anni.",),
    )

    assert second.prompt.text.startswith(first.prompt.prefix)
    assert second.prompt.prefix.startswith(first.prompt.prefix)
    block = persona_block(MARTA)
    stable_parts = []
    for assembly in (first, second):
        prompt = assembly.prompt
        last_turn = prompt.text[len(prompt.prefix) :]
        before_frame, frame = last_turn.split("[Contesto per Giulia, non scritto da Marta]\n", 1)
        assert block not in frame
        if family == "mistral_v3_tekken":
            # This family writes the stable text into the last user turn, just before the frame.
            assert block in before_frame
            stable_parts.append(before_frame)
        else:
            assert block in prompt.prefix
    if family == "mistral_v3_tekken":
        assert stable_parts[0] == stable_parts[1]
