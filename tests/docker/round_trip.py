"""Seeds, changes and reads the content of T-DCK-12 inside the backend container of a test project.

tests/docker/Data.Tests.ps1 pipes this file to `python - <mode>` through `docker compose exec -T lyrveil-backend`:

    seed    creates a character with an avatar, a conversation and an application setting
    change  changes all of them: a new character, a second asset, a new title and a new setting value
    read    prints, as the last line, one JSON document of every character card, conversation, asset with the SHA-256
            of its files, and application setting; two reads of the same data print the same line

The API is called on the backend's own port with the Host and Origin of an installation on port 4200, which the test
project keeps; the setting is written to the database directly, because the settings routes are planned. Exits 1 with
the failing call on standard error. Standard library and Pillow only, as in the backend image; ASCII only, because
Windows PowerShell 5.1 pipes it as ASCII.
"""

import hashlib
import io
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from PIL import Image

ROOT = "http://127.0.0.1:8000"
API = f"{ROOT}/api/v1"
HEADERS = {"Host": "localhost:4200", "Origin": "http://localhost:4200"}
DATABASE = "/data/db/lyrveil.sqlite3"
SETTING_KEY = "t_dck_12.marker"


def call(method: str, url: str, body: bytes | None = None, content_type: str = "application/json") -> bytes:
    headers = dict(HEADERS)
    if body is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return bytes(response.read())
    except urllib.error.HTTPError as error:
        sys.exit(f"{method} {url} answered {error.code}: {error.read()[:500]!r}")


def call_json(method: str, path: str, document: dict[str, Any] | None = None) -> Any:
    body = None if document is None else json.dumps(document).encode("utf-8")
    answer = call(method, f"{API}{path}", body)
    return json.loads(answer) if answer else None


def upload(character_id: str, kind: str, color: tuple[int, int, int]) -> None:
    picture = io.BytesIO()
    Image.new("RGB", (96, 96), color).save(picture, format="PNG")
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="kind"\r\n\r\n{kind}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="t-dck-12.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("ascii")
    body += picture.getvalue() + f"\r\n--{boundary}--\r\n".encode("ascii")
    call("POST", f"{API}/characters/{character_id}/assets", body, f"multipart/form-data; boundary={boundary}")


def put_setting(value: dict[str, Any]) -> None:
    moment = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with closing(sqlite3.connect(DATABASE, timeout=30)) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO application_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
            (SETTING_KEY, json.dumps(value), moment),
        )
        connection.commit()


def seed() -> None:
    character = call_json("POST", "/characters", {"first_name": "Mira", "age": 31})
    upload(character["id"], "avatar", (200, 40, 40))
    call_json("POST", "/conversations", {"character_id": character["id"], "title": "Viaggio a Lisbona"})
    put_setting({"round_trip": "before the backup"})


def change() -> None:
    characters = call_json("GET", "/characters")["items"]
    mira = next(item for item in characters if item["first_name"] == "Mira")
    upload(mira["id"], "profile", (40, 40, 200))
    other = call_json("POST", "/characters", {"first_name": "Teo", "age": 40})
    call_json("POST", "/conversations", {"character_id": other["id"]})
    for item in call_json("GET", "/conversations")["items"]:
        if item["character_id"] == mira["id"]:
            call_json("PATCH", f"/conversations/{item['id']}", {"title": "Cambiato dopo il backup"})
    put_setting({"round_trip": "after the backup"})


def file_hash(url: str | None) -> str | None:
    if url is None:
        return None
    return hashlib.sha256(call("GET", url if url.startswith("http") else f"{ROOT}{url}")).hexdigest()


def read() -> None:
    characters = []
    assets = []
    for status in ("active", "archived"):
        for item in call_json("GET", f"/characters?status={status}")["items"]:
            characters.append(call_json("GET", f"/characters/{item['id']}"))
            for asset in call_json("GET", f"/characters/{item['id']}/assets")["items"]:
                asset["file_sha256"] = file_hash(asset.get("url"))
                asset["thumbnail_sha256"] = file_hash(asset.get("thumbnail_url"))
                assets.append(asset)
    conversations = []
    for status in ("active", "archived"):
        conversations.extend(call_json("GET", f"/conversations?status={status}")["items"])
    with closing(sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True, timeout=30)) as connection:
        settings = connection.execute(
            "SELECT key, value_json, updated_at FROM application_settings ORDER BY key"
        ).fetchall()
    setting = next((json.loads(value) for key, value, _ in settings if key == SETTING_KEY), None)
    state = {
        "characters": sorted(characters, key=lambda card: json.dumps(card, sort_keys=True)),
        "conversations": sorted(conversations, key=lambda row: row["id"]),
        "assets": sorted(assets, key=lambda asset: asset["id"]),
        "settings": [list(row) for row in settings],
        "setting": setting,
    }
    print(json.dumps(state, sort_keys=True, ensure_ascii=True))


if __name__ == "__main__":
    modes = {"seed": seed, "change": change, "read": read}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        sys.exit("usage: python - seed|change|read")
    modes[sys.argv[1]]()
