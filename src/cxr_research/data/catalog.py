"""Build a private SQLite catalog and audited development crosswalks."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
from cxr_research.data.catalog_sources import VERSIONS, mimic, auxiliary, annotations, questions
from cxr_research.data.source_refs import BinaryLines, csv_record
from cxr_research.utils.hashing import file_hash
from cxr_research.utils.io import write_json
from cxr_research.utils.paths import repository_root

class Catalog:
    def __init__(self, root, output, split_policy, repository):
        self.repository = repository.resolve()
        if root.resolve().is_relative_to(self.repository) or output.resolve().is_relative_to(self.repository):
            raise ValueError("Private catalog data must stay outside the source repository")
        if split_policy not in {"audit-only", "mimic-official"}:
            raise ValueError("Unsupported split policy")
        self.root, self.output, self.split_policy = root.resolve(), output.resolve(), split_policy
        if not self.output.is_relative_to(self.root) or self.output == self.root:
            raise ValueError("Output must be a new private directory inside data root")
        output.mkdir(parents=True, exist_ok=False)
        self.db = sqlite3.connect(output / "catalog.sqlite")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.executescript("""
        CREATE TABLE sources(id INTEGER PRIMARY KEY,path TEXT UNIQUE,version TEXT,
          sha256 TEXT,expected_sha256 TEXT,bytes INTEGER,mtime_ns INTEGER,rows INTEGER,
          license_path TEXT,license_sha256 TEXT);
        CREATE TABLE images(dataset TEXT,image_id TEXT,patient_id TEXT,study_id TEXT,
          path TEXT,source_split TEXT,view TEXT,height INTEGER,width INTEGER,
          present INTEGER,actual_bytes INTEGER,expected_bytes INTEGER,expected_sha256 TEXT,
          PRIMARY KEY(dataset,image_id));
        CREATE TABLE refs(dataset TEXT,image_id TEXT,kind TEXT,source_id INTEGER,
          row_index INTEGER,byte_offset INTEGER,byte_length INTEGER,height INTEGER,
          width INTEGER,quality REAL,FOREIGN KEY(source_id) REFERENCES sources(id));
        CREATE INDEX refs_image ON refs(dataset,image_id,kind);
        CREATE UNIQUE INDEX unique_original_mask ON refs(dataset,image_id) WHERE kind='anatomical_mask_original';
        CREATE TABLE scene_graphs(image_id TEXT PRIMARY KEY,source_id INTEGER,entry TEXT,
          FOREIGN KEY(source_id) REFERENCES sources(id));
        CREATE TABLE qa(source_id INTEGER,row_index INTEGER,image_id TEXT,patient_id TEXT,
          study_id TEXT,source_split TEXT,payload TEXT,
          FOREIGN KEY(source_id) REFERENCES sources(id));
        CREATE INDEX qa_image ON qa(image_id);
        CREATE TABLE memberships(patient_id TEXT,source TEXT,split TEXT,
          PRIMARY KEY(patient_id,source,split));
        CREATE TABLE sealed_patients(patient_id TEXT PRIMARY KEY);
        """)
        self.expected, self.sizes, self.sources = {}, {}, {}
        self.report = {"schema": "cxr-local-links-v1", "status": "building",
                       "split_policy": split_policy, "training_ready": False,
                       "created_at": datetime.now(timezone.utc).isoformat()}
        self.phase("initializing")


    def phase(self, name):
        self.db.commit()
        self.report["phase"] = name
        write_json(self.output / "status.json", self.report)
        print(json.dumps({"phase": name}), flush=True)


    def source(self, path):
        relative = path.relative_to(self.root).as_posix()
        if relative in self.sources:
            return self.sources[relative]
        group = relative.split("/")[0]
        base = self.root / group
        if VERSIONS.get(group) != "local-release":
            base /= VERSIONS[group]
        license_path = base / "LICENSE.txt"
        stat = path.stat()
        row = self.db.execute("INSERT INTO sources(path,version,expected_sha256,bytes,mtime_ns,license_path,license_sha256) VALUES(?,?,?,?,?,?,?)",
                              (relative, VERSIONS.get(group), self.expected.get(relative), stat.st_size,
                               stat.st_mtime_ns, license_path.relative_to(self.root).as_posix() if license_path.is_file() else None,
                               file_hash(license_path) if license_path.is_file() else None))
        self.sources[relative] = row.lastrowid
        return row.lastrowid


    def finish_source(self, path, sha, count):
        source = self.source(path)
        stat = path.stat()
        previous = self.db.execute("SELECT bytes,mtime_ns,expected_sha256 FROM sources WHERE id=?", (source,)).fetchone()
        if (stat.st_size, stat.st_mtime_ns) != previous[:2]:
            raise ValueError("Source changed during indexing")
        if previous[2] and previous[2].lower() != sha.lower():
            raise ValueError("Source checksum mismatch")
        self.db.execute("UPDATE sources SET sha256=?,rows=? WHERE id=?", (sha, count, source))


    def csv_rows(self, path):
        csv.field_size_limit(100_000_000)
        self.source(path)
        if path.suffix == ".gz":
            count = 0
            with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
                for count, row in enumerate(csv.DictReader(stream), 1):
                    yield count - 1, None, None, row
            self.finish_source(path, file_hash(path), count)
        else:
            with path.open("rb") as stream:
                lines = BinaryLines(stream)
                reader = csv.reader(lines, strict=True)
                header = next(reader)
                count = 0
                while True:
                    offset = lines.offset
                    try:
                        values = next(reader)
                    except StopIteration:
                        break
                    yield count, offset, lines.offset - offset, csv_record(header, values)
                    count += 1
                self.finish_source(path, lines.sha.hexdigest(), count)


    def checksums(self):
        self.phase("publisher_checksums")
        for slug, version in VERSIONS.items():
            base = self.root / slug / version
            path = base / "SHA256SUMS.txt"
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                sha, name = line.split(maxsplit=1)
                if not re.fullmatch(r"[A-Fa-f0-9]{64}", sha):
                    raise ValueError("Invalid publisher checksum manifest")
                name = name.lstrip("*")
                if name.startswith("./"):
                    name = name[2:]
                parts = PurePosixPath(name)
                if parts.is_absolute() or ".." in parts.parts or "\\" in name or ":" in name:
                    raise ValueError("Unsafe publisher checksum path")
                self.expected[(base / name).relative_to(self.root).as_posix()] = sha
            self.source(path)
            self.finish_source(path, file_hash(path), len(path.read_text().splitlines()))
        # Inventory is only a source of expected sizes, never proof of downloaded bytes.
        for path in (self.root / "_setup").glob("aws-inventory-*.json"):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("IsTruncated") or raw.get("NextContinuationToken"):
                continue
            for item in raw.get("Contents", []):
                key = item["Key"]
                if len(PurePosixPath(key).parts) >= 3 and key.split("/")[0] in VERSIONS:
                    self.sizes[key] = item["Size"]


    def add_image(self, dataset, image_id, patient, study, relative, split, view="", height=None, width=None):
        # Paths are generated from validated source keys, not accepted from QA payloads.
        if not image_id or any(c in image_id for c in "/\\:") or image_id in {".", ".."}:
            raise ValueError("Invalid image key")
        try:
            size = (self.root / relative).stat().st_size
        except FileNotFoundError:
            size = None
        expected = self.sizes.get(relative)
        present = size is not None and (expected is None or size == expected)
        self.db.execute("INSERT INTO images VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (dataset, image_id, patient, study, relative, split, view, height, width,
                         int(present), size, expected, self.expected.get(relative)))


    def csv_refs(self, path, dataset, image_column, kind):
        source = self.source(path)
        for index, offset, length, row in self.csv_rows(path):
            h, w, quality = None, None, None
            if kind == "anatomical_mask_original":
                h, w, quality = int(row["Height"]), int(row["Width"]), float(row["Dice RCA (Mean)"])
                if min(h, w) <= 0 or not math.isfinite(quality) or not 0 <= quality <= 1:
                    raise ValueError("Invalid anatomical mask metadata")
                if not all(c in row for c in ["Left Lung", "Right Lung", "Heart"]):
                    raise ValueError("Missing anatomical mask fields")
            image_id = row[image_column]
            if kind == "chest_imagenome_gold_boxes":
                image_id = PurePosixPath(image_id).stem
            self.db.execute("INSERT INTO refs VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (dataset, image_id, kind, source, index, offset, length, h, w, quality))


    def finish(self):
        self.phase("audit_and_export")
        one = lambda sql: self.db.execute(sql).fetchone()[0]
        official_overlap = one("SELECT COUNT(*) FROM (SELECT patient_id FROM memberships WHERE source='mimic' GROUP BY patient_id HAVING COUNT(*)>1)")
        qa_overlap = one("SELECT COUNT(*) FROM (SELECT patient_id FROM memberships WHERE source='qa' GROUP BY patient_id HAVING COUNT(*)>1)")
        identity_errors = one("SELECT COUNT(*) FROM qa q LEFT JOIN images i ON i.dataset='mimic-cxr' AND i.image_id=q.image_id WHERE i.image_id IS NULL OR i.patient_id!=q.patient_id OR i.study_id!=q.study_id")
        split_conflicts = one("SELECT COUNT(*) FROM qa q JOIN images i ON i.dataset='mimic-cxr' AND i.image_id=q.image_id WHERE q.source_split!=i.source_split")
        if official_overlap or identity_errors:
            raise ValueError("Official patient split or image identity audit failed")
        self.db.executescript("""
        DROP VIEW IF EXISTS vqa_links;
        DROP VIEW IF EXISTS image_links;
        CREATE VIEW image_links AS SELECT i.*,s.path AS mask_source_path,r.byte_offset AS mask_byte_offset,
          r.byte_length AS mask_byte_length,r.row_index AS mask_row_index,
          r.height AS mask_height,r.width AS mask_width,r.quality AS mask_quality,
          gs.path AS graph_archive,g.entry AS graph_entry
        FROM images i LEFT JOIN refs r ON r.dataset=i.dataset AND r.image_id=i.image_id AND r.kind='anatomical_mask_original'
        LEFT JOIN sources s ON s.id=r.source_id
        LEFT JOIN scene_graphs g ON i.dataset='mimic-cxr' AND g.image_id=i.image_id
        LEFT JOIN sources gs ON gs.id=g.source_id;
        CREATE VIEW vqa_links AS SELECT q.source_id,q.row_index,q.image_id,q.patient_id,q.study_id,
          qs.path AS qa_source_path,
          q.source_split AS qa_source_split,i.source_split AS mimic_source_split,
          i.path,i.present,i.expected_sha256,
          i.mask_source_path,i.mask_byte_offset,i.mask_byte_length,i.mask_row_index,i.mask_height,i.mask_width,i.mask_quality,
          i.graph_archive,i.graph_entry,
          CASE WHEN p.patient_id IS NULL THEN 0 ELSE 1 END AS test_sealed,
          CASE WHEN q.source_split=i.source_split THEN 1 ELSE 0 END AS split_agrees,
          CASE WHEN g.image_id IS NULL THEN 0 ELSE 1 END AS has_scene_graph,
          EXISTS(SELECT 1 FROM refs r WHERE r.dataset='mimic-cxr' AND r.image_id=q.image_id AND r.kind='anatomical_mask_original') AS has_anatomical_mask,
          q.payload
        FROM qa q JOIN image_links i ON i.dataset='mimic-cxr' AND i.image_id=q.image_id
        JOIN sources qs ON qs.id=q.source_id
        LEFT JOIN sealed_patients p ON p.patient_id=q.patient_id
        LEFT JOIN scene_graphs g ON g.image_id=q.image_id;
        """)
        self.db.row_factory = sqlite3.Row
        def export(query, path, transform=lambda x: x):
            count = 0
            with path.open("w", encoding="utf-8", newline="\n") as stream:
                for row in self.db.execute(query):
                    stream.write(json.dumps(transform(dict(row)), ensure_ascii=False, sort_keys=True) + "\n")
                    count += 1
            return count
        def references(row):
            path = row.pop("mask_source_path")
            mask = {key: row.pop("mask_" + key) for key in
                    ["byte_offset", "byte_length", "row_index", "height", "width", "quality"]}
            row["anatomical_mask"] = {"path": path, **mask, "role": "anatomical_qc"} if path else None
            archive, entry = row.pop("graph_archive"), row.pop("graph_entry")
            row["scene_graph"] = {"archive": archive, "entry": entry} if archive else None
            return row
        export("SELECT * FROM image_links ORDER BY dataset,image_id", self.output / "images.jsonl", references)
        export("SELECT * FROM sources ORDER BY id", self.output / "sources.jsonl")
        def crosswalk(row):
            row.pop("payload")
            row["resolved_split"] = row["mimic_source_split"] if self.split_policy == "mimic-official" else None
            return references(row)
        export("SELECT * FROM vqa_links ORDER BY source_id,row_index", self.output / "vqa_links.jsonl", crosswalk)
        dev_counts = {}
        if self.split_policy == "mimic-official":
            for split in ["train", "validation"]:
                def development(row):
                    payload = json.loads(row.pop("payload"))
                    row.update(payload)
                    row["split"] = row["mimic_source_split"]
                    row["dataset"] = "mimic-cxr"
                    row["schema"] = "cxr-local-links-v1"
                    return references(row)
                dev_counts[split] = export(f"SELECT * FROM vqa_links WHERE mimic_source_split='{split}' AND present=1 AND test_sealed=0 AND payload IS NOT NULL ORDER BY source_id,row_index",
                                           self.output / f"vqa.{split}.jsonl", development)
        assets = {r["dataset"]: dict(r) for r in self.db.execute("SELECT dataset,COUNT(*) AS indexed_images,SUM(present) AS local_images,SUM(CASE WHEN present=0 THEN 1 ELSE 0 END) AS missing_or_wrong_size FROM images GROUP BY dataset")}
        self.report.update(status="complete", phase="complete", source_count=one("SELECT COUNT(*) FROM sources"),
                           datasets=assets, qa_rows=one("SELECT COUNT(*) FROM qa"), qa_source_patient_overlaps=qa_overlap,
                           official_patient_overlaps=official_overlap, qa_source_split_conflicts=split_conflicts,
                           identity_conflicts=identity_errors, sealed_test_patients=one("SELECT COUNT(*) FROM sealed_patients"),
                           qa_missing_local_image=one("SELECT COUNT(*) FROM vqa_links WHERE present=0"),
                           qa_with_scene_graph=one("SELECT COUNT(*) FROM vqa_links WHERE has_scene_graph=1"),
                           qa_with_anatomical_mask=one("SELECT COUNT(*) FROM vqa_links WHERE has_anatomical_mask=1"),
                           qa_with_gold_boxes=one("SELECT COUNT(*) FROM qa q WHERE EXISTS(SELECT 1 FROM refs r WHERE r.dataset='mimic-cxr' AND r.kind='chest_imagenome_gold_boxes' AND r.image_id=q.image_id)"),
                           development_rows=dev_counts,
                           blocked_reasons=["Primary template eligibility and uncertain-label policy are not locked",
                             "Image expected SHA256 is recorded; individual image bytes were not rehashed by this catalog",
                             "This crosswalk schema requires an explicit adapter to the training manifest schema",
                             "VinDr has no patient identity in its annotation CSVs; NIH train_val is not subdivided"],
                           integrity={"source_tables": "sha256_computed_and_compared_when_publisher_hash_available",
                                      "images": "presence_and_inventory_size_checked_expected_sha256_recorded"},
                           auxiliary_sources_not_pooled=True,
                           chexmask_unmapped_original_collections=["CheXpert", "PadChest"],
                           test_policy="Seal the union of official-MIMIC and source-VQA test patients; no test QA payload exported")
        self.report["anatomical_mask_mapping"] = [dict(row) for row in self.db.execute("""
          SELECT r.dataset,COUNT(*) AS mask_rows,SUM(i.image_id IS NOT NULL) AS indexed_image_joins,
          SUM(COALESCE(i.present,0)) AS local_image_joins,
          SUM(i.height IS NOT NULL AND (i.height!=r.height OR i.width!=r.width)) AS dimension_conflicts
          FROM refs r LEFT JOIN images i ON i.dataset=r.dataset AND i.image_id=r.image_id
          WHERE r.kind='anatomical_mask_original' GROUP BY r.dataset ORDER BY r.dataset
        """)]
        repository = self.repository
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repository, capture_output=True, text=True)
        self.report["producer_commit"] = commit.stdout.strip() if commit.returncode == 0 else None
        self.report["producer_dirty"] = bool(dirty.stdout.strip()) if dirty.returncode == 0 else None
        exports = ["images.jsonl", "sources.jsonl", "vqa_links.jsonl"] + [f"vqa.{s}.jsonl" for s in dev_counts]
        self.report["output_sha256"] = {name: file_hash(self.output / name) for name in exports}
        modules = [Path(__file__), Path(__file__).with_name("catalog_sources.py"),
                   Path(__file__).with_name("source_refs.py")]
        self.report["producer_modules_sha256"] = {p.name: file_hash(p) for p in modules}
        self.db.commit()
        self.db.close()
        self.report["catalog_sqlite_sha256"] = file_hash(self.output / "catalog.sqlite")
        write_json(self.output / "report.json", self.report)
        write_json(self.output / "status.json", self.report)
        return self.report



def build(root, output, split_policy="audit-only", *, repository=None):
    catalog = Catalog(Path(root), Path(output), split_policy, repository_root(repository))
    try:
        catalog.checksums()
        mimic(catalog)
        auxiliary(catalog)
        annotations(catalog)
        questions(catalog)
        return catalog.finish()
    except BaseException as exc:
        catalog.db.rollback()
        catalog.db.close()
        catalog.report.update(status="failed", error_type=type(exc).__name__)
        write_json(catalog.output / "status.json", catalog.report)
        raise
