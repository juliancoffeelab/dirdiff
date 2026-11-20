import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
  isCancelledError,
} from "@tanstack/solid-query";
import type { JSX } from "solid-js";
import { assert } from "../utils";

type ErrorMeta = Record<string, unknown> & {
  errorTitle: string | ((error: unknown) => string);
};

// Type-only: TanStack still declares `meta` optional, so QueryProvider asserts
// its presence at runtime.
declare module "@tanstack/query-core" {
  interface Register {
    queryMeta: ErrorMeta;
    mutationMeta: ErrorMeta;
  }
}

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
