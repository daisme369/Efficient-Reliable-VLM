"""Atomic JSON artifact writes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    for attempt in range(7):
        try:
            os.replace(temporary, path)
            return
        except PermissionError as error:
            # Windows readers without FILE_SHARE_DELETE can briefly lease the
            # destination. Keep the old JSON intact and bound the total wait.
            if os.name != "nt" or error.winerror not in {5, 32, 33} or attempt == 6:
                raise
            time.sleep(0.01 * 2**attempt)
