"""The `character_book` of every card imported before P12.4 becomes its character's own lore book (ADR 0049, P12.4).

Revision ID: 0023
Revises: 0022

A data migration: the schema does not change. Each `character_sources` row whose stored card has a `character_book` is
converted by lyrveil.services.lorebook, the code a card save runs since P12.4, with the keys stemmed in the character's
primary language. Stemming and the decorator rules live only in Python, so this revision imports the application
instead of repeating them in SQL. A character whose own book already has entries is left as it is, and an empty own
book, made by opening the editor, is filled. Running it again converts nothing more.

The downgrade keeps the books: revision 0022 holds them unchanged, and they may have been edited since.
"""

import json
from datetime import UTC, datetime

from alembic import op
from sqlalchemy import text

from lyrveil.domain.identifiers import to_utc_text
from lyrveil.services.lore import LoreService
from lyrveil.services.lorebook import character_book_of, convert_book

revision = "0023"
down_revision = "0022"


def upgrade() -> None:
    connection = op.get_bind()
    now = to_utc_text(datetime.now(UTC))
    rows = connection.execute(
        text(
            "SELECT s.character_id, s.source_json, c.first_name, c.primary_language FROM character_sources AS s "
            "JOIN characters AS c ON c.id = s.character_id ORDER BY s.imported_at, s.id"
        )
    ).all()
    for character_id, source_json, first_name, language in rows:
        book = character_book_of(json.loads(source_json))
        if book is None:
            continue
        LoreService.store_own_book_in(connection, character_id, convert_book(book, language, first_name), now)


def downgrade() -> None:
    pass
