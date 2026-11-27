"""Signed demo readings on the synthetic August studies, so Similar cases has data to show.

Run: python -m thorax_api.seed_cases   (uses THORAX_DATABASE_URL or THORAX_DB_PATH; safe to re-run)
"""

from __future__ import annotations

import json
import os

from .db import connect
from .reading import ReadingStore

SIGNED_AT = "2026-08-18T10:00:00Z"
# (study, signing doctor, findings, impression) — synthetic text for synthetic demo patients.
CASES = (
    (2430, 1, ["Small right pleural effusion.", "Heart size is normal."], "Small right pleural effusion."),
    (2440, 1, ["Moderate left pleural effusion with adjacent atelectasis.", "Mild cardiomegaly."], "Left pleural effusion and mild cardiomegaly."),
    (2450, 1, ["Right lower lobe consolidation.", "No pleural effusion."], "Right lower lobe pneumonia."),
    (2460, 1, ["Cardiomegaly.", "Pulmonary vascular congestion with small bilateral pleural effusions."], "Cardiomegaly with mild pulmonary oedema and small bilateral effusions."),
    (2470, 1, ["Mild bibasilar linear atelectasis.", "No pneumothorax."], "Bibasilar atelectasis."),
    (2480, 1, ["No acute cardiopulmonary abnormality."], "Normal chest radiograph."),
    (2510, 2, ["Small left pleural effusion.", "Left basal atelectasis."], "Small left pleural effusion."),
    (2520, 2, ["Cardiomegaly.", "Small right pleural effusion."], "Cardiomegaly with small right pleural effusion."),
)


def seed(target) -> int:
    """Adds each case whose study has no reading yet and whose doctor is assigned; returns how many were added."""
    added = 0
    with connect(target) as db:
        for study_id, doctor_id, items, impression in CASES:
            study = db.execute("SELECT patient_id FROM studies WHERE id = ?", (study_id,)).fetchone()
            if not study or db.execute("SELECT 1 FROM reading_sessions WHERE study_id = ?", (study_id,)).fetchone():
                continue
            if not db.execute("SELECT 1 FROM assignments WHERE doctor_id = ? AND patient_id = ? AND active = 1", (doctor_id, study["patient_id"])).fetchone():
                continue
            image_id = db.execute("SELECT MIN(id) FROM images WHERE study_id = ?", (study_id,)).fetchone()[0]
            sid = ReadingStore._next_id(db)
            db.execute("INSERT INTO reading_sessions (id, doctor_id, patient_id, study_id, status, revision, analyzed_image_id) VALUES (?, ?, ?, ?, 'final', 1, ?)",
                       (sid, doctor_id, study["patient_id"], study_id, image_id))
            rows = [{"id": f"f{n}", "text": text, "location": None, "image_id": image_id, "source": "doctor", "action": "accepted", "box_ids": []}
                    for n, text in enumerate(items)]
            db.execute("""INSERT INTO findings_versions (id, session_id, version, status, items, ai_items, no_abnormality_confirmed, approved_by, approved_at)
                          VALUES (?, ?, 1, 'approved', ?, '[]', 0, ?, ?)""", (ReadingStore._next_id(db), sid, json.dumps(rows), doctor_id, SIGNED_AT))
            report_id = ReadingStore._next_id(db)
            content = {"comparison": "", "findings": " ".join(items), "impression": impression, "recommendation": ""}
            db.execute("INSERT INTO reports (id, session_id, findings_version, status, content, approved_by, approved_at, updated_at) VALUES (?, ?, 1, 'final', ?, ?, ?, ?)",
                       (report_id, sid, json.dumps(content), doctor_id, SIGNED_AT, SIGNED_AT))
            # Audit trail says plainly that this reading was seeded, not read.
            db.execute("INSERT INTO reading_events (id, session_id, user_id, type, data, created_at) VALUES (?, ?, ?, 'gate2_approved', ?, ?)",
                       (ReadingStore._next_id(db), sid, doctor_id, json.dumps({"report_id": report_id, "findings_version": 1, "source": "demo_seed"}), SIGNED_AT))
            added += 1
    return added


if __name__ == "__main__":
    print(f"Seeded {seed(os.environ.get('THORAX_DATABASE_URL') or os.environ.get('THORAX_DB_PATH', 'thorax.sqlite3'))} demo case(s).")
