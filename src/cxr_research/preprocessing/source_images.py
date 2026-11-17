"""Validate source color modes before grayscale conversion."""
from cxr_research.utils.errors import ContractError


def check_source_mode(image):
    if image.mode == "RGBA":
        if image.getchannel("A").getextrema() != (255, 255):
            raise ContractError("source RGBA contains non-opaque alpha")
    elif image.mode not in {"L", "RGB"}:
        raise ContractError("source image requires an explicit bit-depth conversion")
