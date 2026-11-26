// Renders `text` with <mark> around each [start, end) span (character offsets); spans must be sorted and non-overlapping.
export function Highlight({ text, spans }: { text: string; spans: [number, number][] }) {
  const parts: React.ReactNode[] = [];
  let at = 0;
  spans.forEach(([start, end], i) => {
    if (start < at || end > text.length || end <= start) return;
    parts.push(text.slice(at, start), <mark key={i}>{text.slice(start, end)}</mark>);
    at = end;
  });
  parts.push(text.slice(at));
  return <>{parts}</>;
}
