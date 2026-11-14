"""Image storage behind a two-method interface: a local folder now, an S3 bucket later."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Protocol


class ImageStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...


class LocalImageStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        relative = PurePosixPath(key)
        # Keys come from our own database, but never let one escape the storage root.
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("invalid storage key")
        return self.root.joinpath(*relative.parts)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

# ponytail: add an S3ImageStore (boto3 get_object/put_object, private bucket) when deploying to AWS;
# the API only depends on put/get.
