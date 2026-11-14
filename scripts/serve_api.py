"""Start the Thorax AI backend with local defaults: python scripts/serve_api.py

Overrides: THORAX_DB_PATH, THORAX_WEB_ORIGINS, THORAX_API_PORT (default 8000).
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    # Loopback only, one worker: logins are held in this process's memory.
    uvicorn.run("thorax_api.app:create_app", factory=True, host="127.0.0.1",
                port=int(os.environ.get("THORAX_API_PORT", "8000")), workers=1)
