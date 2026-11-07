"""T-CHR-10: the assisted draft with `fake_kobold`, over real HTTP, through the job worker and the API.

The draft is queued by `POST /characters/drafts`, run by the real JobScheduler under an llm lease with
CharacterCreatorService and KoboldCppRuntime against tests/fakes/fake_kobold.py, and read back with `GET /jobs/{id}`.
Valid output completes the job; refused output is repaired once with the error in the prompt; a second refusal fails
the job with STRUCTURED_OUTPUT_INVALID. In no case does a character exist before the user saves one.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from fakes.fake_kobold import GENERATE, FakeKobold
from fakes.scripted_server import Step
from lyrveil.jobs.policy import JobKind, JobStatus, RequiredMode
from lyrveil.jobs.queue import Job, JobQueue
from lyrveil.jobs.scheduler import JobScheduler
from lyrveil.main import create_app
from lyrveil.orchestration.client import Lease, LeaseMode, LeaseOutcome
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.prompts.character_draft import EXAMPLE_DRAFT
from lyrveil.runtimes.koboldcpp import KoboldCppRuntime
from lyrveil.services.character_creator import DRAFT_GRAMMAR, CharacterCreatorService
from lyrveil.settings import Settings

API = "/api/v1"
DESCRIPTION = "Crea un personaggio di 26 anni, appassionato di fantasy, ironico e indipendente."
AGE_DETAIL = "L'età deve essere almeno 18."


class Leases:
    """Grants every lease and records each one, as an orchestrator in LLM_READY with no other job would."""

    def __init__(self) -> None:
        self.acquired: list[tuple[LeaseMode, str]] = []

    def acquire_lease(self, mode: LeaseMode, job_id: str) -> Lease:
        self.acquired.append((mode, job_id))
        return Lease(id=f"lease-{job_id}", mode=mode, job_id=job_id, ttl_s=30, expires_in_s=30.0)

    def renew_lease(self, lease: Lease) -> Lease:
        return lease

    def release_lease(self, lease: Lease, outcome: LeaseOutcome = "ok") -> bool:
        return True


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def migrated(engine: Engine) -> Engine:
    upgrade_to_head(engine)
    return engine


@pytest.fixture
def client(settings: Settings, migrated: Engine) -> TestClient:
    return TestClient(create_app(settings, migrated), base_url="http://localhost:4200")


@pytest.fixture
def fake() -> Iterator[FakeKobold]:
    with FakeKobold() as running:
        yield running


def draft(**identity: object) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(json.dumps(EXAMPLE_DRAFT))
    raw["identity"].update(identity)
    return raw


def answer(text: str) -> Step:
    """A generation that returns `text` as the model's output."""
    return Step(body={"results": [{"text": text}]})


def generations(fake: FakeKobold) -> list[dict[str, Any]]:
    return [request.body for request in fake.requests if request.route == GENERATE and isinstance(request.body, dict)]


async def run_draft(engine: Engine, fake: FakeKobold, leases: Leases | None = None) -> Job:
    """Runs the next job with the draft handler under a granted llm lease and returns it in its new state."""
    async with KoboldCppRuntime(fake.url) as runtime:
        handlers = {JobKind.CHARACTER_DRAFT: CharacterCreatorService(engine, runtime)}
        scheduler = JobScheduler(engine, JobQueue(), handlers, leases or Leases(), lambda: RequiredMode.LLM)
        finished = await scheduler.run_once()
    assert finished is not None
    return finished


def start(client: TestClient, **body: object) -> dict[str, Any]:
    response = client.post(f"{API}/characters/drafts", json={"description": DESCRIPTION, **body})
    assert response.status_code == 202, response.text
    job: dict[str, Any] = response.json()
    return job


@pytest.mark.anyio
async def test_valid_output_completes_the_job_with_a_card_to_review_and_saves_nothing(
    client: TestClient, migrated: Engine, fake: FakeKobold
) -> None:
    job = start(client)
    assert (job["kind"], job["status"], job["queue_position"]) == ("character_draft", "queued", 1)
    fake.script("POST /api/v1/generate", answer(json.dumps(draft(), ensure_ascii=False)))
    leases = Leases()

    finished = await run_draft(migrated, fake, leases)

    assert finished.status is JobStatus.COMPLETED
    assert leases.acquired == [("llm", job["id"])]
    [request] = generations(fake)
    assert request["grammar"] == DRAFT_GRAMMAR
    assert DESCRIPTION in request["prompt"]
    assert request["max_length"] == 1500
    detail = client.get(f"{API}/jobs/{job['id']}").json()
    assert (detail["status"], detail["queue_position"], detail["error_code"]) == ("completed", 0, None)
    result = detail["result"]
    assert result["attempts"] == 1
    assert result["character_id"] is None
    assert (result["card"]["identity"]["first_name"], result["card"]["identity"]["age"]) == ("Dario", 34)
    assert result["card"]["visual"]["locked_fields"] == []
    assert result["proposed_fields"][:2] == ["identity.first_name", "identity.age"]
    assert client.get(f"{API}/characters").json()["items"] == [], "a draft is never a saved character"

    saved = client.post(f"{API}/characters", json=result["card"])
    assert saved.status_code == 201, "the draft card is accepted as it is by the normal create route"


