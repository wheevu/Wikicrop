"""Test-only Draft 2020-12 oracle for reviewed projection JSON documents."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


SCHEMA = Path(__file__).resolve().parents[2] / "schema" / "reviewed-projection.v1.schema.json"
MAX_DIAGNOSTICS = 20
MAX_DIAGNOSTIC_LENGTH = 500


def pointer(path: Any) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(parts) if parts else "/"


def main() -> int:
    try:
        import jsonschema
    except ImportError:
        print(
            "error: jsonschema is required; install tools/requirements-reviewed-projection.txt",
            file=sys.stderr,
        )
        return 2

    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        document = json.load(sys.stdin)
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(document),
            key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
        )
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, jsonschema.SchemaError) as error:
        print(f"error: schema oracle configuration/input error: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"error: schema oracle failed: {error}", file=sys.stderr)
        return 2

    if errors:
        for error in errors[:MAX_DIAGNOSTICS]:
            diagnostic = f"{pointer(error.absolute_path)}: {error.message}"
            if len(diagnostic) > MAX_DIAGNOSTIC_LENGTH:
                diagnostic = diagnostic[: MAX_DIAGNOSTIC_LENGTH - 3] + "..."
            print(diagnostic, file=sys.stderr)
        if len(errors) > MAX_DIAGNOSTICS:
            print(f"... {len(errors) - MAX_DIAGNOSTICS} more validation errors", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
