# Demo image set decision (SCRUM-22)

Status: **proposed** by Cáp Việt Anh on 17/11/2026. Needs review by Gia Khánh (demo-data gate in `DETAILED_ACTION_PLAN.md`, phase A1) before the walkthrough or video uses it.

## Decision

Use the **Random Sample of NIH Chest X-ray Dataset** on Kaggle for the app walkthrough, supervisor demo and video.

| Item | Value |
|---|---|
| Kaggle dataset | [`nih-chest-xrays/sample`](https://www.kaggle.com/datasets/nih-chest-xrays/sample), "Random Sample of NIH Chest X-ray Dataset" |
| Publisher | National Institutes of Health Chest X-Ray Dataset (Kaggle owner) |
| License shown on Kaggle | **CC0: Public Domain** (read from the Kaggle API on 17/11/2026; the full set [`nih-chest-xrays/data`](https://www.kaggle.com/datasets/nih-chest-xrays/data) shows the same) |
| NIH terms | "There are no restrictions on the use of the NIH chest x-ray images." ([Google Cloud copy of the NIH terms](https://docs.cloud.google.com/healthcare-api/docs/resources/public-datasets/nih-chest)) |
| Size | 5,606 PNG images, 1024 × 1024, about 2.25 GB (sample of the full 112,120 images from 30,805 patients) |
| Labels | 14 findings plus "No Finding", text-mined from reports (NIH estimates >90% accurate); view position, age and sex per image |
| Origin | NIH Clinical Center, Bethesda, Maryland; released 2017 |

### Attribution (required by NIH, must appear in the video credits, report and app About text)

1. Link to the NIH download site: https://nihcc.app.box.com/v/ChestXray-NIHCC
2. Acknowledge that the NIH Clinical Center is the data provider.
3. Cite: Xiaosong Wang, Yifan Peng, Le Lu, Zhiyong Lu, Mohammadhadi Bagheri, Ronald Summers. ChestX-ray8: Hospital-scale Chest X-ray Database and Benchmarks on Weakly-Supervised Classification and Localization of Common Thorax Diseases. IEEE CVPR, pp. 3462-3471, 2017.

### Not a MIMIC copy

- The images come from the NIH Clinical Center. MIMIC-CXR comes from Beth Israel Deaconess Medical Center, a different hospital, and was released in 2019, two years after this set.
- File names follow NIH's `<patient>_<follow-up>.png` pattern (for example `00000013_005.png`), not MIMIC's study and DICOM identifiers.
- The set is published by NIH itself, not re-uploaded by a third party.

### Why this set

- Adult chest X-rays with PA views, matching the app's use (pediatric sets such as the Kaggle pneumonia set do not).
- The clearest permission found: CC0 plus NIH's "no restrictions" statement. Other Kaggle sets checked carry non-commercial or no-derivatives terms, per-image licenses, or competition-only rules.
- Its labels map onto most of the app's finding list (see the mapping in `scripts/demo_data/select_nih_demo.py`).

## Rules for using it

- **Never commit the images.** Keep them outside the repository, for example `D:\thorax-demo-images`.
- **Upload through the app** (Admin → Patients → Manage studies → Add study), ticking the "synthetic or permitted" box.
- **Use made-up patient names** such as "Demo patient 01" when creating patients. Do not use NIH patient IDs as names. Age and sex may be copied from the label file.
- **Check each chosen image by eye** before uploading, and reject any with burned-in text, names or dates. NIH states the set is de-identified, but checking costs little.
- **Label scores as uncalibrated.** Model confidence on these images is outside the MIMIC calibration and must say so in the app and video (SCRUM-14, SCRUM-28).
- **Not a benchmark.** NIH labels are text-mined and noisy. Results on these images are demonstrations, not accuracy claims.

## Choosing the images

1. Download the sample from Kaggle in a browser (sign-in needed): `sample.zip` with `sample_labels.csv` and `images/`. Unzip it outside the repository.
2. Run:

   ```powershell
   python scripts/demo_data/select_nih_demo.py --labels <unzipped>\sample\sample_labels.csv --images <unzipped>\sample\images --out D:\thorax-demo-images --per-finding 2
   ```

   It picks up to 2 images per finding (pleural effusion, pneumothorax, enlarged heart, atelectasis, consolidation, edema, nodule/mass, and "No Finding"). It only takes adults aged 18–90, PA views, single-label images and one image per patient, in a fixed order, so the same file always gives the same list. It writes `selection.csv` next to the copied images.
3. Check each image by eye (rule above), then upload them.
4. Record the final file names in the table below.

| Image file | NIH label | Uploaded as | Checked by eye |
|---|---|---|---|
| *(to fill after step 2)* | | | |

## Walkthrough questions (unvalidated demo questions)

These are hand-written, template-style questions for showing the app. They are **not** benchmark items, were not taken from MIMIC rows, and their answers are not validated.

1. Is there a pleural effusion?
2. Is there a pneumothorax?
3. Is the cardiac silhouette enlarged?
4. Are there any lung nodules or masses?
5. Is there consolidation in the lungs?
6. Is there evidence of pulmonary edema?
7. Which side shows the abnormality, left or right?
8. Does this chest X-ray look normal?
9. (With a drawn region) What is seen in this region?

## SCRUM-22 acceptance criteria

| Criterion | Status |
|---|---|
| Record source, license and display rights; check it is not a MIMIC copy | Done (this file); waiting for Gia Khánh's review |
| Template-style walkthrough questions, labeled unvalidated | Done (above) |
| Freeze the response fields: answer, grounding or unavailable state, confidence/risk definition, abstain decision, latency | Defined in `SYSTEM_DESIGN_PLAN.md` section 9.1. The app shows answers and latency; confidence, threshold, abstention and real grounding are still open in SCRUM-14, SCRUM-27 and SCRUM-28 |
| Synthetic input/output smoke test, with unavailable outputs shown honestly | Done: backend tests and 10 browser tests on synthetic data (SCRUM-36) |
