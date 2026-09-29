/** Join class names, dropping falsy parts. No merge magic: owned components pass disjoint classes. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
