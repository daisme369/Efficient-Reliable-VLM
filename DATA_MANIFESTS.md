# Local dataset manifests

Run the source code from `C:\Users\Root\Documents\Swinburne\Efficient-Reliable-VLM`. The data root remains `E:\Project\Efficient-Reliable-VLM\data`; restricted datasets and generated manifests stay there.

Build image, QA and annotation links from the release files already present on disk:

```powershell
Set-Location 'C:\Users\Root\Documents\Swinburne\Efficient-Reliable-VLM'
$env:PYTHONPATH = (Resolve-Path src).Path
.\.venv\Scripts\python.exe -m cxr_research catalog --data-root E:\Project\Efficient-Reliable-VLM\data --output E:\Project\Efficient-Reliable-VLM\data\manifests\next-snapshot --split-policy mimic-official
```

Set `PYTHONPATH` as above when running from the source checkout without installing the package. An installed package also exposes the `cxr-research` command. Choose a new output directory for each snapshot. The command refuses to overwrite an existing directory. All row-level files must stay in private, ignored data storage. The tool uses the standard library and does not download, move or duplicate images.

The delivered snapshot is `E:\Project\Efficient-Reliable-VLM\data\manifests\2026-10-19-v4\`, selected by `E:\Project\Efficient-Reliable-VLM\data\manifests\latest.json`. Its parent snapshots are retained for audit. Version 1 stopped on the NIH trailing header column. Version 2 exposed the Chest ImaGenome `_SceneGraph` filename suffix during coverage checks. Version 3 repairs those image keys and regenerates exports from the verified source index. Version 4 normalizes the gold-box `.dcm` IDs to JPG image IDs in the reference index. Every repair records its parent report and script hashes. Source sizes and modification times were checked before index reuse; unchanged JSONL exports were copied with SHA-256 verification.

The snapshot indexes 377,110 MIMIC images, 112,120 NIH images and 18,000 VinDr images. All indexed paths are present, with expected sizes checked where an AWS inventory exists. It maps all 377,391 VQA records to images, original-resolution anatomical masks and silver scene-graph references. Development exports contain 356,457 training QA and 2,705 validation QA. The test-patient union contains 789 patients. The gold-box index maps 25,989 source rows to MIMIC image keys.

`validation.json` records independent output fingerprint checks, development patient isolation, test sealing, deterministic image SHA-256 samples, CSV-reference roundtrips and scene-graph image/patient/study identity samples. Image checks are samples, not a full rehash of every asset or a clinical validation.

## Split decision

On 2026-10-19 the user selected the official MIMIC image split, retaining the VQA source split for audit. The source VQA release contains patients in multiple source splits, so the source QA split must not be used directly for training. The catalog audits the full official image population before filtering unavailable images.

For development exports, the union of source-VQA and official-MIMIC test patients is sealed. Any QA belonging to these patients has no copied question/answer payload in the catalog and cannot enter development exports. This keeps test gold separate even if its source split disagrees with the official image split. No test question/answer export is produced.

Catalog ingestion discovers the complete test-patient union before inserting any
QA payload. Rebuild snapshots created by the earlier insert-then-clear code into
a new private directory. Clearing a SQLite column does not guarantee removal of
its old bytes. Existing snapshots are not modified by this change.

## Outputs

- `catalog.sqlite`: indexed tables `images`, `qa`, `sources`, `refs`, `scene_graphs`, `memberships` and `sealed_patients`; views `image_links` and `vqa_links`.
- `images.jsonl`: namespaced image records, original splits, local presence/size checks, expected image SHA-256, anatomical-mask references and Chest ImaGenome scene-graph references.
- `vqa_links.jsonl`: every QA-to-image link, source QA/image splits, resolved official split, source record location and test-seal status. Questions and answers are omitted.
- `vqa.train.jsonl`, `vqa.validation.jsonl`: available development QA linked to the official split; questions, answer arrays, templates and semantic/content types remain as released. No answer normalization or primary-template filtering is invented.
- `sources.jsonl`: source versions, paths, checksums, record counts and available license-file checksums.
- `report.json`, `status.json`: aggregate joins, missing assets, mask dimension conflicts, patient audits, provenance fingerprints and readiness limitations. Only these aggregates should be shared.

The completed report records `catalog_sqlite_sha256` after closing the database.
QC verifies this digest against the pinned report before opening SQLite. A
snapshot without the digest requires rebuilding before QC.

SQLite retains per-row references for original-resolution CheXmask CSVs, VinDr annotations and Chest ImaGenome gold boxes. The reference is a byte offset and length, so no RLE or annotation file is duplicated. Chest ImaGenome silver graphs stay in their original ZIP. An available graph does not establish question-to-finding grounding.

## Reading linked data

Import the package reference readers, using an installed package or `PYTHONPATH=src`, then load a private development JSONL record from the E data root. Its `path` is relative to that data root. Its optional `anatomical_mask` and `scene_graph` can be read with:

```python
from cxr_research.data.source_refs import read_csv_reference, read_scene_graph

