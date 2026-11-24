"""Patients, studies, images and doctor assignments (read side), plus the synthetic demo seed."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import unicodedata

from .db import columns as table_columns, connect, utc_now
from .storage import ImageStore

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        birth_year INTEGER,
        sex TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS studies (
        id INTEGER PRIMARY KEY,
        patient_id INTEGER NOT NULL REFERENCES patients (id),
        study_date TEXT NOT NULL,
        indication TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS images (
        id INTEGER PRIMARY KEY,
        study_id INTEGER NOT NULL REFERENCES studies (id),
        view TEXT NOT NULL CHECK (view IN ('PA', 'AP', 'LAT')),
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        media_type TEXT NOT NULL CHECK (media_type IN ('image/png', 'image/jpeg')),
        storage_key TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS history_reports (
        id INTEGER PRIMARY KEY,
        patient_id INTEGER NOT NULL REFERENCES patients (id),
        report_date TEXT NOT NULL,
        text TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS assignments (
        doctor_id INTEGER NOT NULL REFERENCES users (id),
        patient_id INTEGER NOT NULL REFERENCES patients (id),
        active INTEGER NOT NULL,
        PRIMARY KEY (doctor_id, patient_id)
    )""",
    """CREATE TABLE IF NOT EXISTS fixture_imports (
        fixture_key TEXT PRIMARY KEY, checksum TEXT NOT NULL, imported_at TEXT NOT NULL)""",
)


# A study can be edited or deleted only while it is made of uploaded images (never the seeded
# synthetic set) and nothing depends on it yet: no reading by any doctor and no labeling task.
EDITABLE = """(EXISTS (SELECT 1 FROM images i WHERE i.study_id = s.id)
    AND NOT EXISTS (SELECT 1 FROM images i WHERE i.study_id = s.id AND i.uploaded_by IS NULL)
    AND NOT EXISTS (SELECT 1 FROM reading_sessions r WHERE r.study_id = s.id)
    AND NOT EXISTS (SELECT 1 FROM label_tasks t JOIN images i ON i.id = t.image_id WHERE i.study_id = s.id))"""