@pytest.mark.anyio
async def test_refused_output_is_repaired_once_with_the_error_in_the_prompt(
    client: TestClient, migrated: Engine, fake: FakeKobold
) -> None:
    job = start(client)
    minor = json.dumps(draft(age=16), ensure_ascii=False)
    fake.script("POST /api/v1/generate", answer(minor), answer(json.dumps(draft(age=26), ensure_ascii=False)))

    finished = await run_draft(migrated, fake)

    assert finished.status is JobStatus.COMPLETED
    first, repair = generations(fake)
    assert AGE_DETAIL not in first["prompt"]
    assert AGE_DETAIL in repair["prompt"]
    assert minor in repair["prompt"], "the repair shows the refused output"
    assert repair["grammar"] == DRAFT_GRAMMAR
    result = client.get(f"{API}/jobs/{job['id']}").json()["result"]
    assert (result["attempts"], result["card"]["identity"]["age"]) == (2, 26)


@pytest.mark.anyio
async def test_a_second_refusal_fails_the_job_with_structured_output_invalid_and_no_retry(
    client: TestClient, migrated: Engine, fake: FakeKobold
) -> None:
    job = start(client)
    fake.script("POST /api/v1/generate", answer("non è JSON"), answer(json.dumps(draft(age=15))))

    finished = await run_draft(migrated, fake)

    assert (finished.status, finished.error_code, finished.error_detail) == (
        JobStatus.FAILED,
        "STRUCTURED_OUTPUT_INVALID",
        AGE_DETAIL,
    )
    requests = generations(fake)
    assert len(requests) == 2, "the repair is the only second generation; the job is not run again"
    assert "non è un JSON valido" in requests[1]["prompt"]
    detail = client.get(f"{API}/jobs/{job['id']}").json()
    assert (detail["status"], detail["error_code"], detail["result"]) == ("failed", "STRUCTURED_OUTPUT_INVALID", None)
    assert client.get(f"{API}/characters").json()["items"] == []


@pytest.mark.anyio
async def test_a_draft_over_a_character_keeps_its_locked_trait_and_field_and_leaves_it_unchanged(
    client: TestClient, migrated: Engine, fake: FakeKobold
) -> None:
    card = {
        "identity": {"first_name": "Nerea", "age": 27},
        "traits": [{"category": "main_trait", "description": "Ironica.", "locked": True}],
        "visual": {"hair": "Capelli rossi corti.", "locked_fields": ["hair"]},
    }
    created = client.post(f"{API}/characters", json=card).json()
    job = start(client, character_id=created["id"])
    fake.script("POST /api/v1/generate", answer(json.dumps(draft(first_name="Nerea", age=28), ensure_ascii=False)))

    finished = await run_draft(migrated, fake)

    assert finished.status is JobStatus.COMPLETED
    assert "Capelli rossi corti." in generations(fake)[0]["prompt"], "the model sees the current card"
    result = client.get(f"{API}/jobs/{job['id']}").json()["result"]
    assert result["character_id"] == created["id"]
    traits = result["card"]["traits"]
    assert traits[0] == {"category": "main_trait", "description": "Ironica.", "locked": True}
    assert all(not trait["locked"] for trait in traits[1:])
    assert result["card"]["visual"]["hair"] == "Capelli rossi corti."
    assert result["card"]["visual"]["locked_fields"] == ["hair"]
    assert "visual.hair" not in result["proposed_fields"]
    stored = client.get(f"{API}/characters/{created['id']}").json()
    assert (stored["current_version_no"], stored["card"]["identity"]["age"]) == (1, 27)


def test_a_blank_or_long_description_and_an_unknown_character_queue_nothing(client: TestClient) -> None:
    for body, status, detail in (
        ({"description": "   "}, 422, "Scrivi una descrizione del personaggio."),
        ({"description": "x" * 2001}, 422, "La descrizione può avere al massimo 2000 caratteri."),
        ({"description": DESCRIPTION, "character_id": "missing"}, 404, "Il personaggio non esiste."),
    ):
        response = client.post(f"{API}/characters/drafts", json=body)
        assert (response.status_code, response.json()["detail"]) == (status, detail)
    assert client.get(f"{API}/jobs/missing").status_code == 404
