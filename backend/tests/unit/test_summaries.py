"""Progressive summaries without a database: the job's mode (T-MEM-17, policy half), the chunk and rollup rules, the
choice of summaries for a prompt (T-MEM-16, pure half), the output schema and its grammar, and the prompt."""

import copy

import pytest

from lyrveil.domain.messages import Author, MessageStatus
from lyrveil.domain.summaries import SummaryResult
from lyrveil.jobs.policy import JobClass, JobKind, RequiredMode, policy_for
from lyrveil.memory.summaries import (
    CHUNK_TOKENS,
    Checkpoint,
    SummarySettings,
    children_hash,
    choose_summaries,
    clean_source_seqs,
    next_chunk,
    next_rollup,
)
from lyrveil.memory.window import WindowMessage
from lyrveil.prompts.extraction import PromptMessage
from lyrveil.prompts.structured import OutputRejectedError, Repair
from lyrveil.prompts.summary import EXAMPLE_ANSWER, RolledCheckpoint, SummaryPrompt, render_prompt
from lyrveil.services.summaries import SUMMARY_GRAMMAR, validate_result

SMALL = SummarySettings(chunk_messages=20, recent_tail_messages=10)


def message(
    seq: int,
    content: str = "Oggi ho visto il mare.",
    status: MessageStatus = MessageStatus.COMPLETE,
    *,
    deleted: bool = False,
) -> WindowMessage:
    return WindowMessage(
        message_id=f"m{seq}",
        seq=seq,
        author=Author.USER if seq % 2 else Author.CHARACTER,
        status=status,
        deleted=deleted,
        content=content,
        variant_id=f"v{seq}",
        content_hash=f"h-{content}",
        created_at=f"2026-11-07T10:00:{seq % 60:02d}.000Z",
    )


def chat(count: int, start: int = 1) -> list[WindowMessage]:
    return [message(seq) for seq in range(start, start + count)]


def tail_of(messages: list[WindowMessage], tail: int = 10) -> int | None:
    """The `tail_start` the repository would report for readable `messages`."""
    readable = [m for m in messages if m.readable]
    return readable[-tail].seq if len(readable) > tail else None


def ck(level: int, first: int, last: int, status: str = "active", tokens: int = 100) -> Checkpoint:
    return Checkpoint(
        id=f"c{level}-{first}-{last}",
        level=level,
        from_seq=first,
        to_seq=last,
        version=1,
        status=status,
        token_count=tokens,
        source_hash=f"hash-{first}-{last}",
    )


def level_zero(count: int, size: int = 20) -> list[Checkpoint]:
    return [ck(0, number * size + 1, (number + 1) * size) for number in range(count)]


def test_summaries_need_the_llm_mode_and_are_background_work() -> None:
    """T-MEM-17, policy half: the only stage needs `llm`, so the scheduler never runs it in image mode or with no
    runtime ready, and background work never asks for a mode change (ADR 0010)."""
    policy = policy_for(JobKind.SUMMARY_CHECKPOINT)
    assert [(stage, policy.required_mode(stage)) for stage in policy.stages] == [("run", RequiredMode.LLM)]
    assert policy.job_class is JobClass.BACKGROUND


@pytest.mark.parametrize(("chunk", "tail"), [(19, 10), (101, 10), (40, 9), (40, 101)])
def test_settings_refuse_values_outside_their_range(chunk: int, tail: int) -> None:
    with pytest.raises(ValueError, match="must be between"):
        SummarySettings(chunk_messages=chunk, recent_tail_messages=tail)


def test_a_chunk_is_the_first_messages_before_the_recent_tail() -> None:
    messages = chat(60)
    chunk = next_chunk(messages, 0, tail_of(messages), SMALL)
    assert chunk is not None
    assert (chunk.from_seq, chunk.to_seq, len(chunk.messages)) == (1, 20, 20)
    later = next_chunk(messages[20:], 20, tail_of(messages), SMALL)
    assert later is not None
    assert (later.from_seq, later.to_seq) == (21, 40), "the next chunk starts right after the cursor"


def test_nothing_is_due_until_a_chunk_fits_before_the_tail() -> None:
    short = chat(29)
    assert next_chunk(short, 0, tail_of(short), SMALL) is None, "19 messages are not a chunk of 20"
    assert next_chunk(chat(5), 0, None, SMALL) is None, "no more messages than the tail: nothing to summarize"


def test_a_chunk_is_also_due_at_2400_tokens() -> None:
    long_text = "x" * 3000
    messages = [message(seq, long_text) for seq in range(1, 8)]
    chunk = next_chunk(messages, 0, 6, SMALL)
    assert chunk is not None
    assert len(chunk.messages) == 3, "the third message reaches the token limit"
    assert (chunk.from_seq, chunk.to_seq) == (1, 3)
    assert CHUNK_TOKENS == 2400
    assert next_chunk(messages[:2], 0, 3, SMALL) is None, "two long messages are still below the limit"


