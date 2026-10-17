# Experiments and evaluation

Read [AGENTS-INSTRUCTIONS.md](../../AGENTS-INSTRUCTIONS.md) first. This module applies when the task routing table names it.

## 7. Experimental integrity

Every experiment used as evidence must record:

- Experiment ID and Jira issue.
- Git commit and dirty-worktree status.
- Config file and all overrides.
- Dataset/manifest version and patient split.
- Model checkpoint and revision.
- Seed, precision, image resolution, prompt template, decoding settings, and token limits.
- GPU model, number of GPUs, software environment, GPU-hours, peak VRAM, batch size, and timing protocol.
- Output artifact path and run status, including failed or interrupted runs.

Use the validation/calibration split for model, prompt, threshold, and temperature selection. The test set is for the locked final evaluation. Do not delete or hide failed experiments that informed a decision.

For main comparisons, keep data, prompt, decoding, and evaluation code fixed. Run at least three seeds unless the run is deterministic or the approved protocol states otherwise. Report confidence intervals for paper-facing primary results.

## 8. Metric contract

Do not report a single aggregate score as evidence of reliability.

Required metric families are:

- Answer quality: Closed-Acc and macro-F1 for closed-ended questions; normalized match, BLEU, ROUGE-L, BERTScore, and an appropriate clinical factual measure for open-ended answers.
- Reliability: ECE, Brier score, risk–coverage curve, AURC, and coverage at a fixed risk.
- Grounding: IoU/Dice, box mAP, pointing-game accuracy, or region hit rate as appropriate to the annotation.
- Counterfactual faithfulness: relevant-region versus irrelevant-region risk delta and answer-change behavior.
- Efficiency: trainable parameters, checkpoint size, GPU-hours, peak training/inference VRAM, throughput, and batch-1 p50/p95 latency.

BLEU and ROUGE are lexical similarity metrics; never present them alone as proof of clinical correctness. State the confidence definition, calibration method, binning rule, threshold-selection split, hardware, precision, and timing method next to results.

## 9. Required baselines and ablations

Use these stable identifiers unless an approved decision record changes them:

- `B0`: zero-shot/frozen VLM.
- `B1`: QLoRA VQA baseline.
- `B2`: `B1` plus answer-only calibration/risk.
- `B3`: `B1` plus grounding-only risk.
- `M1`: joint grounding-conditioned risk/abstention method.
- `M2`: optional counterfactual training or consistency loss, only after `M1` is stable.

Do not call `M1` effective without comparing it to `B1`, `B2`, and `B3` under the same evaluation protocol. Report incremental latency and VRAM, not only quality gains.

## 10. Tests and evidence

Match verification effort to the change:

- Data changes: schema validation, determinism check, patient-overlap test, provenance check, and synthetic end-to-end sample.
- Metric changes: hand-computed fixtures, edge cases, empty/unknown labels, and regression tests.
- Model changes: unit/smoke test, one tiny overfit or synthetic run when appropriate, config serialization, checkpoint load, and inference output validation.
- Performance claims: warm-up, repeated runs, synchronized timing, fixed hardware/settings, p50/p95 latency, throughput, and peak VRAM.
- Report/figure changes: regenerate from machine-readable results and retain the command/config.

A task is not complete because code was written. It is complete when the requested behavior is demonstrated and linked to the Jira acceptance criteria.

