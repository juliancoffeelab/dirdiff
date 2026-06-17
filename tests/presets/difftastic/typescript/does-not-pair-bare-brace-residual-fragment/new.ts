export function syntaxParts(text: string, sortedBoundaries: number[]): string[] {
  const parts: string[] = [];
  for (let index = 0; index < sortedBoundaries.length - 1; index += 1) {
    const start = sortedBoundaries[index];
    const end = sortedBoundaries[index + 1];
    parts.push(text.slice(start, end));
  }
  return parts;
}
