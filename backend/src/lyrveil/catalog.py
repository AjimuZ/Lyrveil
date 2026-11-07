"""Validates config/model-catalog.json and resolves the default profile of each hardware tier."""

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SCHEMA_VERSION = 1
# Hardware tiers in increasing order of the memory they need (ADR 0027).
TIERS: tuple[Literal["reduced"], Literal["target"]] = ("reduced", "target")
Tier = Literal["reduced", "target"]
TextTemplate = Literal["gemma4", "mistral_v3_tekken", "chatml"]
# Sampler and image presets are defined in code, so a catalog cannot carry arbitrary parameters (docs/models.md).
SamplerPreset = Literal["gemma4", "nemo", "generic"]
ImagePreset = Literal["illustrious", "noobai"]
# The preset of every custom profile, whatever its template family (docs/prompting.md, Sampler presets).
CUSTOM_SAMPLER_PRESET: SamplerPreset = "generic"

ID_PATTERN = r"^[a-z0-9][a-z0-9.-]{1,47}$"
_FILE_STEM = r"[A-Za-z0-9._-]{1,128}"
_URL = re.compile(
    r"^https://huggingface\.co/(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+)"
    r"/resolve/(?P<revision>[0-9a-f]{40})/(?P<file>[A-Za-z0-9._-]+)$"
)
_EVIDENCE_ROOT = PurePosixPath("docs/verification")
_EVIDENCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_LICENSE_URL = re.compile(r"^https://[^\s/]+(/\S*)?$")
_DOT_NAMES = {".", ".."}


class CatalogError(Exception):
    """The catalog is missing, unreadable or breaks a rule. The message lists every problem found."""


