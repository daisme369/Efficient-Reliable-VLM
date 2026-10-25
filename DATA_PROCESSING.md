# Dataset processing and review

The current data path is: immutable release files, audited catalog, private QA/QC outputs, then a policy-gated canonical training manifest. All row-level outputs stay under `E:\Project\Efficient-Reliable-VLM\data`. Source code runs from the C checkout.

## Decisions recorded on 2026-10-19

The user selected these processing rules in this chat:

- Normalize Unicode and whitespace while preserving the meaning of source answers. Do not turn uncertain answers into negatives.
- Retain questions that may depend on prior studies and templates needing eligibility review. Attach review flags instead of excluding them automatically.
- Keep the previously selected official MIMIC split and the union of source-VQA and official-MIMIC test patients sealed.

These choices authorize mechanical preprocessing and local review. They do not establish clinical correctness, approve primary template IDs, or select a training protocol.

## Text and labels

`preprocessing/labels.py` defines `cxr-text-nfc-space-v1`. It applies NFC normalization and collapses whitespace. Case, punctuation, compatibility characters, negation, laterality, severity words and uncertainty language remain unchanged. Original text remains in the source snapshot. Evaluation's existing casefold/whitespace scoring is unchanged.

`cxr-review-cues-v1` detects lexical cues for negation, laterality, degree, uncertainty and prior-study references. A cue is a review candidate, not a scoped clinical NLP result. For example, a match for "no" does not label a disease negative, and "stable" is not proof that a question requires another study. There is no synonym substitution or automatic diagnosis.

`label_state` accepts an explicit source-token mapping to `positive`, `negative`, `uncertain` or `missing`. Null, empty and unknown source tokens are distinct. An unmapped token fails rather than silently becoming negative. This utility does not harmonize different datasets' disease vocabularies or merge different radiologists' judgments.

