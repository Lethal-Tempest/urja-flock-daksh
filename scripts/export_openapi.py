"""Write the API's OpenAPI 3.x document to openapi.json (no portal access needed)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from urja_api.config import Settings  # noqa: E402
from urja_api.main import create_app  # noqa: E402

app = create_app(Settings(portal_email="unused", portal_password="unused"))
(ROOT / "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
print(f"wrote {ROOT / 'openapi.json'}")