class _Profile(BaseModel):
    """Fields shared by both kinds. Strict, frozen, and closed: an unknown field is an error."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    id: str = Field(pattern=ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=80)
    file: str
    url: str
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license: str = Field(min_length=1)
    license_url: str
    # Absent when the profile runs on every tier, as for every image profile.
    min_tier: Tier | None = None
    # The tiers on which this profile is the default; empty for a profile that is never one.
    default_tiers: list[Tier] = Field(default_factory=list)

    @field_validator("license_url")
    @classmethod
    def _license_url_is_https(cls, value: str) -> str:
        if not _LICENSE_URL.match(value):
            raise ValueError("must be an https:// URL")
        return value

    @field_validator("default_tiers")
    @classmethod
    def _tiers_are_distinct(cls, value: list[Tier]) -> list[Tier]:
        if len(set(value)) != len(value):
            raise ValueError("must list each tier once")
        return value

    @model_validator(mode="after")
    def _file_and_url_agree(self) -> Self:
        extension = self._file_extension()
        if not re.fullmatch(rf"{_FILE_STEM}\.{extension}", self.file):
            raise ValueError(f"file {self.file!r} must match {_FILE_STEM}.{extension}")
        match = _URL.match(self.url)
        if match is None:
            raise ValueError(
                "url must be https://huggingface.co/<owner>/<repo>/resolve/<40-hex revision>/<file>;"
                " a branch or tag name is refused because it can move"
            )
        if {match["owner"], match["repo"]} & _DOT_NAMES:
            raise ValueError("url has an invalid repository name")
        if match["file"] != self.file:
            raise ValueError(f"url names the file {match['file']!r}, not {self.file!r}")
        return self

    def _file_extension(self) -> str:
        raise NotImplementedError

    @property
    def runs_on(self) -> tuple[Tier, ...]:
        """The tiers that run this profile: its minimum tier and every tier above it."""
        return ("target",) if self.min_tier == "target" else ("reduced", "target")


class TextProfile(_Profile):
    """A GGUF model of the text runtime, with the launch values and presets that profile needs."""

    template: TextTemplate
    context_size: Literal[4096, 6144, 8192, 12288, 16384, 24576]
    moe: bool
    vram_required_mib: int = Field(gt=0)
    vram_preferred_mib: int = Field(gt=0)
    sampler: SamplerPreset
    use_mmap: bool
    kv_quant: Literal["f16", "q8_0"]
    batch_size: Literal[128, 256, 512, 1024, 2048]
    swa_padding: Literal[0, 1024, 2048]
    smart_cache: Literal[0, 2]
    # A path under docs/verification/ once a quality protocol has been run for the profile (ADR 0034).
    quality_evidence: str | None

    def _file_extension(self) -> str:
        return "gguf"

    @field_validator("quality_evidence")
    @classmethod
    def _evidence_is_under_verification(cls, value: str | None) -> str | None:
        if value is None:
            return value
        path = PurePosixPath(value)
        inside = path.parts[: len(_EVIDENCE_ROOT.parts)] == _EVIDENCE_ROOT.parts
        names = path.parts[len(_EVIDENCE_ROOT.parts) :]
        if (
            path.is_absolute()
            or "\\" in value
            or not inside
            or not names
            or not path.name.endswith(".md")
            or not all(_EVIDENCE_NAME.match(part) for part in names)
        ):
            raise ValueError("must be null or a Markdown path under docs/verification/")
        return value

    @model_validator(mode="after")
    def _levers_fit_the_profile(self) -> Self:
        if self.swa_padding > 0 and self.template != "gemma4":
            raise ValueError("swa_padding above 0 is allowed only for a gemma4 profile")
        if self.smart_cache > 0 and self.min_tier != "target":
            raise ValueError("smart_cache 2 is allowed only for a target-tier profile")
        if self.vram_preferred_mib < self.vram_required_mib:
            raise ValueError("vram_preferred_mib must not be below vram_required_mib")
        return self


class ImageProfile(_Profile):
    """An SDXL checkpoint of the image runtime."""

    architecture: Literal["sdxl"]
    prediction: Literal["epsilon"]
    defaults: ImagePreset

    def _file_extension(self) -> str:
        return "safetensors"


class Catalog(BaseModel):
    """The whole catalog: unique identifiers and exactly one default profile of each kind for each tier."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal[1]
    text: list[TextProfile]
    image: list[ImageProfile]

    @model_validator(mode="after")
    def _identifiers_and_defaults(self) -> Self:
        identifiers = [profile.id for profile in (*self.text, *self.image)]
        repeated = sorted({name for name in identifiers if identifiers.count(name) > 1})
        if repeated:
            raise ValueError(f"identifiers must be unique; repeated: {', '.join(repeated)}")
        for kind, profiles in (("text", self.text), ("image", self.image)):
            for tier in TIERS:
                defaults = [profile.id for profile in profiles if tier in profile.default_tiers]
                if len(defaults) != 1:
                    raise ValueError(
                        f"the {tier} tier needs exactly one default {kind} profile, found {len(defaults)}"
                        + (f": {', '.join(defaults)}" if defaults else "")
                    )
        for profile in (*self.text, *self.image):
            for tier in profile.default_tiers:
                if tier not in profile.runs_on:
                    raise ValueError(f"{profile.id} is the {tier} default but needs the {profile.min_tier} tier")
        return self

    def default_text(self, tier: Tier) -> TextProfile:
        """Returns the default text profile of tier. Exactly one exists, because validation requires it."""
        return next(profile for profile in self.text if tier in profile.default_tiers)

    def default_image(self, tier: Tier) -> ImageProfile:
        """Returns the default image profile of tier. Exactly one exists, because validation requires it."""
        return next(profile for profile in self.image if tier in profile.default_tiers)


def parse_catalog(document: Any) -> Catalog:
    """Returns the validated catalog of a decoded JSON document. Raises CatalogError listing every problem."""
    try:
        return Catalog.model_validate(document)
    except ValidationError as error:
        problems = []
        for item in error.errors(include_url=False, include_input=False):
            location = ".".join(str(part) for part in item["loc"])
            problems.append(f"{location}: {item['msg']}" if location else item["msg"])
        raise CatalogError("The model catalog is invalid:\n  " + "\n  ".join(problems)) from None


def load_catalog(path: Path) -> Catalog:
    """Reads and validates the catalog file at path. Raises CatalogError when it is missing, unreadable or invalid."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CatalogError(f"The model catalog {path} could not be read: {error}") from error
    try:
        document = json.loads(text)
    except ValueError as error:
        raise CatalogError(f"The model catalog {path} is not valid JSON: {error}") from error
    return parse_catalog(document)


def custom_sampler_preset(_template: TextTemplate) -> SamplerPreset:
    """Returns the sampler preset of a custom text profile: always `generic`, whatever the template family."""
    return CUSTOM_SAMPLER_PRESET
