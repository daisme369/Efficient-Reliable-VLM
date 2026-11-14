"use client";

import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, MessageSquare, Trash2 } from "lucide-react";
import { useId, useRef, type FormEvent, type KeyboardEvent } from "react";
import type { BoxCoordinates, BoxDTO } from "../../lib/contracts";
import { moveBounds, validBounds, type Point, type Size } from "./imageCoordinates";
import styles from "./StudyViewer.module.css";

export interface RegionDraft {
  id: number | null;
  precise: BoxCoordinates;
  fields: [string, string, string, string];
  label: string;
  note: string;
}

export interface RegionChanges {
  image_id?: number;
  bbox_xyxy?: BoxCoordinates;
  label?: string | null;
  note?: string | null;
  review_action?: "accepted" | "edited" | "rejected";
}

export function withBounds(draft: RegionDraft, precise: BoxCoordinates): RegionDraft {
  const [x1, y1, x2, y2] = precise;
  return { ...draft, precise, fields: [x1.toFixed(2), y1.toFixed(2), x2.toFixed(2), y2.toFixed(2)] };
}

export function makeRegionDraft(box: BoxDTO | null, bounds: BoxCoordinates): RegionDraft {
  return withBounds({
    id: box?.id ?? null,
    precise: bounds,
    fields: ["", "", "", ""],
    label: box?.label ?? "",
    note: box?.note ?? "",
  }, bounds);
}

export function draftBounds(draft: RegionDraft): BoxCoordinates {
  function read(index: number) {
    const value = draft.fields[index];
    if (value === draft.precise[index].toFixed(2)) return draft.precise[index];
    return value.trim() === "" ? NaN : Number(value);
  }
  return [read(0), read(1), read(2), read(3)];
}

export function regionChanges(draft: RegionDraft, box: BoxDTO | null, imageId: number): RegionChanges {
  const bbox = draftBounds(draft);
  const label = draft.label || null;
  const note = draft.note || null;
  if (!box) return { image_id: imageId, bbox_xyxy: bbox, label, note };
  const changes: RegionChanges = {};
  if (bbox.some((value, index) => value !== box.bbox_xyxy[index])) changes.bbox_xyxy = bbox;
  if (label !== (box.label ?? null)) changes.label = label;
  if (note !== (box.note ?? null)) changes.note = note;
  if (box.source === "ai" && Object.keys(changes).length) changes.review_action = "edited";
  return changes;
}

/** Doctor-facing name: position among the image's active, non-rejected regions. */
export function regionName(boxes: BoxDTO[], box: BoxDTO) {
  const index = boxes.filter(other => other.image_id === box.image_id && !other.deleted_at && other.review_action !== "rejected").findIndex(other => other.id === box.id);
  return index < 0 ? "Removed region" : `R${index + 1}`;
}

export function regionState(box: BoxDTO) {
  if (box.review_action === "edited") return "Doctor edited";
  if (box.review_action === "accepted") return "Doctor accepted";
  if (box.review_action === "rejected") return "Rejected";
  return box.source === "doctor" ? "Doctor added" : "AI suggestion — needs doctor review";
}

const edges = ["x1", "y1", "x2", "y2"] as const;
const directions = [
  { label: "Move left", Icon: ArrowLeft, x: -1, y: 0 },
  { label: "Move right", Icon: ArrowRight, x: 1, y: 0 },
  { label: "Move up", Icon: ArrowUp, x: 0, y: -1 },
  { label: "Move down", Icon: ArrowDown, x: 0, y: 1 },
];

interface RegionEditorProps {
  image: Size;
  draft: RegionDraft;
  box: BoxDTO | null;
  name?: string;
  canAsk: boolean;
  busy: boolean;
  dirty: boolean;
  findingsApproved?: boolean;
  onChange: (draft: RegionDraft) => void;
  onSave: () => void;
  onCancel: () => void;
  onReview: (action: "accepted" | "rejected") => void;
  onDelete: () => void;
  onAsk: () => void;
}

