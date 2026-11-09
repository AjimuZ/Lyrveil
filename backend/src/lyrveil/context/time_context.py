"""The time context of block 4 in Italian (docs/scheduler.md, Time context; docs/prompting.md, Block 4): the date, the
part of the day and the coarse time since the last interaction, with no database and no runtime.

Values are coarse on purpose, so the dynamic context changes rarely and never shows a clock time or a number of
minutes. Pure: the same instant, zone and last interaction give the same text.
"""

from datetime import datetime, tzinfo

WEEKDAYS = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
MONTHS = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)
_COUNT_WORDS = {
    2: "due",
    3: "tre",
    4: "quattro",
    5: "cinque",
    6: "sei",
    7: "sette",
    8: "otto",
    9: "nove",
    10: "dieci",
    11: "undici",
    12: "dodici",
    13: "tredici",
}
RECENT_MINUTES = 60
FEW_HOURS = 6 * 60
MAX_DAYS = 13
MAX_WEEKS = 4
DAYS_PER_MONTH = 30


def part_of_day(hour: int) -> str:
    """Returns the part of the day of a local `hour` (0 to 23): `di notte` 0 to 5, `di mattina` 6 to 11,
    `a mezzogiorno` 12 to 13, `di pomeriggio` 14 to 17, `di sera` 18 to 23."""
    if hour < 6:
        return "di notte"
    if hour < 12:
        return "di mattina"
    if hour < 14:
        return "a mezzogiorno"
    if hour < 18:
        return "di pomeriggio"
    return "di sera"


def elapsed_human(now: datetime, last: datetime, zone: tzinfo) -> str:
    """Returns how long ago `last` was, in coarse Italian words, or "" when `last` is not before `now`.

    Under an hour is `poco fa`, under six hours `qualche ora fa`; beyond that the local calendar days decide: `oggi`,
    `ieri`, `tre giorni fa` up to thirteen days, then weeks, months and `più di un anno fa`.
    """
    minutes = (now - last).total_seconds() / 60
    if minutes <= 0:
        return ""
    if minutes < RECENT_MINUTES:
        return "poco fa"
    if minutes < FEW_HOURS:
        return "qualche ora fa"
    days = (now.astimezone(zone).date() - last.astimezone(zone).date()).days
    if days <= 0:
        return "oggi"
    if days == 1:
        return "ieri"
    if days <= MAX_DAYS:
        return f"{_COUNT_WORDS[days]} giorni fa"
    weeks = days // 7
    if weeks <= MAX_WEEKS and days < DAYS_PER_MONTH:
        return f"{_COUNT_WORDS[weeks]} settimane fa"
    months = days // DAYS_PER_MONTH
    if months == 1:
        return "un mese fa"
    if months <= 11:
        return f"{_COUNT_WORDS[months]} mesi fa"
    return "più di un anno fa"


def time_lines(now: datetime, last_interaction: datetime | None, zone: tzinfo) -> tuple[str, ...]:
    """Returns the time lines of block 4: `Adesso è sabato di sera, 1 novembre.` and, when `last_interaction` is before
    `now`, `Vi siete sentiti l'ultima volta ieri.` `now` and `last_interaction` must be aware; `zone` is LYRVEIL_TZ."""
    local = now.astimezone(zone)
    lines = [f"Adesso è {WEEKDAYS[local.weekday()]} {part_of_day(local.hour)}, {local.day} {MONTHS[local.month - 1]}."]
    if last_interaction is not None:
        elapsed = elapsed_human(now, last_interaction, zone)
        if elapsed:
            lines.append(f"Vi siete sentiti l'ultima volta {elapsed}.")
    return tuple(lines)
