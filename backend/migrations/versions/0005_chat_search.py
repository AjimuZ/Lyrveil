"""Full-text search over message text and conversation titles.

Revision ID: 0005
Revises: 0004

`message_fts` and `conversation_fts` follow docs/data-model.md (Full-text search) with the `unicode61 remove_diacritics
2` tokenizer, so "perche" finds "perché". Triggers keep them in step with `messages.content` and `conversations.title`
in the same transaction as the write, so no service can forget to. The columns are still named `*_stem` for the day
the memory engine's stemmer runs before the write; until then they hold the text as written. Soft-deleted rows stay
indexed and the query filters them out, so a restore needs no re-index.
"""

from alembic import op

revision = "0005"
down_revision = "0004"

_TOKENIZER = 'tokenize = "unicode61 remove_diacritics 2"'

_STATEMENTS = (
    "CREATE VIRTUAL TABLE message_fts USING fts5(message_id UNINDEXED, conversation_id UNINDEXED, content_stem, "
    f"{_TOKENIZER})",
    f"CREATE VIRTUAL TABLE conversation_fts USING fts5(conversation_id UNINDEXED, title_stem, {_TOKENIZER})",
    "INSERT INTO message_fts (message_id, conversation_id, content_stem) SELECT id, conversation_id, content "
    "FROM messages",
    "INSERT INTO conversation_fts (conversation_id, title_stem) SELECT id, title FROM conversations",
    "CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN "
    "INSERT INTO message_fts (message_id, conversation_id, content_stem) "
    "VALUES (new.id, new.conversation_id, new.content); END",
    "CREATE TRIGGER messages_fts_update AFTER UPDATE OF content ON messages BEGIN "
    "DELETE FROM message_fts WHERE message_id = old.id; "
    "INSERT INTO message_fts (message_id, conversation_id, content_stem) "
    "VALUES (new.id, new.conversation_id, new.content); END",
    "CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN "
    "DELETE FROM message_fts WHERE message_id = old.id; END",
    "CREATE TRIGGER conversations_fts_insert AFTER INSERT ON conversations BEGIN "
    "INSERT INTO conversation_fts (conversation_id, title_stem) VALUES (new.id, new.title); END",
    "CREATE TRIGGER conversations_fts_update AFTER UPDATE OF title ON conversations BEGIN "
    "DELETE FROM conversation_fts WHERE conversation_id = old.id; "
    "INSERT INTO conversation_fts (conversation_id, title_stem) VALUES (new.id, new.title); END",
    "CREATE TRIGGER conversations_fts_delete AFTER DELETE ON conversations BEGIN "
    "DELETE FROM conversation_fts WHERE conversation_id = old.id; END",
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for trigger in (
        "conversations_fts_delete",
        "conversations_fts_update",
        "conversations_fts_insert",
        "messages_fts_delete",
        "messages_fts_update",
        "messages_fts_insert",
    ):
        op.execute(f"DROP TRIGGER {trigger}")
    op.execute("DROP TABLE conversation_fts")
    op.execute("DROP TABLE message_fts")
