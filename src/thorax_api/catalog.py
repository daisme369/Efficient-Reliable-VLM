"""Patients, studies, images and doctor assignments (read side), plus the synthetic demo seed."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .db import connect
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
)


class Catalog:
    def __init__(self, path: str | Path, images: ImageStore):
        self.path, self.images = str(path), images
        with connect(self.path) as db:
            for statement in SCHEMA:
                db.execute(statement)

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
                           (SELECT r.status FROM reading_sessions r WHERE r.study_id = s.id AND r.doctor_id = ?) AS own_session_status
                    FROM studies s WHERE {where} ORDER BY s.study_date DESC, s.id DESC""",
                (actor_id, actor_id, *params),
            ).fetchall()
        return [dict(row) for row in rows]

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


def seed_demo(catalog: Catalog, assets: Path = DEMO_ASSETS) -> None:
    patients, studies, images, assignments = demo_rows()
    hashes = {}
    for view in ("pa", "ap", "lat"):
        data = (assets / f"{view}.png").read_bytes()
        catalog.images.put(f"demo/{view}.png", data)
        hashes[f"demo/{view}.png"] = hashlib.sha256(data).hexdigest()
    with connect(catalog.path) as db:
        # Prior reports were added later; seed them on databases created before that too.
        db.executemany("INSERT OR IGNORE INTO history_reports (id, patient_id, report_date, text) VALUES (?, ?, ?, ?)",
                       [row for patient in patients for row in demo_history(patient[0])]) if db.execute("SELECT COUNT(*) FROM patients").fetchone()[0] else None
        if db.execute("SELECT COUNT(*) FROM patients").fetchone()[0]:
            return
        db.executemany("INSERT INTO patients (id, code, name, birth_year, sex) VALUES (?, ?, ?, ?, ?)", patients)
        db.executemany("INSERT INTO studies (id, patient_id, study_date, indication) VALUES (?, ?, ?, ?)", studies)
        db.executemany("INSERT INTO images (id, study_id, view, width, height, media_type, storage_key, sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       [(*row, hashes[row[-1]]) for row in images])
        db.executemany("INSERT INTO assignments (doctor_id, patient_id, active) VALUES (?, ?, 1)", assignments)
        db.executemany("INSERT OR IGNORE INTO history_reports (id, patient_id, report_date, text) VALUES (?, ?, ?, ?)",
                       [row for patient in patients for row in demo_history(patient[0])])
