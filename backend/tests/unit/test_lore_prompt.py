"""T-LOR-03 without a database: the selection of lore entries (priority, own book first, insertion order, block and
book budgets), block 11 in the stable text after block 10, block 12 in the dynamic context after block 5, the new
reduction order, the placeholders, and a prefix that stays byte-identical over twelve replies while the activated
entries change (ADR 0049; docs/memory-engine.md, Planned for version 1.1).

The runtime counts whitespace-separated words (unit.test_context_builder.WordRuntime), so a block's count is the sum
of its lines' counts and the expected reductions can be worked out from Assembly.blocks.
"""

import json
from datetime import timedelta
from typing import Any

import pytest

from lyrveil.catalog import TextTemplate
from lyrveil.context.blocks import OPTIONAL_IDENTITY, canonical_identity, persona_block
from lyrveil.context.builder import (
    IDENTITY,
    LORE_ACTIVATED,
    LORE_CONSTANT,
    MEMORIES,
    PERSONA,
    REDUCTION_ORDER,
    STATE,
    SUMMARIES,
)
from lyrveil.context.lore import LoreItem, LoreSource, ordered, select
from lyrveil.domain.lore import BookKind, BookStatus, KeyStems, LoreBook, LoreEntry
from lyrveil.domain.messages import Author, Message
from lyrveil.services.lore_matcher import active_lore
from lyrveil.services.lore_rules import checked_entry
from unit.test_context_builder import FAMILIES, MARTA, NOW, Retriever, build, card, chat, memory, message

FRAME = "[Contesto per Giulia, non scritto da Utente]"
MARTA_FRAME = "[Contesto per Giulia, non scritto da Marta]"


def item(entry_id: str, content: str, **fields: Any) -> LoreItem:
    """Returns an active world entry of book `world` with priority 0 and insertion order 100, unless `fields` say
    otherwise."""
    values: dict[str, Any] = {
        "book_id": "world",
        "title": "",
        "own": False,
        "constant": False,
        "priority": 0,
        "insertion_order": 100,
        **fields,
    }
    return LoreItem(entry_id=entry_id, content=content, **values)


def book(book_id: str, kind: BookKind = BookKind.WORLD, scan_depth: int = 4) -> LoreBook:
    return LoreBook(
        id=book_id,
        kind=kind,
        character_id="character" if kind is BookKind.CHARACTER else None,
        name=book_id,
        description="",
        scan_depth=scan_depth,
        token_budget=None,
        status=BookStatus.ACTIVE,
        entry_count=0,
        source={},
        created_at="",
        updated_at="",
    )


def entry(entry_id: str, book_id: str, **fields: Any) -> LoreEntry:
    """Returns an entry as LoreService stores it, its keys stemmed in Italian."""
    columns = checked_entry(fields, "it")
    return LoreEntry(
        id=entry_id,
        book_id=book_id,
        title=columns["title"],
        content=columns["content"],
        keys=tuple(json.loads(columns["keys_json"])),
        secondary_keys=tuple(json.loads(columns["secondary_keys_json"])),
        exclude_keys=tuple(json.loads(columns["exclude_keys_json"])),
        stems=KeyStems.from_json(columns["keys_stem_json"]),
        case_sensitive=columns["case_sensitive"],
        constant=columns["constant"],
        enabled=columns["enabled"],
        priority=columns["priority"],
        insertion_order=columns["insertion_order"],
        note=columns["note"],
        never_inserted=False,
    )


def ids(items: Any) -> list[str]:
    return [lore.entry_id for lore in items]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_entries_are_kept_by_priority_then_own_book_then_insertion_order() -> None:
    items = [
        item("world-late", "A.", priority=5, insertion_order=10),
        item("own-tie", "B.", priority=5, insertion_order=50, own=True, book_id="own"),
        item("own-low", "C.", priority=1, insertion_order=1, own=True, book_id="own"),
        item("world-early", "D.", priority=5, insertion_order=5),
        item("world-high", "E.", priority=9, insertion_order=500),
    ]

    assert ids(ordered(items)) == ["world-high", "own-tie", "world-early", "world-late", "own-low"]


def test_the_block_budget_ends_the_walk_and_a_full_book_stops_only_that_book() -> None:
    items = [
        item("a", "x", book_id="small", book_budget=10),
        item("b", "x", book_id="small", book_budget=10),
        item("c", "x", book_id="open"),
        item("d", "x", book_id="small", book_budget=10),
        item("e", "x", book_id="open"),
        item("f", "x", book_id="open"),
    ]
    costs = {"a": 6, "b": 6, "c": 5, "d": 1, "e": 8, "f": 1}

    # Heading 3: a (9 of 20), b over its book's 10, c (14), d's book is full, e over the target ends the walk.
    assert ids(select(items, costs, 3, 20, 40)) == ["a", "c"]


