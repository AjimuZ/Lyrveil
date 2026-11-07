"""SQL of the character card: the `characters` table, its traits and its visual profile.

Every function works on a connection the caller owns and never commits. Versions arrive with P03.1b.
"""

import json
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from lyrveil.domain.card import CharacterCard
from lyrveil.domain.characters import CharacterSummary, StoredCharacter
from lyrveil.domain.identifiers import new_id

_COLUMNS = "id, first_name, last_name, age, grammatical_gender, primary_language, status, created_at, updated_at"


def _summary(row: RowMapping) -> CharacterSummary:
    return CharacterSummary(
        id=row["id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        age=row["age"],
        grammatical_gender=row["grammatical_gender"],
        primary_language=row["primary_language"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def insert(connection: Connection, *, character_id: str, card: CharacterCard, now: str) -> None:
    """Inserts an active character at version 1 with its whole card: the `characters` row, its traits in saved order
    and its visual profile. The age CHECK of the table is the second line of defence behind the card's own bound."""
    identity, visual = card.identity, card.visual
    connection.execute(
        text(
            "INSERT INTO characters (id, first_name, last_name, age, grammatical_gender, narrative_identity, "
            "biography, background, setting, occupation, interests_json, primary_language, communication_json, "
            "status, created_at, updated_at) "
            "VALUES (:id, :first_name, :last_name, :age, :gender, :narrative_identity, :biography, :background, "
            ":setting, :occupation, :interests, :language, :communication, 'active', :now, :now)"
        ),
        {
            "id": character_id,
            "first_name": identity.first_name,
            "last_name": identity.last_name,
            "age": identity.age,
            "gender": identity.grammatical_gender,
            "narrative_identity": identity.narrative_identity,
            "biography": identity.biography,
            "background": identity.background,
            "setting": identity.setting,
            "occupation": identity.occupation,
            "interests": _json(identity.interests),
            "language": identity.primary_language,
            "communication": card.communication.model_dump_json(),
            "now": now,
        },
    )
    for order, trait in enumerate(card.traits):
        connection.execute(
            text(
                "INSERT INTO character_traits (id, character_id, category, description, sort_order, locked, "
                "created_at, updated_at) VALUES (:id, :character_id, :category, :description, :order, :locked, "
                ":now, :now)"
            ),
            {
                "id": new_id(),
                "character_id": character_id,
                "category": trait.category,
                "description": trait.description,
                "order": order,
                "locked": trait.locked,
                "now": now,
            },
        )
    connection.execute(
        text(
            "INSERT INTO character_visual_profiles (character_id, face, hair, eyes, body, clothing, accessories, "
            "distinctive_marks, art_style, subject_tag, identity_en_json, usual_outfit_en_json, outfits_json, "
            "persistent_negative_en, locked_fields_json, seed_policy, base_seed, updated_at) "
            "VALUES (:character_id, :face, :hair, :eyes, :body, :clothing, :accessories, :marks, :art_style, "
            ":subject_tag, :identity_en, :usual_outfit_en, :outfits, :negative, :locked_fields, :seed_policy, "
            ":base_seed, :now)"
        ),
        {
            "character_id": character_id,
            "face": visual.face,
            "hair": visual.hair,
            "eyes": visual.eyes,
            "body": visual.body,
            "clothing": visual.clothing,
            "accessories": visual.accessories,
            "marks": visual.distinctive_marks,
            "art_style": visual.art_style,
            "subject_tag": visual.subject_tag,
            "identity_en": visual.identity_en.model_dump_json(),
            "usual_outfit_en": _json(visual.usual_outfit_en),
            "outfits": _json([outfit.model_dump() for outfit in visual.outfits]),
            "negative": visual.persistent_negative_en,
            "locked_fields": _json(visual.locked_fields),
            "seed_policy": visual.seed_policy,
            "base_seed": visual.base_seed,
            "now": now,
        },
    )


def get_card(connection: Connection, character_id: str) -> StoredCharacter | None:
    """Returns the character with its whole card, or None when no row has that id. A character without a profile row
    reads with the default visual profile."""
    row = (
        connection.execute(
            text(
                "SELECT c.id, c.status, c.is_demo, c.current_version_no, c.created_at, c.updated_at, c.first_name, "
                "c.last_name, c.age, c.grammatical_gender, c.narrative_identity, c.biography, c.background, "
                "c.setting, c.occupation, c.interests_json, c.primary_language, c.communication_json, "
                "v.face, v.hair, v.eyes, v.body, v.clothing, v.accessories, v.distinctive_marks, v.art_style, "
                "v.subject_tag, v.identity_en_json, v.usual_outfit_en_json, v.outfits_json, "
                "v.persistent_negative_en, v.locked_fields_json, v.seed_policy, v.base_seed "
                "FROM characters AS c LEFT JOIN character_visual_profiles AS v ON v.character_id = c.id "
                "WHERE c.id = :id"
            ),
            {"id": character_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    traits = connection.execute(
        text(
            "SELECT category, description, locked FROM character_traits WHERE character_id = :id "
            "ORDER BY sort_order, id"
        ),
        {"id": character_id},
    ).mappings()
    visual: dict[str, Any] = {}
    if row["subject_tag"] is not None:
        visual = {
            **{
                name: row[name]
                for name in ("face", "hair", "eyes", "body", "clothing", "accessories", "distinctive_marks")
            },
            "art_style": row["art_style"],
            "subject_tag": row["subject_tag"],
            "identity_en": json.loads(row["identity_en_json"]),
            "usual_outfit_en": json.loads(row["usual_outfit_en_json"]),
            "outfits": json.loads(row["outfits_json"]),
            "persistent_negative_en": row["persistent_negative_en"],
            "locked_fields": json.loads(row["locked_fields_json"]),
            "seed_policy": row["seed_policy"],
            "base_seed": row["base_seed"],
        }
    card = CharacterCard.model_validate(
        {
            "identity": {
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "age": row["age"],
                "grammatical_gender": row["grammatical_gender"],
                "narrative_identity": row["narrative_identity"],
                "biography": row["biography"],
                "occupation": row["occupation"],
                "interests": json.loads(row["interests_json"]),
                "background": row["background"],
                "setting": row["setting"],
                "primary_language": row["primary_language"],
            },
            "traits": [
                {"category": trait["category"], "description": trait["description"], "locked": bool(trait["locked"])}
                for trait in traits
            ],
            "communication": json.loads(row["communication_json"]),
            "visual": visual,
        }
    )
    return StoredCharacter(
        id=row["id"],
        status=row["status"],
        is_demo=bool(row["is_demo"]),
        current_version_no=row["current_version_no"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        card=card,
    )


def get(connection: Connection, character_id: str) -> CharacterSummary | None:
    """Returns the character, or None when no row has that id."""
    row = (
        connection.execute(text(f"SELECT {_COLUMNS} FROM characters WHERE id = :id"), {"id": character_id})
        .mappings()
        .first()
    )
    return None if row is None else _summary(row)


def list_all(connection: Connection, status: str, like: str | None) -> list[CharacterSummary]:
    """Returns the characters in `status`, newest update first. `like` is a ready LIKE pattern matched against the
    full name, or None for no filter."""
    sql = f"SELECT {_COLUMNS} FROM characters WHERE status = :status"
    params: dict[str, Any] = {"status": status}
    if like is not None:
        sql += " AND lower(first_name || ' ' || COALESCE(last_name, '')) LIKE :like ESCAPE '\\'"
        params["like"] = like
    sql += " ORDER BY updated_at DESC, id DESC"
    return [_summary(row) for row in connection.execute(text(sql), params).mappings()]
