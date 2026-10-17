# Research scope and change control

Read [AGENTS-INSTRUCTIONS.md](../../AGENTS-INSTRUCTIONS.md) first. This module applies when the task routing table names it.

## 2. Locked research scope

The research objective is an efficient, grounded, and selectively reliable Radiology VQA system.

The default core scope is:

- Chest X-ray only until the X-ray baseline and reliability pipeline pass their gates.
- MIMIC-CXR-JPG v2.1.0 images, MIMIC-Ext-MIMIC-CXR-VQA v1.0.0 questions and answers, and Chest ImaGenome v1.0.0 finding-linked regions, with patient-level split isolation. CheXmask v1.0.0 is optional anatomical mask quality control, not lesion-grounding ground truth.
- Closed-ended and open-ended VQA.
- A zero-shot baseline, a QLoRA/PEFT baseline, answer-only risk, grounding-only risk, and a joint grounding-conditioned risk/abstention method.
- Clinical grounding, uncertainty calibration, selective prediction, counterfactual faithfulness tests, and clinical error analysis.
- Efficiency measured with trainable parameters, GPU-hours, peak VRAM, throughput, and p50/p95 latency.
- A target inference profile that can run within 24 GB VRAM when supported by the selected backbone.

The approved Capstone A plan selects MedGemma 1.5 4B as the first `B0` baseline, subject to its license and runtime gate. Qwen3-VL-4B-Instruct and LLaVA-Med v1.5 remain comparison candidates, not replacements for `B0`. Read the current backbone decision record before changing this order.

## 3. Work that is not authorized by default

Do not do any of the following unless the active Jira issue and an approved scope-change record explicitly require it:

- Add MRI, CT, pathology, or another modality.
- Replace the primary backbone, dataset, patient split, prompt protocol, decoding policy, or primary metric.
- Add pruning, retrieval, an external service, or a model beyond the approved MedGemma, Qwen3-VL-4B-Instruct, and LLaVA-Med v1.5 candidates. Multi-teacher distillation is a post-video research branch and requires its own student-selection and compute decision before execution.
- Perform full-model pretraining or full fine-tuning.
- Tune prompts, thresholds, or hyperparameters on the test set.
- Add a new research contribution because it appears useful while implementing another task.
- Claim clinical deployment readiness, diagnostic safety, state of the art, or clinical equivalence.
- Upload restricted MIMIC data or derived row-level content to Git, Jira, public storage, or external model APIs.

When useful work is outside scope, record it as a proposed Jira item with motivation and dependencies. Do not implement it in the current task.

## 11. Stop conditions

Stop the current implementation and ask for a decision when any of these occurs:

- Required data access or license permission is missing.
- The requested change would expose restricted data.
- Train/validation/test leakage is detected or cannot be ruled out.
- A request changes a research question, modality, dataset/split, primary backbone, primary metric, proposed method, or large compute budget.
- The task requires more than one new research component beyond its acceptance criteria.
- A metric or baseline definition conflicts with an earlier reported result.
- Expected GPU cost or runtime materially exceeds the task contract.
- The requested conclusion is not supported by the available evidence.

Routine reversible engineering choices inside the contract do not require interruption.

## 12. Scope-change protocol

Before implementing a scope change, create or obtain an approved record containing:

```text
Change requested:
Evidence or reason:
Affected research question/claim:
Affected dataset, metric, code, timeline, and compute:
Experiments that must be rerun:
Work displaced by this change:
Decision: accept / reject / defer
Approvers: Team Lead + Research Lead
```

After approval, update the Jira backlog and `DETAILED_ACTION_PLAN.md` before code changes. Chat discussion alone is not a durable scope change.

