"""T-CHR-11, T-SEC-04 and T-SEC-05: uploads, metadata removal, crops and the single primary avatar, the refusal of
hostile uploads, and traversal attempts on the media and asset routes, through the API on a real database."""

import io
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import Engine, text

from lyrveil.main import create_app
from lyrveil.media.images import MAX_UPLOAD_BYTES
from lyrveil.media.store import MediaStore
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

CHARACTERS = "/api/v1/characters"
# EXIF tag 0x010E, ImageDescription: text a camera or an editor may leave in a file.
EXIF_DESCRIPTION = 0x010E


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "media"


@pytest.fixture
def client(settings: Settings, engine: Engine, media_root: Path) -> TestClient:
    upgrade_to_head(engine)
    app = create_app(settings, engine, media_store=MediaStore(media_root))
    return TestClient(app, base_url="http://localhost:4200", raise_server_exceptions=False)


@pytest.fixture
def character_id(client: TestClient) -> str:
    response = client.post(CHARACTERS, json={"first_name": "Alba", "age": 30})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def png_with_metadata(width: int = 300, height: int = 200) -> bytes:
    image = Image.new("RGB", (width, height), (200, 40, 40))
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Persona reale")
    exif = Image.Exif()
    exif[EXIF_DESCRIPTION] = "Via Roma 1"
    buffer = io.BytesIO()
    image.save(buffer, "PNG", pnginfo=info, exif=exif.tobytes())
    return buffer.getvalue()


