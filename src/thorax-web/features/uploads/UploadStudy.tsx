'use client';

import { useState } from 'react';
import { Confirm } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import type { StudyDetail, View } from '@/lib/contracts';

const MAX_BYTES = 10 * 1024 * 1024;
const MAX_FILES = 3;

function localToday() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

/** Adds a study with 1–3 chest X-ray images. The server re-checks everything and strips image metadata. */
export function UploadStudy({ patientId, patientLabel, onClose, onUploaded }: {
  patientId: number; patientLabel: string; onClose: () => void;
  /** `complete` is false when the study was created but a later image failed; the dialog then stays open with the error. */
  onUploaded: (study: StudyDetail, complete: boolean) => void;
}) {
  const [date, setDate] = useState(localToday);
  const [indication, setIndication] = useState('');
  const [files, setFiles] = useState<{ file: File; view: View }[]>([]);
  const [permitted, setPermitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');

  function choose(list: FileList | null) {
    const chosen = Array.from(list ?? []).slice(0, MAX_FILES);
    setFiles(chosen.map((file, index) => ({ file, view: index === 0 ? 'PA' : 'LAT' })));
    setProblem(list && list.length > MAX_FILES ? `Only the first ${MAX_FILES} images are used.` : '');
  }

  async function save() {
    if (!indication.trim()) return setProblem('Enter the indication.');
    if (!files.length) return setProblem('Choose at least one PNG or JPEG image.');
    const bad = files.find(({ file }) => !['image/png', 'image/jpeg'].includes(file.type) || file.size > MAX_BYTES);
    if (bad) return setProblem(`${bad.file.name}: use a PNG or JPEG image of 10 MiB or less.`);
    if (!permitted) return setProblem('Confirm the images are synthetic or permitted for this demo.');
    setBusy(true); setProblem('');
    let study: StudyDetail | undefined;
    try {
      study = await api.createStudy(patientId, { study_date: date, indication: indication.trim(), view: files[0].view }, files[0].file);
      for (const { file, view } of files.slice(1)) study = await api.addStudyImage(study.id, view, file);
      onUploaded(study, true);
    } catch (failure) {
      const reason = failure instanceof ApiError ? (failure.status === 404 && !study ? 'Uploads need the Thorax backend, or this patient is no longer assigned to you.' : failure.message) : 'Could not reach the server. Try again.';
      if (study) {
        // The study exists already: refresh the lists and say which images are missing.
        onUploaded(study, false);
        setProblem(`The study was created with ${study.image_count} of ${files.length} images. ${reason}`);
      } else setProblem(reason);
      setBusy(false);
    }
  }

  return <Confirm title={`Add a study for ${patientLabel}`} action="Upload study" pending={busy} onCancel={onClose} onConfirm={() => void save()}>
    <div className="upload-fields">
      <label className="field">Study date<input type="date" required max={localToday()} value={date} onChange={event => setDate(event.target.value)} /></label>
      <label className="field">Indication<input maxLength={200} placeholder="For example: Persistent cough" value={indication} onChange={event => setIndication(event.target.value)} /></label>
      <label className="field">Images (PNG or JPEG, up to {MAX_FILES}, 10 MiB each)
        <input type="file" accept="image/png,image/jpeg" multiple onChange={event => choose(event.target.files)} />
      </label>
      {files.map(({ file, view }, index) => <label key={`${file.name}-${index}`} className="field upload-view">
        <span className="upload-name">{file.name}</span>
        <select aria-label={`View for ${file.name}`} value={view} onChange={event => setFiles(files.map((item, i) => i === index ? { ...item, view: event.target.value as View } : item))}>
          <option value="PA">PA</option><option value="AP">AP</option><option value="LAT">Lateral</option>
        </select>
      </label>)}
      <label className="upload-permit"><input type="checkbox" checked={permitted} onChange={event => setPermitted(event.target.checked)} />
        <span>These images are synthetic, or I have permission to use them in this demo. They are not from a restricted dataset such as MIMIC.</span></label>
      <p className="muted">Image metadata (such as names in EXIF) is removed on upload. Images can’t be changed once a reading starts.</p>
      {problem && <p className="password-error" role="alert">{problem}</p>}
    </div>
  </Confirm>;
}
