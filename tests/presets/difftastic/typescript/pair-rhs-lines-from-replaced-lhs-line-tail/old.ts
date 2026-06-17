type SyntaxSpan = { start: number; end: number };

export function syntaxParts(text: string, syntax: SyntaxSpan[]): string[] {
  if (!text || !syntax.length) {
    return [text];
  }
  return syntax.map((span) => text.slice(span.start, span.end));
}
