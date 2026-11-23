import hashlib
import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps

from thorax.domain.annotation import PixelBox
from thorax.domain.errors import DomainError


class ImageStore:
    def __init__(self, root: Path, limits: dict):
        self.root, self.limits = root.resolve(), limits

    def resolve(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root) or Path(key).is_absolute():
            raise DomainError("ASSET_UNAVAILABLE", 503)
        return candidate

    def read(self, image) -> bytes:
        try:
            payload = self.resolve(image.file_path).read_bytes()
            if len(payload) > self.limits["image_bytes"] or hashlib.sha256(payload).hexdigest() != image.sha256:
                raise ValueError("Image integrity mismatch")
            decoded = self.decode(payload)
            if decoded.size != (image.width, image.height) or image.byte_size != len(payload) or image.media_type != Image.MIME[decoded.format]:
                raise ValueError("Image dimensions mismatch")
            return payload
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise DomainError("ASSET_UNAVAILABLE", 503) from error

    def decode(self, payload: bytes):
        if len(payload) > self.limits["image_bytes"]:
            raise ValueError("Image too large")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(payload))
            if image.format not in {"PNG", "JPEG"} or max(image.size) > self.limits["image_side"] or image.width * image.height > self.limits["image_pixels"]:
                raise ValueError("Unsupported image")
            image.load()
            return image

    def crop(self, image, geometry: list[float]) -> bytes:
        box = PixelBox(*geometry)
        box.validate(image.width, image.height)
        source = self.decode(self.read(image))
        output = io.BytesIO()
        source.crop(box.crop_bounds()).save(output, format="PNG")
        return output.getvalue()

    def prepare(self, input_path: Path, version: str) -> dict:
        payload = input_path.read_bytes()
        image = ImageOps.exif_transpose(self.decode(payload)).convert("RGB")
        image.info.clear()
        output = io.BytesIO()
        image.save(output, format="PNG")
        canonical = output.getvalue()
        if len(canonical) > self.limits["image_bytes"]:
            raise ValueError("Canonical image too large")
        sha = hashlib.sha256(canonical).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.resolve(f"{sha}.png")
        if not target.exists():
            try:
                with target.open("xb") as stream:
                    stream.write(canonical)
            except FileExistsError:
                pass
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise ValueError("Existing canonical asset failed integrity check")
        return {"file_path": target.name, "width": image.width, "height": image.height, "sha256": sha, "input_sha256": hashlib.sha256(payload).hexdigest(), "preprocessing_version": version, "byte_size": len(canonical), "media_type": "image/png"}
