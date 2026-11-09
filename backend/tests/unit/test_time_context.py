"""T-SCH-07: the time context of block 4 is worded in coarse Italian."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from lyrveil.context.blocks import state_block
from lyrveil.context.time_context import elapsed_human, part_of_day, time_lines

ROME = ZoneInfo("Europe/Rome")
# 2026-11-01 is a Sunday; 17:40 UTC is 18:40 in Rome (CET).
NOW = datetime(2026, 11, 1, 17, 40, tzinfo=UTC)


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, "di notte"),
        (5, "di notte"),
        (6, "di mattina"),
        (11, "di mattina"),
        (12, "a mezzogiorno"),
        (13, "a mezzogiorno"),
        (14, "di pomeriggio"),
        (17, "di pomeriggio"),
        (18, "di sera"),
        (23, "di sera"),
    ],
)
def test_part_of_day_bands(hour: int, expected: str) -> None:
    assert part_of_day(hour) == expected


@pytest.mark.parametrize(
    ("ago", "expected"),
    [
        (timedelta(minutes=5), "poco fa"),
        (timedelta(hours=3), "qualche ora fa"),
        (timedelta(hours=7), "oggi"),
        (timedelta(hours=20), "ieri"),
        (timedelta(days=3), "tre giorni fa"),
        (timedelta(days=15), "due settimane fa"),
        (timedelta(days=45), "un mese fa"),
        (timedelta(days=100), "tre mesi fa"),
        (timedelta(days=500), "più di un anno fa"),
        (timedelta(0), ""),
    ],
)
def test_elapsed_time_is_coarse(ago: timedelta, expected: str) -> None:
    assert elapsed_human(NOW, NOW - ago, ROME) == expected


def test_time_lines_use_the_local_date_and_part_of_day() -> None:
    last = NOW - timedelta(days=3)
    assert time_lines(NOW, last, ROME) == (
        "Adesso è domenica di sera, 1 novembre.",
        "Vi siete sentiti l'ultima volta tre giorni fa.",
    )


def test_time_lines_without_a_last_interaction_have_only_the_date() -> None:
    assert time_lines(NOW, None, ROME) == ("Adesso è domenica di sera, 1 novembre.",)


def test_time_lines_follow_the_zone_across_midnight() -> None:
    late = datetime(2026, 11, 1, 23, 30, tzinfo=UTC)
    assert time_lines(late, None, ROME) == ("Adesso è lunedì di notte, 2 novembre.",)


def test_state_block_ends_with_the_time_lines() -> None:
    assert state_block(["Il vostro rapporto: x."], ["Discorsi: y."], ["Adesso è lunedì di notte, 2 novembre."]) == (
        "Il vostro rapporto: x.\nDiscorsi: y.\nAdesso è lunedì di notte, 2 novembre."
    )