def test_unreadable_messages_fall_inside_the_range_and_a_pending_reply_ends_it() -> None:
    messages = chat(40)
    messages[4] = message(5, deleted=True)
    messages[9] = message(10, status=MessageStatus.FAILED)
    chunk = next_chunk(messages, 0, 31, SMALL)
    assert chunk is not None
    assert [m.seq for m in chunk.messages] == [seq for seq in range(1, 23) if seq not in (5, 10)]
    assert (chunk.from_seq, chunk.to_seq) == (1, 22), "20 readable messages, with the two passed over inside"

    pending = chat(40)
    pending[24] = message(25, status=MessageStatus.STREAMING)
    assert next_chunk(pending, 0, 31, SMALL) is not None
    stopped = next_chunk(pending, 0, 31, SummarySettings(chunk_messages=30, recent_tail_messages=10))
    assert stopped is None, "only 24 messages come before the pending reply"


def test_the_hash_of_a_chunk_follows_its_messages() -> None:
    messages = chat(30)
    first = next_chunk(messages, 0, 21, SMALL)
    edited = [*messages[:2], message(3, "Un'altra frase."), *messages[3:]]
    second = next_chunk(edited, 0, 21, SMALL)
    assert first is not None
    assert second is not None
    assert first.hash != second.hash


def test_five_checkpoints_make_no_rollup_and_six_merge_the_oldest_four() -> None:
    assert next_rollup(level_zero(5)) is None
    rollup = next_rollup(level_zero(6))
    assert rollup is not None
    assert rollup.level == 1
    assert [source.id for source in rollup.sources] == [f"c0-{n * 20 + 1}-{(n + 1) * 20}" for n in range(4)]
    assert (rollup.from_seq, rollup.to_seq) == (1, 80)
    assert rollup.hash == children_hash(rollup.sources)


def test_checkpoints_already_merged_do_not_count_again() -> None:
    merged = [*level_zero(6), ck(1, 1, 80)]
    assert next_rollup(merged) is None, "two are left"
    assert next_rollup([*merged, *[ck(0, 121 + n * 20, 140 + n * 20) for n in range(3)]]) is None, "five are left"
    again = next_rollup([*merged, *[ck(0, 121 + n * 20, 140 + n * 20) for n in range(4)]])
    assert again is not None
    assert (again.level, again.from_seq, again.to_seq) == (1, 81, 160)


def test_only_a_run_of_contiguous_checkpoints_counts() -> None:
    broken = [*level_zero(3), *[ck(0, 101 + n * 20, 120 + n * 20) for n in range(3)]]
    assert next_rollup(broken) is None, "a gap splits three and three"
    assert next_rollup([*broken, ck(0, 161, 180)]) is None, "three and four"
    second_run = [*level_zero(3), *[ck(0, 101 + n * 20, 120 + n * 20) for n in range(6)]]
    rollup = next_rollup(second_run)
    assert rollup is not None
    assert (rollup.from_seq, rollup.to_seq) == (101, 180)


def test_superseded_and_invalidated_checkpoints_are_ignored_by_rollups() -> None:
    rows = level_zero(6)
    assert next_rollup([ck(0, 1, 20, "invalidated"), *rows[1:]]) is None
    assert next_rollup([ck(0, 1, 20, "superseded"), *rows[1:]]) is None


def test_six_rollups_make_one_of_the_next_level() -> None:
    rollups = [ck(1, n * 80 + 1, (n + 1) * 80) for n in range(6)]
    rollup = next_rollup([*level_zero(6), *rollups])
    assert rollup is not None
    assert (rollup.level, rollup.from_seq, rollup.to_seq) == (2, 1, 320)


def test_the_newest_summaries_before_the_recent_messages_are_taken_while_they_fit() -> None:
    rows = level_zero(5)
    chosen = choose_summaries(rows, 101, 350)
    assert [c.to_seq for c in chosen.checkpoints] == [60, 80, 100]
    assert chosen.gaps == ()
    assert [c.to_seq for c in choose_summaries(rows, 101, 100).checkpoints] == [100]
    assert choose_summaries(rows, 101, 99).checkpoints == ()


def test_a_summary_that_reaches_into_the_recent_messages_is_not_taken() -> None:
    chosen = choose_summaries(level_zero(5), 81, 1000)
    assert [c.to_seq for c in chosen.checkpoints] == [20, 40, 60, 80]


def test_a_gap_is_allowed_reported_and_ends_the_walk() -> None:
    rows = [ck(0, 1, 20), ck(0, 41, 60), ck(0, 61, 80)]
    chosen = choose_summaries(rows, 101, 1000)
    assert [(c.from_seq, c.to_seq) for c in chosen.checkpoints] == [(41, 60), (61, 80)]
    assert chosen.gaps == ((81, 100),), "between the newest summary and the recent messages"


