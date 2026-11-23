# Capstone A video and MVP demonstration package (SCRUM-11)

Draft by Nguyễn Văn Tuấn Kiệt, 23 November 2026, for review by Giáp Hoàng Gia Khánh. Due 6 December 2026, 20:59. The recording itself, the rehearsal and the review still need the team; this file fixes what to show, who says it and where every number comes from.

## Rules for the recording

- Target length is 10 minutes. All four members speak.
- **Never show:** MIMIC images, reports, patient or study identifiers, row-level predictions, or the bundle's `restricted/` folder. The same applies to rental host details, keys and passwords: sign in before recording, or blur the password field.
- **Demo images:** use only the NIH Kaggle sample in [`docs/demo-data/README.md`](docs/demo-data/README.md), and only after Gia Khánh approves it. Otherwise use the synthetic phantom images. Put the NIH attribution in the credits.
- **Label demo output.** Every AI answer, score or region on demo images is labelled "demo only — not calibrated for this image source". The walkthrough questions are demo prompts, not benchmark items.
- **Show only numbers from the table below**, with their scope stated out loud.

## Numbers allowed on screen

| Claim | Value | Source | Scope to state |
| --- | --- | --- | --- |
| B1 closed-ended accuracy | 81.4% (1,706 questions); macro-F1 0.433 | B1 bundle `completion-report.json`, run `b1-fast-demo-20261030-v2`, validation | Secondary MIMIC validation, one seed, exploratory; test sealed |
| B1 open-ended normalized match | 56.6% (708 questions) | same | same; normalized match is not clinical correctness |
| B1 trainable parameters | 14.9 M (LoRA r=8, language projections) | bundle `downloads/training/metrics.json` | BF16 base frozen |
| Software tests | 89 backend API tests and the full repository suite pass; CI green on `kiet` | `THORAX_HANDOFF.md`, GitHub Actions run on `3b32a6c` | Synthetic data |
| Worklist / viewer latency | p95 31.9 ms / 65.0 ms over 20 runs | `THORAX_HANDOFF.md` | Development PC, not demo hardware; re-measure on the demo laptop |
| Real-AI latency | Measure on the GPU host with `verify_demo.py journey` | — | Show only if measured with the real adapter |

Do **not** claim calibration, abstention quality, grounding accuracy, M1 results, clinical correctness, or comparisons with Qwen/LLaVA: none were measured.

## Running order (about 10 minutes)

| Time | Speaker | Content | On screen |
| --- | --- | --- | --- |
| 0:00–1:00 | Giáp Hoàng Gia Khánh | Problem: reliable chest X-ray VQA; research questions RQ1–RQ3; what Capstone A delivers | Title and RQ slides |
| 1:00–2:30 | Cáp Việt Anh | Data: MIMIC-CXR-JPG, MIMIC-Ext-VQA, Chest ImaGenome; patient-level splits; privacy rules; demo data choice | Data pipeline diagram (no MIMIC images) |
| 2:30–4:00 | Lê Đức Anh | Model: MedGemma 1.5 4B, B0 → B1 BF16 LoRA, planned M1 grounding and risk; B1 validation numbers with scope | Model diagram, metrics slide |
| 4:00–7:30 | Nguyễn Văn Tuấn Kiệt | App walkthrough (steps below) | Live app or recording |
| 7:30–8:30 | Cáp Việt Anh | Architecture and deployment: Next.js, FastAPI + SQLite, model service on a rented GPU, Compose with HTTPS | Architecture slide |
| 8:30–9:30 | Giáp Hoàng Gia Khánh | Limitations and ethics: no clinical use, uncalibrated demo scores, test sealed, privacy controls | Limitations slide |
| 9:30–10:00 | Lê Đức Anh | Work after the course: M1 risk/abstention, counterfactual checks, distillation; thanks and credits | Credits with NIH attribution |

## Walkthrough steps (4:00–7:30)

The doctor journey matches `scripts/verify_demo.py journey`, so it can be checked before recording.

1. Sign in as a doctor; point out the VI/EN switch and the "mock AI" or "real AI" badge.
2. Worklist: only assigned patients; open a demo study.
3. Viewer: zoom, pan, brightness. Say that the original image is never changed.
4. **Analyze:** preliminary findings and AI regions appear, marked "AI suggestion — needs doctor review".
5. **Chat:** ask one walkthrough question, then draw a region and ask about it. Show that the answer keeps the region snapshot.
6. **Gate 1:** accept or reject each AI region and finding, then approve.
7. **Report:** generate the draft, edit one sentence, approve at Gate 2.
8. Reload the page: the reading is final and read-only.
9. Admin view (short): AI monitoring, and the pause switch with manual signing still possible.

Say which mode is running. If the real adapter is not served, say "this walkthrough uses the mock model; the trained B1 adapter needs a GPU host" (see `THORAX_HANDOFF.md`).

## Fallback, rehearsed before recording

1. **Primary:** live app with the real model service on the GPU host, if it passes `verify_demo.py journey` the same day.
2. **Fallback A:** the same walkthrough in mock mode on the development PC (`.claude/launch.json` or `src/thorax-web/README.md`).
3. **Fallback B:** a screen recording of fallback A made during rehearsal, kept with the slides.

## Acceptance checklist

| SCRUM-11 criterion | How this package meets it | Status |
| --- | --- | --- |
| Recording close to ten minutes | Running order totals 10:00 | Pending recording |
| All four members speak | Every member has a slot above | Pending recording |
| Every metric matches a versioned experiment; demo output labelled | Allowed-numbers table with sources; demo labelling rule | Ready |
| No restricted MIMIC content in the recording | Rules above; check the final cut frame by frame | Pending final check |
| Walkthrough has a rehearsed fallback | Fallbacks A and B | Pending rehearsal |
| Gia Khánh reviews the final package | Reviewer named above | Pending review |
