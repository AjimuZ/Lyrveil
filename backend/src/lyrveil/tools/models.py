"""Downloads, verifies, lists and removes the model files of the catalog in the models volume.

`scripts\\lyrveil.cmd models` runs this module in the one-shot `lyrveil-models` service (docs/operations.md, Model
downloads). That service is the only container with a route out and mounts only the models volume, so the module
imports the standard library only: none of the backend's dependencies runs next to an Internet connection.

Commands, each returning an exit status of docs/operations.md (Scripts):

    plan --tier TIER [--model ID ... | --all]      JSON of the chosen profiles, their licenses and local state
    download --tier TIER [--model ID ... | --all] --accept-licenses
    list --tier TIER                              the catalog with sizes, licenses and local state
    verify                                        recomputes the SHA-256 of every present file
    remove ID                                     deletes the file of one profile

Without --model or --all, the chosen profiles are the default text and image profiles of TIER. Statuses: 0 success,
1 any other failure, 2 not enough free space, 4 a file whose size or SHA-256 differs from the catalog, 5 licenses
not accepted. Human-readable lines go to standard output, failures to standard error; `plan` prints only its JSON to
standard output, so the caller can parse it.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from typing import Any, NoReturn

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NO_SPACE = 2
EXIT_INVALID_FILE = 4
EXIT_DECLINED = 5

DEFAULT_CATALOG = Path("/app/config/model-catalog.json")
DEFAULT_ROOT = Path("/models")
# Both variables exist for tests/docker/compose.models-test.yaml: a test catalog, and the CA of the local test server,
# which then replaces the system trust store. compose.yaml sets neither.
CATALOG_ENV = "LYRVEIL_MODEL_CATALOG"
CA_FILE_ENV = "LYRVEIL_MODELS_CA_FILE"

VERIFIED_FILE = ".verified.json"
PART_SUFFIX = ".part"
KIND_FOLDERS = {"text": "llm", "image": "image/checkpoints"}
KIND_EXTENSIONS = {"text": ".gguf", "image": ".safetensors"}
# Hardware tiers in increasing order of the memory they need (ADR 0027).
TIERS = ("reduced", "target")

RETRIES = 5
RETRY_DELAY_SECONDS = 5.0
TIMEOUT_SECONDS = 60.0
CHUNK_BYTES = 1 << 20
PROGRESS_STEP_PERCENT = 10
USER_AGENT = "lyrveil-models/1"
# Statuses after which the same request can succeed later; any other error status ends the download at once.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 10

STATE_MISSING = "missing"
STATE_VERIFIED = "verified"
STATE_UNVERIFIED = "unverified"
STATE_WRONG_SIZE = "wrong_size"
STATE_TEXT = {
    STATE_MISSING: "missing",
    STATE_VERIFIED: "present, verified",
    STATE_UNVERIFIED: "present, not verified",
    STATE_WRONG_SIZE: "present, wrong size",
}

_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{1,47}$")
_FILE = re.compile(r"^[A-Za-z0-9._-]{1,128}\.(gguf|safetensors)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")


class ToolError(Exception):
    """A failure reported with its message on standard error; `status` is the exit status it maps to."""

    status = EXIT_FAILURE


class CatalogError(ToolError):
    """The catalog is missing, unreadable or breaks a rule this tool depends on."""


class DownloadError(ToolError):
    """A download failed in a way that is not a mismatch: a refused redirect, an HTTP error, retries exhausted."""


class MismatchError(ToolError):
    """A file's size or SHA-256 differs from the catalog."""

    status = EXIT_INVALID_FILE


class _InterruptedError(Exception):
    """A transfer stopped in a way that a later request can resume: a dropped connection, a timeout, a 5xx status."""


@dataclass(frozen=True)
class Profile:
    """One catalog entry, reduced to what downloads need. `size` is the exact number of bytes of the file."""

    id: str
    kind: str
    display_name: str
    file: str
    url: str
    size: int
    sha256: str
    license: str
    license_url: str
    min_tier: str
    default_tiers: tuple[str, ...]

    @property
    def relative_path(self) -> str:
        """The file's path under the volume root: `llm/<file>` for text, `image/checkpoints/<file>` for images."""
        return f"{KIND_FOLDERS[self.kind]}/{self.file}"


