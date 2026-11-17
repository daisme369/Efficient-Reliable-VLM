"""Synthetic M1 entry point, mapping, and scoring checks."""

import json
from importlib.util import find_spec
from contextlib import closing
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cxr_research.data.manifest import load_manifest
from cxr_research.data.synthetic import create_fixture
from cxr_research.experiments.config import load_config
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json

HAS_M1 = all(find_spec(package) is not None for package in ("torch", "scipy", "transformers", "peft"))
if HAS_M1:
    from cxr_research.data.m1 import M1Bundle, REGION_PROMPT, validation_partition
    from cxr_research.evaluation.m1 import box_iou, parse_boxes, selectivity


def fixture(root):
    config_file = create_fixture(root)
    config = json.loads(config_file.read_text())
    manifest = Path(config["data"]["manifest"])
    provenance = Path(config["data"]["provenance"])
    proof = json.loads(provenance.read_text())
    rows = [r for r in map(json.loads, manifest.read_text().splitlines()) if r["task"] == "medgemma"]
    folds = {validation_partition(str(i)): str(i) for i in range(20)}
    for row, fold in zip((r for r in rows if r["split"] == "validation"),
                         ("selection", "calibration", "report"), strict=True):
        row["patient_id"] = folds[fold]
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    proof.update(rows_before=len(rows), rows_after=len(rows), exclusions={}, exclusion_stages=[],
                 manifest_sha256=file_hash(manifest))
    write_json(provenance, proof)
    train = rows[0]
    image, patient = train["image_id"], train["patient_id"]
    impression = {"id": f"{patient}_study", "image": f"mimic/p{patient[:2]}/p{patient}/sstudy/{image}.jpg",
                  "generate_method": "gpt4", "impression": "Synthetic rectangle."}
    encoded = json.dumps(impression).encode()
    (root / "mock-reports.json").write_bytes(b"[" + encoded + b"]")
    write_json(root / "mock-coco.json", {
        "images": [{"id": 1, "file_name": image + ".jpg"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 2, "split": "train",
                         "label_text": "bright rectangle", "bbox": [1, 2, 8, 8]}]})
    db_path = root / "mapping.sqlite"
    with closing(sqlite3.connect(db_path)) as db, db:
        db.executescript("""
            CREATE TABLE images(image_id TEXT PRIMARY KEY,patient_id TEXT,study_id TEXT,
                split TEXT,relative_path TEXT,view TEXT);
            CREATE TABLE vqa(sample_id TEXT PRIMARY KEY,image_id TEXT REFERENCES images);
            CREATE TABLE chest_imagenome_bbox(sample_id TEXT REFERENCES vqa,xyxy TEXT);
            CREATE TABLE ms_cxr_phrase(phrase_id INTEGER,image_id TEXT,source_ref TEXT,
                category_id INTEGER,annotation_ids TEXT,boxes_xyxy TEXT);
            CREATE TABLE context_study(study_id TEXT,image_id TEXT);
            CREATE TABLE context_image(image_id TEXT,source_ref TEXT,byte_offset INTEGER,byte_length INTEGER,method TEXT);
        """)
        for row in rows:
            iid = row["image_id"]
            db.execute("INSERT INTO images VALUES(?,?,?,?,?,?)",
                       (iid, row["patient_id"], "study" if iid == image else iid,
                        row["split"], row["image"], row["view"]))
            db.execute("INSERT INTO vqa VALUES(?,?)", (row["sample_id"], iid))
        report_qa = next(row for row in rows if validation_partition(row["patient_id"]) == "report"
                         and row["split"] == "validation")
        for row in (train, report_qa):
            db.execute("INSERT INTO chest_imagenome_bbox VALUES(?,?)", (row["sample_id"], "[1,2,9,10]"))
        db.execute("INSERT INTO ms_cxr_phrase VALUES(1,?,'mock-coco.json',2,'[1]','[[1,2,9,10]]')", (image,))
        db.execute("INSERT INTO context_study VALUES('study',?)", (image,))
        db.execute("INSERT INTO context_image VALUES(?,'mock-reports.json',1,?,'gpt4')", (image, len(encoded)))

    write_json(root / "report.json", {"status": "development_review_only",
               "outputs_sha256": {p.name: file_hash(p) for p in (db_path, manifest, provenance)},
               "source_metadata": {name: {"sha256": file_hash(root / name)}
                                   for name in ("mock-reports.json", "mock-coco.json")},
               "patient_checks": {"held_patient_leaks": 0, "train_validation_overlap": 0}})
    write_json(root / "verification.json", {"status": "passed", "manifest_hash": file_hash(manifest),
                                             "held_or_cross_split_patients": 0})
    protocol = root / "protocol.json"
    write_json(protocol, {"m1": {"target_risk": 0.25, "bundle_report_sha256": file_hash(root / "report.json")}})
    config.update(protocol=str(protocol), task="medgemma", baseline="B1", run_id="synthetic-m1")
    config["medgemma"].update(revision="a"*40, quantization="none", local_files_only=True)
    path = root / "m1.json"
    write_json(path, config)
    return path


