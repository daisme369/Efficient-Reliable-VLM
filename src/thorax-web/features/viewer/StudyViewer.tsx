"use client";

import { BoxSelect, Contrast, Eye, EyeOff, Hand, Maximize, Minus, MousePointer2, Plus, SlidersHorizontal, Sun } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { BoxCoordinates, BoxDTO, BoxSnapshot, ImageDTO } from "../../lib/contracts";
import { drawBounds, imageTransform, moveBounds, resizeBounds, toSource, validBounds, zoomAt, type Corner, type Point } from "./imageCoordinates";
import RegionEditor, { draftBounds, makeRegionDraft, regionChanges, regionName, regionState, withBounds, type RegionChanges, type RegionDraft } from "./RegionEditor";
import styles from "./StudyViewer.module.css";

export type { RegionChanges } from "./RegionEditor";

interface ViewerProps {
  images: ImageDTO[];
  imageId: number | null;
  boxes: BoxDTO[];
  approvedSnapshot?: BoxSnapshot[] | null;
  selectedBoxId: number | null;
  canAnnotate: boolean;
  canAskRegion: boolean;
  readOnly: boolean;
  findingsApproved?: boolean;
  blockedReason?: string;
  linkedFindings?: Readonly<Record<number, string>>;
  loadImage: (imageId: number, signal: AbortSignal) => Promise<Blob>;
  onImageChange: (id: number) => void;
  onBoxSelect: (id: number | null) => void;
  onBoxSave: (id: number | null, changes: RegionChanges) => Promise<void>;
  onBoxDelete: (id: number) => Promise<void>;
  onAskRegion: (box: BoxDTO) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onImageStatus?: (ready: boolean) => void;
}

type Tool = "select" | "pan" | "draw";
type Asset = { state: "loading" | "error" } | { state: "ready"; bitmap: ImageBitmap };
type Gesture = { pointerId: number; origin: Point } & (
  { kind: "pan"; pan: Point } |
  { kind: "draw"; source: Point } |
  { kind: "move" | "resize"; source: Point; box: BoxDTO; bounds: BoxCoordinates; corner?: Corner }
);

const DEFAULT_VIEW = { zoom: 1, pan: { x: 0, y: 0 } };
const corners: Corner[] = ["top-left", "top-right", "bottom-left", "bottom-right"];
const panKeys: Record<string, Point> = {
  ArrowLeft: { x: -1, y: 0 }, ArrowRight: { x: 1, y: 0 },
  ArrowUp: { x: 0, y: -1 }, ArrowDown: { x: 0, y: 1 },
};

function sourceCorner(bounds: BoxCoordinates, corner: Corner): Point {
  return { x: bounds[corner.endsWith("right") ? 2 : 0], y: bounds[corner.startsWith("bottom") ? 3 : 1] };
}

function nearestCorner(bounds: BoxCoordinates, point: Point): Corner {
  let nearest = corners[0];
  let closestDistance = Infinity;
  for (const corner of corners) {
    const { x, y } = sourceCorner(bounds, corner);
    const distance = Math.hypot(point.x - x, point.y - y);
    if (distance < closestDistance) {
      closestDistance = distance;
      nearest = corner;
    }
  }
  return nearest;
}

function errorMessage(error: unknown) {
  const status = typeof error === "object" && error !== null && "status" in error ? error.status : undefined;
  if (status === 412) return "This reading changed in another tab. Reload before saving.";
  if (status === 409) return "This reading changed. Reload to check its current state.";
  if (status === 422) return "Check the region coordinates, label, and note, then try again.";
  return "The region change could not be saved. Try again.";
}

export default function StudyViewer(props: ViewerProps) {
  const image = props.images.find((candidate) => candidate.id === props.imageId);
  if (!image) return <section className={styles.viewer} aria-label="Study viewer"><div className={styles.emptyImage}><h2>Chest X-ray</h2><p>No images are available for this study.</p></div></section>;
  const boxes = props.readOnly && props.approvedSnapshot
    ? props.approvedSnapshot.flatMap(({ box_id, image_id, bbox_xyxy, revision }) => {
        const metadata = props.boxes.find((box) => box.id === box_id && box.image_id === image_id);
        return metadata ? [{ ...metadata, bbox_xyxy, revision, deleted_at: null }] : [];
      })
    : props.boxes;
  return <SelectedImageViewer key={image.id} image={image} {...props} boxes={boxes} />;
}