The VQA release explicitly permits empty answer arrays when a `choose` or `query` program has no result. The audit preserves `[]` with an `empty_answer_array` flag. It also preserves other incomplete source targets with review flags. `supervision_valid: false` means the current nonempty-string training adapter cannot consume that target; it does not establish that the source annotation is clinically wrong. Scalar rendering of an empty answer set needs an explicit protocol decision. See the [release's answer schema](https://physionet.org/content/mimic-ext-mimic-cxr-vqa/1.0.0/).

`prepare-catalog` now requires `normalization`, `uncertain_label_policy: preserve_source_answers` and `review_policy: retain_and_flag`. It normalizes the question and each answer before the approved answer rendering, and writes `qa-review.jsonl` separately from canonical records. It records review counts, the sidecar checksum and processing versions in provenance. All existing schema, checksum and test-seal gates still apply.

## Run private QC

Copy `configs/catalog-qc.template.json` outside the source repository. Pin the SHA-256 of the selected catalog's `report.json` and choose a new `output_dir` under `data_root/qc/<run>`. The QC root itself and raw release directories are rejected before any lock or output is created. The supplied template checks 32 image IDs in lexical order per dataset. Set `image_limit_per_dataset` to `-1` only when a full image scan is intended. This changes runtime substantially; a sample pass does not validate the whole dataset.

```powershell
Set-Location 'C:\Users\Root\Documents\Swinburne\Efficient-Reliable-VLM'
$env:PYTHONPATH = (Resolve-Path src).Path
.\.venv\Scripts\python.exe -m cxr_research audit-catalog --spec E:\Project\Efficient-Reliable-VLM\data\catalog-qc-2026-10-19.json
```

The audit processes all development QA, independently checks image/patient/study identities and the test seal, and never opens source test QA payloads. It verifies the SQLite digest published by the pinned catalog report, snapshot export fingerprints and source sizes/mtimes before using byte offsets and ZIP references. Image checks cover decoding, dimensions, source modes, SHA-256 and publisher checksum comparison when available. Mask checks decode left lung, right lung and heart separately, check dimensions and RCA range, and report empty masks and left/right lung overlap. It does not set an RCA exclusion threshold or claim segmentation accuracy.

Outputs include:

- `vqa.processed.train.jsonl` and `vqa.processed.validation.jsonl`: private intermediate QA with normalized text, processing versions and review flags. These are not schema-3 training manifests.
- `template-review.jsonl`: private template inventory with development split/type counts and unreviewed eligibility.
- `program-review.jsonl`: private source-program inventory grouping text variants under stable `template_program` IDs, with per-program empty-answer counts. This supports primary-template review without assigning eligibility automatically.
- `qa-audit.sqlite`: private exact-text-pair index. Recurring question/answer language across patients is counted and retained; it is not by itself evidence of image leakage.
- `image-audit.sqlite`: private per-image results and exact/perceptual fingerprints. Equal difference hashes are review candidates, not confirmed duplicates. No rows are deleted automatically.
- `checkpoint.json`, `status.json` and `report.json`: resume identity, progress and aggregate results. Audit completion does not imply that every image check passed or that training is approved.

Rerun the same command to resume. Completed QA outputs must retain their checksums. Changed source/specification fingerprints require a new output directory. Image rows whose file size or mtime changes are checked again. A process lock prevents simultaneous writers to one QC directory. A stopped QA phase is regenerated on retry; completed image rows are reused.

Completed QA phases record their processing-code fingerprints and reject changed QA code on resume. Image records are rechecked when their image/annotation decoder code changes. The final report records producer commit, dirty state and module hashes. A private execution record captured the original QA phase while running; its unchanged fingerprints were carried into the first QC checkpoint before the image parser refinement.

## CXR-specific image and annotation handling

`preprocessing/dicom.py` handles single-frame grayscale auxiliary DICOM images. The caller must choose whether to apply the first DICOM VOI view. Processing applies modality LUT/rescale first, then optional VOI, excludes pixel-padding values from display scaling and handles MONOCHROME1 polarity. It never rotates, flips or crops. Unsupported presentation LUTs, multiframe/color images, missing decoders and invalid pixel ranges fail explicitly. The uint8 result is a display/QC representation; original DICOM bytes are preserved, and the primary MIMIC JPG input policy is unchanged. The order follows the [pydicom pixel-processing documentation](https://pydicom.github.io/pydicom/stable/reference/generated/pydicom.pixels.apply_voi_lut.html).

`preprocessing/annotations.py` validates `xyxy` boxes in original image coordinates and provides reversible letterbox mapping using the image transform's rounded dimensions and padding. It does not silently clip invalid boxes. Chest ImaGenome parsing uses `original_*` coordinates and explicit `yes`/`no` attribute relations, retaining their anatomical region and source. It omits report phrases from parsed evidence. This follows the [release's scene-graph schema](https://physionet.org/content/chest-imagenome/1.0.0/).

Strict parsing rejects invalid or unlocalized geometry. QC uses review mode, retaining attributes with `xyxy: null` and `box_status: invalid_source_box` or `unlocalized_attribute`. These records cannot be treated as localized grounding targets. An out-of-bounds region is counted instead of causing all other regions in its graph to be discarded. No box is invented or clipped. Raster QC accepts fully opaque RGBA PNGs by dropping only the opaque alpha channel; nonopaque alpha and unsupported bit depths require a separate conversion policy.

VinDr annotation references and radiologist identities remain separate in the catalog. Its independent annotator labels are not converted into majority-vote targets; the [release describes different training and test annotation protocols](https://physionet.org/content/vindr-cxr/1.0.0/). Anatomical CheXmask masks are not substituted for finding-grounding targets. The [CheXmask data dictionary](https://physionet.org/content/chexmask-cxr-segmentation-data/1.0.0/DATA_DICTIONARY.md) defines the three organ RLE fields and RCA metadata.

## Remaining gates

For the GPU pilot, the user delegated the remaining input/rendering choices on
2026-10-19. The selected policy keeps the released QA-linked AP/PA image, maps
`verify`/`choose` to closed and `query` to open using the release schema, joins
multiple answers in source order with `; `, and explicitly excludes empty answer
sets from supervision. Empty sets remain in the catalog/QC and receive an
exclusion entry in the preparation sidecar. They never become a negative answer.
Set `empty_answer_policy: exclude_from_supervision` to enable that exclusion;
without it, empty source targets still stop conversion. These are pilot data
choices, not a clinical review or an approved final evaluation protocol.

Primary flags remain false pending eligibility review. The full secondary
development population can be prepared without asserting that every template is
answerable from one image. See [the rental procedure](docker/RENTAL.md) for draft
configuration generation and the container bootstrap command.

Final benchmark review still needs to resolve primary-template eligibility and confirm the pilot input/taxonomy policies for the main comparisons. Source access at the intended compute location and any RCA selection threshold remain separate gates. Finding-linked QA targets require their own explicit question-to-region mapping and validation. No eligibility choices are inferred from lexical cues. No full real-dataset clinical validation or training is claimed by these utilities.

Use the source `template_program` as `template_id_field` when reviewing eligibility at the program level. The VQA release associates each program with its answer-generation logic. The inventory preserves every surface template separately within that program; it does not choose which programs can be answered from images. Do not treat all unreviewed programs as primary merely because the QA was retained.

Synthetic tests cover NFC preservation, clinical-word distinctions, four-state labels, cue retention, test sealing, DICOM rescale/padding/polarity, original box coordinates, scene-graph negation, QC output checksums and resume behavior.