@unittest.skipUnless(HAS_M1, "M1 smoke requires the research Python dependencies")
class M1Tests(unittest.TestCase):
    def test_cli_validates_actual_bundle_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            config_path = fixture(root)
            config = load_config(config_path)
            command = [sys.executable, str(ROOT / "scripts/gpu/run_m1.py"), "validate", "--config", str(config_path)]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            out = json.loads(result.stdout)
            self.assertEqual(out["splits"]["train"], {"vqa": 8, "impression": 1, "phrase": 1, "region": 1})
            self.assertEqual(out["splits"]["validation"], {"vqa": 3, "impression": 0, "phrase": 0, "region": 1})
            bundle = M1Bundle(load_manifest(config.data), root, out["bundle_report_sha256"], synthetic=True)
            self.assertEqual(len(bundle.selection), 1)
            self.assertEqual(bundle.train[8]["question"], REGION_PROMPT + "Is there a rectangle?")
            self.assertEqual(bundle.train[8]["answer"], "[[20,62,188,313]]")
            self.assertEqual(bundle.train[10]["answer"], "Synthetic rectangle.")
            self.assertNotIn("rectangle", bundle.train[10]["question"])
            (root / "mock-reports.json").write_bytes(b"changed")
            failed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertIn("source data digest", failed.stderr)

    def test_boxes_and_selectivity(self):
        boxes = parse_boxes("[[8,8,10,10],[0,0,4,4],[8,8,10,10]]")
        self.assertEqual(boxes, [(8, 8, 10, 10), (0, 0, 4, 4)])
        self.assertEqual(box_iou(boxes, [(0, 0, 4, 4)]), 0.8)
        self.assertIsNone(parse_boxes("[[0,0,1001,1]]"))
        self.assertIsNone(parse_boxes("[[0,0,true,1]]"))
        rows = [(f"mock-{i}", i % 2, (0.8 if i % 2 else 0.2, 0.1 * (i % 3))) for i in range(100)]
        result = selectivity(rows, 0.01)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(set(result["signal_ablations_same_adapter"]), {"answer_signal", "grounding_signal", "joint"})
        self.assertEqual(result["signal_ablations_same_adapter"]["joint"]["risk"], 0.0)

    def test_tiny_optimizer_checkpoint_and_evaluation(self):
        import torch
        from transformers import BatchEncoding

        class Processor:
            chat_template = "synthetic-template"

            def to_dict(self):
                return {"template": self.chat_template}

            def save_pretrained(self, path):
                write_json(Path(path) / "processor.json", self.to_dict())

            def decode(self, tokens, **kwargs):
                return "[[20,62,188,313]]" if tokens[0] == 3 else "yes"

        class Collator:
            def __init__(self, processor, settings):
                pass

            def encode(self, examples, *, supervised):
                if not supervised:
                    self.assert_no_targets(examples)
                return BatchEncoding({"input_ids": torch.tensor([
                    [1, 3 if x["question"].startswith(REGION_PROMPT) else 2] for x in examples])})

            def assert_no_targets(self, examples):
                if any(set(x) != {"question", "image"} for x in examples):
                    raise AssertionError("inference received targets")

            def __call__(self, examples):
                return {"input_ids": torch.tensor([[1, 2, 3]] * len(examples)),
                        "labels": torch.tensor([[-100, -100, 3]] * len(examples))}

        class Tiny(torch.nn.Module):
            device = torch.device("cpu")

            def __init__(self):
                super().__init__()
                self.embedding = torch.nn.Embedding(4, 4)

            def forward(self, input_ids, labels=None):
                from transformers.modeling_outputs import CausalLMOutput
                return CausalLMOutput(logits=self.embedding(input_ids))

            def save_pretrained(self, path, **kwargs):
                Path(path).mkdir(parents=True, exist_ok=True)
                torch.save(self.state_dict(), Path(path) / "weights.pt")

            def generate(self, input_ids, **kwargs):
                token = input_ids[:, -1:]
                return SimpleNamespace(sequences=torch.cat((input_ids, token), dim=1),
                                       scores=[torch.ones((1, 4))])

        def reload(model, path, **kwargs):
            model.load_state_dict(torch.load(Path(path) / "weights.pt", weights_only=True))
            return model

        def print_result(*args, **kwargs):
            if kwargs.get("file") is sys.stderr and sys.exception() is not None:
                raise sys.exception()
            return print(*args, **kwargs)

        torch.set_num_threads(1)
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            root = Path(directory) / "fixture"
            config_path = fixture(root)
            raw = json.loads(config_path.read_text())
            raw["train"].update(epochs=1, batch_size=4, checkpoint_steps=1, gradient_checkpointing=False)
            raw["io"] = {"pin_memory": False}
            write_json(config_path, raw)
            # The production runner shares mechanics with B1. Only model download and text processing are substituted.
            with patch("peft.PeftModel.from_pretrained", side_effect=reload), \
                 patch("cxr_research.training.medgemma.load_model", side_effect=lambda *a, **k: (Tiny(), Processor())), \
                 patch("cxr_research.training.medgemma.VQACollator", Collator), \
                 patch("cxr_research.evaluation.m1.load_model", side_effect=lambda *a, **k: (Tiny(), Processor())), \
                 patch("cxr_research.evaluation.m1.VQACollator", Collator), \
                 patch("sys.path", [str(ROOT / "scripts/gpu"), *sys.path]):
                from run_m1 import main
                with patch("run_m1.print", side_effect=print_result), patch("run_m1.os.umask"), patch.dict("os.environ"):
                    self.assertEqual(main(["train", "--config", str(config_path)]), 0)
                    run_dir = Path(raw["output_root"]) / raw["run_id"]
                    self.assertTrue((run_dir / "latest_checkpoint.json").is_file())
                    adapter = run_dir / "adapter"
                    self.assertTrue((adapter / "artifact_hashes.json").is_file())
                    raw["run_id"] = "synthetic-m1-eval"
                    write_json(config_path, raw)
                    self.assertEqual(main(["evaluate", "--config", str(config_path), "--checkpoint", str(adapter)]), 0)
            metrics = json.loads((Path(raw["output_root"]) / raw["run_id"] / "metrics.json").read_text())
            self.assertEqual(metrics["chest_imagenome_silver"]["pointing_accuracy_at_1"], 1.0)
            self.assertEqual(metrics["timing"]["examples"], 1)


if __name__ == "__main__":
    unittest.main()
