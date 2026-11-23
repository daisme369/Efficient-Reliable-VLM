# Scope-change record (DRAFT): send rejected AI regions to labeling

Status: draft for review, not approved. Nothing in this record is implemented. Format: `docs/agents/RESEARCH-SCOPE.md` section 12.

```text
Change requested:
  When a doctor rejects or changes an AI-proposed region in the Thorax web app, create a labeling
  task for that image, pre-filled with the AI box. Annotators correct it and a second annotator
  reviews it (the existing labeler -> reviewer flow). Approved corrections are exported as labels
  that may later be used to fine-tune or evaluate the grounding model ("continuous learning").

Evidence or reason:
  Rejected regions are the model's errors, so they are the most useful images to label next.
  The app already records each region's review action (accepted/rejected) and has a labeling
  workflow with review and JSONL export. No evidence yet: AI regions are fixed demo answers until
  the trained model is connected, so this has no value before that.

Affected research question/claim:
  Adds a new label source for anatomical grounding beyond Chest ImaGenome (DOMAIN-NOTES D7-a).
  Any grounding result trained or evaluated on these labels is a different claim from one that
  uses Chest ImaGenome only, and must say so.

Affected dataset, metric, code, timeline, and compute:
  - Dataset: new app-labeled set. Only synthetic images, or images the uploader confirmed are
    permitted, can enter it; restricted MIMIC data must never be uploaded to the app. Region
    names must use the verified vocabulary (configs/labeling_vocabulary.json, labeling-vocab-v1).
  - Split: labels from an image must stay in one split. Test-split images must never be sent to
    labeling, to avoid leakage.
  - Metrics: grounding metrics (B3, M1) need to report which label source they use.
  - Code: src/thorax_api (reading.py hook on region rejection, labeling.py task source field),
    the labeling UI (show the AI box as a starting point), and the export manifest.
  - Timeline: after the model is connected; not before the 06/12/2026 demo unless the team
    decides otherwise.
  - Compute: any retraining on the new labels is a new GPU budget item.

Experiments that must be rerun:
  Any grounding evaluation or fine-tuning that adds these labels. Existing baselines stay as they
  are and remain comparable only on the unchanged Chest ImaGenome test split.

Work displaced by this change:
  Connecting the trained model, real AI outputs and deployment come first; this would displace
  later labeling and evaluation work.

Decision: pending (accept / reject / defer)
Approvers: Team Lead + Research Lead
```

After approval, update the Jira backlog and `DETAILED_ACTION_PLAN.md` before any code change.
