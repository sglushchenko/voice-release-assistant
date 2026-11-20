"""Writes the OpenAPI contract that client developers build against.

    uv run python -m release_assistant.api.openapi_export ../docs/openapi.json

The output is deterministic (sorted keys, fixed indentation) so a diff shows real API
changes only. A test fails when the committed file is out of date.
"""

import json
import sys
from pathlib import Path

from release_assistant.api.app import create_app
from release_assistant.config import Settings


def render() -> str:
    """The contract as text. Independent of the environment: no credentials are needed to
    describe the API."""
    app = create_app(Settings(_env_file=None))
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m release_assistant.api.openapi_export <output-file>")
    Path(sys.argv[1]).write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
