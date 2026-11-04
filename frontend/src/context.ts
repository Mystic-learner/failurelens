export interface ContextLine {
  text: string;
  hit: boolean;
  gap?: boolean;
}

/** Mark the line of `chunk` that equals `quote` (whole line, trimmed). */
export function splitContext(chunk: string, quote: string): ContextLine[] {
  const target = quote.trim();
  return chunk.split(/\r?\n/).map((text) => ({
    text,
    hit: target.length > 0 && text.trim() === target,
  }));
}

/** Keep `radius` lines around each hit; replace skipped ranges with a gap marker. */
export function trimContext(lines: ContextLine[], radius = 4): ContextLine[] {
  const hits = lines.flatMap((l, i) => (l.hit ? [i] : []));
  if (hits.length === 0) {
    if (lines.length <= radius * 2 + 1) return lines;
    const hidden = lines.length - radius * 2;
    return [
      ...lines.slice(0, radius * 2),
      { text: `… ${hidden} more lines …`, hit: false, gap: true },
    ];
  }
  const keep = new Set<number>();
  for (const h of hits) {
    for (let i = Math.max(0, h - radius); i <= Math.min(lines.length - 1, h + radius); i++) {
      keep.add(i);
    }
  }
  const out: ContextLine[] = [];
  let skipped = 0;
  lines.forEach((line, i) => {
    if (keep.has(i)) {
      if (skipped > 0) {
        out.push({ text: `… ${skipped} lines hidden …`, hit: false, gap: true });
        skipped = 0;
      }
      out.push(line);
    } else {
      skipped += 1;
    }
  });
  if (skipped > 0) out.push({ text: `… ${skipped} lines hidden …`, hit: false, gap: true });
  return out;
}