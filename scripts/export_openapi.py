"""Vuelca el esquema OpenAPI de `app.main:app` a `contracts/openapi.json`.

No hay nada que construir a mano aquí: FastAPI ya arma el esquema completo
para servir `/openapi.json` en local (ver `create_app` en `app/main.py`) —
este script solo lo escribe a un archivo versionado en git, para que
`theclub-web` tenga un contrato estable que leer sin tener que levantar la
API, y para que el CI pueda detectar cuándo diverge (`make openapi-check`).
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "contracts" / "openapi.json"

# Se ejecuta como script suelto (`uv run python scripts/export_openapi.py`),
# no como módulo de un paquete instalado -- sin esto, `import app` falla
# porque solo el directorio de este archivo entra en `sys.path` por defecto.
sys.path.insert(0, str(REPO_ROOT))

from app.main import app  # noqa: E402


def main() -> None:
    schema = app.openapi()
    OUTPUT_PATH.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
