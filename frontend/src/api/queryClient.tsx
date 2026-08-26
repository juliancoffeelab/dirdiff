/**
 * Defines the application-wide TanStack Query provider boundary.
 *
 * The module exports QueryProvider and defines the metadata shape required to
 * give every query and mutation failure a specific user-visible title. Each
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
import { assert } from "../utils";

/**
 * Describes the application-specific TanStack metadata recognized on failure.
 *
 * Every application operation must supply either its complete user-visible Toast
 * title or a resolver for operations with distinct typed failures. A resolver
 * receives only the failed attempt's error and must return one complete title.
 * This record controls metadata contents while the provider asserts presence
 * because TanStack's metadata field remains optional. It must not carry query data.
 */
type ErrorMeta = Record<string, unknown> & {
  errorTitle: string | ((error: unknown) => string);
};

// TanStack derives QueryMeta and MutationMeta from this merged Register
// interface. As a result, TypeScript checks metadata supplied to
// queryOptions(...) and mutationOptions(...) against ErrorMeta.
//
// This changes only the shape of metadata when present. TanStack still declares
// `meta` as optional, so QueryProvider asserts the application contract at the
// runtime cache boundary. The declaration emits no runtime validation code.
declare module "@tanstack/query-core" {
  /**
   * Registers the metadata shape accepted by application query and mutation
   * definitions.
   *
   * TanStack continues to permit operations without metadata at its library
   * boundary. Application operations require it, and present metadata has the
   * ErrorMeta field shape.
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
 * failed query or mutation attempt invokes `onError` once using its required
 * metadata title. Missing metadata violates the application query contract and
 * throws at this cache boundary. Intentional query cancellation remains silent.
 * Descendants access the mounted client through TanStack Query's
 * `useQueryClient()`.
 */
export function QueryProvider(props: {
  children: JSX.Element;
  onError(title: string, error: unknown): void;
}): JSX.Element {
  const queryClient = new QueryClient({
    queryCache: new QueryCache({
      /**
       * Presents ordinary query failures through the application Toast boundary.
       *
       * TanStack invokes this after a query attempt fails. This callback ignores
       * intentional cancellation. Every ordinary query must provide its
       * application-specific error title.
       */
      onError(error, query) {
        if (isCancelledError(error)) {
          return;
        }
        assert(
          query.meta !== undefined,
          "Failed query requires error-title metadata.",
        );
        const errorTitle = query.meta.errorTitle;
        props.onError(
          typeof errorTitle === "string" ? errorTitle : errorTitle(error),
          error,
        );
      },
    }),
    mutationCache: new MutationCache({
      /**
       * Presents mutation failures through the application Toast boundary.
       *
       * TanStack invokes this after a mutation attempt fails. Metadata supplies
       * the visible title. Every application mutation must provide that metadata
       * even though TanStack's underlying option remains optional.
       */
      onError(error, _variables, _result, mutation) {
        assert(
          mutation.meta !== undefined,
          "Failed mutation requires error-title metadata.",
        );
        const errorTitle = mutation.meta.errorTitle;
        props.onError(
          typeof errorTitle === "string" ? errorTitle : errorTitle(error),
          error,
        );
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
