/**
 * Defines the application-wide TanStack Query ownership boundary.
 *
 * The module exports QueryProvider and defines the optional metadata shape used
 * to give query and mutation failures a specific user-visible title. Each
 * mounted provider owns exactly one QueryClient, QueryCache, and MutationCache.
 * It does not define backend operations, export cache instances, or depend on
 * presentation components.
 */
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
  isCancelledError,
} from "@tanstack/solid-query";
import type { JSX } from "solid-js";

/**
 * Describes the application-specific TanStack metadata recognized on failure.
 *
 * When an operation supplies metadata, it must provide the complete
 * user-visible title used by the global error Toast. This record describes
 * metadata contents only; it does not make TanStack's metadata field mandatory
 * and must not carry query data or error state.
 */
type ErrorMeta = Record<string, unknown> & {
  errorTitle: string;
};

// TanStack derives QueryMeta and MutationMeta from this merged Register
// interface. As a result, TypeScript checks metadata supplied to
// queryOptions(...) and mutationOptions(...) against ErrorMeta.
//
// This changes only the shape of metadata when present. TanStack still declares
// `meta` as optional, and the declaration emits no runtime validation code.
declare module "@tanstack/query-core" {
  /**
   * Registers the metadata shape accepted by application query and mutation
   * definitions.
   *
   * TanStack continues to permit operations without metadata. When metadata is
   * present, callers and cache callbacks may rely on the ErrorMeta field shape.
   */
  interface Register {
    queryMeta: ErrorMeta;
    mutationMeta: ErrorMeta;
  }
}

/**
 * Gives descendants one configured QueryClient and reports failed attempts.
 *
 * Callers provide the complete application subtree and an error reporter. Each
 * failed query or mutation attempt invokes `onError` once using its metadata
 * title when present and a generic operation title otherwise; intentional query
 * cancellation is silent. Descendants must access the mounted client through
 * TanStack Query's `useQueryClient()`.
 */
export function QueryProvider(props: {
  children: JSX.Element;
  onError(title: string, error: unknown): void;
}): JSX.Element {
  const queryClient = new QueryClient({
    queryCache: new QueryCache({
      onError(error, query) {
        if (isCancelledError(error)) {
          return;
        }
        // Register augmentation narrows present metadata to ErrorMeta, but
        // TanStack's real `meta?` contract still permits it to be absent.
        const title =
          query.meta === undefined ? "Query failed" : query.meta.errorTitle;
        props.onError(title, error);
      },
    }),
    mutationCache: new MutationCache({
      onError(error, _variables, _result, mutation) {
        // mutationOptions(...) receives the same shape checking as
        // queryOptions(...), while metadata presence remains optional.
        const title =
          mutation.meta === undefined
            ? "Mutation failed"
            : mutation.meta.errorTitle;
        props.onError(title, error);
      },
    }),
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
      mutations: {
        retry: false,
      },
    },
  });

  return (
    <QueryClientProvider client={queryClient}>
      {props.children}
    </QueryClientProvider>
  );
}
