"""Inventory source program IDs separately from their surface-text variants."""
from collections import Counter
import json

from cxr_research.utils.errors import ContractError
from cxr_research.utils.hashing import file_hash


def review_programs(output):
    programs = {}
    for split in ("train", "validation"):
        with (output / f"vqa.processed.{split}.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                program = row["template_program"]
                if not isinstance(program, str) or not program:
                    raise ContractError("source template program ID is missing")
                item = programs.setdefault(program, {"template_program": program, "counts": Counter(),
                                                    "template_variants": Counter(), "semantic_types": Counter(),
                                                    "content_types": Counter(), "eligibility": "unreviewed"})
                item["counts"][split] += 1
                item["counts"]["empty_answer_array"] += row["answer"] == []
                item["template_variants"][row["template"]] += 1
                item["semantic_types"][str(row["semantic_type"])] += 1
                item["content_types"][str(row["content_type"])] += 1
    target = output / "program-review.jsonl"
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        for program in sorted(programs):
            stream.write(json.dumps(programs[program], ensure_ascii=False, sort_keys=True) + "\n")
    return {"source_program_ids": len(programs), "eligible_programs_selected": False,
            "output_sha256": file_hash(target)}
