/**
 * Provides the application-wide TanStack Query ownership boundary.
 *
 * The module owns the QueryClient used by the component tree and exports the
 * provider through which callers access its cache. It does not define backend
 * requests, query keys, domain data, or presentation behavior.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/solid-query";
import type { JSX } from "solid-js";

const queryClient = new QueryClient();

/**
 * Gives every descendant access to the application QueryClient.
 *
 * Callers provide the complete application subtree as `children` and mount one
 * provider at its root. Descendants must access the client through TanStack
 * Query; the client instance is intentionally not part of this module's public
 * interface.
 */
export function QueryProvider(props: { children: JSX.Element }) {
  return (
    <QueryClientProvider client={queryClient}>
      {props.children}
    </QueryClientProvider>
  );
}
