"""Dumps `app.main:app`'s OpenAPI schema to `contracts/openapi.json`.

Nothing to build by hand here: FastAPI already assembles the full schema
to serve `/openapi.json` locally (see `create_app` in `app/main.py`) —
this script just writes it to a file versioned in git, so `theclub-web`
has a stable contract to read without having to spin up the API, and so
CI can detect when it drifts (`make openapi-check`).
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "contracts" / "openapi.json"

# Run as a standalone script (`uv run python scripts/export_openapi.py`),
# not as a module of an installed package -- without this, `import app`
# fails because only this file's own directory ends up on `sys.path` by default.
sys.path.insert(0, str(REPO_ROOT))

from app.main import app  # noqa: E402


def main() -> None:
    schema = app.openapi()
    OUTPUT_PATH.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
