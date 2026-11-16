import { render } from "./receipt";

/** Render the supplied total using the explicitly imported receipt function. */
export function checkout(total: number): string {
  return render(total);
}
