"""Tests the model catalog file and its validator (T-MOD-01): the shipped file, docs/models.md and every refusal."""

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from lyrveil.catalog import CatalogError, custom_sampler_preset, load_catalog, parse_catalog

CATALOG_NAME = "model-catalog.json"
MODELS_PAGE = "models.md"


def find(folder: str, name: str) -> Path:
    """Returns the file in the repository's folder or, in the test image, in /app/<folder>."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / folder / name, here.parents[2] / folder / name):
        if candidate.is_file():
            return candidate
    raise AssertionError(f"{name} is in neither candidate {folder} folder")


@pytest.fixture(name="document")
def document_fixture() -> dict[str, Any]:
    """A fresh decoded copy of the shipped catalog, for a test to break one rule in."""
    decoded: dict[str, Any] = json.loads(find("config", CATALOG_NAME).read_text(encoding="utf-8"))
    return copy.deepcopy(decoded)


def refused(document: dict[str, Any], fragment: str) -> None:
    with pytest.raises(CatalogError) as raised:
        parse_catalog(document)
    assert fragment in str(raised.value)


def test_the_shipped_catalog_validates() -> None:
    catalog = load_catalog(find("config", CATALOG_NAME))
    assert [profile.id for profile in catalog.text] == [
        "gemma-4-26b-a4b",
        "gemma-4-12b",
        "orion-26b-a4b",
        "rocinante-x-12b",
        "rocinante-12b-v1.1",
    ]
    assert [profile.id for profile in catalog.image] == ["illustrious-xl-v2.0", "illustrious-xl-v1.0", "noobai-xl-1.1"]


def test_tier_defaults_follow_adr_0027() -> None:
    catalog = load_catalog(find("config", CATALOG_NAME))
    assert catalog.default_text("target").id == "gemma-4-26b-a4b"
    assert catalog.default_text("reduced").id == "gemma-4-12b"
    assert catalog.default_image("target").id == "illustrious-xl-v2.0"
    assert catalog.default_image("reduced").id == "illustrious-xl-v2.0"


def documented_rows(section_title: str) -> dict[str, dict[str, str]]:
    """Returns the rows of the first table under `## <section_title>` of docs/models.md by profile identifier.

    Each value maps `file`, `repo`, `revision`, `bytes` and `sha256` to the text of the row.
    """
    page = find("docs", MODELS_PAGE).read_text(encoding="utf-8")
    section = page.split(f"## {section_title}\n", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, dict[str, str]] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        match = re.match(r"^`([a-z0-9.-]+)`$", cells[0]) if cells else None
        source = re.match(r"^`([^`]+)` at `([0-9a-f]{40})`$", cells[2]) if len(cells) > 5 else None
        if match and source and re.match(r"^`[^`]+`$", cells[1]):
            rows[match[1]] = {
                "file": cells[1].strip("`"),
                "repo": source[1],
                "revision": source[2],
                "bytes": cells[3].replace(",", ""),
                "sha256": cells[4].strip("`"),
            }
    return rows


@pytest.mark.parametrize(("title", "kind"), [("Text profiles", "text"), ("Image profiles", "image")])
def test_every_pin_equals_the_documented_table(title: str, kind: str) -> None:
    catalog = load_catalog(find("config", CATALOG_NAME))
    documented = documented_rows(title)
    profiles = {profile.id: profile for profile in getattr(catalog, kind)}
    assert documented.keys() == profiles.keys()
    for name, expected in documented.items():
        profile = profiles[name]
        assert profile.file == expected["file"], name
        assert profile.url == (
            f"https://huggingface.co/{expected['repo']}/resolve/{expected['revision']}/{expected['file']}"
        ), name
        assert str(profile.bytes) == expected["bytes"], name
        assert profile.sha256 == expected["sha256"], name


def test_documented_tiers_and_templates_match_the_catalog() -> None:
    page = find("docs", MODELS_PAGE).read_text(encoding="utf-8")
    catalog = load_catalog(find("config", CATALOG_NAME))
    for profile in catalog.text:
        row = next(line for line in page.splitlines() if line.startswith(f"| `{profile.id}` | `{profile.file}`"))
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert cells[6] == f"`{profile.template}`", profile.id
        assert cells[7].lower() == profile.min_tier, profile.id


def test_the_shipped_levers_are_off_and_no_profile_has_evidence_yet() -> None:
    catalog = load_catalog(find("config", CATALOG_NAME))
    for profile in catalog.text:
        assert (profile.swa_padding, profile.smart_cache, profile.quality_evidence) == (0, 0, None), profile.id


@pytest.mark.parametrize("identifier", ["Gemma", "a", "-start", "has space", "x" * 49, "under_score", ""])
def test_invalid_identifiers_are_refused(document: dict[str, Any], identifier: str) -> None:
    document["text"][0]["id"] = identifier
    refused(document, "text.0.id")


def test_repeated_identifiers_are_refused(document: dict[str, Any]) -> None:
    document["image"][1]["id"] = document["text"][0]["id"]
    refused(document, "identifiers must be unique")


@pytest.mark.parametrize(
    "revision", ["main", "refs/pr/1", "v1.0", "d1c082be", "D1C082BE9CF3C8A514ACF63B8761F4B41935842E"]
)
def test_moving_revisions_are_refused(document: dict[str, Any], revision: str) -> None:
    profile = document["text"][0]
    profile["url"] = f"https://huggingface.co/google/gemma-4-26B-A4B-it-qat/resolve/{revision}/{profile['file']}"
    refused(document, "text.0")


@pytest.mark.parametrize(
    "url",
    [
        "http://huggingface.co/o/r/resolve/{rev}/{file}",
        "https://example.com/o/r/resolve/{rev}/{file}",
        "https://huggingface.co/o/r/blob/{rev}/{file}",
        "https://huggingface.co/../r/resolve/{rev}/{file}",
        "https://huggingface.co/o/r/resolve/{rev}/other.gguf",
    ],
)
def test_urls_off_the_pinned_pattern_are_refused(document: dict[str, Any], url: str) -> None:
    profile = document["text"][0]
    profile["url"] = url.format(rev="d1c082be9cf3c8a514acf63b8761f4b41935842e", file=profile["file"])
    refused(document, "text.0")


@pytest.mark.parametrize("name", ["model.bin", "../model.gguf", "model.safetensors", "a b.gguf", ".gguf"])
def test_invalid_file_names_are_refused(document: dict[str, Any], name: str) -> None:
    document["text"][0]["file"] = name
    refused(document, "text.0")


def test_a_checkpoint_needs_the_safetensors_extension(document: dict[str, Any]) -> None:
    image = document["image"][0]
    image["file"] = "Illustrious-XL-v2.0.gguf"
    image["url"] = image["url"].replace(".safetensors", ".gguf")
    refused(document, "image.0")


@pytest.mark.parametrize(
    ("field", "value"),
    [("bytes", 0), ("bytes", "1"), ("bytes", True), ("sha256", "A" * 64), ("sha256", "ab")],
)
def test_invalid_size_and_hash_are_refused(document: dict[str, Any], field: str, value: object) -> None:
    document["text"][0][field] = value
    refused(document, f"text.0.{field}")


@pytest.mark.parametrize(
    ("field", "value"),
    [("template", "alpaca"), ("sampler", "creative"), ("kv_quant", "q4_0"), ("context_size", 1000)],
)
def test_unknown_templates_and_presets_are_refused(document: dict[str, Any], field: str, value: object) -> None:
    document["text"][0][field] = value
    refused(document, f"text.0.{field}")


def test_an_unknown_image_preset_is_refused(document: dict[str, Any]) -> None:
    document["image"][0]["defaults"] = "photoreal"
    refused(document, "image.0.defaults")


def test_only_epsilon_prediction_and_sdxl_are_accepted(document: dict[str, Any]) -> None:
    document["image"][0]["prediction"] = "v_prediction"
    refused(document, "image.0.prediction")


@pytest.mark.parametrize("value", [1, 512, 3072, "0", None, True])
def test_invalid_swa_padding_is_refused(document: dict[str, Any], value: object) -> None:
    document["text"][0]["swa_padding"] = value
    refused(document, "text.0.swa_padding")


@pytest.mark.parametrize("padding", [1024, 2048])
def test_swa_padding_is_allowed_for_gemma4_only(document: dict[str, Any], padding: int) -> None:
    document["text"][0]["swa_padding"] = padding
    assert parse_catalog(document).text[0].swa_padding == padding
    document["text"][3]["swa_padding"] = padding
    refused(document, "swa_padding above 0 is allowed only for a gemma4 profile")


@pytest.mark.parametrize("value", [1, 3, 5, "2", None])
def test_invalid_smart_cache_is_refused(document: dict[str, Any], value: object) -> None:
    document["text"][0]["smart_cache"] = value
    refused(document, "text.0.smart_cache")


def test_smart_cache_is_allowed_on_a_target_tier_profile_only(document: dict[str, Any]) -> None:
    document["text"][0]["smart_cache"] = 2
    assert parse_catalog(document).text[0].smart_cache == 2
    document["text"][1]["smart_cache"] = 2
    refused(document, "smart_cache 2 is allowed only for a target-tier profile")


def test_a_text_profile_without_a_minimum_tier_is_not_a_target_profile(document: dict[str, Any]) -> None:
    del document["text"][1]["min_tier"]
    document["text"][1]["smart_cache"] = 2
    refused(document, "smart_cache 2 is allowed only for a target-tier profile")


@pytest.mark.parametrize(
    "evidence",
    [
        "docs/verification/2026-11-20-italian-quality.md",
        "docs/verification/gemma/summary.md",
    ],
)
def test_quality_evidence_under_docs_verification_is_accepted(document: dict[str, Any], evidence: str) -> None:
    document["text"][0]["quality_evidence"] = evidence
    assert parse_catalog(document).text[0].quality_evidence == evidence


@pytest.mark.parametrize(
    "evidence",
    [
        "",
        "docs/verification/",
        "docs/verification",
        "docs/verification/../secrets.md",
        "docs/verification/a/../../../etc/passwd.md",
        "docs/other/result.md",
        "/docs/verification/result.md",
        "docs\\verification\\result.md",
        "docs/verification/result.txt",
        "verification/result.md",
        "https://example.com/docs/verification/result.md",
        5,
    ],
)
def test_quality_evidence_elsewhere_is_refused(document: dict[str, Any], evidence: object) -> None:
    document["text"][0]["quality_evidence"] = evidence
    refused(document, "text.0.quality_evidence")


def test_a_missing_quality_evidence_field_is_refused(document: dict[str, Any]) -> None:
    del document["text"][0]["quality_evidence"]
    refused(document, "text.0.quality_evidence")


def test_two_defaults_for_one_tier_are_refused(document: dict[str, Any]) -> None:
    document["text"][2]["default_tiers"] = ["target"]
    refused(document, "the target tier needs exactly one default text profile, found 2")


def test_two_default_images_for_one_tier_are_refused(document: dict[str, Any]) -> None:
    document["image"][2]["default_tiers"] = ["reduced"]
    refused(document, "the reduced tier needs exactly one default image profile, found 2")


def test_a_tier_without_a_default_is_refused(document: dict[str, Any]) -> None:
    document["text"][1]["default_tiers"] = []
    refused(document, "the reduced tier needs exactly one default text profile, found 0")


def test_a_default_above_its_minimum_tier_is_refused(document: dict[str, Any]) -> None:
    document["text"][0]["default_tiers"] = ["reduced", "target"]
    document["text"][1]["default_tiers"] = []
    refused(document, "gemma-4-26b-a4b is the reduced default but needs the target tier")


def test_default_tiers_must_be_known_and_distinct(document: dict[str, Any]) -> None:
    document["image"][0]["default_tiers"] = ["reduced", "reduced", "target"]
    refused(document, "image.0.default_tiers")
    document["image"][0]["default_tiers"] = ["reduced", "target", "huge"]
    refused(document, "image.0.default_tiers")


def test_preferred_vram_below_the_requirement_is_refused(document: dict[str, Any]) -> None:
    document["text"][0]["vram_preferred_mib"] = 6000
    refused(document, "vram_preferred_mib must not be below vram_required_mib")


def test_unknown_and_missing_fields_are_refused(document: dict[str, Any]) -> None:
    document["text"][0]["temperature"] = 5
    refused(document, "text.0.temperature")
    del document["text"][0]["temperature"]
    del document["text"][0]["batch_size"]
    refused(document, "text.0.batch_size")


def test_the_schema_version_and_top_level_shape_are_checked(document: dict[str, Any]) -> None:
    document["schema_version"] = 2
    refused(document, "schema_version")
    with pytest.raises(CatalogError):
        parse_catalog([])


def test_every_problem_is_listed_at_once(document: dict[str, Any]) -> None:
    document["text"][0]["swa_padding"] = 7
    document["text"][1]["smart_cache"] = 9
    with pytest.raises(CatalogError) as raised:
        parse_catalog(document)
    assert "text.0.swa_padding" in str(raised.value)
    assert "text.1.smart_cache" in str(raised.value)


def test_a_missing_or_malformed_file_raises_catalog_error(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="could not be read"):
        load_catalog(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(CatalogError, match="not valid JSON"):
        load_catalog(broken)


@pytest.mark.parametrize("template", ["gemma4", "mistral_v3_tekken", "chatml"])
def test_custom_profiles_get_the_generic_preset(template: str) -> None:
    assert custom_sampler_preset(template) == "generic"  # type: ignore[arg-type]


def test_the_generic_preset_is_a_valid_catalog_value(document: dict[str, Any]) -> None:
    document["text"][3]["sampler"] = "generic"
    assert parse_catalog(document).text[3].sampler == "generic"
