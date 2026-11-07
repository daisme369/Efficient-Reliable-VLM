import type { BoxCoordinates } from "../../lib/contracts";

export interface Size {
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface ViewTransform {
  zoom: number;
  pan: Point;
}

export type Corner = "top-left" | "top-right" | "bottom-left" | "bottom-right";

export function imageTransform(image: Size, viewport: Size, view: ViewTransform) {
  const fit = Math.min(viewport.width / image.width, viewport.height / image.height);
  const scale = fit * view.zoom;
  return {
    scale,
    x: (viewport.width - image.width * scale) / 2 + view.pan.x,
    y: (viewport.height - image.height * scale) / 2 + view.pan.y,
  };
}

export function toSource(point: Point, transform: ReturnType<typeof imageTransform>): Point {
  return {
    x: (point.x - transform.x) / transform.scale,
    y: (point.y - transform.y) / transform.scale,
  };
}

export function zoomAt(image: Size, viewport: Size, view: ViewTransform, anchor: Point, factor: number): ViewTransform {
  const zoom = clamp(view.zoom * factor, 0.25, 8);
  const source = toSource(anchor, imageTransform(image, viewport, view));
  const centered = imageTransform(image, viewport, { zoom, pan: { x: 0, y: 0 } });
  return {
    zoom,
    pan: {
      x: anchor.x - centered.x - source.x * centered.scale,
      y: anchor.y - centered.y - source.y * centered.scale,
    },
  };
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(value, max));
}

export function drawBounds(start: Point, end: Point, image: Size): BoxCoordinates {
  const x = clamp(end.x, 0, image.width);
  const y = clamp(end.y, 0, image.height);
  return [Math.min(start.x, x), Math.min(start.y, y), Math.max(start.x, x), Math.max(start.y, y)];
}

export function moveBounds(box: BoxCoordinates, delta: Point, image: Size): BoxCoordinates {
  const x = clamp(delta.x, -box[0], image.width - box[2]);
  const y = clamp(delta.y, -box[1], image.height - box[3]);
  return [box[0] + x, box[1] + y, box[2] + x, box[3] + y];
}

export function resizeBounds(box: BoxCoordinates, point: Point, corner: Corner, image: Size): BoxCoordinates {
  const [x1, y1, x2, y2] = box;
  const minWidth = Math.min(1, x2 - x1);
  const minHeight = Math.min(1, y2 - y1);
  switch (corner) {
    case "top-left": return [clamp(point.x, 0, x2 - minWidth), clamp(point.y, 0, y2 - minHeight), x2, y2];
    case "top-right": return [x1, clamp(point.y, 0, y2 - minHeight), clamp(point.x, x1 + minWidth, image.width), y2];
    case "bottom-left": return [clamp(point.x, 0, x2 - minWidth), y1, x2, clamp(point.y, y1 + minHeight, image.height)];
    case "bottom-right": return [x1, y1, clamp(point.x, x1 + minWidth, image.width), clamp(point.y, y1 + minHeight, image.height)];
  }
}

export function validBounds(box: BoxCoordinates, image: Size) {
  const [x1, y1, x2, y2] = box;
  return box.every(Number.isFinite) && x1 >= 0 && y1 >= 0 && x1 < x2 && y1 < y2 && x2 <= image.width && y2 <= image.height;
}