function SelectedImageViewer({ image, images, boxes, selectedBoxId, canAnnotate, canAskRegion, readOnly, findingsApproved, blockedReason, linkedFindings, loadImage, onImageChange, onBoxSelect, onBoxSave, onBoxDelete, onAskRegion, onDirtyChange, onImageStatus }: ViewerProps & { image: ImageDTO }) {
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const editorAnchor = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState({ width: 0, height: 0 });
  const [asset, setAsset] = useState<Asset>({ state: "loading" });
  const [retry, setRetry] = useState(0);
  const [view, setView] = useState(DEFAULT_VIEW);
  const [tool, setTool] = useState<Tool>("select");
  const [brightness, setBrightness] = useState(1);
  const [contrast, setContrast] = useState(1);
  const [showOverlays, setShowOverlays] = useState(true);
  const [showFilters, setShowFilters] = useState(false);
  const [showRegions, setShowRegions] = useState(false);
  const [draft, setDraft] = useState<RegionDraft | null>(null);
  const [preview, setPreview] = useState<BoxCoordinates | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ text: string; error: boolean } | null>(null);
  const activeBoxes = boxes.filter((box) => box.image_id === image.id && !box.deleted_at && box.review_action !== "rejected");
  const selected = activeBoxes.find((box) => box.id === selectedBoxId) ?? null;
  const transform = imageTransform(image, viewport, view);
  const ready = asset.state === "ready";
  const editable = canAnnotate && ready && !readOnly && !busy;
  const currentDraft = draft?.id === (selected?.id ?? null) ? draft : null;
  const editor = currentDraft ?? (selected ? makeRegionDraft(selected, selected.bbox_xyxy) : null);
  const dirty = Boolean(currentDraft && Object.keys(regionChanges(currentDraft, selected, image.id)).length);

  useEffect(() => {
    const controller = new AbortController();
    let bitmap: ImageBitmap | undefined;
    setAsset({ state: "loading" });
    async function decode() {
      try {
        const blob = await loadImage(image.id, controller.signal);
        if (controller.signal.aborted) return;
        if (blob.type !== "image/png" && blob.type !== "image/jpeg") throw new Error("Unsupported image type");
        bitmap = await createImageBitmap(blob, { imageOrientation: "none" });
        if (controller.signal.aborted) return bitmap.close();
        if (bitmap.width !== image.width || bitmap.height !== image.height) throw new Error("Unexpected image dimensions");
        setAsset({ state: "ready", bitmap });
      } catch {
        bitmap?.close();
        bitmap = undefined;
        if (!controller.signal.aborted) setAsset({ state: "error" });
      }
    }
    void decode();
    return () => { controller.abort(); bitmap?.close(); };
  }, [image.id, image.width, image.height, loadImage, retry]);

  useEffect(() => { onImageStatus?.(ready); }, [ready, onImageStatus]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  const addingRegion = currentDraft?.id === null;
  useEffect(() => {
    if (addingRegion) editorAnchor.current?.focus();
  }, [addingRegion]);

  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const update = () => setViewport({ width: element.clientWidth, height: element.clientHeight });
    const observer = new ResizeObserver(update);
    observer.observe(element);
    window.addEventListener("resize", update);
    update();
    return () => { observer.disconnect(); window.removeEventListener("resize", update); };
  }, []);

  useEffect(() => {
    if (asset.state !== "ready" || !canvas.current || !viewport.width || !viewport.height) return;
    const element = canvas.current;
    const width = Math.round(viewport.width * window.devicePixelRatio);
    const height = Math.round(viewport.height * window.devicePixelRatio);
    if (element.width !== width) element.width = width;
    if (element.height !== height) element.height = height;
    const ctx = element.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(width / viewport.width, 0, 0, height / viewport.height, 0, 0);
    ctx.clearRect(0, 0, viewport.width, viewport.height);
    ctx.drawImage(asset.bitmap, transform.x, transform.y, image.width * transform.scale, image.height * transform.scale);
  }, [asset, image.width, image.height, transform.x, transform.y, transform.scale, viewport]);

  useEffect(() => {
    const element = stage.current;
    if (!element || !ready || !viewport.width || !viewport.height) return;
    function wheel(event: WheelEvent) {
      if (event.ctrlKey || event.metaKey || !event.deltaY || gesture.current) return;
      event.preventDefault();
      const rect = element!.getBoundingClientRect();
      const anchor = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      setView((current) => zoomAt(image, viewport, current, anchor, event.deltaY < 0 ? 1.2 : 1 / 1.2));
    }
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [image, ready, viewport]);

  function reset() {
    setView(DEFAULT_VIEW);
    setBrightness(1);
    setContrast(1);
    setTool("select");
  }

  function zoom(factor: number) {
    if (!ready || !viewport.width || !viewport.height || gesture.current) return;
    setView((current) => zoomAt(image, viewport, current, { x: viewport.width / 2, y: viewport.height / 2 }, factor));
  }

  function selectBox(id: number | null) {
    if (id === selectedBoxId) return true;
    if (busy || (dirty && !window.confirm("Leave without saving these changes?"))) return false;
    setDraft(null);
    setFeedback(null);
    onBoxSelect(id);
    return true;
  }

  function beginRegion(bounds: BoxCoordinates) {
    onBoxSelect(null);
    setDraft(makeRegionDraft(null, bounds));
    setShowRegions(true);
    setTool("select");
    setFeedback(null);
  }

  function point(event: PointerEvent<HTMLDivElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function pointerDown(event: PointerEvent<HTMLDivElement>) {
    if (!ready || event.button !== 0 || gesture.current) return;
    const origin = point(event);
    const source = toSource(origin, transform);
    const target = event.target instanceof Element ? event.target.closest("[data-box-id]") : null;
    const box = activeBoxes.find((candidate) => String(candidate.id) === target?.getAttribute("data-box-id"));
    if (tool === "pan") {
      gesture.current = { kind: "pan", pointerId: event.pointerId, origin, pan: view.pan };
    } else if (tool === "draw" && editable && !dirty && source.x >= 0 && source.y >= 0 && source.x <= image.width && source.y <= image.height) {
      gesture.current = { kind: "draw", pointerId: event.pointerId, origin, source };
    } else if (tool === "select" && box && selectBox(box.id)) {
      setShowRegions(true);
      if (!editable) return;
      const localBounds = currentDraft?.id === box.id ? draftBounds(currentDraft) : box.bbox_xyxy;
      if (!validBounds(localBounds, image)) return;
      const corner = target?.hasAttribute("data-corner") ? nearestCorner(localBounds, source) : undefined;
      gesture.current = { kind: corner ? "resize" : "move", pointerId: event.pointerId, origin, source, box, bounds: localBounds, corner };
    } else {
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function gestureBounds(active: Exclude<Gesture, { kind: "pan" }>, current: Point) {
    const source = toSource(current, transform);
    if (active.kind === "draw") return drawBounds(active.source, source, image);
    const delta = { x: source.x - active.source.x, y: source.y - active.source.y };
    if (active.kind === "resize" && active.corner) {
      const anchor = sourceCorner(active.bounds, active.corner);
      return resizeBounds(active.bounds, { x: anchor.x + delta.x, y: anchor.y + delta.y }, active.corner, image);
    }
    return moveBounds(active.bounds, delta, image);
  }

  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const current = point(event);
    const dx = current.x - active.origin.x;
    const dy = current.y - active.origin.y;
    if (active.kind === "pan") {
      setView((previous) => ({ ...previous, pan: { x: active.pan.x + dx, y: active.pan.y + dy } }));
    } else if (editable && (active.kind === "draw" || Math.hypot(dx, dy) >= 2)) {
      setPreview(gestureBounds(active, current));
    } else {
      setPreview(null);
    }
  }

  function endGesture() {
    gesture.current = null;
    setPreview(null);
    setDragging(false);
  }

  function pointerUp(event: PointerEvent<HTMLDivElement>) {
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    if (active.kind !== "pan" && editable) {
      const current = point(event);
      const bounds = gestureBounds(active, current);
      const distance = Math.hypot(current.x - active.origin.x, current.y - active.origin.y);
      if (validBounds(bounds, image)) {
        if (active.kind === "draw") beginRegion(bounds);
        else if (distance >= 2) setDraft(withBounds(currentDraft?.id === active.box.id ? currentDraft : makeRegionDraft(active.box, bounds), bounds));
      }
    }
    endGesture();
  }

  function cancelDraft() {
    setDraft(null);
    setFeedback(null);
    endGesture();
  }

  function stageKeys(event: KeyboardEvent<HTMLDivElement>) {
    if (!ready || event.target !== event.currentTarget) return;
    if (event.key === "Escape") { if (!busy) cancelDraft(); return; }
    const direction = panKeys[event.key];
    if (!direction || gesture.current) return;
    event.preventDefault();
    const distance = event.shiftKey ? 50 : 10;
    setView((previous) => ({ ...previous, pan: { x: previous.pan.x + direction.x * distance, y: previous.pan.y + direction.y * distance } }));
  }

  async function mutate(action: () => Promise<void>, message: string, clearSelection = false) {
    if (!editable) return;
    setBusy(true);
    setFeedback(null);
    try {
      await action();
      setDraft(null);
      if (clearSelection) onBoxSelect(null);
      setFeedback({ text: message, error: false });
    } catch (error) {
      setFeedback({ text: errorMessage(error), error: true });
    } finally {
      setBusy(false);
    }
  }

  const editingBounds = currentDraft ? draftBounds(currentDraft) : null;
  const position = (bounds: BoxCoordinates) => ({
    x: transform.x + bounds[0] * transform.scale,
    y: transform.y + bounds[1] * transform.scale,
    width: (bounds[2] - bounds[0]) * transform.scale,
    height: (bounds[3] - bounds[1]) * transform.scale,
  });
  const activeGesture = gesture.current;
  const newBounds = activeGesture?.kind === "draw" ? preview : currentDraft?.id === null ? editingBounds : null;
  const imageIndex = images.findIndex((candidate) => candidate.id === image.id) + 1;
  const reason = readOnly ? "Final — read-only" : blockedReason || (!canAnnotate ? "Analyze this study first." : !ready ? "Load the image to edit regions." : "");

  return (
    <section className={styles.viewer} aria-label="Study viewer">
      <div className={styles.imaging}>
        <header className={styles.viewerHeading}>
          <h2>Chest X-ray <span>{image.view}</span></h2>
          <span>Image {imageIndex} of {images.length}</span>
        </header>
        <div className={styles.toolbar} role="group" aria-label="Image viewer tools">
          <div className={styles.toolGroup}>
            <ToolButton label="Select" Icon={MousePointer2} pressed={tool === "select"} disabled={!ready} onClick={() => setTool("select")} />
            <ToolButton label="Pan" Icon={Hand} pressed={tool === "pan"} disabled={!ready} onClick={() => setTool("pan")} />
            {!readOnly && <ToolButton label="Draw region" Icon={BoxSelect} pressed={tool === "draw"} disabled={!editable || dirty} onClick={() => { setTool("draw"); setShowOverlays(true); }} />}
          </div>
          <div className={styles.toolGroup}>
            <ToolButton label="Zoom out" Icon={Minus} disabled={!ready || view.zoom <= 0.25} onClick={() => zoom(1 / 1.2)} />
            <output aria-label="Zoom relative to source pixels" className={styles.zoomValue}>{Math.round(transform.scale * 100)}%</output>
            <ToolButton label="Zoom in" Icon={Plus} disabled={!ready || view.zoom >= 8} onClick={() => zoom(1.2)} />
            <ToolButton label="Fit and reset" Icon={Maximize} disabled={!ready} onClick={reset} />
          </div>
          <div className={styles.toolGroup}>
            <ToolButton label={showOverlays ? "Hide overlays" : "Show overlays"} Icon={showOverlays ? Eye : EyeOff} pressed={showOverlays} disabled={!ready} onClick={() => setShowOverlays(!showOverlays)} />
            <ToolButton label="Brightness and contrast" Icon={SlidersHorizontal} expanded={showFilters} disabled={!ready} onClick={() => setShowFilters(!showFilters)} />
          </div>
        </div>
        {showFilters && <div className={styles.filters}>
          <label><Sun size={16} aria-hidden="true" /><span>Brightness</span><input type="range" min={0.5} max={2} step={0.05} value={brightness} onChange={(event) => setBrightness(event.target.valueAsNumber)} /><output>{brightness.toFixed(2)}</output></label>
          <label><Contrast size={16} aria-hidden="true" /><span>Contrast</span><input type="range" min={0.5} max={2} step={0.05} value={contrast} onChange={(event) => setContrast(event.target.valueAsNumber)} /><output>{contrast.toFixed(2)}</output></label>
        </div>}
        <div ref={stage} className={styles.stage} role="region" aria-label="Image viewport. Use the arrow keys to pan." tabIndex={0} data-tool={tool === "draw" && !editable ? "select" : tool} data-dragging={dragging} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={endGesture} onLostPointerCapture={endGesture} onKeyDown={stageKeys}>
          <canvas ref={canvas} className={styles.canvas} hidden={!ready} role="img" aria-label={`Chest X-ray, ${image.view}, ${image.width} by ${image.height} pixels`} style={{ filter: `brightness(${brightness}) contrast(${contrast})` }} />
          {ready && showOverlays && <svg className={styles.overlays} aria-hidden="true">
            {activeBoxes.map((box, index) => {
              const isSelected = box.id === selectedBoxId;
              const bounds = activeGesture && (activeGesture.kind === "move" || activeGesture.kind === "resize") && activeGesture.box.id === box.id && preview
                ? preview
                : isSelected && editingBounds && validBounds(editingBounds, image) ? editingBounds : box.bbox_xyxy;
              const rect = position(bounds);
              const selectedColor = box.source === "ai" ? "ai" : "doctor";
              return <g key={box.id} data-source={selectedColor} data-selected={isSelected} data-pending={box.review_action === "pending"}>
                <rect {...rect} className={styles.boxBack} />
                <rect {...rect} className={styles.boxOutline} />
                <rect {...rect} className={styles.boxHit} data-box-id={box.id} />
                <rect x={rect.x + 2} y={rect.y + 2} width={30} height={18} rx={2} className={styles.boxLabel} />
                <text x={rect.x + 17} y={rect.y + 15} textAnchor="middle" className={styles.boxLabelText}>R{index + 1}</text>
                {isSelected && editable && tool === "select" && corners.map((corner) => {
                  const x = rect.x + (corner.endsWith("right") ? rect.width : 0);
                  const y = rect.y + (corner.startsWith("bottom") ? rect.height : 0);
                  return <g key={corner}>
                    <rect x={x - 3} y={y - 3} width={6} height={6} className={styles.handleMark} />
                    <rect x={x - 22} y={y - 22} width={44} height={44} className={styles.handleHit} data-box-id={box.id} data-corner={corner} />
                  </g>;
                })}
              </g>;
            })}
            {newBounds && validBounds(newBounds, image) && <g data-source="doctor" data-pending="true"><rect {...position(newBounds)} className={styles.boxBack} /><rect {...position(newBounds)} className={styles.boxOutline} /></g>}
          </svg>}
          {asset.state !== "ready" && <div className={styles.imageStatus} role={asset.state === "error" ? "alert" : "status"}>
            {asset.state === "loading" ? <p>Loading original image…</p> : <><p>This image is unavailable.</p><button type="button" onClick={() => setRetry(retry + 1)}>Retry image</button></>}
          </div>}
        </div>
        <div className={styles.imageFooter}><span>{image.width} × {image.height} px · {image.media_type === "image/png" ? "PNG" : "JPEG"}</span><span>{showOverlays ? "Overlays visible" : "Overlays hidden"}</span></div>
      </div>
      {images.length > 1 && <div className={styles.imageStrip} aria-label="Study images" role="group">
        {images.map((candidate, index) => <button type="button" key={candidate.id} aria-pressed={candidate.id === image.id} disabled={busy} onClick={() => onImageChange(candidate.id)}>
          <ImageThumbnail image={candidate} loadImage={loadImage} /> <span><strong>{candidate.view}</strong><span>Image {index + 1}</span></span>
        </button>)}
      </div>}
      <div className={styles.regionSection}>
        <button type="button" className={styles.regionToggle} aria-expanded={showRegions || Boolean(editor)} onClick={() => {
          if (showRegions || editor) {
            if (busy || (dirty && !window.confirm("Leave without saving these changes?"))) return;
            cancelDraft();
            onBoxSelect(null);
            setShowRegions(false);
          } else {
            setShowRegions(true);
          }
        }}><BoxSelect size={18} aria-hidden="true" /> Regions <span>{activeBoxes.length}</span></button>
        {reason && <p className={styles.reason}>{reason}</p>}
        {feedback && <p className={feedback.error ? styles.fieldError : styles.reason} role={feedback.error ? "alert" : "status"}>{feedback.text}</p>}
        {(showRegions || editor) && <div className={styles.regionContent}>
          {activeBoxes.length ? <ul className={styles.regionList}>
            {activeBoxes.map((box, index) => <li key={box.id}>
              <button type="button" aria-pressed={box.id === selectedBoxId} disabled={busy} onClick={() => selectBox(box.id === selectedBoxId ? null : box.id)}>
                <span className={styles.regionIndex} data-source={box.source}>R{index + 1}</span>
                <span><strong>{box.label || "Unlabelled region"}</strong><span>{regionState(box)}</span><span>{box.bbox_xyxy.map((value) => value.toFixed(2)).join(", ")} px</span><span>{linkedFindings?.[box.id] || "No linked finding"}</span></span>
              </button>
            </li>)}
          </ul> : <p className={styles.reason}>No saved regions on this image.</p>}
          {!readOnly && <button type="button" className={styles.numericAdd} disabled={!editable || dirty} onClick={() => beginRegion([image.width * 0.25, image.height * 0.25, image.width * 0.75, image.height * 0.75])}><Plus size={16} aria-hidden="true" /> Add region by coordinates</button>}
          {editor && canAnnotate && ready && !readOnly && <div ref={editorAnchor} tabIndex={-1} role="group" aria-label="Region coordinates and details"><RegionEditor image={image} draft={editor} box={selected} name={selected ? regionName(boxes, selected) : undefined} canAsk={canAskRegion} busy={busy} dirty={dirty} findingsApproved={findingsApproved}
            onChange={setDraft} onCancel={cancelDraft}
            onSave={() => { if (validBounds(draftBounds(editor), image) && dirty) void mutate(() => onBoxSave(editor.id, regionChanges(editor, selected, image.id)), "Region saved."); }}
            onReview={(review_action) => { if (selected && !dirty) void mutate(() => onBoxSave(selected.id, { review_action }), review_action === "accepted" ? "Region accepted." : "Region rejected.", review_action === "rejected"); }}
            onDelete={() => { if (selected && !dirty) void mutate(() => onBoxDelete(selected.id), "Region removed.", true); }}
            onAsk={() => { if (selected && canAskRegion && !dirty) onAskRegion(selected); }} /></div>}
          {selected && (!canAnnotate || !ready || readOnly) && <div className={styles.readOnlyDetails}>
            <strong>{regionName(boxes, selected)}{selected.label ? ` · ${selected.label}` : ""}</strong><small className={styles.reason}>ID {selected.id}</small>
            <p>{selected.source === "ai" && selected.review_action !== "pending" ? `AI suggestion · ${regionState(selected)}` : regionState(selected)}</p>
            {selected.note && <p>{selected.note}</p>}
          </div>}
        </div>}
      </div>
    </section>
  );
}

function ToolButton({ label, Icon, pressed, expanded, disabled, onClick }: { label: string; Icon: typeof Eye; pressed?: boolean; expanded?: boolean; disabled?: boolean; onClick: () => void }) {
  return <button type="button" className={styles.toolButton} aria-label={label} title={label} aria-pressed={pressed} aria-expanded={expanded} disabled={disabled} onClick={onClick}><Icon size={18} strokeWidth={1.75} aria-hidden="true" /></button>;
}

function ImageThumbnail({ image, loadImage }: { image: ImageDTO; loadImage: ViewerProps["loadImage"] }) {
  const thumbnail = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const element = thumbnail.current;
    if (!element) return;
    const controller = new AbortController();
    async function draw() {
      try {
        const blob = await loadImage(image.id, controller.signal);
        if (controller.signal.aborted) return;
        const bitmap = await createImageBitmap(blob, { resizeWidth: 80, resizeQuality: "low" });
        if (!controller.signal.aborted) {
          const scale = Math.min(80 / bitmap.width, 80 / bitmap.height);
          const width = bitmap.width * scale;
          const height = bitmap.height * scale;
          element?.getContext("2d")?.drawImage(bitmap, (80 - width) / 2, (80 - height) / 2, width, height);
        }
        bitmap.close();
      } catch { /* The main viewport handles image loading and recovery. */ }
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      void draw();
    });
    observer.observe(element);
    return () => { observer.disconnect(); controller.abort(); };
  }, [image.id, loadImage]);
  return <canvas ref={thumbnail} className={styles.thumbnail} width={80} height={80} aria-hidden="true" />;
}