class Catalog:
    def __init__(self, path: str | Path, images: ImageStore):
        self.path, self.images = str(path), images
        with connect(self.path) as db:
            for statement in SCHEMA:
                db.execute(statement)
            # Upload provenance, added after the first release; NULL for seeded synthetic images.
            columns = table_columns(db, "images", self.path)
            for column, kind in (("uploaded_by", "INTEGER REFERENCES users (id)"), ("uploaded_at", "TEXT")):
                if column not in columns:
                    db.execute(f"ALTER TABLE images ADD COLUMN {column} {kind}")
            if "priority" not in table_columns(db, "studies", self.path):  # set at intake; urgent studies allow a preliminary report
                db.execute("ALTER TABLE studies ADD COLUMN priority TEXT NOT NULL DEFAULT 'routine' CHECK (priority IN ('routine', 'urgent'))")

    def is_assigned(self, doctor_id: int, patient_id: int) -> bool:
        with connect(self.path) as db:
            return db.execute("SELECT 1 FROM assignments WHERE doctor_id = ? AND patient_id = ? AND active = 1",
                              (doctor_id, patient_id)).fetchone() is not None

    # --- admin views ---
    def counts(self) -> dict:
        with connect(self.path) as db:
            return {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("patients", "studies", "images")}

    def patient_codes(self) -> list[tuple[int, str]]:
        with connect(self.path) as db:
            return [(row["id"], row["code"]) for row in db.execute("SELECT id, code FROM patients ORDER BY id")]

    def assignments(self) -> list[tuple[int, int]]:
        with connect(self.path) as db:
            return [(row[0], row[1]) for row in db.execute("SELECT doctor_id, patient_id FROM assignments WHERE active = 1")]

    def set_assignment(self, doctor_id: int, patient_id: int, active: bool) -> bool:
        """Grant or revoke access; returns whether anything changed."""
        with connect(self.path) as db:
            changed = db.execute("UPDATE assignments SET active = ? WHERE doctor_id = ? AND patient_id = ? AND active != ?",
                                 (int(active), doctor_id, patient_id, int(active))).rowcount
            if not changed and active and db.execute("SELECT 1 FROM assignments WHERE doctor_id = ? AND patient_id = ?", (doctor_id, patient_id)).fetchone() is None:
                db.execute("INSERT INTO assignments (doctor_id, patient_id, active) VALUES (?, ?, 1)", (doctor_id, patient_id))
                changed = 1
        return bool(changed)

    def patients_for(self, doctor_id: int, search: str = "") -> list[dict]:
        pattern = f"%{search.lower()}%"
        with connect(self.path) as db:
            rows = db.execute(
                """SELECT p.id, p.code, p.name, p.birth_year, p.sex,
                          (SELECT COUNT(*) FROM studies s WHERE s.patient_id = p.id) AS study_count
                   FROM patients p JOIN assignments a ON a.patient_id = p.id
                   WHERE a.doctor_id = ? AND a.active = 1 AND (LOWER(p.name) LIKE ? OR LOWER(p.code) LIKE ?)
                   ORDER BY p.id""",
                (doctor_id, pattern, pattern),
            ).fetchall()
        return [dict(row) for row in rows]

    def _study_rows(self, where: str, params: tuple, actor_id: int) -> list[dict]:
        # Reading status comes from reading_sessions (created by ReadingStore, see reading.py).
        with connect(self.path) as db:
            rows = db.execute(
                f"""SELECT s.id, s.patient_id, s.study_date, s.indication,
                           (SELECT COUNT(*) FROM images i WHERE i.study_id = s.id) AS image_count,
                           CASE WHEN EXISTS (SELECT 1 FROM reading_sessions r WHERE r.study_id = s.id AND r.status = 'final') THEN 'final'
                                WHEN EXISTS (SELECT 1 FROM reading_sessions r WHERE r.study_id = s.id) THEN 'in_progress' ELSE 'new' END AS status,
                           (SELECT r.id FROM reading_sessions r WHERE r.study_id = s.id AND r.doctor_id = ?) AS own_session_id,
                           (SELECT r.status FROM reading_sessions r WHERE r.study_id = s.id AND r.doctor_id = ?) AS own_session_status,
                           {EDITABLE} AS editable
                    FROM studies s WHERE {where} ORDER BY s.study_date DESC, s.id DESC""",
                (actor_id, actor_id, *params),
            ).fetchall()
        return [{**dict(row), "editable": bool(row["editable"])} for row in rows]

    def update_study(self, study_id: int, study_date: str, indication: str) -> bool:
        """Returns False when the study is not editable (see EDITABLE)."""
        with connect(self.path) as db:
            return db.execute(f"UPDATE studies SET study_date = ?, indication = ? WHERE id IN (SELECT s.id FROM studies s WHERE s.id = ? AND {EDITABLE})",
                              (study_date, indication, study_id)).rowcount == 1

    def delete_study(self, study_id: int) -> bool:
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(f"SELECT 1 FROM studies s WHERE s.id = ? AND {EDITABLE}", (study_id,)).fetchone():
                return False
            keys = [row[0] for row in db.execute("SELECT storage_key FROM images WHERE study_id = ?", (study_id,))]
            db.execute("DELETE FROM images WHERE study_id = ?", (study_id,))
            db.execute("DELETE FROM studies WHERE id = ?", (study_id,))
            self._drop_unused_files(db, keys)
        return True

    def delete_image(self, study_id: int, image_id: int) -> str:
        """Returns 'deleted', 'locked' (study not editable), 'last' (delete the study instead) or 'missing'."""
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT storage_key FROM images WHERE id = ? AND study_id = ?", (image_id, study_id)).fetchone()
            if row is None:
                return "missing"
            if not db.execute(f"SELECT 1 FROM studies s WHERE s.id = ? AND {EDITABLE}", (study_id,)).fetchone():
                return "locked"
            if db.execute("SELECT COUNT(*) FROM images WHERE study_id = ?", (study_id,)).fetchone()[0] == 1:
                return "last"
            db.execute("DELETE FROM images WHERE id = ?", (image_id,))
            self._drop_unused_files(db, [row[0]])
        return "deleted"

    def _drop_unused_files(self, db, keys: list[str]) -> None:
        # Uploads are stored by content hash, so another image may share the same file.
        for key in set(keys):
            if not db.execute("SELECT 1 FROM images WHERE storage_key = ?", (key,)).fetchone():
                self.images.delete(key)

    def studies_for_patient(self, patient_id: int, actor_id: int = 0) -> list[dict]:
        return self._study_rows("s.patient_id = ?", (patient_id,), actor_id)

    def study(self, study_id: int, actor_id: int = 0) -> dict | None:
        rows = self._study_rows("s.id = ?", (study_id,), actor_id)
        if not rows:
            return None
        with connect(self.path) as db:
            images = db.execute("SELECT id, study_id, view, width, height, sha256, media_type FROM images WHERE study_id = ? ORDER BY id",
                                (study_id,)).fetchall()
        return {**rows[0], "images": [{**dict(row), "content_url": f"/api/v1/images/{row['id']}/content"} for row in images]}

    def image(self, image_id: int) -> dict | None:
        with connect(self.path) as db:
            row = db.execute("""SELECT i.*, s.patient_id FROM images i JOIN studies s ON s.id = i.study_id WHERE i.id = ?""",
                             (image_id,)).fetchone()
        return dict(row) if row else None

    def image_bytes(self, image: dict) -> bytes:
        data = self.images.get(image["storage_key"])
        if hashlib.sha256(data).hexdigest() != image["sha256"]:
            raise ValueError("stored image does not match its recorded checksum")
        return data

    # --- write side: admin-created patients, uploaded studies and images ---
    def create_patient(self, name: str, birth_year: int | None, sex: str | None) -> dict:
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            pid = db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM patients").fetchone()[0]
            code = f"PX-{1041 + pid:04d}"  # the demo's code scheme, so codes stay unique and ordered
            db.execute("INSERT INTO patients (id, code, name, birth_year, sex) VALUES (?, ?, ?, ?, ?)", (pid, code, name, birth_year, sex))
        return {"id": pid, "code": code}

    def patient_exists(self, patient_id: int) -> bool:
        with connect(self.path) as db:
            return db.execute("SELECT 1 FROM patients WHERE id = ?", (patient_id,)).fetchone() is not None

    def _store_upload(self, png: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(png).hexdigest()
        key = f"uploads/{digest}.png"
        self.images.put(key, png)
        return key, digest

    def create_study(self, patient_id: int, study_date: str, indication: str, view: str, upload: tuple[bytes, int, int], user_id: int) -> int:
        """A study is created together with its first image, so no study is ever empty."""
        png, width, height = upload
        key, digest = self._store_upload(png)
        with connect(self.path) as db:
            study_id = db.execute("INSERT INTO studies (patient_id, study_date, indication) VALUES (?, ?, ?) RETURNING id",
                                  (patient_id, study_date, indication)).fetchone()[0]
            self._insert_image(db, study_id, view, width, height, digest, key, user_id)
        return study_id

    def add_image(self, study_id: int, view: str, upload: tuple[bytes, int, int], user_id: int) -> int | None:
        """Returns None when the study already has a reading: its image list is fixed once reading starts."""
        png, width, height = upload
        key, digest = self._store_upload(png)
        with connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM reading_sessions WHERE study_id = ?", (study_id,)).fetchone():
                return None
            return self._insert_image(db, study_id, view, width, height, digest, key, user_id)

    @staticmethod
    def _insert_image(db, study_id, view, width, height, digest, key, user_id) -> int:
        return db.execute("""INSERT INTO images (study_id, view, width, height, sha256, media_type, storage_key, uploaded_by, uploaded_at)
                             VALUES (?, ?, ?, ?, ?, 'image/png', ?, ?, ?) RETURNING id""",
                          (study_id, view, width, height, digest, key, user_id, utc_now())).fetchone()[0]


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_PIXELS = 16_000_000


def normalize_upload(data: bytes) -> tuple[bytes, int, int]:
    """Fully decodes a PNG or JPEG and re-encodes it as PNG, which drops EXIF and text metadata
    (they can carry patient details) and applies the EXIF orientation. Raises ValueError with a user message."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG"):
                raise ValueError("Upload a PNG or JPEG image.")
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Animated images are not supported.")
            if image.width * image.height > MAX_UPLOAD_PIXELS:
                raise ValueError("Upload an image of at most 16 megapixels.")
            image.load()
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("L", "RGB", "I;16", "I"):  # keep grayscale and 16-bit radiographs as they are
                image = image.convert("RGB")
            out = io.BytesIO()
            image.save(out, format="PNG")
    except (OSError, SyntaxError, Image.DecompressionBombError):
        raise ValueError("The file is not a readable PNG or JPEG image.") from None
    return out.getvalue(), image.width, image.height


# --- Synthetic demo seed: the same patients, studies, images and assignments as the web mock (thorax-demo-v1). ---
DEMO_ASSETS = Path(__file__).resolve().parents[1] / "thorax-web" / "lib" / "server" / "assets"
IDENTITIES = (
    ("Eleanor Brooks", 1958, "Female"), ("James Whitfield", 1967, "Male"), ("Marina Chen", 1972, "Female"),
    ("Daniel Morgan", 1953, "Male"), ("Avery Patel", 1983, "Female"), ("Julian Rivera", 1977, "Male"),
    ("Nora Bennett", 1962, "Female"), ("Theo Wilson", 1991, "Male"), ("Amara Okafor", 1974, "Female"),
    ("Liam Foster", 1985, "Male"), ("Sofia Reyes", 1969, "Female"), ("Miles Hart", 1957, "Male"),
)
INDICATIONS = ("Persistent cough and shortness of breath", "Follow-up chest radiograph", "Chest discomfort",
               "Dyspnea on exertion", "Persistent cough", "Interval review")


def demo_rows():
    """Mirror of src/thorax-web/lib/server/fixtures.ts so both backends serve identical IDs."""
    patients, studies, images, assignments = [], [], [], []
    for pid, (name, birth_year, sex) in enumerate(IDENTITIES, start=1):
        patients.append((pid, f"PX-{1041 + pid:04d}", name, birth_year, sex))
        for current in ((True,) if pid == 2 else (True, False)):
            sid = 2400 + pid * 10 + int(current)
            date = f"2026-11-{7 - pid % 7:02d}" if current else "2026-08-18"
            indication = INDICATIONS[(pid - 1) % len(INDICATIONS)] if current else "Prior chest examination"
            studies.append((sid, pid, date, indication))
            views = ["PA", "LAT"] if current and pid % 3 == 1 else ["AP" if pid % 3 == 0 else "PA"]
            for index, view in enumerate(views):
                images.append((sid * 10 + index, sid, view, 800, 880, "image/png", f"demo/{view.lower()}.png"))
        doctors = [1] if pid <= 10 else [2]
        if pid == 1:
            doctors.append(2)
        assignments.extend((doctor, pid) for doctor in doctors)
    return patients, studies, images, assignments


def demo_history(patient_id: int) -> list[tuple]:
    """Synthetic prior reports, mirroring studyHistory() in the web fixture."""
    if patient_id == 2:
        return []
    months = [10, 8, 7, 6, 5, 4, 3] if patient_id == 1 else [8, 5]
    return [(patient_id * 100 + i + 1, patient_id, f"2026-{month:02d}-02",
             "Synthetic prior report. Mild bibasilar linear markings were described." if i == 0
             else "Synthetic prior report. No acute cardiopulmonary finding was described.") for i, month in enumerate(months)]


FIXTURE_KEY = "thorax-demo-v1"
# shortcut: fixed demo limits, not values from the spec; revisit if real-size radiographs are imported.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Text and EXIF chunks can carry identity; fixtures must arrive with metadata stripped.
PNG_METADATA_CHUNKS = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


class FixtureError(ValueError):
    """The fixture bundle is invalid or conflicts with data already imported; nothing was written."""


def demo_bundle(assets: Path = DEMO_ASSETS) -> dict:
    patients, studies, images, assignments = demo_rows()
    files = {key: (assets / Path(key).name).read_bytes() for key in sorted({row[-1] for row in images})}
    history = [row for patient in patients for row in demo_history(patient[0])]
    return {"patients": patients, "studies": studies, "images": images, "assignments": assignments,
            "history": history, "files": files}


def image_problem(data: bytes, media_type: str, width: int, height: int) -> str | None:
    """Check the real format from the bytes, size, pixel limits, recorded dimensions and metadata."""
    if len(data) > MAX_IMAGE_BYTES:
        return "image exceeds the size limit"
    if width * height > MAX_IMAGE_PIXELS:
        return "image exceeds the pixel limit"
    if media_type == "image/png" and data.startswith(PNG_SIGNATURE):
        offset, chunks = 8, []
        while offset + 8 <= len(data):
            length = int.from_bytes(data[offset:offset + 4], "big")
            chunks.append((data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]))
            offset += 12 + length
        if not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND" or offset != len(data):
            return "image is not a complete PNG"
        if (int.from_bytes(chunks[0][1][:4], "big"), int.from_bytes(chunks[0][1][4:8], "big")) != (width, height):
            return "image dimensions do not match the fixture"
        if any(kind in PNG_METADATA_CHUNKS for kind, _ in chunks):
            return "image contains metadata"
        return None
    if media_type == "image/jpeg" and data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(data) and data[offset] == 0xFF:
            marker, length = data[offset + 1], int.from_bytes(data[offset + 2:offset + 4], "big")
            if marker in (0xE1, 0xFE):  # EXIF/XMP and comments
                return "image contains metadata"
            if marker in (0xC0, 0xC1, 0xC2):
                size = (int.from_bytes(data[offset + 7:offset + 9], "big"), int.from_bytes(data[offset + 5:offset + 7], "big"))
                return None if size == (width, height) else "image dimensions do not match the fixture"
            offset += 2 + length
        return "image is not a complete JPEG"
    return "image bytes do not match the declared media type"


def bundle_problems(bundle: dict, doctor_ids: set[int]) -> list[str]:
    """Return every reason the bundle cannot be imported; an empty list means it is valid."""
    problems = []
    patient_ids = {row[0] for row in bundle["patients"]}
    study_ids = {row[0] for row in bundle["studies"]}
    for name, rows in (("patients", bundle["patients"]), ("studies", bundle["studies"]),
                       ("images", bundle["images"]), ("history", bundle["history"])):
        if len({row[0] for row in rows}) != len(rows):
            problems.append(f"{name} has duplicate IDs")
    if len({row[1] for row in bundle["patients"]}) != len(bundle["patients"]):
        problems.append("patients has duplicate codes")
    problems += [f"study {row[0]} references a missing patient" for row in bundle["studies"] if row[1] not in patient_ids]
    problems += [f"history {row[0]} references a missing patient" for row in bundle["history"] if row[1] not in patient_ids]
    problems += [f"assignment references missing doctor {d}" for d, _ in bundle["assignments"] if d not in doctor_ids]
    problems += [f"assignment references missing patient {p}" for _, p in bundle["assignments"] if p not in patient_ids]
    for image_id, study_id, _view, width, height, media_type, key in bundle["images"]:
        if study_id not in study_ids:
            problems.append(f"image {image_id} references a missing study")
        elif key not in bundle["files"]:
            problems.append(f"image {image_id} references a missing file")
        elif problem := image_problem(bundle["files"][key], media_type, width, height):
            problems.append(f"image {image_id}: {problem}")
    # Narratives must never repeat a patient's name or code (identity guard, implementation_plan.md).
    identities = {_normal(text) for row in bundle["patients"] for text in (row[1], row[2])}
    texts = [(f"study {row[0]}", row[3]) for row in bundle["studies"]] + [(f"history {row[0]}", row[3]) for row in bundle["history"]]
    problems += [f"{where} contains patient identity text" for where, text in texts
                 if any(identity in _normal(text) for identity in identities)]
    return problems


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def bundle_checksum(bundle: dict) -> str:
    rows = {name: bundle[name] for name in ("patients", "studies", "images", "assignments", "history")}
    rows["files"] = {key: hashlib.sha256(data).hexdigest() for key, data in sorted(bundle["files"].items())}
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _stored_rows(db) -> dict:
    query = {"patients": "SELECT id, code, name, birth_year, sex FROM patients ORDER BY id",
             "studies": "SELECT id, patient_id, study_date, indication FROM studies ORDER BY id",
             "images": "SELECT id, study_id, view, width, height, media_type, storage_key FROM images ORDER BY id"}
    return {name: [tuple(row) for row in db.execute(sql)] for name, sql in query.items()}


def seed_demo(catalog: Catalog, assets: Path = DEMO_ASSETS, bundle: dict | None = None) -> str:
    """Insert-only import of the synthetic fixture: same checksum is a no-op, a different one is refused."""
    bundle = bundle or demo_bundle(assets)
    checksum = bundle_checksum(bundle)
    with connect(catalog.path) as db:
        receipt = db.execute("SELECT checksum FROM fixture_imports WHERE fixture_key = ?", (FIXTURE_KEY,)).fetchone()
        has_patients = db.execute("SELECT COUNT(*) FROM patients").fetchone()[0] > 0
        doctor_ids = {row[0] for row in db.execute("SELECT id FROM users WHERE role = 'doctor'")}
    if receipt is not None:
        if receipt[0] != checksum:
            raise FixtureError("fixture checksum differs from the imported bundle; existing records are never overwritten")
        return "unchanged"
    if problems := bundle_problems(bundle, doctor_ids):
        raise FixtureError("fixture is invalid: " + "; ".join(problems))
    hashes = {key: hashlib.sha256(data).hexdigest() for key, data in bundle["files"].items()}
    for key, data in bundle["files"].items():
        _put_once(catalog.images, key, data)
    with connect(catalog.path) as db:
        if has_patients:
            # Databases seeded before receipts existed: accept only if every fixture row is stored unchanged.
            # Rows added later (admin-created patients, uploaded studies) are not fixture rows and are ignored.
            expected = {name: sorted(tuple(row) for row in bundle[name]) for name in ("patients", "studies", "images")}
            stored = {name: [row for row in rows if row[0] in {r[0] for r in expected[name]}] for name, rows in _stored_rows(db).items()}
            if stored != expected:
                raise FixtureError("existing patients differ from the fixture; existing records are never overwritten")
        else:
            db.executemany("INSERT INTO patients (id, code, name, birth_year, sex) VALUES (?, ?, ?, ?, ?)", bundle["patients"])
            db.executemany("INSERT INTO studies (id, patient_id, study_date, indication) VALUES (?, ?, ?, ?)", bundle["studies"])
            db.executemany("INSERT INTO images (id, study_id, view, width, height, media_type, storage_key, sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           [(*row, hashes[row[-1]]) for row in bundle["images"]])
            db.executemany("INSERT INTO assignments (doctor_id, patient_id, active) VALUES (?, ?, 1)", bundle["assignments"])
        # Prior reports were added after the first seeds; older databases receive them here.
        db.executemany("INSERT INTO history_reports (id, patient_id, report_date, text) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                       bundle["history"])
        db.execute("INSERT INTO fixture_imports (fixture_key, checksum, imported_at) VALUES (?, ?, ?)",
                   (FIXTURE_KEY, checksum, utc_now()))
    return "imported"


def _put_once(images: ImageStore, key: str, data: bytes) -> None:
    """Image files are immutable: an identical file is reused, a different one is a conflict."""
    try:
        existing = images.get(key)
    except FileNotFoundError:
        images.put(key, data)
        return
    if existing != data:
        raise FixtureError("a stored image differs from the fixture; existing images are never overwritten")
