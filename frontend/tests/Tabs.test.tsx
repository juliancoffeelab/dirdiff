/**
 * Contains jsdom rendering tests for Tabs without selected repository.
 *
 * Tests drive transitions through TabStrip with real Solid, Toast, and query
 * lifetimes.
 * API fixtures must satisfy the validated backend contracts.
 * Assert active controls, repository gates, emitted selections, and absence
 * of reported errors.
 *
 * File rendering stays outside this module; ChangeSet must fail the test
 * if a repository-gated Tab mounts it.
 */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { afterEach, expect, test, vi } from "vitest";

import { QueryProvider } from "../src/api/queryClient";
import { ToastProvider } from "../src/comp/Toasts";
import { TabStrip, Tabs, type TabId } from "../src/hud/Tabs";

// File rendering is outside this module.
// Repo-gated ChangeSet mounting fails, Preset may reach its valid selected
// boundary without rendering file content.
vi.mock("../src/hud/changeSet/ChangeSet", () => ({
  ChangeSet: (props: {
    /**
     * Complete selection passed to the mocked ChangeSet boundary by a Tab.
     *
     * The mock inspects only its discriminant because file rendering is outside
     * this suite; every repository-backed value must fail immediately.
     */
    params: {
      /**
       * Tab workflow that constructed this selected parameter entity.
       *
       * Only `preset` is permitted to cross this mock in repository-less tests.
       */
      tab: string;
    };
  }) => {
    if (props.params.tab !== "preset") {
      throw new Error("A repository-gated Tab mounted ChangeSet.");
    }
    return null;
  },
}));

// Tab activation does not depend on SVG output; replacing these imports also
// prevents Vitest from transforming the complete Lucide icon collection.
//
// (And saves a load of time)
vi.mock("lucide-solid", () => ({
  RefreshCw: () => null,
  Save: () => null,
  Trash2: () => null,
}));

// Dispose mounted resources and transport stubs before the next test.
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("renders every repo-backed Tab gate after visiting Preset", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path !== "/api/presets" && path !== "/api/repos") {
        throw new Error(`Unexpected frontend test request: ${path}`);
      }
      return new Response("[]", {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );

  const [active, setActive] = createSignal<TabId>("head");
  const statusOutlet = document.createElement("div");
  const summaryOutlet = document.createElement("div");
  render(() => (
    <ToastProvider>
      <QueryProvider
        onError={(_title, error) => {
          throw error;
        }}
      >
        <TabStrip active={active()} onSelect={setActive} />
        <Tabs
          active={active()}
          repoId={null}
          engine="dirdiff"
          view="split"
          fileTreeOpen={false}
          debugHudOpen={false}
          selectedProfile={null}
          appHeaderOutlets={{
            status: () => statusOutlet,
            summary: () => summaryOutlet,
          }}
          metadataTarget={null}
          onRepoSelected={() => {
            throw new Error("A gate selection was not requested.");
          }}
          onHeadSelected={() => {
            throw new Error("Head was selected without a repository.");
          }}
          onRefsSelected={() => {
            throw new Error("Refs were selected without a repository.");
          }}
          onBranchReviewSelected={() => {
            throw new Error("Branch Review was selected without a repository.");
          }}
          onPresetSelected={() => {
            throw new Error("The empty preset catalog selected a preset.");
          }}
          onPullRequestSelected={() => {
            throw new Error("An unprepared Pull Request was selected.");
          }}
          onPullRequestPrepared={() => {
            throw new Error("An empty Pull Request was prepared.");
          }}
          onToggleView={() => {}}
          onFileTreeOpenChange={() => {}}
          onDebugHudOpenChange={() => {}}
        />
      </QueryProvider>
    </ToastProvider>
  ));

  expect(
    screen.getByRole("region", { name: "Marked repositories" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Preset" }));
  expect(screen.getByRole("group", { name: "Preset type" })).toBeVisible();

  for (const label of ["Diff against HEAD", "Compare refs", "Branch review"]) {
    fireEvent.click(screen.getByRole("button", { name: label }));
    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: "Marked repositories" }),
      ).toBeVisible(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Preset" }));
    expect(screen.getByRole("group", { name: "Preset type" })).toBeVisible();
  }
  expect(screen.queryByRole("alert")).toBeNull();
});

test("selects Presets and renders Pull Request without a repo", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/presets") {
        return new Response(
          JSON.stringify([
            {
              id: "rendering",
              name: "Rendering",
              default_preset: "basic",
              groups: [
                { id: "basic", display_name: "Basic" },
                { id: "edge", display_name: "Edge cases" },
              ],
            },
          ]),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        );
      }
      if (path === "/api/repos") {
        return new Response("[]", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      throw new Error(`Unexpected frontend test request: ${path}`);
    }),
  );

  const [active, setActive] = createSignal<TabId>("head");
  const selectedPresets: string[] = [];
  const statusOutlet = document.createElement("div");
  const summaryOutlet = document.createElement("div");
  render(() => (
    <ToastProvider>
      <QueryProvider
        onError={(_title, error) => {
          throw error;
        }}
      >
        <TabStrip active={active()} onSelect={setActive} />
        <Tabs
          active={active()}
          repoId={null}
          engine="dirdiff"
          view="split"
          fileTreeOpen={false}
          debugHudOpen={false}
          selectedProfile={null}
          appHeaderOutlets={{
            status: () => statusOutlet,
            summary: () => summaryOutlet,
          }}
          metadataTarget={null}
          onRepoSelected={() => {
            throw new Error("A repository selection was not requested.");
          }}
          onHeadSelected={() => {
            throw new Error("Head was selected without a repository.");
          }}
          onRefsSelected={() => {
            throw new Error("Refs were selected without a repository.");
          }}
          onBranchReviewSelected={() => {
            throw new Error("Branch Review was selected without a repository.");
          }}
          onPresetSelected={(presetType, preset) => {
            selectedPresets.push(`${presetType}:${preset}`);
          }}
          onPullRequestSelected={() => {
            throw new Error("An unprepared Pull Request was selected.");
          }}
          onPullRequestPrepared={() => {
            throw new Error("An empty Pull Request was prepared.");
          }}
          onToggleView={() => {}}
          onFileTreeOpenChange={() => {}}
          onDebugHudOpenChange={() => {}}
        />
      </QueryProvider>
    </ToastProvider>
  ));

  fireEvent.click(screen.getByRole("button", { name: "Preset" }));
  await waitFor(() => expect(selectedPresets).toEqual(["rendering:basic"]));
  expect(screen.getByRole("button", { name: "Basic" })).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Edge cases" }));
  expect(selectedPresets).toEqual(["rendering:basic", "rendering:edge"]);

  fireEvent.click(screen.getByRole("button", { name: "PR" }));
  expect(screen.getByRole("textbox", { name: "Pull request" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Load" })).toBeEnabled();
  expect(screen.queryByRole("alert")).toBeNull();
});