@dataclass(frozen=True)
class Record:
    """What `/models/.verified.json` remembers of a file whose SHA-256 matched the catalog."""

    size: int
    mtime_ns: int
    sha256: str


Fetch = Callable[[urllib.request.Request], Any]


@dataclass(frozen=True)
class Context:
    """Where a command works: the volume root, the catalog, and how it opens URLs and waits between retries."""

    root: Path
    catalog_path: Path
    fetch_factory: Callable[[], Fetch]
    sleep: Callable[[float], None] = field(default=time.sleep)
    retry_delay: float = RETRY_DELAY_SECONDS


def _out(message: str) -> None:
    print(message, flush=True)


def _err(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _gb(size: int) -> str:
    return f"{size / 1e9:.2f} GB"


def _text_field(entry: dict[str, Any], where: str, name: str, default: str | None = None) -> str:
    value = entry.get(name, default)
    if not isinstance(value, str) or not value:
        raise CatalogError(f"Catalog entry {where}: {name} must be a non-empty string.")
    return value


def _parse_profile(kind: str, index: int, entry: object) -> Profile:
    """Returns the profile of one catalog entry. Raises CatalogError naming the entry and the field that is wrong.

    Fields this tool does not use are ignored; the backend's validator (lyrveil.catalog) checks the whole entry.
    """
    where = f"{kind}[{index}]"
    if not isinstance(entry, dict):
        raise CatalogError(f"Catalog entry {where} is not an object.")
    profile_id = _text_field(entry, where, "id")
    if not _ID.match(profile_id):
        raise CatalogError(f"Catalog entry {where}: id {profile_id!r} does not match {_ID.pattern}.")
    where = f"{kind} {profile_id}"
    file_name = _text_field(entry, where, "file")
    if not _FILE.match(file_name) or not file_name.endswith(KIND_EXTENSIONS[kind]):
        raise CatalogError(f"Catalog entry {where}: file {file_name!r} is not a valid {KIND_EXTENSIONS[kind]} name.")
    url = _text_field(entry, where, "url")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise CatalogError(f"Catalog entry {where}: url must be an https:// URL.")
    size = entry.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise CatalogError(f"Catalog entry {where}: bytes must be a positive integer.")
    digest = _text_field(entry, where, "sha256")
    if not _SHA256.match(digest):
        raise CatalogError(f"Catalog entry {where}: sha256 must be 64 lowercase hexadecimal characters.")
    min_tier = _text_field(entry, where, "min_tier", "reduced")
    if min_tier not in TIERS:
        raise CatalogError(f"Catalog entry {where}: min_tier must be one of {', '.join(TIERS)}.")
    default_tiers = entry.get("default_tiers", [])
    if (
        not isinstance(default_tiers, list)
        or any(tier not in TIERS for tier in default_tiers)
        or len(set(default_tiers)) != len(default_tiers)
    ):
        raise CatalogError(f"Catalog entry {where}: default_tiers must list distinct tiers among {', '.join(TIERS)}.")
    return Profile(
        id=profile_id,
        kind=kind,
        display_name=_text_field(entry, where, "display_name", profile_id),
        file=file_name,
        url=url,
        size=size,
        sha256=digest,
        license=_text_field(entry, where, "license"),
        license_url=_text_field(entry, where, "license_url"),
        min_tier=min_tier,
        default_tiers=tuple(default_tiers),
    )


def load_catalog(path: Path) -> list[Profile]:
    """Returns the text profiles, then the image profiles, of the catalog at path, in file order.

    Raises CatalogError when the file is missing or unreadable, is not JSON with `schema_version` 1, or holds an
    invalid entry or a repeated identifier.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CatalogError(
            f"The model catalog {path} is not in the image. Rebuild the backend image with .\\setup.cmd -Rebuild."
        ) from None
    except OSError as error:
        raise CatalogError(f"The model catalog {path} could not be read: {error}") from error
    try:
        document = json.loads(text)
    except ValueError as error:
        raise CatalogError(f"The model catalog {path} is not valid JSON: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise CatalogError(f"The model catalog {path} must be a JSON object with schema_version 1.")
    profiles: list[Profile] = []
    seen: set[str] = set()
    for kind in KIND_FOLDERS:
        entries = document.get(kind, [])
        if not isinstance(entries, list):
            raise CatalogError(f"The model catalog {path}: {kind} must be a list.")
        for index, entry in enumerate(entries):
            profile = _parse_profile(kind, index, entry)
            if profile.id in seen:
                raise CatalogError(f"The model catalog {path} lists the profile {profile.id} twice.")
            seen.add(profile.id)
            profiles.append(profile)
    return profiles


def default_profiles(profiles: Sequence[Profile], tier: str) -> list[Profile]:
    """Returns the default text profile, then the default image profile, of tier.

    Raises CatalogError unless exactly one profile of each kind lists tier in `default_tiers`.
    """
    chosen: list[Profile] = []
    for kind in KIND_FOLDERS:
        matches = [profile for profile in profiles if profile.kind == kind and tier in profile.default_tiers]
        if len(matches) != 1:
            raise CatalogError(
                f"The catalog has {len(matches)} default {kind} profiles for the {tier} tier; exactly one is required."
            )
        chosen.append(matches[0])
    return chosen


def select_profiles(profiles: Sequence[Profile], tier: str, model_ids: Sequence[str], every: bool) -> list[Profile]:
    """Returns the profiles a command works on: model_ids in the given order without repeats, else every profile
    allowed on tier when every is set, else the defaults of tier. Raises ToolError for an unknown identifier."""
    if model_ids:
        by_id = {profile.id: profile for profile in profiles}
        unknown = [model_id for model_id in model_ids if model_id not in by_id]
        if unknown:
            raise ToolError(
                f"Unknown profile {', '.join(unknown)}. scripts\\lyrveil.cmd models -List shows the catalog."
            )
        return [by_id[model_id] for model_id in dict.fromkeys(model_ids)]
    if every:
        return [profile for profile in profiles if TIERS.index(profile.min_tier) <= TIERS.index(tier)]
    return default_profiles(profiles, tier)


def load_records(root: Path) -> dict[str, Record]:
    """Returns the records of `.verified.json` under root, keyed by relative path.

    A missing, unreadable or damaged file, or a damaged entry, yields no record for it: that only costs a new hash,
    and never lets a file count as verified.
    """
    try:
        document = json.loads((root / VERIFIED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = document.get("files") if isinstance(document, dict) else None
    if not isinstance(files, dict):
        return {}
    records: dict[str, Record] = {}
    for name, value in files.items():
        if not isinstance(value, dict):
            continue
        size, mtime_ns, digest = value.get("bytes"), value.get("mtime_ns"), value.get("sha256")
        if isinstance(size, int) and isinstance(mtime_ns, int) and isinstance(digest, str):
            records[str(name)] = Record(size=size, mtime_ns=mtime_ns, sha256=digest)
    return records


def save_records(root: Path, records: dict[str, Record]) -> None:
    """Replaces `.verified.json` under root with records, atomically, so a crash leaves the old or the new file."""
    document = {
        "schema_version": 1,
        "files": {
            name: {"bytes": record.size, "mtime_ns": record.mtime_ns, "sha256": record.sha256}
            for name, record in sorted(records.items())
        },
    }
    temporary = root / f"{VERIFIED_FILE}.tmp"
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, root / VERIFIED_FILE)


def _remember(root: Path, profile: Profile, records: dict[str, Record]) -> None:
    # Called only after the file's SHA-256 matched the catalog.
    info = (root / profile.relative_path).stat()
    records[profile.relative_path] = Record(size=info.st_size, mtime_ns=info.st_mtime_ns, sha256=profile.sha256)


def file_state(root: Path, profile: Profile, records: dict[str, Record]) -> str:
    """Returns the state of profile's file under root without reading its content.

    STATE_VERIFIED only when a record holds the file's current size and modification time and the catalog's size and
    SHA-256; STATE_UNVERIFIED when the size is right but no such record exists; STATE_WRONG_SIZE or STATE_MISSING
    otherwise.
    """
    try:
        info = (root / profile.relative_path).stat()
    except FileNotFoundError:
        return STATE_MISSING
    if info.st_size != profile.size:
        return STATE_WRONG_SIZE
    expected = Record(size=profile.size, mtime_ns=info.st_mtime_ns, sha256=profile.sha256)
    return STATE_VERIFIED if records.get(profile.relative_path) == expected else STATE_UNVERIFIED


def sha256_of(path: Path) -> str:
    """Returns the SHA-256 of the file at path as lowercase hexadecimal."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def follow_https_redirects(open_once: Fetch, request: urllib.request.Request) -> Any:
    """Opens request with open_once and follows up to MAX_REDIRECTS redirects itself, each only to an HTTPS URL.

    open_once must not follow redirects: it raises urllib.error.HTTPError for a 3xx answer. Every request keeps the
    headers of the first one, Range included. Returns the first answer that is not a redirect. Raises DownloadError,
    before any connection to it, for a URL that is not HTTPS, and when the redirects do not end.
    """
    url = request.full_url
    headers = dict(request.header_items())
    for _ in range(MAX_REDIRECTS + 1):
        if urllib.parse.urlsplit(url).scheme.lower() != "https":
            raise DownloadError(f"Refused {url}: only HTTPS is allowed, also after a redirect.")
        try:
            return open_once(urllib.request.Request(url, headers=headers))
        except urllib.error.HTTPError as error:
            location = error.headers.get("Location") if error.code in REDIRECT_STATUSES else None
            if not location:
                raise
            error.close()
            url = urllib.parse.urljoin(url, str(location))
    raise DownloadError(f"More than {MAX_REDIRECTS} redirects for {request.full_url}.")


def build_fetch(ca_file: str | None) -> Fetch:
    """Returns a function that opens a request over HTTPS only, with certificate and host name verification.

    The opener has no handler for http, ftp, file or data URLs and none for redirects: follow_https_redirects follows
    them, only to HTTPS. ca_file, for tests, replaces the system trust store with one CA. Proxies come from the usual
    environment variables.
    """
    context = ssl.create_default_context(cafile=ca_file)
    opener = urllib.request.OpenerDirector()
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.ProxyHandler(),
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.UnknownHandler(),
    ]
    for handler in handlers:
        opener.add_handler(handler)

    def open_once(request: urllib.request.Request) -> Any:
        return opener.open(request, timeout=TIMEOUT_SECONDS)

    return lambda request: follow_https_redirects(open_once, request)


