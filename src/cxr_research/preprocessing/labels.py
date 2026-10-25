"""Versioned text processing and review cues, without clinical relabeling."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from cxr_research.utils.errors import ContractError


TEXT_VERSION = "cxr-text-nfc-space-v1"
CUE_VERSION = "cxr-review-cues-v1"
# These are lexical review cues, not a clinical NLP classifier or scoped NegEx.
CUES = {
    "negation_cue": r"\b(?:no|not|without|absent|negative for)\b",
    "laterality_cue": r"\b(?:left|right|bilateral|unilateral)\b",
    "degree_cue": r"\b(?:small|moderate|large|mild|severe|trace|minimal)\b",
    "uncertainty_cue": r"\b(?:possible|possibly|probable|probably|may|might|cannot exclude|can not exclude|suggestive of|suspicious for)\b",
    "prior_study_cue": r"\b(?:unchanged|interval|previous|previously|prior|compared (?:with|to)|improved|worsened|stable)\b",
}


def normalize_text(text: str, policy: str = TEXT_VERSION) -> str:
    if not isinstance(text, str):
        raise ContractError("text processing requires a string")
    if policy == "identity":
        return text
    if policy != TEXT_VERSION:
        raise ContractError("unsupported text normalization version")
    # NFC preserves compatibility distinctions, punctuation, case and units.
    return " ".join(unicodedata.normalize("NFC", text).split())


def review_cues(question: str, answers: list[str]) -> tuple[str, ...]:
    text = " ".join([question, *answers])
    return tuple(name for name, pattern in CUES.items() if re.search(pattern, text, re.IGNORECASE))


@dataclass(frozen=True)
class ProcessedQA:
    question: str
    answers: tuple[str, ...]
    flags: tuple[str, ...]
    changed: bool


def process_qa(question, answers, *, normalization=TEXT_VERSION) -> ProcessedQA:
    result = review_qa(question, answers, normalization=normalization)
    if not result["supervision_valid"]:
        raise ContractError("source QA has invalid or incomplete supervision")
    return ProcessedQA(result["question"], tuple(result["answer"]), tuple(result["flags"]), result["changed"])


def review_qa(question, answers, *, normalization=TEXT_VERSION):
    """Preserve incomplete source targets for review; never invent a negative."""
    errors = []
    q = normalize_text(question, normalization) if isinstance(question, str) else question
    if not isinstance(q, str) or not q.strip():
        errors.append("invalid_or_empty_question")
    if isinstance(answers, list):
        values = [normalize_text(a, normalization) if isinstance(a, str) else a for a in answers]
        if not values:
            errors.append("empty_answer_array")
        if any(not isinstance(a, str) for a in values):
            errors.append("non_string_answer")
        if any(isinstance(a, str) and not a.strip() for a in values):
            errors.append("empty_answer_text")
    else:
        values = answers
        errors.append("answer_not_array")
    text_answers = [a for a in values if isinstance(a, str)] if isinstance(values, list) else []
    cues = review_cues(q if isinstance(q, str) else "", text_answers)
    return {"question": q, "answer": values, "flags": [*cues, *errors],
            "supervision_valid": not errors, "changed": q != question or values != answers}


def label_state(value, mapping: dict[str, str]) -> str:
    """Map only explicitly documented source tokens, preserving all four states."""
    if not isinstance(mapping, dict) or not mapping or any(
            state not in {"positive", "negative", "uncertain", "missing"} for state in mapping.values()):
        raise ContractError("explicit four-state label mapping is required")
    # None and an empty string remain separate source tokens.
    token = "<NULL>" if value is None else str(value)
    if token not in mapping:
        raise ContractError("unmapped source label token")
    return mapping[token]