def jpeg_with_metadata() -> bytes:
    exif = Image.Exif()
    exif[EXIF_DESCRIPTION] = "Via Roma 1"
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 200)).save(buffer, "JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def png_header_only(width: int, height: int) -> bytes:
    """A valid PNG whose header declares `width` x `height` pixels and whose data is a few bytes: what a decompression
    bomb looks like before it is decoded."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")


def upload(
    client: TestClient, character_id: str, data: bytes, media_type: str = "image/png", kind: str = "avatar"
) -> Any:
    return client.post(
        f"{CHARACTERS}/{character_id}/assets", files={"file": ("foto.png", data, media_type)}, data={"kind": kind}
    )


def assert_refused(response: Any, status: int, code: str) -> None:
    assert (response.status_code, response.json()["code"]) == (status, code), response.text


def test_a_valid_upload_is_stored_without_metadata_with_a_thumbnail(client: TestClient, character_id: str) -> None:
    response = upload(client, character_id, png_with_metadata())
    assert response.status_code == 201, response.text
    asset = response.json()
    assert (asset["kind"], asset["source"], asset["media_type"]) == ("avatar", "upload", "image/png")
    assert (asset["width"], asset["height"], asset["is_primary"], asset["crop"]) == (300, 200, True, None)

    stored = client.get(asset["url"])
    assert stored.status_code == 200
    assert stored.headers["content-type"] == "image/png"
    assert len(stored.content) == asset["byte_size"]
    with Image.open(io.BytesIO(stored.content)) as image:
        assert image.size == (300, 200)
        assert "Author" not in image.info
        assert "exif" not in image.info
        assert dict(image.getexif()) == {}

    thumbnail = client.get(asset["thumbnail_url"])
    assert thumbnail.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        assert image.format == "WEBP"
        assert max(image.size) <= 512


def test_a_jpeg_loses_its_exif(client: TestClient, character_id: str) -> None:
    response = upload(client, character_id, jpeg_with_metadata(), "image/jpeg", kind="profile")
    assert response.status_code == 201, response.text
    assert response.json()["is_primary"] is False
    with Image.open(io.BytesIO(client.get(response.json()["url"]).content)) as image:
        assert image.format == "JPEG"
        assert dict(image.getexif()) == {}


@pytest.mark.parametrize(
    ("data", "media_type"),
    [
        (b"<html><script>alert(1)</script></html>", "image/png"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "image/png"),
        (b"\x89PNG\r\n\x1a\n" + b"not really a png", "image/png"),
        (b"\xff\xd8\xff" + b"\x00" * 64, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 64, "image/gif"),
    ],
)
def test_bytes_that_are_no_accepted_image_are_refused(
    client: TestClient, character_id: str, data: bytes, media_type: str
) -> None:
    assert_refused(upload(client, character_id, data, media_type), 415, "UNSUPPORTED_MEDIA")
    assert client.get(f"{CHARACTERS}/{character_id}/assets").json()["items"] == []


def test_a_disguised_upload_whose_declared_type_disagrees_is_refused(client: TestClient, character_id: str) -> None:
    assert_refused(upload(client, character_id, jpeg_with_metadata(), "image/png"), 415, "UNSUPPORTED_MEDIA")
    assert_refused(upload(client, character_id, png_with_metadata(), "image/webp"), 415, "UNSUPPORTED_MEDIA")


def test_a_file_over_15_mb_is_refused(client: TestClient, character_id: str) -> None:
    data = png_with_metadata() + b"\x00" * MAX_UPLOAD_BYTES
    assert_refused(upload(client, character_id, data), 413, "PAYLOAD_TOO_LARGE")


@pytest.mark.parametrize(("width", "height"), [(20000, 20000), (9000, 9000), (9000, 10)])
def test_a_decompression_bomb_or_an_oversized_image_is_refused_before_decoding(
    client: TestClient, character_id: str, media_root: Path, width: int, height: int
) -> None:
    assert_refused(upload(client, character_id, png_header_only(width, height)), 413, "PAYLOAD_TOO_LARGE")
    assert not (media_root / "assets").exists()


def test_other_kinds_and_unknown_characters_are_refused(client: TestClient, character_id: str) -> None:
    assert_refused(upload(client, character_id, png_with_metadata(), kind="scene"), 422, "VALIDATION_ERROR")
    assert_refused(upload(client, "missing", png_with_metadata()), 404, "NOT_FOUND")


def test_a_crop_is_a_new_512_asset_and_the_original_stays(client: TestClient, character_id: str) -> None:
    original = upload(client, character_id, png_with_metadata()).json()
    url = f"{CHARACTERS}/{character_id}/assets/{original['id']}/crop"
    response = client.post(url, json={"x": 50, "y": 0, "width": 200, "height": 200})
    assert response.status_code == 201, response.text
    cropped = response.json()
    assert (cropped["width"], cropped["height"], cropped["kind"], cropped["is_primary"]) == (512, 512, "avatar", False)
    assert cropped["crop"] == {"source_asset_id": original["id"], "x": 50, "y": 0, "width": 200, "height": 200}
    with Image.open(io.BytesIO(client.get(cropped["url"]).content)) as image:
        assert image.size == (512, 512)
    ids = [item["id"] for item in client.get(f"{CHARACTERS}/{character_id}/assets").json()["items"]]
    assert set(ids) == {original["id"], cropped["id"]}

    outside = client.post(url, json={"x": 200, "y": 0, "width": 200, "height": 200})
    assert_refused(outside, 422, "VALIDATION_ERROR")


def test_a_character_has_a_single_primary_avatar(client: TestClient, character_id: str, media_root: Path) -> None:
    first = upload(client, character_id, png_with_metadata()).json()
    second = upload(client, character_id, png_with_metadata(40, 40)).json()
    profile = upload(client, character_id, png_with_metadata(), kind="profile").json()
    assert (first["is_primary"], second["is_primary"]) == (True, False)

    response = client.post(f"{CHARACTERS}/{character_id}/assets/{second['id']}/primary")
    assert (response.status_code, response.json()["is_primary"]) == (200, True)
    avatars = client.get(f"{CHARACTERS}/{character_id}/assets", params={"kind": "avatar"}).json()["items"]
    assert {item["id"]: item["is_primary"] for item in avatars} == {first["id"]: False, second["id"]: True}

    not_avatar = client.post(f"{CHARACTERS}/{character_id}/assets/{profile['id']}/primary")
    assert_refused(not_avatar, 422, "VALIDATION_ERROR")
    assert_refused(client.delete(f"{CHARACTERS}/{character_id}/assets/{second['id']}"), 409, "CONFLICT")

    files = {path.name for path in (media_root / "assets" / character_id).iterdir()}
    assert f"{first['id']}.png" in files
    assert client.delete(f"{CHARACTERS}/{character_id}/assets/{first['id']}").status_code == 204
    left = {path.name for path in (media_root / "assets" / character_id).iterdir()}
    assert f"{first['id']}.png" not in left
    assert f"{first['id']}.thumb.webp" not in left
    assert client.get(first["url"]).status_code == 404
    assert_refused(client.delete(f"{CHARACTERS}/{character_id}/assets/{first['id']}"), 404, "NOT_FOUND")


def test_assets_of_another_character_are_not_found(client: TestClient, character_id: str) -> None:
    asset = upload(client, character_id, png_with_metadata()).json()
    other = client.post(CHARACTERS, json={"first_name": "Bruno", "age": 40}).json()["id"]
    assert_refused(client.post(f"{CHARACTERS}/{other}/assets/{asset['id']}/primary"), 404, "NOT_FOUND")
    assert_refused(client.delete(f"{CHARACTERS}/{other}/assets/{asset['id']}"), 404, "NOT_FOUND")


@pytest.mark.parametrize(
    "path",
    [
        "/media/assets/..%2F..%2F..%2Fetc%2Fpasswd",
        "/media/assets/%2e%2e%2fsecret.txt",
        "/media/assets/..%5C..%5Csecret.txt",
        "/media/assets/%2Fetc%2Fpasswd",
        "/media/assets/.staging",
        "/media/assets/..%2Fsecret.txt/thumbnail",
    ],
)
def test_traversal_attempts_on_the_media_routes_fail(client: TestClient, character_id: str, path: str) -> None:
    upload(client, character_id, png_with_metadata())
    response = client.get(path)
    assert response.status_code == 404
    assert b"root:" not in response.content


@pytest.mark.parametrize("asset_id", ["..%2F..%2Fsecret.txt", "%2e%2e", "..%5Csecret.txt"])
def test_traversal_attempts_on_the_asset_routes_fail(client: TestClient, character_id: str, asset_id: str) -> None:
    assert client.post(f"{CHARACTERS}/{character_id}/assets/{asset_id}/primary").status_code == 404
    assert client.delete(f"{CHARACTERS}/{character_id}/assets/{asset_id}").status_code == 404
    rectangle = {"x": 0, "y": 0, "width": 1, "height": 1}
    assert client.post(f"{CHARACTERS}/{character_id}/assets/{asset_id}/crop", json=rectangle).status_code == 404


def test_a_tampered_path_is_neither_served_nor_deleted(
    client: TestClient, character_id: str, engine: Engine, media_root: Path
) -> None:
    secret = media_root.parent / "secret.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("keep", encoding="utf-8")
    first = upload(client, character_id, png_with_metadata()).json()
    second = upload(client, character_id, png_with_metadata()).json()
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE character_assets SET file_path = '../secret.txt' WHERE id = :id"), {"id": second["id"]}
        )
    assert client.get(second["url"]).status_code == 404
    assert client.delete(f"{CHARACTERS}/{character_id}/assets/{second['id']}").status_code == 500
    assert secret.read_text(encoding="utf-8") == "keep"
    ids = {item["id"] for item in client.get(f"{CHARACTERS}/{character_id}/assets").json()["items"]}
    assert ids == {first["id"], second["id"]}


def test_a_purge_removes_the_asset_rows_and_files(client: TestClient, character_id: str, media_root: Path) -> None:
    upload(client, character_id, png_with_metadata())
    assert any((media_root / "assets" / character_id).iterdir())
    response = client.post(f"{CHARACTERS}/{character_id}/purge", json={"confirm_name": "Alba"})
    assert response.status_code == 204, response.text
    assert list((media_root / "assets" / character_id).iterdir()) == []
