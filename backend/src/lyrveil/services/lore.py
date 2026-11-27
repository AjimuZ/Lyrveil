"""LoreService: creates, lists, edits and deletes lore books, worlds and entries (ADR 0049).

A book is a character's own book or a world. This service owns the rows and the rules the database cannot hold: every
title, content and world name passes the content guard of ADR 0037, keys are stemmed when an entry is saved and a key
with no stem is refused, and a book holds at most 500 entries. Links, the matcher and the prompt blocks belong to later
tasks of P12.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from lyrveil.domain.errors import ConflictError, NotFoundError
from lyrveil.domain.identifiers import new_id, to_utc_text
from lyrveil.domain.lore import (
    DEFAULT_SCAN_DEPTH,
    MAX_ENTRIES_PER_BOOK,
    WORLD_KEY_LANGUAGE,
    BookKind,
    BookStatus,
    LoreBook,
    LoreEntry,
)
from lyrveil.jobs.scheduler import read, write
from lyrveil.persistence import lore as repository
from lyrveil.services.lore_rules import checked_book, checked_entry

NO_BOOK = "Libro di lore non trovato."
NO_ENTRY = "Voce di lore non trovata."
NO_CHARACTER = "Personaggio non trovato."


def _utc_now() -> datetime:
    return datetime.now(UTC)


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

    def list_worlds(self, status: BookStatus = BookStatus.ACTIVE) -> list[LoreBook]:
        """Returns the worlds in `status`, by name."""
        return read(self._engine, lambda connection: repository.list_worlds(connection, status))

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

    def list_entries(self, book_id: str, enabled: bool | None = None) -> list[LoreEntry]:
        """Returns the entries of a book by `insertion_order`, only the enabled or the disabled ones when `enabled` is
        given. Raises NotFoundError for an unknown book."""

        def work(connection: Any) -> list[LoreEntry]:
            self._book(connection, book_id)
            return repository.list_entries(connection, book_id, enabled)

        return read(self._engine, work)

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
