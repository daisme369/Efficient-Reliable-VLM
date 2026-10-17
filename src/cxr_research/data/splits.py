"""Patient and image identity checks shared by source mapping and manifest loading."""

from pathlib import Path

from cxr_research.utils.errors import ContractError


class SplitAudit:
    def __init__(self):
        self._patients = {}
        self._images = {}
        self._paths = {}

    def add(self, namespace: str, patient_id: str, image_id: str, image_path: Path, split: str) -> None:
        if split not in {"train", "validation", "calibration", "test"}:
            raise ContractError("invalid split")
        if not all(isinstance(key, str) and key.strip() for key in (namespace, patient_id, image_id)):
            raise ContractError("missing source patient or image identity")
        patient = (namespace, patient_id)
        if self._patients.setdefault(patient, split) != split:
            raise ContractError("patient split overlap detected")
        identity = (patient, split, image_path.resolve())
        if self._images.setdefault((namespace, image_id), identity) != identity:
            raise ContractError("image identity conflicts across patients or splits")
        if self._paths.setdefault(image_path.resolve(), (patient, split)) != (patient, split):
            raise ContractError("image identity conflicts across patients or splits")