def test_an_entry_over_the_maximum_never_enters_and_one_over_the_target_opens_an_empty_block() -> None:
    items = [item("huge", "x"), item("large", "x"), item("small", "x")]
    costs = {"huge": 38, "large": 30, "small": 2}

    # The heading costs 3: `huge` is over the maximum even alone, `large` is over the target of 20 but opens the block.
    assert ids(select(items, costs, 3, 20, 40)) == ["large"]
    assert ids(select(items[2:], costs, 3, 20, 40)) == ["small"]


@pytest.mark.anyio
@pytest.mark.parametrize("family", FAMILIES)
async def test_block_11_follows_block_10_and_block_12_follows_block_5(family: TextTemplate) -> None:
    """Block 11 sits between block 10 and the scenario; block 12 sits in the frame between block 5 and the anchor. The
    placeholders of both are rendered with the first name and the persona's name."""
    lore = [
        item("world-fact", "Genova è una repubblica marinara."),
        item(
            "own-lab",
            "{{char}} restaura le mappe per {{ user }} in via del Campo.",
            title="Il laboratorio",
            own=True,
            constant=True,
            book_id="own",
        ),
        item("world-sea", "Genova si affaccia sul mare.", constant=True),
        item("lighthouse", "La Lanterna è il faro di {{char}} e {{user}}.", title="Lanterna", matched_key="faro"),
    ]
    scenario = {"title": "Il porto", "description": "Vi incontrate al porto vecchio.", "setting": "Genova"}
    assembly = await build(
        card(),
        chat(5),
        family=family,
        persona=MARTA,
        scenario=scenario,
        lore=lore,
        retriever=Retriever([memory(1, "Vive a Genova.")]),
    )

    text = assembly.prompt.text
    block_11 = (
        "Il tuo mondo:\n- Il laboratorio: Giulia restaura le mappe per Marta in via del Campo.\n"
        "- Genova si affaccia sul mare."
    )
    block_12 = (
        "Cose che sai su quello di cui state parlando:\n- Lanterna: La Lanterna è il faro di Giulia e Marta.\n"
        "- Genova è una repubblica marinara."
    )
    assert f"{persona_block(MARTA)}\n\n{block_11}\n\nScenario: Il porto" in text
    before_frame, frame = text.split(MARTA_FRAME, 1)
    assert block_11 in before_frame
    assert block_12 not in before_frame
    assert f"- [sicuro] Vive a Genova.\n{block_12}\nRicorda: sei Giulia Rossi" in frame
    assert "{{" not in text
    assert ids(assembly.lore) == ["own-lab", "world-sea", "lighthouse", "world-fact"]
    assert assembly.lore_cut == ()
    assert assembly.blocks["lore_constant"] == len(block_11.split())
    assert assembly.blocks["lore_activated"] == len(block_12.split())


@pytest.mark.anyio
async def test_a_block_holds_its_target_and_a_book_its_budget_in_the_prompt() -> None:
    """With 40-word entries, block 12 (target 500) holds twelve and block 11 (target 300) seven; a book budget of 100
    stops its own book after two entries while the other book goes on."""
    words = " ".join(["parola"] * 38)
    activated = [item(f"a{number:02d}", f"voce{number} {words}", insertion_order=number) for number in range(20)]
    constant = [item(f"c{number:02d}", f"fissa{number} {words}", constant=True) for number in range(20)]
    budgeted = [
        item(f"b{number}", f"limitata{number} {words}", constant=True, priority=1, book_id="small", book_budget=100)
        for number in range(4)
    ]
    assembly = await build(card(), chat(5), lore=[*activated, *constant, *budgeted], window=16384)

    kept = ids(assembly.lore)
    assert kept[:2] == ["b0", "b1"], "the higher priority first, up to its book's budget"
    assert len([name for name in kept if name.startswith("c")]) == 5
    assert len([name for name in kept if name.startswith("a")]) == 12
    assert assembly.blocks["lore_constant"] <= 300
    assert assembly.blocks["lore_activated"] <= 500
    assert ids(assembly.lore_cut)[:2] == ["b2", "b3"]


