"""Read source tables through explicit field mappings."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
from cxr_research.utils.errors import ContractError


def table_rows(table):
    path = Path(table["path"])
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        if table["format"] == "csv":
            rows = csv.DictReader(stream)
        elif table["format"] == "jsonl":
            rows = (json.loads(line) for line in stream)
        elif table["format"] == "json":
            document = json.load(stream)
            rows = document[table["records_key"]] if table.get("records_key") else document
            if not isinstance(rows, list):
                raise ContractError("JSON source must expose an explicit array of records")
        else:
            raise ContractError("unsupported table format")
        for row in rows:
            try:
                yield {key: row[column] for key, column in table["columns"].items()}
            except (KeyError, TypeError):
                raise ContractError("source column mapping does not match the table") from None
