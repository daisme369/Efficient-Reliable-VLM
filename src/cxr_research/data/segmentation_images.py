"""Read original segmentation pairs without changing their geometry."""
import numpy as np
from PIL import Image

from cxr_research.data.manifest import local_file
from cxr_research.data.source_refs import read_csv_reference
from cxr_research.preprocessing.masks import decode_rle
from cxr_research.preprocessing.source_images import check_source_mode
from cxr_research.utils.errors import ContractError


class EmptyLungMask(ContractError):
    def __init__(self, *, both_empty: bool):
        self.both_empty = both_empty
        super().__init__("empty lung mask requires source review")


def read_pair(root, sample):
    with Image.open(local_file(root, sample.image)) as raw:
        check_source_mode(raw)
        image = raw.convert("L")
    if sample.mask_format == "chexmask_rle":
        record = read_csv_reference(root, {"path": sample.mask, **sample.mask_reference})
        column = "dicom_id" if sample.patient_namespace == "mimic-cxr" else "Image Index"
        height, width = sample.mask_reference["height"], sample.mask_reference["width"]
        if (record[column] != sample.image_id or (int(record["Height"]), int(record["Width"])) != (height, width)
                or image.size != (width, height)):
            raise ContractError("CheXmask reference identity or dimensions differ")
        left = decode_rle(record["Left Lung"], height, width)
        right = decode_rle(record["Right Lung"], height, width)
        left_empty, right_empty = not left.any(), not right.any()
        if left_empty or right_empty:
            raise EmptyLungMask(both_empty=left_empty and right_empty)
        mask = Image.fromarray(left | right)
    else:
        with Image.open(local_file(root, sample.mask)) as raw:
            mask = raw.convert("L")
        if not set(np.unique(np.array(mask))).issubset({0, 1, 255}):
            raise ContractError("expected a binary lung mask")
    if image.size != mask.size:
        raise ContractError("image and mask dimensions differ")
    return image, mask
