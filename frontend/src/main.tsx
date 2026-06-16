import { QueryClientProvider } from "@tanstack/solid-query";
import { render } from "solid-js/web";
import { App } from "./App";
import { queryClient } from "./queryClient";

render(
  () => (
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  ),
  document.getElementById("root")!,
);
