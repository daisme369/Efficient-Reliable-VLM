"""Original-coordinate annotation parsing; no inferred QA-to-finding targets."""
from __future__ import annotations

import math

from cxr_research.preprocessing.labels import label_state
from cxr_research.utils.errors import ContractError


def validate_box(box, width, height):
    if width <= 0 or height <= 0 or len(box) != 4:
        raise ContractError("invalid box dimensions")
    x1, y1, x2, y2 = map(float, box)
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)) or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ContractError("box is invalid or outside original image coordinates")
    return x1, y1, x2, y2


def transform_box(box, geometry, *, inverse=False):
    """Use the exact rounded image resize and padding, including inverse mapping."""
    g = geometry
    if inverse:
        x1, y1, x2, y2 = box
        original = ((x1-g.left)*g.original_width/g.width, (y1-g.top)*g.original_height/g.height,
                    (x2-g.left)*g.original_width/g.width, (y2-g.top)*g.original_height/g.height)
        return validate_box(original, g.original_width, g.original_height)
    x1, y1, x2, y2 = validate_box(box, g.original_width, g.original_height)
    return (x1*g.width/g.original_width+g.left, y1*g.height/g.original_height+g.top,
            x2*g.width/g.original_width+g.left, y2*g.height/g.original_height+g.top)


def scene_regions(graph, *, width, height, strict=True):
    """Keep anatomical boxes and explicit yes/no attributes, omitting report text."""
    regions = {}
    for item in graph.get("objects", []):
        name = item["bbox_name"]
        if name in regions:
            raise ContractError("duplicate scene-graph anatomical region")
        try:
            box = validate_box([item["original_"+key] for key in ("x1", "y1", "x2", "y2")], width, height)
            status = "valid"
        except (ContractError, KeyError, TypeError, ValueError):
            if strict:
                raise
            box, status = None, "invalid_source_box"
        regions[name] = {"region": name, "xyxy": box, "box_status": status,
                         "attributes": [], "source": "chest-imagenome-silver"}
    for item in graph.get("attributes", []):
        name = item["bbox_name"]
        if name not in regions:
            if strict:
                raise ContractError("attribute has no matching anatomical region")
            regions[name] = {"region": name, "xyxy": None, "box_status": "unlocalized_attribute",
                             "attributes": [], "source": "chest-imagenome-silver"}
        for group in item.get("attributes", []):
            for token in group:
                parts = token.split("|")
                if len(parts) != 3:
                    raise ContractError("invalid scene-graph attribute encoding")
                category, relation, label = parts
                if not category or not label:
                    raise ContractError("empty scene-graph attribute")
                state = label_state(relation, {"yes": "positive", "no": "negative"})
                regions[name]["attributes"].append({"category": category, "label": label, "state": state})
    return tuple(regions.values())
