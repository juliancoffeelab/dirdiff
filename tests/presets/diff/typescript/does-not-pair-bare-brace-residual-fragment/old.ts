type SyntaxSpan = { start: number; end: number };

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function syntaxParts(text: string, syntax: SyntaxSpan[]): string[] {
  const parts: string[] = [];
  let cursor = 0;
  for (const span of syntax) {
    const start = clamp(span.start, 0, text.length);
    parts.push(text.slice(cursor, start));
    cursor = span.end;
  }
  return parts;
}
