"""LoreService: creates, lists, edits and deletes lore books, worlds, entries and links (ADR 0049).

A book is a character's own book or a world. This service owns the rows and the rules the database cannot hold: every
title, content and world name passes the content guard of ADR 0037, keys are stemmed when an entry is saved and a key
with no stem is refused, a book holds at most 500 entries, and a link has exactly one target and only a world is
linked. `match` runs the keyword matcher of `lore_matcher` on a pasted text. A reply reads its books in
lyrveil.services.chat_inference.load_context_request, and lyrveil.context.builder places them in blocks 11 and 12.
"""

import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from lyrveil.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.lore import (
    DEFAULT_SCAN_DEPTH,
    MAX_ENTRIES_PER_BOOK,
    WORLD_KEY_LANGUAGE,
    BookKind,
    BookStatus,
    LoreBook,
    LoreEntry,
    LoreLink,
)
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import lore as repository
from lyrveil.services.lore_matcher import EntryMatch, match_entries
from lyrveil.services.lore_rules import checked_book, checked_entry

NO_BOOK = "Libro di lore non trovato."
NO_ENTRY = "Voce di lore non trovata."
NO_CHARACTER = "Personaggio non trovato."
NO_LINK = "Collegamento non trovato."
_NO_TARGETS = {
    "character_id": NO_CHARACTER,
    "scenario_id": "Scenario non trovato.",
    "conversation_id": "Conversazione non trovata.",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _fold(value: str) -> str:
    """Returns `value` lowercase with accents removed, the form search compares."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _contains_every_word(q: str | None, *texts: str) -> bool:
    """Returns whether every word of `q` appears in the texts, ignoring case and accents. A blank or missing `q`
    matches everything."""
    words = _fold(q or "").split()
    if not words:
        return True
    haystack = _fold(" ".join(texts))
    return all(word in haystack for word in words)


class LoreService:
    """Reads and changes the lore of `engine`. `clock` returns the aware current time; tests pass a fake one."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = _utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def create_world(self, fields: dict[str, Any]) -> LoreBook:
        """Returns a new active world. `fields` holds `name` (required) and optionally `description`, `scan_depth` and
        `token_budget`. Raises what `checked_book` raises."""
        clean = checked_book(fields)
        now = to_utc_text(self._clock())

        def work(connection: Any) -> LoreBook:
            book_id = new_id()
            repository.insert_book(connection, book_id, BookKind.WORLD, None, clean, now)
            return self._book(connection, book_id)

        return write(self._engine, work)

    def own_book(self, character_id: str) -> LoreBook:
        """Returns the character's own book, created empty on first use with the character's first name as its name and
        the default scan depth. A character has at most one, so a second call returns the same book. Raises
        NotFoundError for an unknown character."""
        now = to_utc_text(self._clock())

        def work(connection: Any) -> LoreBook:
            existing = repository.get_character_book(connection, character_id)
            if existing is not None:
                return existing
            name = repository.character_first_name(connection, character_id)
            if name is None:
                raise NotFoundError(NO_CHARACTER)
            book_id = new_id()
            fields = {"name": name, "description": "", "scan_depth": DEFAULT_SCAN_DEPTH, "token_budget": None}
            repository.insert_book(connection, book_id, BookKind.CHARACTER, character_id, fields, now)
            return self._book(connection, book_id)

        return write(self._engine, work)

    def list_worlds(self, status: BookStatus = BookStatus.ACTIVE, q: str | None = None) -> list[LoreBook]:
        """Returns the worlds in `status`, by name. A non-blank `q` keeps those whose name or description contains every
        word of it, with case and accents ignored; it is plain text."""
        found = read(self._engine, lambda connection: repository.list_worlds(connection, status))
        return [book for book in found if _contains_every_word(q, book.name, book.description)]

    def get_book(self, book_id: str) -> LoreBook:
        """Returns the book in any status; raises NotFoundError when none has that id."""
        return read(self._engine, lambda connection: self._book(connection, book_id))

    def update_book(self, book_id: str, fields: dict[str, Any]) -> LoreBook:
        """Replaces the name, description, scan depth and token budget (PUT) and returns the book. Raises NotFoundError
        for an unknown book, and what `checked_book` raises."""
        clean = checked_book(fields)
        now = to_utc_text(self._clock())

        def work(connection: Any) -> LoreBook:
            self._book(connection, book_id)
            repository.update_book(connection, book_id, now, **clean)
            return self._book(connection, book_id)

        return write(self._engine, work)

    def set_archived(self, book_id: str, archived: bool) -> LoreBook:
        """Archives or restores a world and returns it. Raises NotFoundError for an unknown book and ConflictError for
        an own book, which follows its character."""
        now = to_utc_text(self._clock())
        status = BookStatus.ARCHIVED if archived else BookStatus.ACTIVE

        def work(connection: Any) -> LoreBook:
            current = self._book(connection, book_id)
            if current.kind is not BookKind.WORLD:
                raise ConflictError("Il libro di un personaggio non si archivia.")
            if current.status is not status:
                repository.update_book(connection, book_id, now, status=status.value)
            return self._book(connection, book_id)

        return write(self._engine, work)

    def delete_world(self, book_id: str) -> None:
        """Removes a world with its entries and links. Raises NotFoundError for an unknown book and ConflictError for
        an own book, which goes only with its character."""

        def work(connection: Any) -> None:
            if self._book(connection, book_id).kind is not BookKind.WORLD:
                raise ConflictError("Il libro di un personaggio si elimina solo con il personaggio.")
            repository.delete_book(connection, book_id)

        write(self._engine, work)

    def create_entry(self, book_id: str, fields: dict[str, Any]) -> LoreEntry:
        """Returns a new entry in the book. `fields` is what `checked_entry` reads. The keys are stemmed with the
        language of the book: the character's primary language for an own book, Italian for a world.

        Raises NotFoundError for an unknown book, what `checked_entry` raises, and ConflictError when the book already
        holds 500 entries. An entry over its block's maximum is stored, with `never_inserted` true."""
        now = to_utc_text(self._clock())

        def work(connection: Any) -> LoreEntry:
            book = self._book(connection, book_id)
            clean = checked_entry(fields, self._language(connection, book))
            if repository.count_entries(connection, book_id) >= MAX_ENTRIES_PER_BOOK:
                raise ConflictError(f"Un libro può avere al massimo {MAX_ENTRIES_PER_BOOK} voci.")
            entry_id = new_id()
            try:
                repository.insert_entry(connection, entry_id, book_id, clean, now)
            except IntegrityError as error:  # The trigger of revision 0021 is the last guard against a racing writer.
                raise ConflictError(f"Un libro può avere al massimo {MAX_ENTRIES_PER_BOOK} voci.") from error
            return self._entry(connection, entry_id)

        return write(self._engine, work)

    def list_entries(self, book_id: str, enabled: bool | None = None, q: str | None = None) -> list[LoreEntry]:
        """Returns the entries of a book by `insertion_order`, only the enabled or the disabled ones when `enabled` is
        given. A non-blank `q` keeps those whose title, content, keys or note contains every word of it, with case and
        accents ignored. Raises NotFoundError for an unknown book."""

        def work(connection: Any) -> list[LoreEntry]:
            self._book(connection, book_id)
            return repository.list_entries(connection, book_id, enabled)

        found = read(self._engine, work)
        return [
            entry
            for entry in found
            if _contains_every_word(
                q, entry.title, entry.content, entry.note, *entry.keys, *entry.secondary_keys, *entry.exclude_keys
            )
        ]

    def get_entry(self, entry_id: str) -> LoreEntry:
        """Returns the entry; raises NotFoundError when none has that id."""
        return read(self._engine, lambda connection: self._entry(connection, entry_id))

    def update_entry(self, entry_id: str, fields: dict[str, Any]) -> LoreEntry:
        """Replaces every editable field (PUT) and stems the keys again. Raises NotFoundError for an unknown entry, and
        what `checked_entry` raises."""
        now = to_utc_text(self._clock())

        def work(connection: Any) -> LoreEntry:
            current = self._entry(connection, entry_id)
            clean = checked_entry(fields, self._language(connection, self._book(connection, current.book_id)))
            repository.update_entry(connection, entry_id, now, clean)
            return self._entry(connection, entry_id)

        return write(self._engine, work)

    def delete_entry(self, entry_id: str) -> None:
        """Removes an entry. Raises NotFoundError for an unknown entry."""

        def work(connection: Any) -> None:
            self._entry(connection, entry_id)
            repository.delete_entry(connection, entry_id)

        write(self._engine, work)

    def list_links(self, book_id: str) -> list[LoreLink]:
        """Returns the links of a book, oldest first; an own book has none. Raises NotFoundError for an unknown book."""

        def work(connection: Any) -> list[LoreLink]:
            self._book(connection, book_id)
            return repository.list_links(connection, book_id)

        return read(self._engine, work)

    def add_link(
        self,
        book_id: str,
        *,
        character_id: str | None = None,
        scenario_id: str | None = None,
        conversation_id: str | None = None,
    ) -> LoreLink:
        """Links the world to exactly one character, scenario or conversation and returns the link.

        Raises ValidationFailedError unless exactly one target is given, NotFoundError for an unknown book or target (a
        conversation in the trash is unknown), and ConflictError for an own book, which is never linked, and for a
        target the world is already linked to. A link added to a scenario reaches only the conversations that start
        afterwards: each copies the scenario's links once, when it starts.
        """
        given = {
            name: value
            for name, value in (
                ("character_id", character_id),
                ("scenario_id", scenario_id),
                ("conversation_id", conversation_id),
            )
            if value is not None
        }
        if len(given) != 1:
            raise ValidationFailedError(
                "Un collegamento ha una sola destinazione: indica un personaggio, uno scenario o una conversazione."
            )
        target, target_id = next(iter(given.items()))
        now = to_utc_text(self._clock())

        def work(connection: Any) -> LoreLink:
            if self._book(connection, book_id).kind is not BookKind.WORLD:
                raise ConflictError("Il libro di un personaggio non si collega: solo i mondi si collegano.")
            if not repository.target_exists(connection, target, target_id):
                raise NotFoundError(_NO_TARGETS[target])
            link_id = new_id()
            try:
                repository.insert_link(connection, link_id, book_id, target, target_id, now)
            except IntegrityError as error:  # The unique index of revision 0021 holds one link per world and target.
                raise ConflictError("Il mondo è già collegato a questa destinazione.") from error
            link = repository.get_link(connection, link_id)
            assert link is not None
            return link

        return write(self._engine, work)

    def delete_link(self, link_id: str) -> None:
        """Removes a link; the world, its entries and the target stay. A conversation that already read the world stops
        reading it from its next reply. Raises NotFoundError for an unknown link."""

        def work(connection: Any) -> None:
            if repository.get_link(connection, link_id) is None:
                raise NotFoundError(NO_LINK)
            repository.delete_link(connection, link_id)

        write(self._engine, work)

    def match(self, book_id: str, text: str, language: str | None = None) -> list[tuple[LoreEntry, EntryMatch]]:
        """Runs the matcher of ADR 0049 on `text` as the only message of the window and returns every entry of the book
        with its outcome, by `insertion_order`. It reads no model and writes nothing. `language` stems the text; when
        blank, the language of the book's keys is used. The window holds one message, so the book's `scan_depth` does
        not limit it here. Raises NotFoundError for an unknown book."""

        def work(connection: Any) -> tuple[list[LoreEntry], str]:
            book = self._book(connection, book_id)
            return repository.list_entries(connection, book_id, None), self._language(connection, book)

        entries, book_language = read(self._engine, work)
        results = match_entries(entries, [text], 1, language or book_language)
        return list(zip(entries, results, strict=True))

    @staticmethod
    def _language(connection: Any, book: LoreBook) -> str:
        """Returns the language that stems the keys of `book`: its character's primary language, else Italian."""
        if book.character_id is not None:
            return repository.character_language(connection, book.character_id) or WORLD_KEY_LANGUAGE
        return WORLD_KEY_LANGUAGE

    @staticmethod
    def _book(connection: Any, book_id: str) -> LoreBook:
        found = repository.get_book(connection, book_id)
        if found is None:
            raise NotFoundError(NO_BOOK)
        return found

    @staticmethod
    def _entry(connection: Any, entry_id: str) -> LoreEntry:
        found = repository.get_entry(connection, entry_id)
        if found is None:
            raise NotFoundError(NO_ENTRY)
        return found
