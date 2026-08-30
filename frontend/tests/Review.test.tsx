/**
 * Contains jsdom integration tests for Snapshot-scoped review discussion state.
 *
 * Tests mount the real Solid providers, discussion factory, TanStack mutation
 * definitions, and API validation. Transport alone is stubbed: no backend or
 * rendered diff is required to verify shared pending-mutation behavior.
 */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@solidjs/testing-library";
import { createMutation } from "@tanstack/solid-query";
import { afterEach, expect, test, vi } from "vitest";

import { api, ReviewIdSchema } from "../src/api/api";
import { QueryProvider } from "../src/api/queryClient";
import { ToastProvider } from "../src/comp/Toasts";
import { ReviewDraftRoot } from "../src/hud/review/drafts";
import { createThreadDiscussion } from "../src/hud/review/discussion";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

test("Thread creation does not enter existing-Thread lifecycle state", async () => {
  const snapshotId = ReviewIdSchema.parse("1".repeat(32));
  const unrelatedThreadId = ReviewIdSchema.parse("2".repeat(32));
  let creationStarted = false;
  const creationResponse = new Promise<Response>(() => {
    // The regression exists only while creation is pending; test cleanup
    // disposes the provider and its mutation cache after the assertion.
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.startsWith("/api/review/threads?")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              snapshot_id: snapshotId,
              through_activity_id: 0,
              threads: [],
              page: 1,
              limit: 100,
              total_threads: 0,
              has_more: false,
            }),
            {
              status: 200,
              headers: { "content-type": "application/json" },
            },
          ),
        );
      }
      if (path === "/api/review/post_comment") {
        creationStarted = true;
        return creationResponse;
      }
      throw new Error(`Unexpected frontend test request: ${path}`);
    }),
  );

  render(() => (
    <ToastProvider>
      <QueryProvider
        onError={(_title, error) => {
          throw error;
        }}
      >
        <ReviewDraftRoot>
          {(() => {
            const creation = createMutation(() => api.review.thread.create());
            const discussion = createThreadDiscussion({
              snapshotId,
              profile: () => null,
              onSubmitted: () => {},
            });
            return (
              <>
                <button
                  type="button"
                  onClick={() =>
                    creation.mutate({
                      snapshotId,
                      body: {
                        profile_id: 1,
                        target: {
                          kind: "text",
                          file: {
                            left_path: "example.py",
                            right_path: "example.py",
                          },
                          bay: { bay_key: "flatfile" },
                          side: "right",
                          range: { start_line: 1, end_line: 1 },
                        },
                        body: "Pending creation probe",
                      },
                    })
                  }
                >
                  Create Thread
                </button>
                <output>
                  {discussion.threadStatePending(unrelatedThreadId, "resolve")
                    ? "pending"
                    : "idle"}
                </output>
              </>
            );
          })()}
        </ReviewDraftRoot>
      </QueryProvider>
    </ToastProvider>
  ));

  fireEvent.click(screen.getByRole("button", { name: "Create Thread" }));
  await waitFor(() => expect(creationStarted).toBe(true));
  expect(screen.getByRole("status")).toHaveTextContent("idle");
});