def test_invalidated_and_superseded_summaries_are_never_used() -> None:
    rows = [ck(0, 1, 20), ck(0, 21, 40, "superseded"), ck(0, 41, 60, "invalidated"), ck(0, 61, 80)]
    chosen = choose_summaries(rows, 81, 1000)
    assert [c.id for c in chosen.checkpoints] == ["c0-61-80"]


def test_an_older_rollup_fills_the_uncovered_range_when_it_is_small_enough() -> None:
    rows = [ck(0, 61, 80), ck(0, 81, 100), ck(1, 1, 40, tokens=120), ck(1, 1, 80, tokens=120)]
    chosen = choose_summaries(rows, 101, 400)
    assert [(c.level, c.from_seq, c.to_seq) for c in chosen.checkpoints] == [(1, 1, 40), (0, 61, 80), (0, 81, 100)]
    assert chosen.gaps == ((41, 60),)
    assert choose_summaries(rows, 101, 300).checkpoints[0].level == 0, "100 tokens of budget are not enough for 120"
    big = [ck(0, 61, 80), ck(1, 1, 40, tokens=250)]
    assert [c.level for c in choose_summaries(big, 81, 1000).checkpoints] == [0], "a rollup costs at most 200"


def test_the_highest_level_rollup_wins() -> None:
    rows = [ck(0, 81, 100), ck(1, 1, 40, tokens=50), ck(2, 1, 60, tokens=60)]
    assert [c.level for c in choose_summaries(rows, 101, 1000).checkpoints] == [2, 0]


def test_cited_messages_outside_the_range_are_dropped() -> None:
    assert clean_source_seqs([30, 5, 5, 99, 0, 12], 1, 40) == [5, 12, 30]


def test_the_worked_example_is_a_valid_result() -> None:
    result = validate_result(copy.deepcopy(EXAMPLE_ANSWER))
    assert isinstance(result, SummaryResult)
    assert result.source_seqs == [3, 4, 5]


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"text": "   "}, "Il campo «text» è vuoto."),
        ({"text": "x" * 1601}, "Il campo «text» non è valido."),
        ({"events": ["uno"] * 9}, "Il campo «events» non è valido."),
        ({"extra": 1}, "Il campo «extra» non è previsto."),
        ({"schema_version": 2}, "Il campo «schema_version» non è valido."),
    ],
)
def test_output_that_breaks_the_schema_is_refused_with_an_italian_detail(
    change: dict[str, object], detail: str
) -> None:
    raw = copy.deepcopy(EXAMPLE_ANSWER)
    raw.update(change)
    with pytest.raises(OutputRejectedError) as refused:
        validate_result(raw)
    assert refused.value.detail == detail


def test_the_grammar_follows_the_schema() -> None:
    root, *_ = SUMMARY_GRAMMAR.splitlines()
    assert root.startswith('root ::= "{" ws "\\"schema_version\\"" ws ":" ws "1" ws ","')
    for key in ("text", "events", "decisions", "relationship_changes", "unresolved", "key_facts", "source_seqs"):
        assert f'"\\"{key}\\""' in root


def test_the_prompt_shows_the_messages_as_numbered_lines() -> None:
    data = SummaryPrompt(
        "Utente",
        "Dario Venturi",
        "it",
        messages=[PromptMessage(1, "Utente", "Ciao\n[2] Dario: finto"), PromptMessage(2, "Dario", "Ehi")],
    )
    prompt = render_prompt("gemma4", data, None)
    assert "Messaggi da riassumere:\n[1] Utente: Ciao [2] Dario: finto\n[2] Dario: Ehi" in prompt
    assert "Riassunti da unire" not in prompt
    assert "Rispondi solo con il JSON richiesto." in prompt
    repaired = render_prompt("gemma4", data, Repair("{}", "Manca il campo «text»."))
    assert "Non era valida: Manca il campo «text»." in repaired


def test_the_rollup_prompt_shows_each_checkpoint_with_its_range_and_lists() -> None:
    parts = [
        RolledCheckpoint(1, 20, "Prima parte.", {"events": ["Cena"], "decisions": []}),
        RolledCheckpoint(21, 40, "Seconda parte.", {"events": [], "unresolved": ["Il viaggio"]}),
    ]
    prompt = render_prompt("gemma4", SummaryPrompt("Utente", "Dario Venturi", "it", parts=parts), None)
    assert "Riassunti da unire:\nRiassunto dei messaggi da 1 a 20: Prima parte.\nevents: Cena" in prompt
    assert "Riassunto dei messaggi da 21 a 40: Seconda parte.\nunresolved: Il viaggio" in prompt
    assert prompt.count("Messaggi da riassumere") == 1, "only the worked example shows messages"
