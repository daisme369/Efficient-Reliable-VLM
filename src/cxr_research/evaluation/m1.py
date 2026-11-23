"""Development evaluation for M1 predictions and validation-only abstention."""

from collections import Counter
import json
import math
import time

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
import torch

from cxr_research.data.datasets import VQADataset
from cxr_research.data.m1 import GRID, REGION_PROMPT, validation_partition
from cxr_research.experiments.checkpoints import verify_artifacts
from cxr_research.experiments.identity import _identity, _verify_parent
from cxr_research.metrics.vqa import normalize_answer, vqa_metrics
from cxr_research.models.medgemma import load_model
from cxr_research.preprocessing.medgemma import VQACollator
from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import digest
from cxr_research.utils.io import write_json


def parse_boxes(answer):
    try:
        boxes = json.loads(answer)
    except (ValueError, RecursionError):
        return None
    if not isinstance(boxes, list) or len(boxes) > 64:
        return None
    result = []
    for box in boxes:
        if (not isinstance(box, list) or len(box) != 4 or
                any(type(v) is not int or not 0 <= v <= GRID for v in box) or
                box[0] >= box[2] or box[1] >= box[3]):
            return None
        result.append(tuple(box))
    return list(dict.fromkeys(result))


def union_area(boxes):
    xs = sorted({x for box in boxes for x in (box[0], box[2])})
    area = 0
    for x1, x2 in zip(xs, xs[1:]):
        intervals = sorted((b, d) for a, b, c, d in boxes if a <= x1 and c >= x2)
        end = height = 0
        for bottom, top in intervals:
            if top > end:
                height += top - max(bottom, end)
                end = top
        area += height * (x2 - x1)
    return area


def box_iou(predicted, reference):
    if not predicted:
        return 0.0
    union = union_area(predicted + reference)
    return (union_area(predicted) + union_area(reference) - union) / union


def grounding_metrics(items, *, region):
    ious = [box_iou(prediction, reference) for prediction, reference in items]
    result = {"examples": len(ious), "mean_iou_grid": float(np.mean(ious)) if ious else None,
              "iou_at_0_5": float(np.mean(np.array(ious) >= 0.5)) if ious else None}
    if region:
        hits = []
        for prediction, reference in items:
            first = prediction[0] if prediction else None
            if not first:
                hits.append(False)
                continue
            x, y = (first[0] + first[2]) / 2, (first[1] + first[3]) / 2
            hits.append(any(a <= x < c and b <= y < d for a, b, c, d in reference))
        result["pointing_accuracy_at_1"] = float(np.mean(hits)) if hits else None
    return result


def selectivity(rows, target_risk):
    fit = [row for row in rows if validation_partition(row[0]) == "calibration"]
    score = [row for row in rows if validation_partition(row[0]) == "report"]
    result = {"calibration_examples": len(fit), "report_examples": len(score),
              "calibration_patients": len({r[0] for r in fit}), "report_patients": len({r[0] for r in score}),
              "target_risk": target_risk, "development_only": True}
    if len({row[1] for row in fit}) != 2 or not score:
        return result | {"status": "insufficient_calibration_or_report_population"}

    def features(data, axes):
        return np.array([[1.0, *(row[2][axis] for axis in axes)] for row in data])

    def summarize(prob, outcomes, threshold):
        cover = np.zeros(len(prob), dtype=bool) if threshold is None else prob >= threshold
        brier = np.mean((prob - outcomes) ** 2)
        bins = np.minimum((10 * prob).astype(int), 9)
        ece = 0.0
        for index in np.unique(bins):
            mask = bins == index
            ece += mask.mean() * abs(prob[mask].mean() - outcomes[mask].mean())
        order = np.argsort(-prob, kind="stable")
        ranked = prob[order]
        end = np.r_[np.flatnonzero(ranked[:-1] != ranked[1:]), len(ranked) - 1]
        risk = np.cumsum(1 - outcomes[order])[end] / (end + 1)
        coverage_step = np.diff(np.r_[-1, end]) / len(prob)
        return {"coverage": float(cover.mean()),
                "risk": float((1 - outcomes[cover]).mean()) if cover.any() else None,
                "brier": float(brier), "ece_10_equal_width": float(ece),
                "aurc": float(risk @ coverage_step)}

    outcomes = np.array([row[1] for row in fit])
    methods = {}
    for name, axes in (("answer_signal", (0,)), ("grounding_signal", (1,)), ("joint", (0, 1))):
        x = features(fit, axes)

        def loss(weights):
            logits = x @ weights
            penalty = 0.01 * (weights[1:] @ weights[1:])
            value = np.mean(np.logaddexp(0, logits) - outcomes * logits) + penalty
            gradient = x.T @ (expit(logits) - outcomes) / len(outcomes)
            gradient[1:] += 0.02 * weights[1:]
            return value, gradient

        opt = minimize(loss, np.zeros(x.shape[1]), jac=True, method="BFGS")
        if not opt.success or not np.isfinite(opt.x).all():
            raise ContractError("M1 calibration optimizer did not converge")
        p = expit(x @ opt.x)
        order = np.argsort(-p, kind="stable")
        ranked = p[order]
        end = np.r_[np.flatnonzero(ranked[:-1] != ranked[1:]), len(ranked) - 1]
        counts = end + 1
        risk = np.cumsum(1 - outcomes[order])[end] / counts
        feasible = end[risk <= target_risk]
        threshold = float(ranked[feasible[-1]]) if len(feasible) else None
        methods[name] = {"weights": opt.x.tolist(), "threshold": threshold,
                         **summarize(expit(features(score, axes) @ opt.x),
                                     np.array([row[1] for row in score]), threshold)}
    return result | {"status": "complete", "method": "validation_patient_partition_logistic_l2",
                     "signal_ablations_same_adapter": methods}


