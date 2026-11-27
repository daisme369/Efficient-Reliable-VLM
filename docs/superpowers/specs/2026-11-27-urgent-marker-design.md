# Urgent studies easy to spot — short design

Date: 2026-11-27 · Branch: vietanh · Status: approved ("start", 2026-11-27)

**Intent.** A doctor should recognise an urgent study at a glance (user: "priority … easy for doctor to recognize, maybe a red border").
Today patients with an unsigned urgent study are sorted first, but nothing on the patient row says so; the "Urgent" badge only
shows after expanding the patient, and the reading screen shows no marker at all.

**Goals.**
- Patient row (worklist): red left border + "Urgent" badge while the patient has an urgent study not yet signed final.
  Backend adds `has_urgent: bool` to `GET /api/v1/patients`, the same rule that already sorts them first.
- Study row (expanded list): red left border on urgent studies not signed final; the existing badge stays.
- Reading screen: a red "Urgent" bar above the identity strip while the study is urgent and not final.
- The word is always shown with the colour (not colour alone). Vietnamese reuses the existing "Urgent" → "Khẩn" entry.

**Non-goals.** Notifications, sounds, timers/SLA, new priority levels, changes to the admin console.

**Anti-goals.** A marker that stays after the study is signed; colour as the only signal.

**Approaches.** (1) CSS + one backend field — chosen. (2) Compute urgency in the web by fetching every patient's studies — N extra
requests on each worklist load. (3) A separate "Urgent" worklist tab — more UI than asked.

**Left to the builder.** Exact styling (uses the existing `--error-text` colour), mock API: `has_urgent` is optional
("Thorax backend only", like `priority`), so the mock needs no change.

**Tests.** Backend: `has_urgent` true for patient 4 (study 2441 urgent), false after 2441 is signed. e2e (urgent preliminary test):
worklist row for patient 4 shows Urgent; the reading screen shows the bar; after the doctor signs, the bar is gone.
