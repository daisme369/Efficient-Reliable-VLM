'use client';

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Confirm, formatDate } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import type { StudyDetail, StudyDTO, View } from '@/lib/contracts';

const MAX_BYTES = 10 * 1024 * 1024;
const MAX_FILES = 3;
const viewLabel: Record<View, string> = { PA: 'PA', AP: 'AP', LAT: 'Lateral' };

function localToday() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

const reason = (failure: unknown) => failure instanceof ApiError ? failure.message : 'Could not reach the server. Try again.';

/**
 * Adds a study with 1–3 chest X-ray images, or with `study` adds images to that study.
 * The server re-checks everything and strips image metadata.
 */
export function UploadStudy({ patientId, patientLabel, study: existing, onClose, onUploaded }: {
  patientId: number; patientLabel: string; study?: StudyDTO; onClose: () => void;
  /** `complete` is false when only some images were saved; the dialog then stays open with the error. */
  onUploaded: (study: StudyDetail, complete: boolean) => void;
}) {
  const [date, setDate] = useState(localToday);
  const [indication, setIndication] = useState('');
  const [files, setFiles] = useState<{ file: File; view: View }[]>([]);
  const [permitted, setPermitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const room = existing ? MAX_FILES - existing.image_count : MAX_FILES;

  function choose(list: FileList | null) {
    const chosen = Array.from(list ?? []).slice(0, Math.max(room, 0));
    setFiles(chosen.map((file, index) => ({ file, view: index === 0 && !existing ? 'PA' : 'LAT' })));
    setProblem(list && list.length > room ? `Only the first ${room} ${room === 1 ? 'image is' : 'images are'} used: a study holds up to ${MAX_FILES}.` : '');
  }

  async function save() {
    if (!existing && !indication.trim()) return setProblem('Enter the indication.');
    if (!files.length) return setProblem('Choose at least one PNG or JPEG image.');
    const bad = files.find(({ file }) => !['image/png', 'image/jpeg'].includes(file.type) || file.size > MAX_BYTES);
    if (bad) return setProblem(`${bad.file.name}: use a PNG or JPEG image of 10 MiB or less.`);
    if (!permitted) return setProblem('Confirm the images are synthetic or permitted for this demo.');
    setBusy(true); setProblem('');
    let study: StudyDetail | undefined;
    let saved = 0;
    try {
      const queue = [...files];
      if (!existing) {
        const first = queue.shift()!;
        study = await api.createStudy(patientId, { study_date: date, indication: indication.trim(), view: first.view }, first.file);
        saved += 1;
      }
      for (const { file, view } of queue) { study = await api.addStudyImage(existing?.id ?? study!.id, view, file); saved += 1; }
      onUploaded(study!, true);
    } catch (failure) {
      const why = failure instanceof ApiError && failure.status === 404 && !study ? 'Uploads need the Thorax backend, or this patient is no longer assigned to you.' : reason(failure);
      if (study) {
        onUploaded(study, false);  // refresh the lists: part of the upload was saved
        setProblem(`${saved} of ${files.length} images were saved. ${why}`);
      } else setProblem(why);
      setFiles(files.slice(saved));
      setBusy(false);
    }
  }

  return <Confirm title={existing ? `Add images to the study of ${formatDate(existing.study_date)}` : `Add a study for ${patientLabel}`}
    action={existing ? 'Upload images' : 'Upload study'} pending={busy} onCancel={onClose} onConfirm={() => void save()}>
    <div className="upload-fields">
      {!existing && <>
        <label className="field">Study date<input type="date" required max={localToday()} value={date} onChange={event => setDate(event.target.value)} /></label>
        <label className="field">Indication<input maxLength={200} placeholder="For example: Persistent cough" value={indication} onChange={event => setIndication(event.target.value)} /></label>
      </>}
      {room > 0 ? <label className="field">Images (PNG or JPEG, up to {room}, 10 MiB each)
        <input type="file" accept="image/png,image/jpeg" multiple={room > 1} onChange={event => choose(event.target.files)} />
      </label> : <p>This study already has {MAX_FILES} images, the most a study can hold.</p>}
      {files.map(({ file, view }, index) => <label key={`${file.name}-${index}`} className="field upload-view">
        <span className="upload-name">{file.name}</span>
        <select aria-label={`View for ${file.name}`} value={view} onChange={event => setFiles(files.map((item, i) => i === index ? { ...item, view: event.target.value as View } : item))}>
          {(Object.keys(viewLabel) as View[]).map(v => <option key={v} value={v}>{viewLabel[v]}</option>)}
        </select>
      </label>)}
      <label className="upload-permit"><input type="checkbox" checked={permitted} onChange={event => setPermitted(event.target.checked)} />
        <span>These images are synthetic, or I have permission to use them in this demo. They are not from a restricted dataset such as MIMIC.</span></label>
      <p className="muted">Image metadata (such as names in EXIF) is removed on upload. You can edit or delete an uploaded study until a reading starts.</p>
      {problem && <p className="password-error" role="alert">{problem}</p>}
    </div>
  </Confirm>;
}

/** Edit the date and indication, remove single images, or delete an uploaded study before any reading starts. */
export function EditStudy({ study, onClose, onChanged }: {
  study: StudyDTO; onClose: () => void; onChanged: (message: string, closed: boolean) => void;
}) {
  const detail = useQuery({ queryKey: ['study-edit', study.id], queryFn: ({ signal }) => api.study(study.id, signal) });
  const [date, setDate] = useState(study.study_date);
  const [indication, setIndication] = useState(study.indication);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const images = detail.data?.images ?? [];

  async function run(work: () => Promise<unknown>, message: string, closed: boolean) {
    setBusy(true); setProblem('');
    try {
      await work();
      onChanged(message, closed);
      if (!closed) await detail.refetch();
    } catch (failure) { setProblem(reason(failure)); }
    finally { setBusy(false); }
  }

  return <Confirm title={`Edit the study of ${formatDate(study.study_date)}`} action="Save changes" pending={busy} onCancel={onClose}
    onConfirm={() => void run(() => api.editStudy(study.id, { study_date: date, indication: indication.trim() }), 'Study details saved.', true)}>
    <div className="upload-fields">
      <label className="field">Study date<input type="date" required max={localToday()} value={date} onChange={event => setDate(event.target.value)} /></label>
      <label className="field">Indication<input maxLength={200} value={indication} onChange={event => setIndication(event.target.value)} /></label>
      <div className="field">Images
        {detail.isPending ? <p className="muted">Loading images…</p> : detail.isError ? <p className="password-error">Could not load the images.</p> :
          <ul className="upload-images">{images.map(image => <li key={image.id}>
            <span>{viewLabel[image.view]} · {image.width} × {image.height}</span>
            <button type="button" className="button ghost danger-text" disabled={busy || images.length === 1}
              title={images.length === 1 ? 'A study needs at least one image. Delete the study instead.' : undefined}
              onClick={() => void run(() => api.deleteStudyImage(study.id, image.id), `Removed the ${viewLabel[image.view]} image.`, false)}>Remove</button>
          </li>)}</ul>}
      </div>
      <div className="upload-delete">
        {confirmDelete
          ? <><span>Delete this study and its {study.image_count} {study.image_count === 1 ? 'image' : 'images'}? This can’t be undone.</span>
            <button type="button" className="button danger" disabled={busy} onClick={() => void run(() => api.deleteStudy(study.id), 'Study deleted.', true)}>Delete study</button>
            <button type="button" className="button ghost" disabled={busy} onClick={() => setConfirmDelete(false)}>Keep it</button></>
          : <button type="button" className="button ghost danger-text" disabled={busy} onClick={() => setConfirmDelete(true)}>Delete study…</button>}
      </div>
      {problem && <p className="password-error" role="alert">{problem}</p>}
    </div>
  </Confirm>;
}