@pytest.mark.anyio
async def test_the_reduction_order_cuts_activated_lore_before_memories_and_constant_lore_after_the_persona() -> None:
    """docs/memory-engine.md (Planned for version 1.1): the lowest-priority activated entries go after the summaries and
    before the memories; the lowest-priority constant entries go after the persona and before the state details."""
    order = REDUCTION_ORDER.index
    assert order(SUMMARIES) < order(LORE_ACTIVATED) < order(MEMORIES)
    assert order(IDENTITY) < order(PERSONA) < order(LORE_CONSTANT) < order(STATE)

    character = card()
    lore = [
        item("constant-high", "Genova è una repubblica marinara.", constant=True, priority=5),
        item("constant-low", "Il porto vecchio ha un acquario.", constant=True, priority=1),
        item("activated-high", "La Lanterna è il faro di Genova.", priority=5),
        item("activated-low", "Il mercato orientale apre presto.", priority=1),
    ]
    memories = Retriever([memory(1, "Vive a Genova.")])
    roomy = await build(character, chat(3), lore=lore, retriever=memories)
    assert roomy.reductions == ()
    assert len(roomy.lore) == 4

    def window(short: int) -> int:
        """A window `short` tokens too small for the roomy prompt."""
        return roomy.token_total - short + roomy.max_length + 64

    first = await build(character, chat(3), lore=lore, retriever=Retriever(memories.items), window=window(1))
    assert first.fits
    assert first.reductions == (LORE_ACTIVATED,)
    assert ids(first.lore_cut) == ["activated-low"]
    assert "Il mercato orientale" not in first.prompt.text
    assert "La Lanterna" in first.prompt.text
    assert "Vive a Genova." in first.prompt.text

    full_identity = len(canonical_identity(character).split())
    identity_parts = full_identity - len(canonical_identity(character, frozenset(OPTIONAL_IDENTITY)).split())
    short = roomy.blocks["lore_activated"] + roomy.blocks["memories"] + identity_parts + 1
    second = await build(character, chat(3), lore=lore, retriever=Retriever(memories.items), window=window(short))
    assert second.fits
    assert second.reductions == (LORE_ACTIVATED, MEMORIES, IDENTITY, LORE_CONSTANT)
    assert ids(second.lore) == ["constant-high"]
    assert "Il tuo mondo:\n- Genova è una repubblica marinara." in second.prompt.text
    assert "acquario" not in second.prompt.text


PLACES = ("faro", "porto", "mercato", "cattedrale")
SOURCES = (
    LoreSource(
        book("own", BookKind.CHARACTER),
        (entry("own-lab", "own", title="Il laboratorio", content="{{char}} restaura mappe antiche.", constant=True),),
    ),
    # A scan depth of 1 reads only the current message, so each reply activates the place it names.
    LoreSource(
        book("genova", scan_depth=1),
        tuple(
            entry(f"place-{place}", "genova", content=f"Storia del luogo {place}, raccontata dai vecchi.", keys=[place])
            for place in PLACES
        ),
    ),
)


def scripted(count: int) -> list[Message]:
    """Returns `count` messages of one chat: each user message asks about the next place of PLACES, each character
    message is plain, so a longer chat starts with the messages of a shorter one."""
    messages: list[Message] = []
    for seq in range(1, count + 1):
        if seq % 2:
            place = PLACES[(seq // 2) % len(PLACES)]
            messages.append(message(seq, Author.USER, f"Raccontami del {place}, per favore."))
        else:
            messages.append(message(seq, Author.CHARACTER, f"Messaggio numero {seq} della chat."))
    return messages


@pytest.mark.anyio
@pytest.mark.parametrize("family", FAMILIES)
async def test_the_prefix_stays_byte_identical_over_twelve_replies_while_the_activated_entries_change(
    family: TextTemplate,
) -> None:
    """T-LOR-03 with T-MOD-05: over twelve replies the activated entry changes with the place each message names. The
    prefix of each reply starts with the bytes of the one before, block 11 stays in the stable text, and the activated
    entry sits only in the dynamic context."""
    block_11 = "Il tuo mondo:\n- Il laboratorio: Giulia restaura mappe antiche."
    previous = b""
    stable_parts: set[str] = set()
    for turn in range(1, 13):
        messages = scripted(2 * turn + 1)
        place = PLACES[turn % len(PLACES)]
        lore = active_lore(SOURCES, [sent.content for sent in messages], "it")
        assert ids(lore) == ["own-lab", f"place-{place}"]
        assembly = await build(
            card(),
            messages,
            family=family,
            lore=lore,
            retriever=Retriever([memory(turn, f"Anna ricorda il fatto numero {turn}.")]),
            notes=(f"Nota del turno {turn}.",),
            now=NOW + timedelta(hours=turn),
        )
        prompt = assembly.prompt
        prefix = prompt.prefix.encode("utf-8")
        assert prefix.startswith(previous), f"turn {turn}: the prefix of turn {turn - 1} is no longer its start"
        assert len(prefix) > len(previous)
        before_frame, dynamic = prompt.text.split(FRAME, 1)
        stable = prompt.prefix if family != "mistral_v3_tekken" else before_frame[len(prompt.prefix) :]
        assert block_11 in stable
        stable_parts.add(stable if family == "mistral_v3_tekken" else "")
        line = f"- Storia del luogo {place}, raccontata dai vecchi."
        assert f"Cose che sai su quello di cui state parlando:\n{line}\nRicorda: sei" in dynamic
        assert "Storia del luogo" not in prompt.prefix
        assert "Storia del luogo" not in before_frame
        previous = prefix
    assert len(stable_parts) == 1, "the stable text of the last user turn never changed"
