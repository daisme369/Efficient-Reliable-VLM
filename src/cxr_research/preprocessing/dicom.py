"""Explicit single-frame grayscale DICOM display conversion for auxiliary CXR QC."""
from __future__ import annotations

from cxr_research.utils.errors import ContractError


def decode_dicom(path, *, voi_policy):
    """Return uint8 pixels and non-identifying metadata; never reorient or crop.

    Callers must choose `dicom` (first documented VOI view) or `none`.
    This display conversion is not the primary MIMIC JPG input pipeline.
    """
    import numpy as np
    import pydicom
    from pydicom.pixels import apply_modality_lut, apply_voi_lut

    if voi_policy not in {"dicom", "none"}:
        raise ContractError("an explicit DICOM VOI policy is required")
    ds = pydicom.dcmread(path)
    if int(getattr(ds, "NumberOfFrames", 1)) != 1 or int(getattr(ds, "SamplesPerPixel", 1)) != 1:
        raise ContractError("only single-frame grayscale DICOM is supported")
    photometric = str(ds.PhotometricInterpretation)
    if photometric not in {"MONOCHROME1", "MONOCHROME2"}:
        raise ContractError("unsupported DICOM photometric interpretation")
    if hasattr(ds, "PresentationLUTSequence") or str(getattr(ds, "PresentationLUTShape", "IDENTITY")) != "IDENTITY":
        raise ContractError("unsupported DICOM presentation LUT")
    raw = ds.pixel_array
    if raw.ndim != 2:
        raise ContractError("unsupported DICOM pixel dimensions")
    valid = np.ones(raw.shape, dtype=bool)
    if hasattr(ds, "PixelPaddingValue"):
        first = float(ds.PixelPaddingValue)
        last = float(getattr(ds, "PixelPaddingRangeLimit", first))
        valid &= (raw < min(first, last)) | (raw > max(first, last))
    pixels = apply_modality_lut(raw, ds)
    if voi_policy == "dicom":
        pixels = apply_voi_lut(pixels, ds, index=0, prefer_lut=True)
    pixels = np.asarray(pixels, dtype=np.float64)
    if not np.isfinite(pixels).all() or not valid.any():
        raise ContractError("DICOM contains invalid pixels or only padding")
    low, high = float(pixels[valid].min()), float(pixels[valid].max())
    if high <= low:
        raise ContractError("DICOM has no display dynamic range")
    pixels = np.clip((pixels-low) / (high-low), 0, 1)
    if photometric == "MONOCHROME1":
        pixels = 1-pixels
    pixels[~valid] = 0
    output = np.rint(pixels*255).astype(np.uint8)
    metadata = {"height": int(raw.shape[0]), "width": int(raw.shape[1]),
                "bits_stored": int(ds.BitsStored), "photometric": photometric,
                "voi_policy": voi_policy, "orientation_changed": False,
                "display_conversion": "modality_then_optional_voi_then_valid_minmax_uint8-v1"}
    return output, metadata
