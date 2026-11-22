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
import { ThreadCard } from "../src/hud/review/threadViews";

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
              acceptedActivities: () => [],
              onAcceptedActivity: () => {},
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

test("Comment Markdown creates no image, raw HTML or unsafe link, and keeps line breaks", () => {
  const body = [
    "![tracker](https://example.com/pixel.png)",
    "",
    "<script>alert(1)</script>",
    "",
    'inline <img src="x" onerror="alert(2)"> html',
    "",
    "[unsafe](javascript:alert(3)) and [safe](https://example.com/page)",
    "",
    "first line",
    "second line",
  ].join("\n");
  const author = {
    profile_id: 1,
    display_name: "Agent",
    is_agent: true,
    model: null,
    harness: null,
  };
  const { container } = render(() => (
    <ThreadCard
      thread={{
        thread_id: ReviewIdSchema.parse("3".repeat(32)),
        snapshot_id: ReviewIdSchema.parse("1".repeat(32)),
        created_at: "2026-01-01T00:00:00+00:00",
        state: "open",
        attention: "author",
        discussion_revision: 0,
        origin_target: { kind: "review" },
        placement: { kind: "review-scope" },
        location_source: { kind: "displayed-snapshot" },
        comments: [
          {
            comment_id: ReviewIdSchema.parse("4".repeat(32)),
            sequence: 0,
            author,
            revision: 0,
            body,
            deleted: false,
            created_at: "2026-01-01T00:00:00+00:00",
            updated_at: "2026-01-01T00:00:00+00:00",
          },
        ],
      }}
      expanded
      navigation={{ kind: "inline" }}
      profileId={2}
      submittingDraftIds={new Set()}
      commentDeletePending={() => false}
      threadStatePending={() => false}
      replyDraft={null}
      replySubmitting={false}
      toggleable={false}
      onToggle={() => undefined}
      onState={() => undefined}
      onReplyChange={() => true}
      onReplySubmit={() => Promise.resolve(true)}
      onReplyDiscard={() => true}
      editDraft={() => null}
      onEdit={() => undefined}
      onEditChange={() => true}
      onEditSubmit={() => Promise.resolve(true)}
      onEditDiscard={() => true}
      onDeleteComment={() => undefined}
      uploadImages={() => Promise.resolve(null)}
    />
  ));
  const rendered = container.querySelector(".review-comment-body")!;

  expect(rendered.querySelector("img, script")).toBeNull();
  expect(rendered).toHaveTextContent("tracker");
  expect(rendered).toHaveTextContent("<script>alert(1)</script>");
  expect(rendered).toHaveTextContent('<img src="x" onerror="alert(2)">');
  const anchors = [...rendered.querySelectorAll("a")];
  expect(anchors).toHaveLength(1);
  expect(rendered).toHaveTextContent("unsafe and safe");
  expect(anchors[0]).toHaveTextContent("safe");
  expect(anchors[0]).toHaveAttribute("href", "https://example.com/page");
  expect(anchors[0]).toHaveAttribute("target", "_blank");
  expect(anchors[0]).toHaveAttribute("rel", "noopener noreferrer");
  const lines = [...rendered.querySelectorAll("p")].at(-1)!;
  expect(lines.innerHTML).toBe("first line<br>second line");
});
