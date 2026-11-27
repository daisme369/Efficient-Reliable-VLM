"""Case search matching: EN/VI synonyms, diacritic folding, negation."""

from __future__ import annotations

from pathlib import Path
import sys
import json
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from tests.api.test_reading import HAS_API_DEPS, ReadingBase  # noqa: E402
from thorax_api.db import connect  # noqa: E402


class MatchingTests(unittest.TestCase):
    def test_fold_keeps_length_and_matches_vietnamese(self):
        from thorax_api.case_search import fold, detect, match
        text = "Tràn dịch màng phổi trái lượng ít."
        self.assertEqual(len(fold(text)), len(text))
        self.assertEqual(detect("tran dich"), ["pleural_effusion"])
        hit = match(text, ["pleural_effusion"], "tran dich")
        start, end = hit["spans"][0]
        self.assertEqual(hit["snippet"][start:end].lower(), "tràn dịch màng phổi")

    def test_synonyms_cross_languages(self):
        from thorax_api.case_search import detect, match
        self.assertEqual(detect("pleural effusion"), ["pleural_effusion"])
        self.assertIsNotNone(match("Small left effusion.", detect("tràn dịch màng phổi"), "tràn dịch màng phổi"))

    def test_negated_mention_does_not_match(self):
        from thorax_api.case_search import match
        for text in ("No pleural effusion.", "Không tràn dịch màng phổi.", "Lungs clear without effusion."):
            self.assertIsNone(match(text, ["pleural_effusion"], "effusion"), text)

    def test_no_change_is_not_negation(self):
        from thorax_api.case_search import match
        self.assertIsNotNone(match("No change in the small left effusion.", ["pleural_effusion"], "effusion"))

    def test_query_negation_and_fallback(self):
        from thorax_api.case_search import detect
        self.assertEqual(detect("No pneumothorax. Small right effusion."), ["pleural_effusion"])
        self.assertEqual(detect("bibasilar"), ["query"])
        from thorax_api.case_search import match
        self.assertIsNotNone(match("Mild bibasilar linear markings.", ["query"], "bibasilar"))

    def test_snippet_is_the_matching_sentence(self):
        from thorax_api.case_search import match
        hit = match("Heart size normal. Small left effusion; no pneumothorax.", ["pleural_effusion"], "effusion")
        self.assertEqual((hit["snippet"], hit["concepts"]), ("Small left effusion", ["pleural_effusion"]))

    def test_fold_handles_decomposed_input(self):
        import unicodedata
        from thorax_api.case_search import detect, match
        text = unicodedata.normalize("NFD", "Tràn dịch màng phổi trái.")
        self.assertEqual(detect(text), ["pleural_effusion"])
        hit = match(text, ["pleural_effusion"], text)
        start, end = hit["spans"][0]
        self.assertEqual(hit["snippet"][start:end], "Tràn dịch màng phổi")

    def test_negated_query_searches_nothing(self):
        from thorax_api.case_search import detect, match
        self.assertEqual(detect("no pleural effusion"), [])
        self.assertEqual(detect("không tràn dịch"), [])
        self.assertEqual(detect("bibasilar"), ["query"])
        self.assertIsNone(match("Small left effusion.", [], "no effusion"))

    def test_negation_ends_at_but(self):
        from thorax_api.case_search import match
        self.assertIsNotNone(match("No pneumothorax but small left effusion.", ["pleural_effusion"], "effusion"))

    def test_comma_list_stays_negated(self):
        from thorax_api.case_search import match
        self.assertIsNone(match("No pleural effusion, pneumothorax or consolidation.", ["pneumothorax"], "pneumothorax"))

    def test_fallback_respects_negation(self):
        from thorax_api.case_search import match
        self.assertIsNone(match("No bronchiectasis.", ["query"], "bronchiectasis"))
        self.assertIsNone(match("Không giãn phế quản.", ["query"], "giãn phế quản"))
        self.assertIsNotNone(match("Mild bronchiectasis.", ["query"], "bronchiectasis"))

    def test_english_plurals_match(self):
        from thorax_api.case_search import match
        for text, cid, q in (("Bilateral pleural effusions.", "pleural_effusion", "pleural effusion"),
                             ("Patchy bibasilar opacities.", "consolidation", "opacity"),
                             ("Two nodules in the right lung.", "nodule_mass", "nodule"),
                             ("Rib fractures.", "fracture", "fracture"),
                             ("Multiple masses.", "nodule_mass", "mass")):
            hit = match(text, [cid], q)
            self.assertIsNotNone(hit, text)
            s, e = hit["spans"][0]
            self.assertTrue(hit["snippet"][s:e].lower().endswith(("s", "es", "ies")), text)

    def test_no_significant_change_is_not_negation(self):
        from thorax_api.case_search import match
        for text in ("No significant change in the left effusion.", "No significant interval change in left pleural effusion."):
            self.assertIsNotNone(match(text, ["pleural_effusion"], "effusion"), text)

    def test_normal_does_not_pull_in_normal_studies(self):
        from thorax_api.case_search import detect, match
        self.assertEqual(detect("Heart size normal. Small left effusion"), ["pleural_effusion"])
        self.assertEqual(detect("normal chest"), ["normal"])
        self.assertIsNone(match("Heart size normal. No pleural effusion.", ["pleural_effusion"], "effusion"))

    def test_post_negation(self):
        from thorax_api.case_search import match
        self.assertIsNone(match("The left effusion has resolved.", ["pleural_effusion"], "effusion"))
        self.assertIsNone(match("Pneumothorax is not seen.", ["pneumothorax"], "pneumothorax"))
        self.assertIsNone(match("Tràn dịch đã hết.", ["pleural_effusion"], "tràn dịch"))
        self.assertIsNone(match("Small effusion, pneumothorax is not seen.", ["pleural_effusion"], "effusion"))  # accepted cost of crossing commas

    def test_post_negation_crosses_commas(self):
        from thorax_api.case_search import match
        for text in ("Effusion, previously seen, has resolved.", "Left effusion, seen on prior, is no longer seen."):
            self.assertIsNone(match(text, ["pleural_effusion"], "effusion"), text)
        text = "Effusion, pneumothorax and consolidation have resolved."
        for cid in ("pleural_effusion", "consolidation"):
            self.assertIsNone(match(text, [cid], "x"), cid)
        self.assertIsNotNone(match("Small effusion but pneumothorax is not seen.", ["pleural_effusion"], "effusion"))

    def test_query_with_regex_characters_does_not_crash(self):
        from thorax_api.case_search import detect, match
        self.assertIsNone(match("some text", ["query"], "a(y"))
        detect("a(y")

    def test_query_dot_is_literal(self):
        from thorax_api.case_search import match
        self.assertIsNone(match("abcy", ["query"], "a.cy"))

    def test_decimal_does_not_split_sentence(self):
        from thorax_api.case_search import match
        hit = match("Nodule 1.5 cm in right upper lobe.", ["nodule_mass"], "nodule")
        self.assertEqual(hit["snippet"], "Nodule 1.5 cm in right upper lobe")

