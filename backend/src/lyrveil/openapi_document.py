"""Renders the application's OpenAPI document, the contract from which the frontend generates its types."""

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from lyrveil.main import create_app
from lyrveil.settings import Settings

# The control token only has to be well formed: building the application reads no setting that shapes the document.
_PLACEHOLDER_TOKEN = SecretStr("0" * 64)


def build_document() -> dict[str, Any]:
    """Returns the OpenAPI document of the application as a dictionary, the same one `/api/v1/openapi.json` serves."""
    return create_app(Settings(control_token=_PLACEHOLDER_TOKEN)).openapi()


def render_document(document: dict[str, Any]) -> str:
    """Returns the document as JSON text with sorted keys, two-space indentation and one final newline.

    The text is stable for equal documents, so a regenerated file only differs from the committed one when the API
    changed.
    """
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    """Writes the rendered document to the path in `argv[1]` and returns the process exit status.

    Returns 2 with a usage line on stderr when the path is missing. The file is written with LF line ends on every
    platform, because it is committed and compared by the frontend's lint.
    """
    if len(argv) != 2:
        print("usage: python -m lyrveil.openapi_document PATH", file=sys.stderr)
        return 2
    Path(argv[1]).write_text(render_document(build_document()), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
