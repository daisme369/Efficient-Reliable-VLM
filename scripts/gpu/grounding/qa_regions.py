"""Audit QA grounding eligibility using development payloads and held IDs only."""
from collections import defaultdict
import hashlib
import json
import re
import sqlite3
from zipfile import ZipFile

from cxr_research.preprocessing.annotations import scene_regions
from cxr_research.preprocessing.labels import normalize_text, review_cues
from cxr_research.utils.errors import ContractError

SILVER = "imagenome-silver-scene"
SPLITS = ("train", "validation")
CUES = {"negation_cue", "uncertainty_cue", "prior_study_cue"}
LATERALITY = re.compile(r"\b(?:left|right|bilateral|unilateral)\b")


def rows(path):
    with path.open(encoding="utf-8") as file:
        for line in file:
            yield json.loads(line)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def counts(items):
    return {"questions": items[0], "images": len(items[1]), "patients": len(items[2])}


def token(value):
    return normalize_text(value).casefold()


def sides(value):
    return set(LATERALITY.findall(value))


def qa_target(q):
    answers = [token(a) for a in q["answer"]]
    args = q["template_arguments"]
    signature = tuple(len(args[k]) for k in ("attribute", "object", "category"))
    program = q["template_program"], q["semantic_type"], q["content_type"], signature
    shapes = {
        ("program_2", "verify", "presence", (1, 1, 0)): "verify_region",
        ("program_22", "verify", "presence", (1, 0, 0)): "verify_image",
        ("program_31", "query", "anatomy", (1, 0, 0)): "query_region",
        ("program_8", "query", "abnormality", (0, 1, 0)): "query_finding",
    }
    if len(answers) != 1 or not answers[0]:
        return ("missing_answer" if not any(answers) else "multiple_answers"), None
    shape = shapes.get(program)
    if shape is None:
        return "unsupported_program_or_multiple_targets", None
    answer, = answers
    if CUES.intersection(review_cues(q["question"], [])):
        return "polarity_or_temporal_review", None
    if shape.startswith("verify"):
        if answer == "no":
            return "source_negative_review", None
        if answer != "yes":
            return "polarity_or_temporal_review", None
    elif CUES.intersection(review_cues("", [answer])):
        return "polarity_or_temporal_review", None

    region = answer if shape == "query_region" else token(next(iter(args["object"].values()), ""))
    finding = answer if shape == "query_finding" else token(next(iter(args["attribute"].values())))
    region_sides = sides(region)
    query_sides = sides(token(q["question"]))
    if len(region_sides) > 1 or region_sides & {"bilateral", "unilateral"}:
        return "laterality_review", None
    if query_sides and query_sides != region_sides:
        return "laterality_review", None
    if not finding or sides(finding):
        return "finding_scope_review", None
    return "eligible_target", (finding, region)


def chest_target(target, regions):
    finding, name = target
    if not name:
        return "no_explicit_region", []
    region = regions.get(name)
    if region is None:
        return "no_exact_region", []
    states = {
        attr["state"] for attr in region["attributes"]
        if token(attr["label"]) == finding and token(attr["category"]) in {"disease", "anatomicalfinding"}
    }
    if states != {"positive"}:
        return ("conflicting_finding_polarity" if len(states) > 1 else "no_explicit_positive_finding"), []
    if region["xyxy"] is None:
        return "invalid_or_missing_box", []
    return "candidate", [name]


def self_check():
    question = {"template_program": "program_2", "semantic_type": "verify", "content_type": "presence",
                "template_arguments": {"attribute": {"0": "Pleural effusion"},
                                       "object": {"0": "left lung"}, "category": {}},
                "question": "Is pleural effusion visible in the left lung?", "answer": ["yes"]}
    target = ("pleural effusion", "left lung")
    assert qa_target(question) == ("eligible_target", target)
    assert qa_target(question | {"question": "Is pleural effusion visible in the right lung?"})[0] == "laterality_review"
    assert qa_target(question | {"answer": ["no"]})[0] == "source_negative_review"
    assert qa_target(question | {"answer": ["yes", "no"]})[0] == "multiple_answers"
    attr = {"category": "anatomicalfinding", "label": "pleural effusion", "state": "positive"}
    region = {"xyxy": [0, 0, 1, 1], "attributes": [attr]}
    assert chest_target(target, {"right lung": region}) == ("no_exact_region", [])
    assert chest_target(target, {"left lung": region}) == ("candidate", ["left lung"])
    assert chest_target(target, {"left lung": region | {"xyxy": None}}) == ("invalid_or_missing_box", [])
    assert chest_target(target, {"left lung": region | {"attributes": [attr, attr | {"state": "negative"}]}})[0] == "conflicting_finding_polarity"



def build_candidates(root, catalog, mapping, output, catalog_report):
    self_check()
    output.mkdir()
    with sqlite3.connect(mapping.as_uri() + "?mode=ro", uri=True) as db:
        references = {iid: (entry, patient, study, split, width, height)
                      for iid, entry, patient, study, split, width, height in db.execute(
            """SELECT image_id,entry,patient_id,study_id,mimic_split,width,height
               FROM development_references WHERE source=?""", (SILVER,))}
        held = {p for p, in db.execute("SELECT DISTINCT patient_id FROM holds")}
    groups = defaultdict(list)
    for split in SPLITS:
        name = f"vqa.{split}.jsonl"
        assert digest(catalog / name) == catalog_report["output_sha256"][name]
        for row in rows(catalog / name):
            assert row["test_sealed"] == 0 and row["split"] == split
            iid, patient, study = row["image_id"], row["patient_id"], row["study_id"]
            if iid not in references or not row["split_agrees"]:
                continue
            _, p, st, sp, _, _ = references[iid]
            assert (patient, study, split) == (p, st, sp) and patient not in held
            _, target = qa_target(row)
            if target and target[1]:
                groups[iid].append((row["source_id"], row["row_index"], target))

    path = output / "candidate_links.jsonl"
    archive = root / "chest-imagenome/1.0.0/silver_dataset/scene_graph.zip"
    malformed = count = 0
    with ZipFile(archive) as source, path.open("x", encoding="utf-8", newline="\n") as stream:
        for iid, requests in sorted(groups.items()):
            entry, p, st, split, width, height = references[iid]
            graph = json.loads(source.read(entry))
            assert (graph["image_id"], str(graph["patient_id"]), str(graph["study_id"])) == (iid, p, st)
            try:
                regions = {token(r["region"]): r for r in scene_regions(graph, width=width, height=height, strict=False)}
            except ContractError:
                malformed += len(requests)
                continue
            for source_id, row_index, target in requests:
                status, region = chest_target(target, regions)
                if status == "candidate":
                    stream.write(json.dumps(dict(source=SILVER, vqa_source_id=source_id,
                                                 vqa_row_index=row_index, image_id=iid,
                                                 split=split, references=region)) + "\n")
                    count += 1
    (output / "report.json").write_text(json.dumps({
        "candidate_qa": count, "malformed_scene_qa": malformed,
        "inputs_sha256": {"mapping.sqlite": digest(mapping)},
        "outputs_sha256": {"candidate_links.jsonl": digest(path)},
    }, sort_keys=True) + "\n", encoding="utf-8")