def _range_start(value: str | None) -> int | None:
    match = _CONTENT_RANGE.match(value or "")
    return int(match.group(1)) if match else None


def _part_size(part: Path) -> int:
    try:
        return part.stat().st_size
    except FileNotFoundError:
        return 0


class _Progress:
    """Prints a line each time a download crosses another PROGRESS_STEP_PERCENT of its size."""

    def __init__(self, profile: Profile, done: int) -> None:
        self._profile = profile
        self._next = (done * 100 // profile.size) // PROGRESS_STEP_PERCENT * PROGRESS_STEP_PERCENT
        self._next += PROGRESS_STEP_PERCENT

    def update(self, done: int) -> None:
        percent = done * 100 // self._profile.size
        if percent >= self._next:
            _out(f"  {self._profile.id}: {percent}% of {_gb(self._profile.size)}")
            self._next = percent // PROGRESS_STEP_PERCENT * PROGRESS_STEP_PERCENT + PROGRESS_STEP_PERCENT


def _open(profile: Profile, request: urllib.request.Request, fetch: Fetch, part: Path, offset: int) -> Any:
    # Opens request and sorts its failures into resumable (_InterruptedError) and final (DownloadError) ones.
    try:
        return fetch(request)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 416 and offset > 0:
            part.unlink(missing_ok=True)
            raise _InterruptedError(f"the server refused to resume at byte {offset}, so it starts again") from error
        if error.code in RETRY_STATUSES:
            raise _InterruptedError(f"HTTP {error.code}") from error
        raise DownloadError(f"{profile.id}: the server answered HTTP {error.code} for {profile.url}.") from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise DownloadError(f"{profile.id}: the TLS certificate could not be verified: {error.reason}.") from error
        raise _InterruptedError(str(error.reason)) from error
    except (HTTPException, OSError) as error:
        raise _InterruptedError(str(error) or type(error).__name__) from error


def _transfer_once(profile: Profile, part: Path, fetch: Fetch) -> None:
    """Appends the rest of profile's file to part with one request, resuming at part's size with a Range header.

    Returns when the server sent the whole body it announced. Raises _InterruptedError when a retry can resume,
    MismatchError when the server sends more than the catalog's size, and DownloadError when it answers in a way a
    retry cannot fix. A server that ignores the range and answers 200 restarts the file from its first byte.
    """
    offset = _part_size(part)
    if offset > profile.size:
        part.unlink()
        offset = 0
    if offset == profile.size:
        return
    request = urllib.request.Request(profile.url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    if offset > 0:
        request.add_header("Range", f"bytes={offset}-")
    response = _open(profile, request, fetch, part, offset)
    with response:
        status = int(response.status)
        if status == 206:
            start = _range_start(response.headers.get("Content-Range"))
            if start != offset:
                raise DownloadError(f"{profile.id}: the server resumed at byte {start}, not at byte {offset}.")
            mode = "ab"
        elif status == 200:
            offset, mode = 0, "wb"
        else:
            raise DownloadError(f"{profile.id}: unexpected HTTP status {status} for {profile.url}.")
        length = response.headers.get("Content-Length")
        announced = int(length) if length and length.isdigit() else None
        received = 0
        progress = _Progress(profile, offset)
        with part.open(mode) as sink:
            while True:
                try:
                    chunk = response.read(CHUNK_BYTES)
                except (HTTPException, OSError) as error:
                    raise _InterruptedError(str(error) or type(error).__name__) from error
                if not chunk:
                    break
                if offset + received + len(chunk) > profile.size:
                    raise MismatchError(f"{profile.id}: the server sent more than the catalog's {profile.size} bytes.")
                sink.write(chunk)
                received += len(chunk)
                progress.update(offset + received)
    # http.client returns an empty read when the connection closes early, so the announced length is checked here.
    if announced is not None and received < announced:
        raise _InterruptedError(f"the connection closed at byte {offset + received} of {profile.size}")


def download_profile(
    profile: Profile,
    root: Path,
    fetch: Fetch,
    *,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = RETRIES,
    retry_delay: float = RETRY_DELAY_SECONDS,
) -> None:
    """Downloads profile's file into `<file>.part` under root, checks it, and moves it to its final name.

    On return the file is at its final path with the catalog's size and SHA-256; an older file there is replaced in one
    rename. An interrupted transfer is resumed with a Range request up to `retries` times, `retry_delay` seconds apart.
    Raises MismatchError (status 4) when the size or SHA-256 differ, and DownloadError or OSError for other failures.
    Every failure deletes the `.part` file first, so nothing unverified is kept. Only a signal or KeyboardInterrupt
    leaves it, and the next run resumes from it.
    """
    final = root / profile.relative_path
    part = final.with_name(final.name + PART_SUFFIX)
    final.parent.mkdir(parents=True, exist_ok=True)
    try:
        attempt = 0
        while True:
            try:
                _transfer_once(profile, part, fetch)
                break
            except _InterruptedError as error:
                if attempt >= retries:
                    raise DownloadError(f"{profile.id}: {error}. Gave up after {retries} retries.") from error
                attempt += 1
                _out(
                    f"  {profile.id}: {error}. Retry {attempt} of {retries} in {retry_delay:g} s,"
                    f" resuming at byte {_part_size(part)}."
                )
                sleep(retry_delay)
        size = _part_size(part)
        if size != profile.size:
            raise MismatchError(f"{profile.id}: the file has {size} bytes, the catalog says {profile.size}.")
        _out(f"  {profile.id}: checking SHA-256")
        digest = sha256_of(part)
        if digest != profile.sha256:
            raise MismatchError(f"{profile.id}: SHA-256 {digest} differs from the catalog's {profile.sha256}.")
        os.replace(part, final)
    except Exception:
        part.unlink(missing_ok=True)
        raise


def _profile_json(profile: Profile, tier: str, state: str) -> dict[str, Any]:
    return {
        "id": profile.id,
        "kind": profile.kind,
        "display_name": profile.display_name,
        "file": profile.relative_path,
        "bytes": profile.size,
        "license": profile.license,
        "license_url": profile.license_url,
        "min_tier": profile.min_tier,
        "above_tier": TIERS.index(profile.min_tier) > TIERS.index(tier),
        "state": state,
    }


def command_plan(args: argparse.Namespace, context: Context) -> int:
    """Prints, as one JSON object, the tier, the identifiers of its default profiles and the chosen profiles with their
    licenses and states. Reads only, and opens no connection."""
    profiles = load_catalog(context.catalog_path)
    chosen = select_profiles(profiles, args.tier, args.model, args.all)
    records = load_records(context.root)
    states = [file_state(context.root, profile, records) for profile in chosen]
    document = {
        "tier": args.tier,
        "defaults": [profile.id for profile in default_profiles(profiles, args.tier)],
        "profiles": [_profile_json(profile, args.tier, state) for profile, state in zip(chosen, states, strict=True)],
    }
    print(json.dumps(document), flush=True)
    return EXIT_OK


def command_download(args: argparse.Namespace, context: Context) -> int:
    """Downloads the chosen profiles that are not verified yet, after the licenses were accepted.

    A present file of the right size is hashed first and kept when it matches. Stops before any download when the
    files to fetch need more space than the volume has free (status 2), and at the first file that fails (status 4
    for a mismatch). Each verified file is recorded in `.verified.json` at once, so a later run skips it.
    """
    if not args.accept_licenses:
        _err("No model was downloaded: the licenses of the chosen profiles were not accepted (docs/licensing.md).")
        return EXIT_DECLINED
    profiles = load_catalog(context.catalog_path)
    chosen = select_profiles(profiles, args.tier, args.model, args.all)
    root = context.root
    records = load_records(root)
    pending: list[Profile] = []
    for profile in chosen:
        state = file_state(root, profile, records)
        if state == STATE_VERIFIED:
            _out(f"  {profile.id}: present and verified, skipped")
            continue
        if state == STATE_UNVERIFIED:
            _out(f"  {profile.id}: present, checking SHA-256")
            if sha256_of(root / profile.relative_path) == profile.sha256:
                _remember(root, profile, records)
                save_records(root, records)
                _out(f"  {profile.id}: verified, skipped")
                continue
            _out(f"  {profile.id}: SHA-256 differs from the catalog, downloading it again")
        pending.append(profile)
    if not pending:
        _out("Every chosen model file is present and verified.")
        return EXIT_OK

    part_sizes = {
        profile.id: min(_part_size(root / f"{profile.relative_path}{PART_SUFFIX}"), profile.size) for profile in pending
    }
    required = sum(profile.size - part_sizes[profile.id] for profile in pending)
    free = shutil.disk_usage(root).free
    _out(f"Space required: {_gb(required)}. Free in the models volume: {_gb(free)}.")
    if required > free:
        _err(f"FAILURE  Not enough free space: {_gb(required - free)} more is needed. Nothing was downloaded.")
        return EXIT_NO_SPACE

    fetch = context.fetch_factory()
    for profile in pending:
        _out(f"Downloading {profile.id} ({_gb(profile.size)}) to {profile.relative_path}")
        download_profile(profile, root, fetch, sleep=context.sleep, retry_delay=context.retry_delay)
        _remember(root, profile, records)
        save_records(root, records)
        _out(f"  {profile.id}: downloaded and verified")
    _out("Downloads complete.")
    return EXIT_OK


def command_list(args: argparse.Namespace, context: Context) -> int:
    """Prints every catalog profile with its kind, size, local state and license, and marks the defaults of the tier
    and the profiles above it. Reads only."""
    profiles = load_catalog(context.catalog_path)
    records = load_records(context.root)
    _out(f"Model catalog {context.catalog_path}, {args.tier} tier:")
    for profile in profiles:
        notes = []
        if args.tier in profile.default_tiers:
            notes.append("default")
        if TIERS.index(profile.min_tier) > TIERS.index(args.tier):
            notes.append(f"needs the {profile.min_tier} tier")
        state = STATE_TEXT[file_state(context.root, profile, records)]
        note = f" ({', '.join(notes)})" if notes else ""
        _out(f"  {profile.id:<24} {profile.kind:<5} {_gb(profile.size):>9}  {state:<22} {profile.license}{note}")
    return EXIT_OK


def command_verify(_args: argparse.Namespace, context: Context) -> int:
    """Recomputes the SHA-256 of every present catalog file and rewrites `.verified.json` with the results.

    Returns 4 when any present file differs from the catalog; that file loses its record and is kept for the user to
    replace. Missing files are reported and lose their records, but are no failure.
    """
    profiles = load_catalog(context.catalog_path)
    root = context.root
    records = load_records(root)
    failures = 0
    present = 0
    for profile in profiles:
        path = root / profile.relative_path
        if not path.is_file():
            records.pop(profile.relative_path, None)
            continue
        present += 1
        _out(f"  {profile.id}: checking SHA-256 of {profile.relative_path}")
        if path.stat().st_size == profile.size and sha256_of(path) == profile.sha256:
            _remember(root, profile, records)
            _out(f"  {profile.id}: verified")
        else:
            records.pop(profile.relative_path, None)
            failures += 1
            _err(
                f"FAILURE  {profile.id}: {profile.relative_path} differs from the catalog. Download it again with"
                f" scripts\\lyrveil.cmd models -Model {profile.id}"
            )
    save_records(root, records)
    if present == 0:
        _out("No model file of the catalog is present.")
    return EXIT_INVALID_FILE if failures else EXIT_OK


def command_remove(args: argparse.Namespace, context: Context) -> int:
    """Deletes the file of one profile, and its `.part` file, and forgets its record. The caller decides whether the
    profile may be removed; this command does not ask. A profile with no file succeeds and changes nothing."""
    profiles = load_catalog(context.catalog_path)
    (profile,) = select_profiles(profiles, TIERS[-1], [args.profile], every=False)
    root = context.root
    final = root / profile.relative_path
    removed = False
    for path in (final, final.with_name(final.name + PART_SUFFIX)):
        if path.is_file():
            path.unlink()
            removed = True
    records = load_records(root)
    if records.pop(profile.relative_path, None) is not None:
        save_records(root, records)
    _out(f"{profile.id}: {profile.relative_path} deleted" if removed else f"{profile.id}: no file to delete")
    return EXIT_OK


class _Parser(argparse.ArgumentParser):
    # argparse exits with 2 on a usage error, which docs/operations.md reserves for a failed preflight.
    def error(self, message: str) -> NoReturn:
        raise ToolError(f"{self.prog}: {message}")


def _parser() -> _Parser:
    parser = _Parser(prog="lyrveil.tools.models", description="Model files of the catalog in the models volume.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("plan", command_plan), ("download", command_download)):
        command = commands.add_parser(name)
        command.add_argument("--tier", choices=TIERS, required=True)
        choice = command.add_mutually_exclusive_group()
        choice.add_argument("--model", action="append", default=[], metavar="ID")
        choice.add_argument("--all", action="store_true")
        if name == "download":
            command.add_argument("--accept-licenses", action="store_true")
        command.set_defaults(handler=handler)
    listing = commands.add_parser("list")
    listing.add_argument("--tier", choices=TIERS, required=True)
    listing.set_defaults(handler=command_list)
    commands.add_parser("verify").set_defaults(handler=command_verify)
    removal = commands.add_parser("remove")
    removal.add_argument("profile", metavar="ID")
    removal.set_defaults(handler=command_remove)
    return parser


def run(argv: Sequence[str], context: Context) -> int:
    """Runs the command in argv and returns its exit status. A ToolError is printed as a FAILURE line."""
    try:
        args = _parser().parse_args(list(argv))
        handler: Callable[[argparse.Namespace, Context], int] = args.handler
        return handler(args, context)
    except ToolError as error:
        _err(f"FAILURE  {error}")
        return error.status


def main() -> int:
    """Runs the command of the process arguments on `/models`, with the catalog of the image."""
    catalog = Path(os.environ.get(CATALOG_ENV) or DEFAULT_CATALOG)
    ca_file = os.environ.get(CA_FILE_ENV) or None
    context = Context(root=DEFAULT_ROOT, catalog_path=catalog, fetch_factory=lambda: build_fetch(ca_file))
    return run(sys.argv[1:], context)


if __name__ == "__main__":
    sys.exit(main())