mask_row = read_csv_reference(data_root, sample["anatomical_mask"])
graph = read_scene_graph(data_root, sample["scene_graph"], sample["image_id"])
```

The helpers return restricted dictionaries for local processing. Do not print them or send them to remote APIs. The mask record exposes the original Left Lung, Right Lung and Heart RLE fields, dimensions and RCA. Decode with a separately verified coordinate/RLE implementation. Do not substitute anatomical masks for finding labels.

The SQLite `refs` table holds gold boxes and auxiliary finding annotations. Join `refs.source_id = sources.id` to obtain the source path and use its byte offset/length with `read_csv_reference`. Preserve source coordinate fields and annotators instead of mixing their conventions.

## Scope and remaining training gates

These files use schema `cxr-local-links-v1`. They are crosswalks and development exports. The `prepare-catalog` command converts their development QA to the canonical schema-3 manifest accepted by `cxr_research.data.manifest.load_manifest`.

Copy `configs/catalog-preparation.template.json` into private storage outside the C source checkout. Fill the policy fields and pin the SHA-256 of the selected snapshot's `report.json`. Choose a new `prepared_dir` inside the E data root, separate from the catalog snapshot. Then run:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.\.venv\Scripts\python.exe -m cxr_research prepare-catalog --spec E:\Project\Efficient-Reliable-VLM\data\catalog-preparation.json
```

The template deliberately contains `UNRESOLVED` values. Conversion requires recorded access/license and derivation reviews, input views, uncertain-label and template policies, primary template IDs, an explicit source field and mapping for closed/open answers, and answer rendering. `answer_type_field` selects `semantic_type` or `content_type`; `answer_type_values` maps each encountered source value to `closed` or `open`. `template_id_field` selects the unchanged `template_program` ID or the raw `template` string; the supplied template uses program IDs so surface-text variants can be reviewed together. `answer_rendering` is `single`, which rejects multi-answer arrays, or `join`, which requires an explicit `answer_separator`. Empty answer sets still require a rendering decision and cannot silently become a negative answer. An empty `primary_template_ids` list explicitly marks every QA as secondary.

The adapter verifies the pinned report and export checksums, audits all official MIMIC image splits before filtering, checks the union test seal and development row identities, and hashes selected image bytes against publisher checksums when available. It emits `manifest.jsonl`, `qa-review.jsonl`, `provenance.json` and `status.json`, then validates the manifest through the canonical loader. Provenance retains the catalog audit, export fingerprints, conversion specification and view-exclusion counts. Set `producer_commit` to the full current checkout SHA before conversion. The adapter checks it against Git and records the actual commit, dirty state and processing-module hashes. Use a rebuilt snapshot when the earlier ingestion code or missing SQLite digest applies.

The user-approved text policy now uses `normalization: cxr-text-nfc-space-v1`, `uncertain_label_policy: preserve_source_answers` and `review_policy: retain_and_flag`. NFC/whitespace changes preserve source meaning; clinical terms are not replaced. Review flags remain in a checksummed private sidecar and do not remove temporal/uncertain QA. See [the processing guide](DATA_PROCESSING.md) for the resumable `audit-catalog` command, auxiliary DICOM handling, mask QC and annotation geometry.

The VQA adapter covers MIMIC QA only. The separate `scripts/gpu/unet.py prepare`
command converts original-resolution MIMIC CheXmask references into binary lung
targets with their own schema-3 manifest and provenance. It preserves official
patient splits and the union test seal, validates source offsets/checksums and
records RCA/view/budget exclusions. See [the U-Net pipeline](configs/UNET_PIPELINE.md).
Chest ImaGenome, NIH and VinDr references remain separate; neither adapter
generates question-to-finding targets. The existing `prepare --spec` command is
still available for explicitly mapped source tables. Training requires an actual
reviewed protocol and the normal CLI checks.

The catalog does not choose primary template IDs, uncertain-label handling, answer rendering, calibration or an experiment protocol. Its report retains `training_ready: false`; successful schema conversion is a separate preparation step and does not approve a training protocol. Neither command performs training.

Source tables are hashed while indexed and checked against publisher SHA-256 where available. Images are checked for presence and expected size when an AWS inventory is available. Their publisher checksums are recorded, but individual image bytes are not rehashed by this tool. Keep source tables immutable after indexing; CSV byte offsets depend on exact source bytes.

VinDr-CXR and ChestX-ray14 remain separate namespaces with original source splits. The tool does not invent VinDr patient IDs or subdivide NIH `train_val`. Their CheXmask masks are linked for inventory/QC; their labels are not pooled into MIMIC VQA. CheXpert and PadChest masks remain unmapped when their original images are absent.

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/data -p "test_*.py" -v
```

Tests use synthetic identities and placeholder assets. They check split reconciliation, patient-level test sealing, missing-image filtering, identity conflicts, publisher checksum failures, deterministic exports, CSV multiline byte references and scene-graph identity checks. Adapter tests exercise CLI dispatch, canonical-loader acceptance, deterministic manifests, policy gates, input-view exclusions, checksums and test-patient injection rejection.

## Code ownership

- `src/cxr_research/data/catalog.py` owns SQLite storage, source fingerprints, aggregate split audit and exports.
- `src/cxr_research/data/catalog_sources.py` owns dataset release layouts and ingestion for MIMIC, VQA, CheXmask, Chest ImaGenome and auxiliary collections.
- `src/cxr_research/data/source_refs.py` owns CSV byte references and ZIP scene-graph reads.
- `src/cxr_research/data/catalog_adapter.py` owns explicit conversion to canonical VQA records, reusing `SplitAudit` and the canonical loader.
- `src/cxr_research/cli.py` dispatches both commands with lazy imports. Hashing and atomic JSON writes use the existing `utils` modules.

The former `scripts/data/build_manifests.py` entry point has been removed. Use the package CLI and normal Python imports; no script-path imports are needed. No dependencies were added.
