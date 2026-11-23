"""Fetch the optional CC0 demo images and verify their pinned SHA-256."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BUNDLE = Path(__file__).resolve().parent / "fixtures" / "public-radiographs"
MAX_BYTES = 5 * 1024 * 1024


def download_images():
    fixture = json.loads((BUNDLE / "fixture.json").read_text(encoding="utf-8"))
    for image in fixture["images"]:
        filename = image["asset"]
        destination = BUNDLE / filename
        url = image["download_url"]
        if Path(filename).name != filename or urlparse(url).netloc != "upload.wikimedia.org" or urlparse(url).scheme != "https":
            raise ValueError("Unexpected image path or download host")
        if destination.exists():
            data = destination.read_bytes()
        else:
            request = Request(url, headers={"User-Agent": "ThoraxDemo/1.0 (academic fixture download)"})
            with urlopen(request, timeout=25) as response:
                data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != image["input_sha256"]:
            raise ValueError(f"Image checksum mismatch: {filename}")
        if not destination.exists():
            destination.write_bytes(data)
    print(f"Verified {len(fixture['images'])} public radiographs.")


if __name__ == "__main__":
    download_images()
