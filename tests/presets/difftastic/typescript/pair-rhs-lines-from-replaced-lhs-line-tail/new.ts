type InlineToken = { text: string };
type SyntaxSpan = { start: number; end: number };

export function decoratedParts(
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
): string[] {
  if (!text || (!tokens.length && !syntax.length)) {
    return [text];
  }
  return [...tokens.map((token) => token.text), ...syntax.map((span) => text.slice(span.start, span.end))];
}