@unittest.skipUnless(HAS_API_DEPS, "FastAPI test dependencies are not installed")
class CaseIndexTests(ReadingBase):
    next_id = 90000

    def final_reading(self, doctor_id, study_id, items, impression, *, report_id=None, report_findings=None):
        """Insert a signed reading straight into the tables; returns the session id."""
        CaseIndexTests.next_id += 1
        sid = CaseIndexTests.next_id
        with connect(self.db) as db:
            patient_id, = db.execute("SELECT patient_id FROM studies WHERE id = ?", (study_id,)).fetchone()
            image_id, = db.execute("SELECT MIN(id) FROM images WHERE study_id = ?", (study_id,)).fetchone()
            db.execute("INSERT INTO reading_sessions (id, doctor_id, patient_id, study_id, status, revision, analyzed_image_id) VALUES (?, ?, ?, ?, 'final', 1, ?)",
                       (sid, doctor_id, patient_id, study_id, image_id))
            rows = [{"id": f"f{n}", "text": text, "location": None, "image_id": image_id, "source": "doctor", "action": "accepted", "box_ids": []}
                    for n, text in enumerate(items)]
            db.execute("INSERT INTO findings_versions (id, session_id, version, status, items, ai_items, no_abnormality_confirmed, approved_by, approved_at) VALUES (?, ?, 1, 'approved', ?, '[]', 0, ?, '2026-11-20T00:00:00Z')",
                       (sid, sid, json.dumps(rows), doctor_id))
            content = {"comparison": "", "findings": " ".join(items) if report_findings is None else report_findings, "impression": impression, "recommendation": ""}
            db.execute("INSERT INTO reports (id, session_id, findings_version, status, content, approved_by, approved_at, updated_at) VALUES (?, ?, 1, 'final', ?, ?, '2026-11-20T00:00:00Z', '2026-11-20T00:00:00Z')",
                       (report_id or sid, sid, json.dumps(content), doctor_id))
        return sid

    def test_prior_lists_history_and_earlier_final_readings(self):
        self.final_reading(1, 2410, ["Small left pleural effusion."], "Left effusion.")
        body = self.doctor.get("/api/v1/studies/2411/case-search").json()
        self.assertEqual(body["cases"], [])
        self.assertEqual([p["source"] for p in body["prior"]][:1], ["history"])  # history 2026-10-02 is newest
        self.assertIn("reading", [p["source"] for p in body["prior"]])
        found = self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "tràn dịch"}).json()
        self.assertEqual([(p["source"], p["study_id"]) for p in found["prior"]], [("reading", 2410)])

    def test_prior_uses_signed_report_text(self):
        self.final_reading(1, 2410, ["Small left pleural effusion."], "Left effusion.", report_findings="Moderate left pleural effusion.")
        body = self.doctor.get("/api/v1/studies/2411/case-search").json()
        reading = [p for p in body["prior"] if p["source"] == "reading"][0]
        self.assertIn("Moderate", reading["text"])
        self.assertNotIn("Small", reading["text"])
        self.final_reading(1, 2431, ["Small right effusion."], "R.", report_findings="Moderate right effusion.")
        case = self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "effusion"}).json()["cases"][0]
        self.assertEqual(case["findings"], ["Moderate right effusion."])

    def test_cases_identity_follows_assignment(self):
        self.final_reading(1, 2431, ["Moderate right pleural effusion."], "Right effusion.")   # patient 3: doctor 1 only
        self.final_reading(2, 2511, ["Small left effusion."], "Left effusion.")                # patient 11: doctor 2 only
        one = self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "effusion"}).json()["cases"]
        self.assertEqual(len(one), 2)
        identified = [c for c in one if "patient" in c]
        hidden = [c for c in one if "patient" not in c]
        self.assertEqual((identified[0]["patient"]["code"], len(hidden)), ("PX-1044", 1))
        self.assertTrue(set(hidden[0]).isdisjoint({"patient", "study_date", "study_id", "patient_id"}))
        # patient 11 = Sofia Reyes, born 1969, study 2511 dated 2026-11-03
        self.assertEqual((hidden[0]["study_month"], hidden[0]["age_band"], hidden[0]["sex"]), ("2026-11", "50–59", "Female"))
        own = self.doctor.get("/api/v1/studies/2431/case-search", params={"q": "effusion"}).json()["cases"]
        self.assertNotIn("PX-1044", [c.get("patient", {}).get("code") for c in own])  # current patient never a case

    def test_one_case_per_study_latest_final(self):
        self.final_reading(1, 2431, ["Small right effusion."], "Old impression.")
        self.final_reading(2, 2431, ["Small right effusion."], "Newest impression.")
        cases = self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "effusion"}).json()["cases"]
        self.assertEqual([c["impression"] for c in cases], ["Newest impression."])

    def test_negated_and_unsigned_are_not_cases(self):
        self.final_reading(1, 2431, ["No pleural effusion."], "Normal chest.")
        self.open(2441)  # an in-progress reading is never a case
        self.assertEqual(self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "effusion"}).json()["cases"], [])

    def test_case_image_only_for_final(self):
        sid = self.final_reading(1, 2431, ["Small right effusion."], "Right effusion.")
        draft = self.open(2441)["session"]["id"]
        self.assertEqual(self.doctor.get(f"/api/v1/cases/{draft}/image").status_code, 404)
        self.assertEqual(self.doctor.get("/api/v1/cases/999999/image").status_code, 404)
        image = self.doctor.get(f"/api/v1/cases/{sid}/image")
        self.assertEqual((image.status_code, image.headers["content-type"]), (200, "image/png"))
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM case_views").fetchone()[0], 1)

    def test_case_views_get_distinct_ids(self):
        index = self.app.state.case_index
        sid = self.final_reading(1, 2431, ["Small right effusion."], "Right effusion.")
        index.record_view(1, sid)
        index.record_view(1, sid)
        with connect(self.db) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM case_views")]
        self.assertEqual(len(set(ids)), 2)

    def test_access_rules(self):
        admin = self.client("admin@example.test")
        self.assertEqual(admin.get("/api/v1/studies/2411/case-search").status_code, 403)
        self.assertEqual(self.doctor.get("/api/v1/studies/2511/case-search").status_code, 404)  # patient 11 not assigned
        self.assertEqual(self.doctor.get("/api/v1/studies/2411/case-search", params={"q": "x" * 501}).status_code, 422)  # app's invalid() is 422


if __name__ == "__main__":
    unittest.main()