export default function RegionEditor({ image, draft, box, name, canAsk, busy, dirty, findingsApproved, onChange, onSave, onCancel, onReview, onDelete, onAsk }: RegionEditorProps) {
  const id = useId();
  const confirmRemove = useRef<HTMLDialogElement>(null);
  const removeButton = useRef<HTMLButtonElement>(null);
  const bounds = draftBounds(draft);
  const valid = validBounds(bounds, image);
  const pending = box?.source === "ai" && box.review_action === "pending";

  function changeField(index: number, value: string) {
    const fields = [...draft.fields] as RegionDraft["fields"];
    fields[index] = value;
    onChange({ ...draft, fields });
  }

  function stepField(event: KeyboardEvent<HTMLInputElement>, index: number) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const direction = event.key === "ArrowUp" ? 1 : -1;
    const distance = (event.shiftKey ? 10 : 1) * direction;
    const base = Number.isFinite(bounds[index]) ? bounds[index] : draft.precise[index];
    const limit = index % 2 === 0 ? image.width : image.height;
    const value = Math.max(0, Math.min(limit, base + distance));
    const precise = [...draft.precise] as BoxCoordinates;
    const fields = [...draft.fields] as RegionDraft["fields"];
    precise[index] = value;
    fields[index] = value.toFixed(2);
    onChange({ ...draft, precise, fields });
  }

  function move(delta: Point, shift: boolean) {
    if (!valid) return;
    const factor = shift ? 10 : 1;
    onChange(withBounds(draft, moveBounds(bounds, { x: delta.x * factor, y: delta.y * factor }, image)));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (valid && dirty && !busy) onSave();
  }

  return (
    <form className={styles.regionEditor} onSubmit={submit} onKeyDown={(event) => {
      if (event.key === "Escape" && dirty && !busy && !confirmRemove.current?.open) {
        event.preventDefault();
        onCancel();
      }
    }} noValidate>
      <div className={styles.editorTitle}>
        <h4>{box ? `${name ?? "Region"}${box.label ? ` · ${box.label}` : ""}` : "New region"}</h4>
        <span>{dirty ? "Unsaved changes" : "Saved"}</span>
      </div>
      {box && <p className={styles.regionProvenance}>
        {box.source === "ai" && box.review_action !== "pending" ? `AI suggestion · ${regionState(box)}` : regionState(box)}
      </p>}
      {findingsApproved && <p className={styles.regionWarning}>Saving this change creates a new findings version and makes any draft report stale.</p>}
      <fieldset disabled={busy}>
        <legend>Original image coordinates (px)</legend>
        <div className={styles.coordinateGrid}>
          {edges.map((edge, index) => <label key={edge} htmlFor={`${id}-${edge}`}>
            {edge}
            <input id={`${id}-${edge}`} type="number" inputMode="decimal" step="any" value={draft.fields[index]} aria-invalid={!valid} aria-describedby={!valid ? `${id}-bounds` : undefined} onKeyDown={(event) => stepField(event, index)} onChange={(event) => changeField(index, event.target.value)} />
          </label>)}
        </div>
        {!valid && <p id={`${id}-bounds`} className={styles.fieldError}>Enter finite coordinates within {image.width} × {image.height} px. x1 and y1 must precede x2 and y2.</p>}
        <div className={styles.moveButtons} aria-label="Move region in source pixels" role="group">
          {directions.map(({ label, Icon, x, y }) => <button type="button" key={label} className={styles.lightIconButton} aria-label={label} title={`${label} one pixel (Shift for ten)`} disabled={!valid} onClick={(event) => move({ x, y }, event.shiftKey)}><Icon size={16} aria-hidden="true" /></button>)}
        </div>
      </fieldset>
      <div className={styles.editorFields}>
        <label htmlFor={`${id}-label`}>Label <input id={`${id}-label`} maxLength={200} value={draft.label} disabled={busy} onChange={(event) => onChange({ ...draft, label: event.target.value })} /></label>
        <label htmlFor={`${id}-note`}>Note <textarea id={`${id}-note`} maxLength={2000} rows={2} value={draft.note} disabled={busy} onChange={(event) => onChange({ ...draft, note: event.target.value })} /></label>
      </div>
      <div className={styles.editorActions}>
        {dirty && <><button type="submit" className={styles.primaryButton} disabled={!valid || busy}>{busy ? "Saving…" : "Save region"}</button><button type="button" disabled={busy} onClick={onCancel}>Cancel</button></>}
        {pending && !dirty && <><button type="button" className={styles.primaryButton} disabled={busy} onClick={() => onReview("accepted")}>Accept region</button><button type="button" disabled={busy} onClick={() => onReview("rejected")}>Reject</button></>}
        {box && !dirty && <>
          <button type="button" disabled={!canAsk || busy} onClick={onAsk}><MessageSquare size={16} aria-hidden="true" /> Ask about this region</button>
          <button type="button" ref={removeButton} className={styles.lightIconButton} aria-label="Remove region" title="Remove region" disabled={busy} onClick={() => confirmRemove.current?.showModal()}><Trash2 size={16} aria-hidden="true" /></button>
        </>}
      </div>
      <dialog ref={confirmRemove} className={styles.removeDialog} aria-labelledby={`${id}-remove-title`} onClose={() => removeButton.current?.focus()}>
        <h4 id={`${id}-remove-title`}>Remove this region?</h4>
        <p>Its submitted question history is retained.</p>
        {findingsApproved && <p className={styles.regionWarning}>Removing this region creates a new findings version and makes any draft report stale.</p>}
        <div className={styles.editorActions}>
          <button type="button" autoFocus onClick={() => confirmRemove.current?.close()}>Keep region</button>
          <button type="button" className={styles.dangerButton} onClick={() => { confirmRemove.current?.close(); onDelete(); }}>Remove region</button>
        </div>
      </dialog>
    </form>
  );
}
