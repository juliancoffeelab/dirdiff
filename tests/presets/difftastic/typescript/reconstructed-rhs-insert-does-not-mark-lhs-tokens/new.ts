type InlineToken = { text: string };
type SyntaxSpan = { start: number; end: number };

export function decoratedParts(
  syntax: SyntaxSpan[],
  tokens: InlineToken[],
): Array<SyntaxSpan | InlineToken> {
  return [...syntax, ...tokens];
}
