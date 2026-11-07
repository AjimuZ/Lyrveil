"""Tests the model download tool of docs/operations.md (Model downloads) with scripted HTTP answers and no network."""

import ast
import email.message
import hashlib
import io
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lyrveil.tools import models as models_tool
from lyrveil.tools.models import (
    EXIT_DECLINED,
    EXIT_INVALID_FILE,
    CatalogError,
    Context,
    DownloadError,
    MismatchError,
    Profile,
    default_profiles,
    download_profile,
    follow_https_redirects,
    load_catalog,
    load_records,
    run,
)

# Larger than two read chunks of 1 MiB, so a transfer needs several reads.
CONTENT = bytes(range(256)) * 9000
OTHER = b"checkpoint" * 1000


class FakeResponse:
    """An HTTP answer with the attributes the tool reads: status, headers, read() and the context manager."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self.headers = headers
        self._body = io.BytesIO(body)

    def read(self, size: int) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def serve(content: bytes) -> Callable[[urllib.request.Request], FakeResponse]:
    def fetch(_request: urllib.request.Request) -> FakeResponse:
        return FakeResponse(200, content, {"Content-Length": str(len(content))})

    return fetch


def no_network() -> Callable[[urllib.request.Request], Any]:
    raise AssertionError("no request was expected")


def no_sleep(_seconds: float) -> None:
    raise AssertionError("no retry was expected")


def make_profile(content: bytes = CONTENT, *, size: int | None = None, sha256: str | None = None) -> Profile:
    return Profile(
        id="test-model",
        kind="text",
        display_name="Test model",
        file="model.gguf",
        url="https://models.example/model.gguf",
        size=len(content) if size is None else size,
        sha256=hashlib.sha256(content).hexdigest() if sha256 is None else sha256,
        license="Apache-2.0",
        license_url="https://example.org/license",
        min_tier="reduced",
        default_tiers=("reduced", "target"),
    )


def redirect(url: str, location: str) -> urllib.error.HTTPError:
    headers = email.message.Message()
    headers["Location"] = location
    return urllib.error.HTTPError(url, 302, "Found", headers, io.BytesIO(b""))


def catalog_entry(profile_id: str, file_name: str, content: bytes, **fields: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": profile_id,
        "display_name": profile_id,
        "file": file_name,
        "url": f"https://models.example/{file_name}",
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "license": "Apache-2.0",
        "license_url": "https://example.org/license",
        "min_tier": "reduced",
        "default_tiers": ["reduced", "target"],
    }
    entry.update(fields)
    return entry


def write_catalog(path: Path, text: list[dict[str, Any]], image: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"schema_version": 1, "text": text, "image": image}), encoding="utf-8")
    return path


@pytest.fixture
def context(tmp_path: Path) -> Context:
    root = tmp_path / "models"
    root.mkdir()
    catalog = write_catalog(
        tmp_path / "catalog.json",
        [catalog_entry("text-model", "text.gguf", CONTENT)],
        [catalog_entry("image-model", "image.safetensors", OTHER)],
    )
    return Context(root=root, catalog_path=catalog, fetch_factory=no_network, sleep=no_sleep)


def test_an_interrupted_download_resumes_with_a_range_request(tmp_path: Path) -> None:
    half = len(CONTENT) // 2
    ranges: list[str | None] = []

    def fetch(request: urllib.request.Request) -> FakeResponse:
        ranges.append(request.get_header("Range"))
        if len(ranges) == 1:
            # The connection closes after half of the announced body.
            return FakeResponse(200, CONTENT[:half], {"Content-Length": str(len(CONTENT))})
        headers = {"Content-Length": str(len(CONTENT) - half), "Content-Range": f"bytes {half}-{len(CONTENT) - 1}/*"}
        return FakeResponse(206, CONTENT[half:], headers)

    delays: list[float] = []
    download_profile(make_profile(), tmp_path, fetch, sleep=delays.append)

    assert ranges == [None, f"bytes={half}-"]
    assert delays == [5.0]
    assert (tmp_path / "llm" / "model.gguf").read_bytes() == CONTENT
    assert not (tmp_path / "llm" / "model.gguf.part").exists()


def test_a_part_file_left_by_an_interruption_is_resumed(tmp_path: Path) -> None:
    part = tmp_path / "llm" / "model.gguf.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(CONTENT[:1000])

    def fetch(request: urllib.request.Request) -> FakeResponse:
        assert request.get_header("Range") == "bytes=1000-"
        headers = {"Content-Length": str(len(CONTENT) - 1000), "Content-Range": f"bytes 1000-{len(CONTENT) - 1}/*"}
        return FakeResponse(206, CONTENT[1000:], headers)

    download_profile(make_profile(), tmp_path, fetch, sleep=no_sleep)
    assert (tmp_path / "llm" / "model.gguf").read_bytes() == CONTENT


@pytest.mark.parametrize(
    "profile",
    [
        make_profile(sha256="0" * 64),
        make_profile(size=len(CONTENT) + 10),
        make_profile(size=len(CONTENT) - 10),
    ],
    ids=["hash", "larger-size", "smaller-size"],
)
def test_a_size_or_hash_mismatch_keeps_nothing(tmp_path: Path, profile: Profile) -> None:
    with pytest.raises(MismatchError) as caught:
        download_profile(profile, tmp_path, serve(CONTENT), sleep=no_sleep)
    assert caught.value.status == EXIT_INVALID_FILE
    assert list((tmp_path / "llm").iterdir()) == []


def test_exhausted_retries_fail_and_delete_the_part_file(tmp_path: Path) -> None:
    calls: list[int] = []

    def fetch(_request: urllib.request.Request) -> FakeResponse:
        calls.append(1)
        if len(calls) == 1:
            return FakeResponse(200, CONTENT[:10], {"Content-Length": str(len(CONTENT))})
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    delays: list[float] = []
    with pytest.raises(DownloadError, match="Gave up after 5 retries"):
        download_profile(make_profile(), tmp_path, fetch, sleep=delays.append)
    assert len(calls) == 6
    assert delays == [5.0] * 5
    assert list((tmp_path / "llm").iterdir()) == []


def test_redirects_are_followed_only_to_https() -> None:
    opened: list[str] = []

    def open_once(request: urllib.request.Request) -> FakeResponse:
        opened.append(request.full_url)
        if request.full_url == "https://a.example/file":
            raise redirect(request.full_url, "https://b.example/file")
        raise redirect(request.full_url, "http://c.example/file")

    with pytest.raises(DownloadError, match="only HTTPS"):
        follow_https_redirects(open_once, urllib.request.Request("https://a.example/file"))
    assert opened == ["https://a.example/file", "https://b.example/file"]


def test_a_redirect_keeps_the_range_header() -> None:
    seen: list[tuple[str, str | None]] = []
    answer = FakeResponse(206, b"", {})

    def open_once(request: urllib.request.Request) -> FakeResponse:
        seen.append((request.full_url, request.get_header("Range")))
        if len(seen) == 1:
            raise redirect(request.full_url, "/resolved/file")
        return answer

    request = urllib.request.Request("https://a.example/file", headers={"Range": "bytes=5-"})
    assert follow_https_redirects(open_once, request) is answer
    assert seen == [("https://a.example/file", "bytes=5-"), ("https://a.example/resolved/file", "bytes=5-")]


def test_a_plain_http_url_is_refused_before_any_request() -> None:
    def open_once(_request: urllib.request.Request) -> FakeResponse:
        raise AssertionError("no request was expected")

    with pytest.raises(DownloadError, match="only HTTPS"):
        follow_https_redirects(open_once, urllib.request.Request("http://a.example/file"))


def test_no_download_starts_without_license_acceptance(context: Context, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["download", "--tier", "target"], context) == EXIT_DECLINED
    assert "licenses" in capsys.readouterr().err
    assert list(context.root.iterdir()) == []


def test_the_defaults_are_downloaded_once_then_skipped(context: Context, capsys: pytest.CaptureFixture[str]) -> None:
    files = {"text.gguf": CONTENT, "image.safetensors": OTHER}

    def fetch(request: urllib.request.Request) -> FakeResponse:
        content = files[request.full_url.rsplit("/", 1)[1]]
        return FakeResponse(200, content, {"Content-Length": str(len(content))})

    downloading = Context(root=context.root, catalog_path=context.catalog_path, fetch_factory=lambda: fetch)
    assert run(["download", "--tier", "target", "--accept-licenses"], downloading) == 0
    assert (context.root / "llm" / "text.gguf").read_bytes() == CONTENT
    assert (context.root / "image" / "checkpoints" / "image.safetensors").read_bytes() == OTHER
    assert set(load_records(context.root)) == {"llm/text.gguf", "image/checkpoints/image.safetensors"}
    capsys.readouterr()

    # The second run opens no connection: both files are recorded in .verified.json with their size and time.
    assert run(["download", "--tier", "target", "--accept-licenses"], context) == 0
    assert capsys.readouterr().out.count("present and verified, skipped") == 2


def test_verify_reports_a_changed_file_and_forgets_it(context: Context, capsys: pytest.CaptureFixture[str]) -> None:
    text = context.root / "llm" / "text.gguf"
    text.parent.mkdir(parents=True)
    text.write_bytes(CONTENT)
    assert run(["verify"], context) == 0
    assert "llm/text.gguf" in load_records(context.root)

    text.write_bytes(bytes(reversed(CONTENT)))
    assert run(["verify"], context) == EXIT_INVALID_FILE
    assert "differs from the catalog" in capsys.readouterr().err
    assert load_records(context.root) == {}
    assert text.exists()


def test_plan_reports_states_and_defaults(context: Context, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["plan", "--tier", "reduced", "--model", "image-model"], context) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["defaults"] == ["text-model", "image-model"]
    assert [(item["id"], item["state"], item["file"]) for item in plan["profiles"]] == [
        ("image-model", "missing", "image/checkpoints/image.safetensors")
    ]


def test_remove_deletes_the_file_and_its_record(context: Context) -> None:
    text = context.root / "llm" / "text.gguf"
    text.parent.mkdir(parents=True)
    text.write_bytes(CONTENT)
    assert run(["verify"], context) == 0
    assert run(["remove", "text-model"], context) == 0
    assert not text.exists()
    assert load_records(context.root) == {}


def test_an_unknown_profile_or_option_exits_1(context: Context) -> None:
    assert run(["plan", "--tier", "target", "--model", "nope"], context) == 1
    assert run(["download", "--tier", "target", "--model", "text-model", "--all"], context) == 1


def test_the_tool_imports_only_the_standard_library() -> None:
    # The download container is the only one with a route out, so no third-party package may run in it.
    source = Path(models_tool.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "the tool must not import other lyrveil modules"
            imported.add((node.module or "").split(".")[0])
    assert imported - sys.stdlib_module_names == set()


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"url": "http://models.example/text.gguf"}, "https"),
        ({"sha256": "ABC"}, "sha256"),
        ({"file": "text.safetensors"}, "file"),
        ({"bytes": True}, "bytes"),
        ({"default_tiers": ["huge"]}, "default_tiers"),
    ],
)
def test_the_catalog_refuses_invalid_entries(tmp_path: Path, fields: dict[str, Any], message: str) -> None:
    entry = catalog_entry("text-model", "text.gguf", CONTENT, **fields)
    path = write_catalog(tmp_path / "catalog.json", [entry], [])
    with pytest.raises(CatalogError, match=message):
        load_catalog(path)


def test_each_tier_needs_exactly_one_default_per_kind(tmp_path: Path) -> None:
    path = write_catalog(
        tmp_path / "catalog.json",
        [
            catalog_entry("small", "small.gguf", CONTENT, default_tiers=["reduced"]),
            catalog_entry("large", "large.gguf", CONTENT, default_tiers=["target"], min_tier="target"),
        ],
        [catalog_entry("image-model", "image.safetensors", OTHER)],
    )
    profiles = load_catalog(path)
    assert [profile.id for profile in default_profiles(profiles, "target")] == ["large", "image-model"]
    assert [profile.id for profile in default_profiles(profiles, "reduced")] == ["small", "image-model"]
    with pytest.raises(CatalogError, match="0 default text profiles"):
        default_profiles(profiles[:1], "target")
