"""Case search matching: EN/VI synonyms, diacritic folding, negation."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


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


if __name__ == "__main__":
    unittest.main()
