"""Print the OpenAPI schema as JSON (consumed by the frontend type generator).

Usage: python -m app.cli.export_openapi > frontend/openapi.json
"""

import json
import sys

from app.main import app


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(app.openapi(), indent=2))


if __name__ == "__main__":
    main()