@torch.inference_mode()
def evaluate(config, manifest, bundle, directory, adapter, target_risk):
    from peft import PeftModel

    model, processor = load_model(config, training=False)
    identity = _identity(config, manifest, processor) | bundle.identity
    _verify_parent(adapter / "model_identity.json", identity)
    identity["loaded_adapter_sha256"] = digest(verify_artifacts(adapter))
    model = PeftModel.from_pretrained(model, str(adapter), is_trainable=False)
    model.eval()
    write_json(directory / "model_identity.json", identity)
    collator = VQACollator(processor, config.medgemma)
    durations, counts = [], Counter()

    def sync():
        if model.device.type == "cuda":
            torch.cuda.synchronize()

    def predict(image, question, *, measure=False):
        sync()
        start = time.perf_counter()
        # The caller supplies image and question only.
        batch = collator.encode([{"image": image, "question": question}], supervised=False).to(model.device)
        output = model.generate(**batch, do_sample=False, max_new_tokens=config.medgemma.max_new_tokens,
                                return_dict_in_generate=True, output_scores=True)
        sync()
        if measure:
            durations.append(time.perf_counter() - start)
        tokens = output.sequences[0, batch["input_ids"].shape[-1]:]
        if not output.scores:
            return "", 0.0
        log_p = [torch.log_softmax(s[0].float(), dim=-1)[t] for s, t in zip(output.scores, tokens, strict=True)]
        confidence = float(torch.stack(log_p).mean().exp().item())
        if not math.isfinite(confidence):
            raise ContractError("M1 decoder produced a non-finite confidence")
        return processor.decode(tokens, skip_special_tokens=True).strip(), confidence

    def report(patient):
        return validation_partition(patient) == "report"

    samples = [sample for sample in bundle.samples["validation"]
               if validation_partition(sample.patient_id) != "selection"]
    dataset = VQADataset(manifest, samples)
    report_indices = [index for index, sample in enumerate(samples) if report(sample.patient_id)]
    for index in report_indices[:5]:
        example = dataset[index]
        predict(example["image"], example["question"])
        predict(example["image"], REGION_PROMPT + example["question"])
    qa, region, phrase, risk = [], [], [], []
    for i, sample in enumerate(samples):
        example = dataset[i]
        measured = report(sample.patient_id)
        answer, confidence = predict(example["image"], example["question"], measure=measured)
        box_text, box_confidence = predict(example["image"], REGION_PROMPT + example["question"], measure=measured)
        boxes = parse_boxes(box_text)
        if measured:
            counts["invalid_region_prediction"] += boxes is None
            target = bundle.regions.get(sample.sample_id)
            if target is not None:
                region.append((boxes, parse_boxes(target)))
            else:
                counts["region_reference_missing"] += 1
            qa.append((answer, sample.answer, sample.answer_type, sample.primary))
        correct = normalize_answer(answer) == normalize_answer(sample.answer)
        risk.append((sample.patient_id, int(correct), (confidence, box_confidence if boxes else 0.0)))

    reports = []
    validation = bundle.validation
    offset = len(validation.qa)
    for i, target in enumerate(validation.targets, offset):
        if target.kind == "region" or not report(target.patient):
            continue
        example = validation[i]
        text, _ = predict(example["image"], example["question"])
        if target.kind == "phrase":
            boxes = parse_boxes(text)
            counts["invalid_phrase_prediction"] += boxes is None
            phrase.append((boxes, parse_boxes(target.answer)))
        else:
            predicted = Counter(normalize_answer(text).split())
            expected = Counter(normalize_answer(target.answer).split())
            overlap = sum((expected & predicted).values())
            denominator = sum(predicted.values()) + sum(expected.values())
            reports.append(2 * overlap / denominator if denominator else 0.0)
    # Each pair measures two forwards; pair the elapsed times for the full VQA path.
    pair_seconds = np.asarray(durations).reshape(-1, 2).sum(axis=1)
    result = {
        "status": "exploratory_development", "vqa": vqa_metrics(qa),
        "chest_imagenome_silver": grounding_metrics(region, region=True),
        "ms_cxr_phrase": grounding_metrics(phrase, region=False),
        "llava_impression": {"examples": len(reports), "lexical_token_f1": float(np.mean(reports)) if reports else None},
        "selectivity": selectivity(risk, target_risk), "prediction_counts": dict(counts),
        "timing": {"scope": "processor_and_two_generations_in_memory_image", "batch_size": 1,
                   "warmup_examples_discarded": min(5, len(report_indices)),
                   "examples": len(pair_seconds), "p50_ms": float(np.quantile(pair_seconds, .5) * 1000),
                   "p95_ms": float(np.quantile(pair_seconds, .95) * 1000)},
    }
    write_json(directory / "metrics.json", result)
    return result
