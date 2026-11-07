"""Contract tests: Schemathesis generates requests from the backend's own OpenAPI document and validates the answers."""

from typing import Any

import pytest
import schemathesis
from hypothesis import HealthCheck, given, settings
from schemathesis.schemas import BaseSchema
from sqlalchemy import Engine

from lyrveil.main import API_PREFIX, create_app
from lyrveil.persistence.migrations import upgrade_to_head
from lyrveil.settings import Settings

# The Host and Origin of the local app: any other Host is refused with 403, so requests must name this one.
HOST = "localhost:4200"


@pytest.fixture
def contract_schema(settings: Settings, engine: Engine) -> BaseSchema:
    """Returns the schema read from the application's own OpenAPI route, with the app on a migrated database."""
    upgrade_to_head(engine)
    app = create_app(settings, engine, control_probe=lambda: True)
    # Schemathesis 4.33 forwards every unknown keyword of from_asgi to the HTTP request that loads the document, so
    # base_url is not accepted here; the Host header is what the application checks, and it goes in the headers.
    return schemathesis.openapi.from_asgi(f"{API_PREFIX}/openapi.json", app, headers={"Host": HOST})


def test_every_documented_route_answers_as_its_schema_says(contract_schema: BaseSchema) -> None:
    """Fails when a status code, a content type or a body differs from the document, or when a route answers 5xx."""
    # Each item is an Ok or an Err result whose .ok() raises for an invalid operation, so a broken document fails here.
    # The results are typed Any because the library's Ok and Err classes do not share the .ok() method for mypy.
    results: list[Any] = list(contract_schema.get_all_operations())
    operations = [result.ok() for result in results]
    assert operations, "the OpenAPI document lists no operation"
    for operation in operations:

        @given(case=operation.as_strategy())
        @settings(max_examples=25, deadline=None, database=None, suppress_health_check=list(HealthCheck))
        def check(case: Any) -> None:
            case.call_and_validate(headers={"Host": HOST})

        check()
