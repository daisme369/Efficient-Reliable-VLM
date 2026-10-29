# DOMAIN-NOTES: Chest X-ray VQA, Grounding, and Selective Reliability

Status: DRAFT v0.1. Entries tagged `PROPOSED` need Research Lead and, when clinical judgment is involved, clinical reviewer approval before the team treats them as decisions.

Read this file when the [agent routing table](../../AGENTS-INSTRUCTIONS.md#read-next) selects it. The approved dataset and benchmark choices are in `DETAILED_ACTION_PLAN.md`.

Authority: this draft is subordinate to `AGENTS-INSTRUCTIONS.md`, its policy modules, and approved decision records. If they conflict, stop at the affected decision and report the conflict.

---

## 0. How to use this file

### Status tags

| Tag | Meaning | What the agent does |
| --- | --- | --- |
| `POLICY` | Restated from an approved policy, plan, or decision record. | Binding. |
| `PROPOSED` | Candidate rule for this domain. | Use for reversible exploration only. Obtain approval before it locks a protocol or changes research scope. |
| `OPEN` | Not decided. | Do not choose. Stop, list it in the report, and work only on unblocked parts. |
| `VERIFY` | Factual statement from general knowledge, not yet confirmed against an official source. | Do not rely on it, cite it, or put it in a paper until a human confirms it against the official source and replaces the tag with a link. |

### Limits of the agent in this domain

- You are not a clinical authority. Never judge clinical correctness, severity, or significance yourself.
- You may compute aggregates, pre-sort items into candidate categories, and flag cases for the clinical reviewer. You may not assign a final clinical label.
- Never view, print, paste, or summarize real images, reports, QA rows, or identifiers in chat, logs, issues, or commit messages. Use synthetic fixtures for tests and debugging.
- To investigate a failure on real data, use aggregate statistics or hashed/anonymized keys. If a human must inspect rows, write them only to an access-controlled, untracked local path that the human names. Never to the repository, shared artifacts, or an external tool.
- If you do not know a domain fact (dataset field, model card statement, annotation protocol), say so and read the official source. Do not answer from memory.

### Entry format

```text
### Dn. Title
Status:   POLICY | PROPOSED | OPEN | VERIFY
Rule:     what must or must not happen
Why:      the failure this prevents
Check:    how it is verified (test, validator, report field)
Stop if:  condition that requires stopping and asking
Source:   link to data card, decision record, or primary paper (fill when known)
```

---

## D1. Dataset provenance

**Status:** POLICY (scope and recording duties). Specifics `OPEN`.

- **Rule:** Scope is chest X-ray and the approved datasets in `DETAILED_ACTION_PLAN.md` §2.3. Record dataset name, version, license/access terms, checksum or manifest, exclusions, and uncertain-label policy ([data policy](DATA-PRIVACY.md#6-data-governance-and-privacy)).
- **Rule (manifest fields):** For every dataset or derivative used, the manifest must contain: source and version; file checksums; row counts before and after each exclusion step, with the reason for each exclusion; the commit of the script that produced the derivative; the access/license statement; and the date.
- **Rule (derived datasets):** For every derived VQA or grounding dataset, record who produced it, how (template, LLM-generated, manual), which report section it was derived from, and what filtering was applied. Do not assume derived labels were clinically verified.
- **Why:** Derived QA from reports inherits report noise and generator bias. Without provenance, error analysis cannot separate model error from label error.
- **Check:** A manifest validator rejects a dataset entry with missing fields.
- **Stop if:** Access or license permission is unclear, or a derivative's terms are unknown.
- **POLICY (D1-a):** The plan selects MIMIC-CXR-JPG v2.1.0 images, MIMIC-Ext-MIMIC-CXR-VQA v1.0.0 questions and answers, and Chest ImaGenome v1.0.0 finding-linked regions. CheXmask v1.0.0 is an anatomical mask and quality-control source only. Verify separate access terms, versions, and image joins before use.
- **VERIFY (D1-b):** Candidate derivatives mentioned in the literature include Medical-Diff-VQA, MIMIC-CXR-VQA, and Medical-CXR-VQA. Confirm names, versions, splits, license terms, and generation method from each dataset's paper and release page before use.
- **OPEN (D1-c):** Linking MIMIC-CXR to other MIMIC modules (for example demographic tables from MIMIC-IV) requires separate access and an approval record. Linkage can raise re-identification risk. Do not join without approval.

## D2. Splits and unit of analysis

**Status:** POLICY (patient-level isolation). Specifics `PROPOSED` / `OPEN`.

- **POLICY:** Train/validation/test are isolated at the patient level. Validate before training or evaluation.
- **PROPOSED:** The unit for splitting, deduplication grouping, and bootstrap is the patient (`subject_id`). Studies and QA items from the same patient are correlated and must never straddle a split.
- **PROPOSED (derived-split reconciliation):** A derived dataset may ship its own train/val/test split. Compare it against the locked patient split. If any patient appears in different splits across the two, stop. Do not silently use either one. Derived splits are a common source of leakage relative to the official one.
- **OPEN (D2-a):** Use the official MIMIC-CXR split or a project-specific split. `VERIFY:` the official split file is patient-level. Record the decision in the dataset decision record.
- **OPEN (D2-b):** Whether a separate calibration split exists. If the same validation split is used for early stopping, hyperparameter selection, calibration fitting, and threshold selection, validation-set ECE and risk estimates are optimistic. State the choice explicitly. If reuse is chosen, mark validation-based reliability numbers as optimistic.
- **PROPOSED (test seal):** The test set is evaluated once per locked configuration. Test-set error analysis happens after the lock. If any finding from test-set analysis changes the method, prompt, threshold, or metric, report the result as post-hoc and re-evaluate on data not used in that analysis.
- **PROPOSED:** Date fields are shifted in MIMIC. Do not build temporal splits or use dates as features without approval.
- **Check:** A split-leakage test asserts pairwise-disjoint `subject_id` sets across train, validation, calibration (if any), and test. It runs on synthetic fixtures in CI and on real manifests locally, printing counts only.
- **Stop if:** Overlap is detected or cannot be ruled out.

## D3. Images, views, and preprocessing

**Status:** mostly `OPEN`.

- **POLICY (D3-a):** Use MIMIC-CXR-JPG v2.1.0 for the approved baseline. `VERIFY:` Check conversion, bit depth, and preprocessing details against the official release before describing them. Switching to DICOM needs a scope decision.
- **PROPOSED:** Record view position per image (for example PA, AP, lateral, other) and report all main metrics stratified by view. `VERIFY` the metadata field names and value set in the dataset documentation.
- **OPEN (D3-b):** Input policy for studies with multiple images: single frontal only, frontal plus lateral, or all. The selection must be deterministic, recorded, and independent of labels, reports, or model outputs.
- **PROPOSED:** Use the backbone's official image processor. Resolution is an experimental variable: do not change it between `B0`, `B1`, `B2`, `B3`, and `M1` without a decision record. Record the resolution in every run. Downscaling can remove small findings such as small pneumothorax or nodules.
- **PROPOSED (shortcuts):** Models can exploit shortcuts such as view position (portable AP images correlate with sicker patients), support devices (tubes, lines, pacemakers), burned-in markers or text, and scanner characteristics. Do not crop these out silently. Report stratified results (view, device presence if available) and mention shortcut risk in limitations.
- **PROPOSED (laterality convention):** In standard frontal radiographs, the patient's left appears on the viewer's right. Questions and reports use the patient's left/right. Box coordinates are in image space. Verify orientation from image metadata and markers before implementing this mapping in one tested function. Document exceptions.

## D4. Augmentation and geometry

**Status:** PROPOSED.

- **Rule:** No horizontal flip anywhere: training, evaluation, test-time augmentation, or counterfactual generation.
- **Why:** A flip swaps left and right. QA answers, report statements, and boxes encode laterality, and normal anatomy is asymmetric (cardiac silhouette, stomach bubble). A flipped image with the original label is a wrong label.
- **Rule (boxes):** Every spatial transform (resize, pad, crop, rotate) applied to an image must be applied identically to its boxes, through one shared function.
- **Rule (box format):** One internal format: `xyxy`, absolute pixels, original image coordinates. Convert to the model's format only at the model boundary, in one place.
- **Rule (cropping):** No random crop that can remove regions a question may refer to (apices, costophrenic angles, upper abdomen).
- **Rule (default augmentation):** Default is no augmentation beyond the backbone processor. Any addition requires an entry in the config and approval. The augmentation list is serialized with every run.
- **Check (to implement):** `test_no_horizontal_flip` scanning configs and code; `test_box_transform_roundtrip` using a synthetic image with a known box through every transform.
- **Stop if:** A pipeline applies a spatial transform to images without a matching box transform.

## D5. Question taxonomy and answer normalization

**Status:** `OPEN` for taxonomy; `PROPOSED` for normalization principles.

- **OPEN (D5-a):** Question types and the closed-versus-open boundary. Derive the taxonomy from the chosen dataset's documentation. Do not invent categories.
- **PROPOSED (normalization):** Apply a fixed, versioned normalization (case, whitespace, punctuation, synonym map). Normalization must never erase meaning that matters clinically:
  - Negation: "no pneumothorax" is not "pneumothorax".
  - Laterality: "left" is not "right".
  - Degree: "small", "moderate", and "large" are distinct.
- **OPEN (D5-b):** Mapping of hedged language ("possible", "likely", "cannot exclude") in open-ended answers.
- **PROPOSED (parser):** The answer parser is fixed before validation runs. Outputs that fail to parse are counted as incorrect, not dropped. Report the parse-failure rate for every run. Dropping failures inflates accuracy.
- **PROPOSED:** If the parser or normalization changes, all compared runs are re-scored with the new version. Never compare scores produced by different parser versions.
- **PROPOSED (leakage audit):** Check whether the answer appears in the question text or is trivially implied by it. Report the rate.
- **Check:** Fixtures covering negation, laterality, degree, empty output, truncated output, and multi-answer output, with hand-computed expected scores.

## D6. Labels, uncertainty, and prior-study references

**Status:** `OPEN` (uncertain-label policy required by [data governance](DATA-PRIVACY.md#6-data-governance-and-privacy)).

- **OPEN (D6-a):** Uncertain-label policy: exclude, treat as negative, treat as a separate class, or soft label. `VERIFY:` CheXpert-style labelers use positive, negative, uncertain, and not-mentioned values, and they are automatic and noisy. Confirm the encoding in the dataset documentation.
- **Rule:** "Not mentioned in the report" is not "absent in the image". Do not treat it as negative without an explicit decision.
- **PROPOSED (label audit):** Before the first training run, a human reviewer audits a sample of QA pairs for label noise and answer leakage. Sample size is `OPEN`. Record the estimated noise rate with a confidence interval. Audited rows stay local.
- **OPEN (D6-b):** Questions or reports that depend on a prior study ("unchanged", "interval change", "compared with prior") cannot be answered from a single image. Decide whether to exclude them or analyze them as a separate stratum. Count how many exist.
- **OPEN (D6-c):** Whether report text may be used as training supervision. At inference and evaluation, report text must not be an input.
- **Stop if:** The source of a label cannot be traced to a recorded derivation.

## D7. Grounding

**Status:** `OPEN` for sources; `PROPOSED` for scoring discipline.

- **POLICY (D7-a):** The plan selects Chest ImaGenome for finding-linked anatomical regions. CheXmask supports organ-mask quality control or labeled visualization; it is not lesion-grounding ground truth. `VERIFY:` Confirm annotation method, coordinate frame, license, and join coverage from official releases. MS-CXR is a literature example, not an approved additional source.
- **PROPOSED:** For each source, record what is boxed (finding or anatomical region), coordinate frame, number of boxes per finding, annotator count, and whether annotation was expert or automatic. Report results per source. Do not pool expert and automatic annotations without stratifying.
- **PROPOSED (metric per annotation type):** Choose the metric before the experiment, per source: IoU/Dice for tight finding boxes; pointing-game or region hit rate for anatomical regions; box mAP only with consistent definitions. Record the choice in the metric definition file.
- **OPEN (D7-b):** Scoring for questions with no ground-truth box (negative or "no finding" questions). Do not score them as IoU = 0.
- **PROPOSED (coverage):** Grounding annotations usually cover only a subset of QA items. Report the coverage and compute grounding metrics only on that subset. Do not extrapolate to all questions.
- **PROPOSED (predicted boxes):** Clip to image bounds. Invalid, empty, or unparsable boxes count as misses.
- **Rule (no label leakage into inference):** At inference, `M1` and any risk model may use only information available without ground-truth labels or boxes. Ground-truth boxes may be used for training targets and evaluation only.
- **Check (to implement):** An input-audit test that asserts the risk module's inference inputs contain no ground-truth fields.
- **Stop if:** A ground-truth box or label can reach the model or risk module at test time.

## D8. Confidence, calibration, and selective prediction

**Status:** POLICY (metric families). Definitions `OPEN`. This is the most under-specified part of the contract and blocks `B2`, `B3`, and `M1`.

- **OPEN (D8-a, blocks all reliability work): Correctness function for open-ended answers.** Risk is `1 - correctness`, so "correct" must be defined before any selective-prediction experiment. Candidates and caveats:
  - Normalized exact match: strict, penalizes valid paraphrase.
  - A clinical factual score: must be validated for short answers; some candidates were designed for full reports. `VERIFY` suitability before use.
  - Expert judgment on a subset: expensive, usable for validation of the automatic function.
  - Whatever is chosen must be fixed before experiments, checked against clinician judgment on a sample, and applied identically to every baseline.
- **OPEN (D8-b): Confidence definition.** Candidates: option-level probability for closed-ended questions; answer-token probability; length-normalized sequence log-probability; a learned head. Raw sequence probabilities are length-biased. State the definition next to every result. `B2`, `B3`, and `M1` must use the same definition, or the difference must be stated.
- **OPEN (D8-c):** Calibration method (for example temperature scaling) and the split used to fit it. See D2-b.
- **PROPOSED (binning):** Lock the number of bins and the binning type (equal-width or equal-mass) in config. ECE depends on binning. Always report it together with the Brier score and the per-bin counts in the machine-readable output.
- **PROPOSED (deployed form):** Calibrate and evaluate the same deployed form of the model. Quantization and adapter merging change numerics. A temperature fitted on one form is not guaranteed valid for another.
- **PROPOSED (risk-coverage):** Define coverage as the fraction of questions answered. Fix the tie-breaking rule for equal confidences. Fix the AURC computation procedure and keep its implementation under test.
- **PROPOSED (fixed-risk operating point):** Choose the threshold for "coverage at fixed risk" on the selection split only. On test, report the realized risk at that threshold and whether it met the target. Do not re-select the threshold on test.
- **PROPOSED:** Report risk-coverage by question type and by view as well as aggregate. Aggregate curves can hide a failing subgroup.
- **Rule:** Abstention is a measured trade-off, not a safety guarantee (see D14).
- **Check:** Hand-computed fixtures for ECE, Brier, risk-coverage, AURC, and coverage at fixed risk, including empty bins, ties, all-correct, and all-wrong cases.

## D9. Sanity baselines and backbone contamination

**Status:** PROPOSED (baselines); `VERIFY` (contamination).

- **PROPOSED (sanity baselines):** Report next to every main `B*`/`M*` result:
  - Question-only baseline (no image or blank image): quantifies language prior.
  - Shuffled-image baseline (image from a different patient, same question): tests whether the model uses the image.
  - Majority-answer and prior-frequency baselines.
- **Why:** In VQA, high accuracy can come from answer priors. Without these baselines, accuracy gains and any "grounding" claim cannot be interpreted.
- **VERIFY (D9-a, contamination):** Read the official model card of the exact backbone version used and record whether MIMIC-CXR or its derivatives were in pretraining or fine-tuning data. A patient-level split in this project does not protect against the backbone having seen the test images. The agent's recollection that a MedGemma card lists MIMIC-CXR is unconfirmed. A human must confirm it for the selected version.
- **PROPOSED:** If overlap is confirmed or cannot be excluded, label `B0` results "possible training-data overlap" and do not describe them as zero-shot generalization. Put it in limitations.
- **Check:** The backbone decision record contains a "training data overlap" field. A run manifest without it is rejected.

## D10. Counterfactual faithfulness

**Status:** POLICY (metric). Protocol `OPEN` / `PROPOSED`.

- **POLICY:** Report the relevant-region versus irrelevant-region risk delta and the answer-change behavior.
- **OPEN (D10-a):** Exact masking method (fill value, blur, or other) and exact definition of the irrelevant region. Choose one method and lock it. Masks are applied at model input resolution, after the D4 transforms.
- **PROPOSED (required control):** For each item, also mask an irrelevant region of matched area that does not overlap the relevant region. If masking either region changes risk similarly, the effect reflects occlusion artifacts, not evidence use.
- **PROPOSED:** State the expected behavior before running: masking the relevant region should raise risk, lower confidence, or trigger abstention; masking an irrelevant region should change little. Do not tune the masking method to produce the expected result.
- **PROPOSED:** Counterfactual metrics use only the grounded subset (D7). Report coverage.
- **PROPOSED:** Fix the protocol on the validation split. Run once on test.
- **Stop if:** The masking procedure is changed after seeing counterfactual results.

## D11. Statistics and uncertainty

**Status:** POLICY (three seeds, confidence intervals). Details `PROPOSED`.

- **PROPOSED:** Separate two sources of variance and report both: training variance (seeds) and test-sampling variance (patients).
- **PROPOSED:** Confidence intervals come from a patient-cluster bootstrap on the test set: resample patients, then include all their items. Never resample images or QA items independently, because correlated items make intervals too narrow.
- **PROPOSED:** For `M1` versus `B1`/`B2`/`B3`, use a paired bootstrap on the same resampled patients. The number of resamples and the bootstrap seed are recorded. Choose the number before running.
- **PROPOSED (primary comparison):** Declare one primary comparison and one primary metric per claim before the test run. Everything else is labeled exploratory.
- **OPEN (D11-a):** Minimum cell size for reporting subgroup results. Below it, suppress or flag the cell. This protects against both unstable estimates and re-identification through very small aggregates ([data policy](DATA-PRIVACY.md#6-data-governance-and-privacy)).
- **Rule:** Never change a metric, parser, or definition after seeing results to improve them ([task policy](TASK-WORKFLOW.md#5-smallest-valid-change)).
- **Check:** A unit test showing that the bootstrap resamples by patient (for example, duplicate-patient fixtures).

## D12. Efficiency measurement

**Status:** POLICY (metrics, 24 GB target). Protocol `PROPOSED`.

- **PROPOSED (latency protocol):** Batch size 1. Discard warm-up runs. Synchronize the GPU before reading the clock. Fix decoding and `max_new_tokens`. Record generated token count. Report both model-only and end-to-end (including preprocessing) latency, and say which is which. Report p50 and p95 over a recorded number of runs on an otherwise idle GPU.
- **PROPOSED:** Latency depends on output length. Report by answer-length bucket or under a fixed token budget.
- **PROPOSED (24 GB claim):** The 24 GB profile is claimed only if measured on a 24 GB device (RTX 4090) or enforced by a hard memory cap on a larger device. Record peak allocated and peak reserved memory, at the maximum input length.
- **PROPOSED (incremental cost):** For `M1`, report incremental latency and VRAM relative to `B1`, including extra forward passes, extra heads, or repeated decoding.
- **PROPOSED (training cost):** Trainable parameters count adapter parameters only, with the quantization and adapter configuration stated. GPU-hours include failed and interrupted runs that informed decisions.
- **Rule:** Report training and inference measurements separately with the actual GPU and workload role, including when RTX PRO 6000 Blackwell is used for both (see [GPU policy](GPU-DOCKER.md#22-linux-docker-and-gpu-execution-policy)).

## D13. Error analysis and clinical review

**Status:** PROPOSED. Needs clinical reviewer approval before use.

- **Agent may:** compute error counts, stratify by confidence, question type, view, and grounding outcome, and pre-sort items into candidate categories.
- **Agent may not:** assign final clinical error labels, judge clinical severity, or state clinical significance.
- **PROPOSED starting taxonomy** (clinical reviewer to revise):
  - false-positive finding
  - missed finding (false negative)
  - wrong laterality
  - wrong location or region
  - negation error
  - wrong severity or extent
  - wrong attribute
  - correct answer, wrong grounding
  - wrong answer, correct grounding
  - hallucinated device or finding
  - unanswerable from a single image (needs prior study or history)
  - label or annotation problem (not a model error)
  - format or parse failure
- **PROPOSED (priority):** High-confidence wrong answers and failures of abstention are the primary reliability analysis. Sample them deliberately, not only at random.
- **OPEN (D13-a):** Sampling frame and size, who annotates, number of annotators, and agreement statistic. Reviewers should be blind to model variant where feasible.
- **Rule:** Item-level review material stays local and untracked. Only aggregate counts leave the restricted environment.

## D14. Claims and language

**Status:** POLICY ([research scope](RESEARCH-SCOPE.md#3-work-that-is-not-authorized-by-default) and [completion reports](TASK-WORKFLOW.md#14-required-completion-report)). Wording rules `PROPOSED`.

- **Allowed form:** "On the `<split>` of `<dataset version>`, under `<protocol>`, `<system>` achieved `<metric>` = `<value>` (95% patient-bootstrap CI `<lo, hi>`, n = `<patients/items>`)."
- **Not allowed:** "clinically safe", "ready for deployment", "diagnostic", "equivalent to radiologists", "trustworthy", "reduces clinical errors", or "state of the art". The last needs a systematic same-protocol comparison on the same split and Research Lead approval.
- **Abstention wording:** Describe it as "selective prediction at a measured risk-coverage trade-off", not as a safety mechanism.
- **Generalization:** Single-source data without external validation limits claims to that source. Say so.
- **Label every result** as planned, partial, simulated, or measured.
- **Dataset and model facts** in text must cite the official source. `VERIFY` items are not citable until confirmed.

## D15. Reproducibility specifics

**Status:** PROPOSED.

- Seed every random source (Python, NumPy, PyTorch, data-loader workers). Record whether deterministic kernels were enforced. Mixed-precision and GPU kernels can be nondeterministic even with fixed seeds, so report seed variance, not only the seed.
- Primary evaluation uses a fixed decoding policy (greedy unless an approved record says otherwise). If sampling is used, record the number of samples and seeds.
- Store each prompt template as a versioned file. Record its hash and the tokenizer chat-template version in every run.
- Prompt selection happens on validation before the lock. After the lock, prompts, parser, and thresholds are frozen.

---

## Per-task preflight checklists

Paste the relevant checklist into the first progress update and mark each item.

**Data or split task:** D1 manifest fields complete; D2 patient-disjoint test passes (counts only); derived-split reconciled; D3 view and input policy recorded; D6 uncertain-label policy present.

**Preprocessing or augmentation task:** D4 no horizontal flip; box transforms tested; box format is the internal format; augmentation list in config.

**Metric or parser task:** D5 fixtures for negation, laterality, degree, parse failure; D8 hand-computed fixtures; metric definition file updated; all runs rescored if the definition changed.

**Training task:** D9 contamination field filled; D12 cost fields recorded; D15 seeds and prompt hash recorded; no test-set access.

**Evaluation task:** D2 test seal respected; D9 sanity baselines included; D11 patient-cluster bootstrap and paired comparison; D8 threshold chosen on the selection split; D12 protocol followed.

**Reliability or counterfactual task:** D8-a and D8-b decided (otherwise stop); D10 control mask included; D7 no ground-truth inputs at inference.

**Report, figure, or claim task:** D14 wording; every number regenerated from machine-readable results; every `VERIFY` resolved or removed; limitations list includes contamination, shortcut risk, single-source data, and label noise.

---

## Decision and verification register

| ID | Decision | Blocks | Suggested owner | Status |
| --- | --- | --- | --- | --- |
| D1-a | Approved datasets; verify versions, joins, and access | Data gate | Data | Selected; verification pending |
| D1-c | Approval for linking other MIMIC modules | Subgroup analysis by demographics | Research Lead | Open |
| D2-a | Official split vs project split | Splits, all experiments | Data | Open |
| D2-b | Separate calibration split vs reuse validation | `B2`, `B3`, `M1` | Metrics/Stats | Open |
| D3-a | JPG selected; verify conversion details | Preprocessing | Data | Selected; verification pending |
| D3-b | Multi-image study input policy | Model input, all baselines | Model | Open |
| D5-a | Question taxonomy | Per-type reporting | Data | Open |
| D5-b | Hedged-language mapping | Open-ended scoring | Metrics/Stats + clinical reviewer | Open |
| D6-a | Uncertain-label policy | Training labels, closed-ended eval | Data | Open |
| D6-b | Prior-study-dependent questions | Dataset filtering | Data + clinical reviewer | Open |
| D6-c | Report text as training supervision | `B1` design | Research Lead | Open |
| D7-a | Chest ImaGenome selected; verify annotations and joins | Grounding metrics, `B3`, `M1` | Model | Selected; verification pending |
| D7-b | Scoring of no-box questions | Grounding metrics | Metrics/Stats | Open |
| D8-a | Correctness function for open-ended answers | All reliability work | Metrics/Stats + clinical reviewer | Open |
| D8-b | Confidence definition | `B2`, `B3`, `M1` | Metrics/Stats | Open |
| D8-c | Calibration method | `B2`, `B3`, `M1` | Metrics/Stats | Open |
| D10-a | Masking method and irrelevant-region definition | Counterfactual evaluation | Model + Metrics/Stats | Open |
| D11-a | Minimum subgroup cell size | Subgroup reporting | Research Lead | Open |
| D13-a | Error-review sampling and annotators | Clinical error analysis | Research Lead + clinical reviewer | Open |
| D9-a | Backbone training-data overlap (model card) | `B0` interpretation | Research Lead | Verify |

Suggested owners follow [the role boundaries](TASK-WORKFLOW.md#13-role-boundaries-and-review) and should be confirmed by the team.

---

## Maintaining this file

- When an agent makes a domain mistake, add one entry in the format above. Do not add general advice.
- When a rule becomes a test, validator, or CI check, replace the prose with a one-line pointer to that control.
- When an `OPEN` item is decided, record the decision, link the decision record, and change the tag to `POLICY` or `PROPOSED`.
- When a `VERIFY` item is confirmed, replace the tag with the source link. If it is wrong, correct it and note the change.
- Keep this file under about 400 lines. Move long background material to cited sources, not here.

