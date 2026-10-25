"""Read original CSV and ZIP references without copying annotations."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile
from cxr_research.data.manifest import local_file

def bounded(root, relative):
    p = PurePosixPath(relative)
    if p.is_absolute() or not relative or ".." in p.parts or "\\" in relative or ":" in relative:
        raise ValueError("Unsafe reference path")
    return local_file(root, relative)


class BinaryLines:
    def __init__(self, stream):
        self.stream, self.offset, self.sha = stream, 0, hashlib.sha256()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.stream.readline()
        if not line:
            raise StopIteration
        self.offset += len(line)
        self.sha.update(line)
        return line.decode("utf-8-sig" if self.offset == len(line) else "utf-8")


def csv_record(header, values):
    # NIH's released metadata has an unnamed trailing header without a data column.
    columns = list(header)
    while columns and not columns[-1].strip():
        columns.pop()
    if len(values) > len(columns) and all(not v for v in values[len(columns):]):
        values = values[:len(columns)]
    if len(values) != len(columns):
        raise ValueError("Invalid CSV record width")
    return dict(zip(columns, values))


def read_csv_reference(root, reference):
    """Read a complete original CSV record, including quoted multiline fields."""
    csv.field_size_limit(100_000_000)
    path = bounded(Path(root), reference["path"])
    with path.open("rb") as stream:
        header = next(csv.reader(BinaryLines(stream)))
        stream.seek(reference["byte_offset"])
        raw = stream.read(reference["byte_length"]).decode("utf-8")
    rows = list(csv.reader(io.StringIO(raw, newline=""), strict=True))
    if len(rows) != 1:
        raise ValueError("CSV reference does not resolve to one valid record")
    return csv_record(header, rows[0])


def read_scene_graph(root, reference, expected_image_id):
    with zipfile.ZipFile(bounded(Path(root), reference["archive"])) as archive:
        result = json.loads(archive.read(reference["entry"]))
    if str(result.get("image_id")) != expected_image_id:
        raise ValueError("Scene graph image identity mismatch")
    return result
