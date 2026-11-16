/**
 * THROWAWAY: five structure-first review designs on the existing HUD URL.
 *
 * `?prototype=semantic&variant=A..E&fixture=...` selects a fixture-only review.
 * The question is which arrangement makes structural changes understandable.
 * No engine runs, no API is called, and no review state is persisted. The JSON
 * contains hand-authored examples and one frozen sem capture of the working diff.
 * The capture keeps sem's reported relationships, including false matches.
 * This module holds the experimental layouts together so they can be discarded
 * after a design decision. The ordinary application is mounted at every other URL.
 */
import {
  For,
  Match,
  Show,
  Switch,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";
import { assert, expect } from "../utils";
import syntheticFixtures from "./semantic-prototype.fixtures.json";
import semCapture from "./semantic-prototype.sem.json";
import "./semantic-prototype.css";

/** One supplied patch with entities, directed relationships, and explicit review groups. */
type PrototypeFixture = {
  /** Stable scenario key used in the URL. */
  id: string;
  /** Human-readable scenario selector label. */
  title: string;
  /** The review situation this supplied patch illustrates. */
  description: string;
  /** Display label for the after snapshot. */
  branch: string;
  /** Display label for the before snapshot. */
  base: string;
  /** Complete selectable declarations and residual source chunks. */
  entities: PrototypeEntity[];
  /** Directed edges supplied by the fixture, never inferred by the UI. */
  relations: {
    /** Source entity id present in entities. */
    from: string;
    /** Destination entity id present in entities. */
    to: string;
    /** Supplied relationship category displayed on the arrow. */
    label: string;
  }[];
  /** Explicit chapters used by the review queue. */
  groups: {
    /** Unique chapter key within this fixture. */
    id: string;
    /** Chapter navigation label. */
    title: string;
    /** Explanation of why these entities appear together. */
    description: string;
    /** Nonempty ordered list of entity ids present in entities. */
    entityIds: string[];
  }[];
};
/** Synthetic scenarios and the frozen tool capture share the same display contract. */
const fixtureData: PrototypeFixture[] = [
  ...syntheticFixtures,
  semCapture.fixture,
];
/** One explicitly paired declaration or residual text change, with original source coordinates. */
type PrototypeEntity = {
  /** Unique identity within the fixture, also used by relationships. */
  id: string;
  /** After-side declaration name or supplied residual chunk label. */
  name: string;
  /** Previous name for a rename; null when no distinct previous name applies. */
  oldName: string | null;
  /** Supplied syntax category, such as function, type, or chunk. */
  kind: string;
  /** Supplied change classification; context denotes a supporting declaration. */
  status: string;
  /** Source path used for navigation and the after-side heading. */
  path: string;
  /** Distinct previous path for moves; null when the path is shared. */
  oldPath: string | null;
  /** Enclosing entity id present in this fixture, or null at file level. */
  parentId: string | null;
  /** Supplied explanation shown beside the source. */
  summary: string;
  /** Supplied pairing explanation, or null when no explanation was provided. */
  match: string | null;
  /** Aligned original source lines with absence represented explicitly. */
  rows: PrototypeRow[];
};
/** One supplied pair of original source lines; a null side denotes actual absence. */
type PrototypeRow = {
  /** Before-side source line, absent for an insertion. */
  old: {
    /** One-based physical source line number. */
    line: number;
    /** Original line text, excluding the newline terminator. */
    text: string;
    /** Whether this line differs in the supplied alignment. */
    changed: boolean;
  } | null;
  /** After-side source line, absent for a deletion. */
  new: {
    /** One-based physical source line number. */
    line: number;
    /** Original line text, excluding the newline terminator. */
    text: string;
    /** Whether this line differs in the supplied alignment. */
    changed: boolean;
  } | null;
};
/** Every design receives exactly the same fixture and source display preference. */
type PrototypeVariantProps = {
  /** Immutable example patch shared by every design. */
  fixture: PrototypeFixture;
  /** User-selected source display, independent of structural navigation. */
  view: "split" | "inline";
};

/**
 * Displays the fixture's exact source rows without matching, syntax inference or
 * renumbering. Every side retains its path and physical line number. This shared
 * code display lets the experiments disagree about navigation and composition.
 */
function PrototypeCode(props: {
  /** Paired source rows with original file paths and line numbers. */
  entity: PrototypeEntity;
  /** Whether to display the two sides beside one another or interleaved. */
  view: "split" | "inline";
}): JSX.Element {
  return (
    <div class="sp-code" data-code-entity={props.entity.id}>
      <div class="sp-code-paths">
        <span>
          BEFORE <b>{props.entity.oldPath ?? props.entity.path}</b>
        </span>
        <span>
          AFTER <b>{props.entity.path}</b>
        </span>
      </div>
      <Show
        when={props.view === "split"}
        fallback={
          <div class="sp-code-inline">
            <For each={props.entity.rows}>
              {(row: PrototypeRow) => (
                <>
                  <Show
                    when={
                      row.old !== null &&
                      (row.new === null || row.old.changed || row.new.changed)
                    }
                  >
                    <div
                      class="sp-code-line"
                      data-tone={row.old?.changed === true ? "removed" : "same"}
                    >
                      <span>{row.old?.line}</span>
                      <span class="sp-code-sign">−</span>
                      <code>{row.old?.text ?? " "}</code>
                    </div>
                  </Show>
                  <Show when={row.new !== null}>
                    <div
                      class="sp-code-line"
                      data-tone={row.new?.changed === true ? "added" : "same"}
                    >
                      <span>{row.new?.line}</span>
                      <span class="sp-code-sign">
                        {row.new?.changed === true ? "+" : " "}
                      </span>
                      <code>{row.new?.text ?? " "}</code>
                    </div>
                  </Show>
                </>
              )}
            </For>
          </div>
        }
      >
        <div class="sp-code-columns">
          <For each={["old", "new"] as const}>
            {(side) => (
              <div class="sp-code-side">
                <For each={props.entity.rows}>
                  {(row) => (
                    <div
                      class="sp-code-line"
                      data-tone={
                        row[side] === null
                          ? "absent"
                          : row[side]?.changed
                            ? side === "old"
                              ? "removed"
                              : "added"
                            : "same"
                      }
                    >
                      <span>{row[side]?.line ?? ""}</span>
                      <code>{row[side]?.text ?? " "}</code>
                    </div>
                  )}
                </For>
              </div>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}

/**
 * Hosts the five experiments with URL-backed variant/fixture choices. Per-design
 * interaction state is memory-only and resets on fixture or design replacement.
 * The keyboard listener exists only for this mounted prototype and is disposed
 * with it. Native form fields and editable text keep their arrow-key behavior.
 */
export function SemanticPrototype(): JSX.Element {
  const search = new URLSearchParams(window.location.search);
  const initialVariant = search.get("variant") ?? "A";
  assert(
    ["A", "B", "C", "D", "E"].includes(initialVariant),
    "Unknown prototype variant.",
  );
  const [variant, setVariant] = createSignal(initialVariant);
  const [fixtureId, setFixtureId] = createSignal(
    search.get("fixture") ?? "extraction",
  );
  const [view, setView] = createSignal<"split" | "inline">("split");
  const [stateOpen, setStateOpen] = createSignal(false);
  const fixture = createMemo(() =>
    expect(
      fixtureData.find((item) => item.id === fixtureId()),
      "Unknown prototype fixture.",
    ),
  );
  const isSemCapture = createMemo(() => fixture().id === semCapture.fixture.id);
  const designs = [
    {
      id: "A",
      name: "Outline",
      author: "Sol",
      premise: "Pick a symbol from its file and class hierarchy.",
    },
    {
      id: "B",
      name: "Ledger",
      author: "Sol",
      premise: "Scan the whole patch by operation; open changes in place.",
    },
    {
      id: "C",
      name: "Relations",
      author: "Sol",
      premise: "Follow the connections between changed declarations.",
    },
    {
      id: "D",
      name: "Review queue",
      author: "Sol",
      premise: "Work through related changes one decision at a time.",
    },
    {
      id: "E",
      name: "Reading view",
      author: "Astra",
      premise: "Read in file order, with structure in the margin.",
    },
  ];
  const design = createMemo(() =>
    expect(
      designs.find((item) => item.id === variant()),
      "Unknown prototype design.",
    ),
  );
  // Writing both choices in one explicit action makes copied URLs reproducible.
  /**
   * Replaces the experiment selection and URL without mounting the real application.
   * @param nextVariant One of the five explicit design letters.
   * @param nextFixture An id present in the static fixture collection.
   */
  function choose(nextVariant: string, nextFixture: string): void {
    assert(
      designs.some((item) => item.id === nextVariant),
      "Unknown prototype variant.",
    );
    assert(
      fixtureData.some((item) => item.id === nextFixture),
      "Unknown prototype fixture.",
    );
    const url = new URL(window.location.href);
    url.searchParams.set("prototype", "semantic");
    url.searchParams.set("variant", nextVariant);
    url.searchParams.set("fixture", nextFixture);
    window.history.replaceState(null, "", url);
    setVariant(nextVariant);
    setFixtureId(nextFixture);
    window.scrollTo(0, 0);
  }
  /** Cycles the five designs in either direction, including wraparound. */
  function cycle(direction: number): void {
    const index = designs.findIndex((item) => item.id === variant());
    choose(
      expect(
        designs[(index + direction + designs.length) % designs.length],
        "Missing prototype variant.",
      ).id,
      fixtureId(),
    );
  }
  onMount(() => {
    /** Handles only unmodified horizontal arrows outside native editing controls. */
    const onKey = (event: KeyboardEvent): void => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
        return;
      }
      if (
        event.target instanceof HTMLElement &&
        event.target.closest(
          'input,textarea,select,[contenteditable]:not([contenteditable="false"])',
        ) !== null
      ) {
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        cycle(event.key === "ArrowRight" ? 1 : -1);
      }
    };
    window.addEventListener("keydown", onKey);
    onCleanup(() => window.removeEventListener("keydown", onKey));
  });
  return (
    <div class="sp-root" data-variant={variant()} data-fixture={fixture().id}>
      <header class="sp-shell-header">
        <a class="sp-brand" href="/">
          dirdiff
        </a>
        <span class="sp-shell-divider" />
        <span class="sp-repository">
          {isSemCapture() ? "dirdiff" : "acme / review-lab"}
        </span>
        <span class="sp-branch">
          {fixture().base} <span>←</span> {fixture().branch}
        </span>
        <span class="sp-prototype-tag">Structure prototype</span>
        <div class="sp-view-toggle" role="group" aria-label="Code view">
          <button
            aria-pressed={view() === "split"}
            onClick={() => setView("split")}
          >
            Split
          </button>
          <button
            aria-pressed={view() === "inline"}
            onClick={() => setView("inline")}
          >
            Inline
          </button>
        </div>
        <button
          class="sp-state-toggle"
          aria-expanded={stateOpen()}
          onClick={() => setStateOpen(!stateOpen())}
        >
          Fixture & state
        </button>
      </header>
      <div class="sp-scenario-bar">
        <label>
          Scenario{" "}
          <select
            aria-label="Scenario"
            value={fixtureId()}
            onChange={(event) => choose(variant(), event.currentTarget.value)}
          >
            <For each={fixtureData}>
              {(item) => <option value={item.id}>{item.title}</option>}
            </For>
          </select>
        </label>
        <p>{fixture().description}</p>
        <span class="sp-fixture-note">
          {isSemCapture() ? "Captured sem output" : "Hand-authored JSON"}
        </span>
      </div>
      <Show when={isSemCapture()}>
        <aside class="sp-sem-evidence" aria-label="Sem capture provenance">
          <div>
            <strong>sem {semCapture.provenance.semVersion}</strong>
            <span>
              {semCapture.provenance.changedEntities} changes ·{" "}
              {semCapture.provenance.contextEntities} context entities ·{" "}
              {semCapture.provenance.retainedEdges} relationships
            </span>
            <span>
              After snapshot only · captured{" "}
              {semCapture.provenance.capturedAt.slice(0, 16).replace("T", " ")}{" "}
              UTC
            </span>
          </div>
          <details>
            <summary>
              Unverified tool output · two confirmed false matches retained
            </summary>
            <p>
              All six changed files are included, including untracked files. CSS
              appears as{" "}
              {
                semCapture.fixture.entities.filter(
                  (entity) => entity.kind === "chunk",
                ).length
              }{" "}
              chunks. Context includes direct neighbors and enclosing
              declarations. Review chapters group by file. This is a fixed
              capture, not live analysis.
            </p>
            <ul>
              <li>
                <code>main → render</code>: sem points into TextDiffGrid, but
                main imports render from solid-js/web.
              </li>
              <li>
                <code>PrototypeB → expanded</code>: sem points into FileTree,
                but expanded is a local Solid signal.
              </li>
            </ul>
            <p>
              Every arrow and its calls/typeref label is preserved from sem.
              Source rows use difflib alignment for display.
            </p>
          </details>
        </aside>
      </Show>
      <div class="sp-experiment-intro">
        <span>
          {design().id} / {design().name}
        </span>
        <p>{design().premise}</p>
        <small>
          {fixture().entities.length} entities ·{" "}
          {new Set(fixture().entities.map((entity) => entity.path)).size} files
        </small>
      </div>
      <Show when={`${variant()}:${fixtureId()}`} keyed>
        {(_key) => (
          <Switch>
            <Match when={variant() === "A"}>
              <PrototypeA fixture={fixture()} view={view()} />
            </Match>
            <Match when={variant() === "B"}>
              <PrototypeB fixture={fixture()} view={view()} />
            </Match>
            <Match when={variant() === "C"}>
              <PrototypeC fixture={fixture()} view={view()} />
            </Match>
            <Match when={variant() === "D"}>
              <PrototypeD fixture={fixture()} view={view()} />
            </Match>
            <Match when={variant() === "E"}>
              <PrototypeE fixture={fixture()} view={view()} />
            </Match>
          </Switch>
        )}
      </Show>
      <Show when={stateOpen()}>
        <aside class="sp-state-drawer" aria-label="Fixture and prototype state">
          <header>
            <strong>Prototype state</strong>
            <button
              onClick={() => setStateOpen(false)}
              aria-label="Close fixture state"
            >
              ×
            </button>
          </header>
          <p>
            {isSemCapture()
              ? "Entities and relationships come from a frozen sem run. The capture includes raw edges and provenance below. Review marks live only in this page."
              : "Pairings, descriptions, and relationships are supplied by the fixture. Nothing here is engine output. Review marks live only in this page."}
          </p>
          <pre>
            {JSON.stringify(
              {
                variant: variant(),
                author: design().author,
                view: view(),
                fixture: fixture(),
                source: isSemCapture()
                  ? semCapture.provenance
                  : "Hand-authored fixture",
                rawEdges: isSemCapture() ? semCapture.rawEdges : null,
              },
              null,
              2,
            )}
          </pre>
        </aside>
      </Show>
      <nav class="sp-switcher" aria-label="Prototype variants">
        <button aria-label="Previous variant" onClick={() => cycle(-1)}>
          ←
        </button>
        <span class="sp-switcher-caption">TRY A VIEW</span>
        <For each={designs}>
          {(item) => (
            <button
              class="sp-variant-button"
              aria-current={variant() === item.id ? "page" : undefined}
              onClick={() => choose(item.id, fixtureId())}
            >
              <b>{item.id}</b> {item.name}
            </button>
          )}
        </For>
        <button aria-label="Next variant" onClick={() => cycle(1)}>
          →
        </button>
      </nav>
    </div>
  );
}

/**
 * E tests a continuous source-order reading experience. File headers and entity
 * margins carry structure while the code remains the main document. Fold and
 * review choices are local to this mounted fixture; original line numbers and
 * cross-file move origins are always visible when the entity is expanded.
 */
function PrototypeE(props: PrototypeVariantProps): JSX.Element {
  const [closed, setClosed] = createSignal<Set<string>>(new Set());
  const [reviewed, setReviewed] = createSignal<Set<string>>(new Set());
  const [fileFilter, setFileFilter] = createSignal("all");
  const [unreadOnly, setUnreadOnly] = createSignal(false);
  const paths = [
    ...new Set(props.fixture.entities.map((entity) => entity.path)),
  ].sort();
  const files = createMemo(() =>
    paths
      .filter((path) => fileFilter() === "all" || path === fileFilter())
      .map((path) => ({
        path,
        entities: props.fixture.entities
          .filter(
            (entity) =>
              entity.path === path &&
              (!unreadOnly() || !reviewed().has(entity.id)),
          )
          .sort((a, b) => {
            // Removed declarations have only an old coordinate; all other
            // declarations are placed at their required new source start.
            const aStart =
              a.status === "removed"
                ? a.rows.find((row) => row.old !== null)?.old
                : a.rows.find((row) => row.new !== null)?.new;
            const bStart =
              b.status === "removed"
                ? b.rows.find((row) => row.old !== null)?.old
                : b.rows.find((row) => row.new !== null)?.new;
            return (
              expect(aStart, "Entity needs a source start.").line -
              expect(bStart, "Entity needs a source start.").line
            );
          }),
      }))
      .filter((file) => file.entities.length > 0),
  );
  return (
    <section class="sp-e" aria-label="Source-order reading view">
      <div class="sp-e-toolbar">
        <div>
          <strong>Read the patch</strong>
          <span>Source order · changed excerpts</span>
        </div>
        <label>
          File{" "}
          <select
            aria-label="Reading view file"
            value={fileFilter()}
            onChange={(event) => setFileFilter(event.currentTarget.value)}
          >
            <option value="all">All files</option>
            <For each={paths}>
              {(path) => <option value={path}>{path}</option>}
            </For>
          </select>
        </label>
        <button onClick={() => setClosed(new Set())}>Expand all</button>
        <button
          onClick={() =>
            setClosed(
              new Set(props.fixture.entities.map((entity) => entity.id)),
            )
          }
        >
          Fold all
        </button>
        <label class="sp-e-unread">
          <input
            type="checkbox"
            checked={unreadOnly()}
            onChange={(event) => setUnreadOnly(event.currentTarget.checked)}
          />{" "}
          Unreviewed only
        </label>
        <span class="sp-e-progress">
          {reviewed().size} / {props.fixture.entities.length} reviewed
        </span>
      </div>
      <div class="sp-e-page">
        <aside class="sp-e-contents">
          <span>IN THIS PATCH</span>
          <For each={files()}>
            {(file) => (
              <a href={`#sp-e-file-${paths.indexOf(file.path)}`}>
                <b>{file.path.split("/").at(-1)}</b>
                <small>{file.path.split("/").slice(0, -1).join("/")}</small>
                <span>{file.entities.length}</span>
              </a>
            )}
          </For>
        </aside>
        <main class="sp-e-document">
          <Show
            when={files().length > 0}
            fallback={
              <div class="sp-e-empty">
                <strong>All visible changes reviewed.</strong>
                <button onClick={() => setUnreadOnly(false)}>
                  Show reviewed changes
                </button>
              </div>
            }
          >
            <For each={files()}>
              {(file) => (
                <section
                  class="sp-e-file"
                  id={`sp-e-file-${paths.indexOf(file.path)}`}
                >
                  <header class="sp-e-file-header">
                    <span>
                      FILE{" "}
                      {String(paths.indexOf(file.path) + 1).padStart(2, "0")}
                    </span>
                    <h2>{file.path}</h2>
                    <span>{file.entities.length} entities</span>
                  </header>
                  <For each={file.entities}>
                    {(entity) => (
                      <article
                        class="sp-e-entity"
                        data-status={entity.status}
                        data-reviewed={reviewed().has(entity.id)}
                      >
                        <div class="sp-e-margin">
                          <span class="sp-e-kind">{entity.kind}</span>
                          <button
                            class="sp-e-entity-toggle"
                            aria-expanded={!closed().has(entity.id)}
                            onClick={() =>
                              setClosed((previous) => {
                                const next = new Set(previous);
                                if (next.has(entity.id)) {
                                  next.delete(entity.id);
                                } else {
                                  next.add(entity.id);
                                }
                                return next;
                              })
                            }
                          >
                            <span>{closed().has(entity.id) ? "▸" : "▾"}</span>
                            <strong>{entity.name}</strong>
                          </button>
                          <span class="sp-status" data-status={entity.status}>
                            {entity.status}
                          </span>
                          <label class="sp-e-review">
                            <input
                              type="checkbox"
                              aria-label={`Reviewed ${entity.name}`}
                              checked={reviewed().has(entity.id)}
                              onChange={(event) =>
                                setReviewed((previous) => {
                                  const next = new Set(previous);
                                  if (event.currentTarget.checked) {
                                    next.add(entity.id);
                                  } else {
                                    next.delete(entity.id);
                                  }
                                  return next;
                                })
                              }
                            />{" "}
                            Reviewed
                          </label>
                        </div>
                        <div class="sp-e-source">
                          <p class="sp-e-summary">{entity.summary}</p>
                          <Show when={entity.oldName !== null}>
                            <div class="sp-e-provenance">
                              {entity.oldName} → {entity.name}
                            </div>
                          </Show>
                          <Show when={entity.oldPath !== null}>
                            <div class="sp-e-provenance">
                              Moved from {entity.oldPath}:
                              {
                                entity.rows.find((row) => row.old !== null)?.old
                                  ?.line
                              }
                            </div>
                          </Show>
                          <Show when={!closed().has(entity.id)}>
                            <PrototypeCode entity={entity} view={props.view} />
                            <Show when={entity.match !== null}>
                              <details class="sp-e-match">
                                <summary>About this pairing</summary>
                                <p>{entity.match}</p>
                              </details>
                            </Show>
                          </Show>
                          <Show when={closed().has(entity.id)}>
                            <button
                              class="sp-e-folded"
                              onClick={() =>
                                setClosed((previous) => {
                                  const next = new Set(previous);
                                  next.delete(entity.id);
                                  return next;
                                })
                              }
                            >
                              {entity.rows.length} source rows folded · expand
                            </button>
                          </Show>
                        </div>
                      </article>
                    )}
                  </For>
                </section>
              )}
            </For>
          </Show>
        </main>
      </div>
      <details class="sp-local-state">
        <summary>Interaction state</summary>
        <pre>
          {JSON.stringify(
            {
              file: fileFilter(),
              unreadOnly: unreadOnly(),
              folded: [...closed()],
              reviewed: [...reviewed()],
            },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  );
}

/**
 * Tests a symbol outline beside one fixture source diff. Fixture data remains
 * immutable; selection, search, collapsed branches, and reviewed ids live only
 * for this mounted experiment. It does not infer matches or mutate a review.
 */
function PrototypeA(props: PrototypeVariantProps): JSX.Element {
  const [selected, setSelected] = createSignal(
    expect(props.fixture.entities[0], "The outline requires an entity").id,
  );
  const [search, setSearch] = createSignal("");
  const [pendingOnly, setPendingOnly] = createSignal(false);
  const [collapsed, setCollapsed] = createSignal(new Set<string>());
  const [reviewed, setReviewed] = createSignal(new Set<string>());
  const entity = createMemo(() =>
    expect(
      props.fixture.entities.find((item) => item.id === selected()),
      "Selected entity must exist",
    ),
  );
  const files = createMemo(() => [
    ...new Set(props.fixture.entities.map((item) => item.path)),
  ]);
  const visible = createMemo(() =>
    props.fixture.entities.filter(
      (item) =>
        (!pendingOnly() || !reviewed().has(item.id)) &&
        `${item.path} ${item.name} ${item.oldName ?? ""} ${item.kind} ${item.status}`
          .toLowerCase()
          .includes(search().toLowerCase().trim()),
    ),
  );

  /** Includes an ancestor when its descendant matches the current outline filter. */
  function shown(item: PrototypeEntity): boolean {
    return (
      visible().some((candidate) => candidate.id === item.id) ||
      props.fixture.entities.some(
        (child) =>
          child.parentId === item.id &&
          child.path === item.path &&
          shown(child),
      )
    );
  }

  /** Toggles the clicked branch only; search temporarily exposes matching descendants. */
  function toggleBranch(key: string): void {
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  /** Renders one fixture symbol and its same-file children without duplicating entity data. */
  function OutlineNode(node: {
    /** Current symbol, including its explicit parent relationship. */
    item: PrototypeEntity;
    /** Nesting depth used only to indent the outline. */
    depth: number;
  }): JSX.Element {
    /** Reads same-file child declarations for this outline branch. */
    const children = () =>
      props.fixture.entities.filter(
        (child) =>
          child.parentId === node.item.id && child.path === node.item.path,
      );
    return (
      <Show when={shown(node.item)}>
        <div
          class="sp-a-symbol-row"
          classList={{
            "sp-a-selected": selected() === node.item.id,
            "sp-a-reviewed": reviewed().has(node.item.id),
          }}
          style={{ "--sp-a-depth": node.depth }}
        >
          <Show
            when={children().length > 0}
            fallback={<span class="sp-a-branch-space" />}
          >
            <button
              class="sp-a-branch-toggle"
              aria-label={`${collapsed().has(node.item.id) ? "Expand" : "Collapse"} ${node.item.name}`}
              aria-expanded={
                search().trim() !== "" || !collapsed().has(node.item.id)
              }
              onClick={() => toggleBranch(node.item.id)}
            >
              {search().trim() !== "" || !collapsed().has(node.item.id)
                ? "⌄"
                : "›"}
            </button>
          </Show>
          <button
            class="sp-a-symbol-select"
            aria-pressed={selected() === node.item.id}
            onClick={() => setSelected(node.item.id)}
            title={`${node.item.kind}: ${node.item.name} · ${node.item.status}`}
          >
            <span class="sp-a-kind">
              {node.item.kind === "method"
                ? "m"
                : node.item.kind === "function"
                  ? "ƒ"
                  : node.item.kind === "class"
                    ? "C"
                    : "≡"}
            </span>
            <span class="sp-a-symbol-name">{node.item.name}</span>
            <span
              class="sp-a-status-mark"
              data-status={node.item.status}
              title={node.item.status}
            >
              {node.item.status === "added"
                ? "+"
                : node.item.status === "removed"
                  ? "−"
                  : node.item.status === "moved"
                    ? "↗"
                    : node.item.status === "renamed"
                      ? "R"
                      : node.item.status === "unchanged" ||
                          node.item.status === "context"
                        ? "·"
                        : "M"}
            </span>
            <Show when={reviewed().has(node.item.id)}>
              <span class="sp-a-review-check" title="Reviewed">
                ✓
              </span>
            </Show>
          </button>
        </div>
        <Show when={search().trim() !== "" || !collapsed().has(node.item.id)}>
          <For each={children()}>
            {(child) => <OutlineNode item={child} depth={node.depth + 1} />}
          </For>
        </Show>
      </Show>
    );
  }

  return (
    <div class="sp-a">
      <aside class="sp-a-outline" aria-label="Code outline">
        <div class="sp-a-outline-heading">
          <div>
            <span class="sp-a-eyebrow">Review by symbol</span>
            <h2>Code outline</h2>
          </div>
          <span class="sp-a-total">{props.fixture.entities.length}</span>
        </div>
        <div class="sp-a-search">
          <span aria-hidden="true">⌕</span>
          <input
            aria-label="Search symbols and files"
            placeholder="Find a symbol or file…"
            value={search()}
            onInput={(event) => setSearch(event.currentTarget.value)}
          />
          <Show when={search() !== ""}>
            <button
              aria-label="Clear symbol search"
              onClick={() => setSearch("")}
            >
              ×
            </button>
          </Show>
        </div>
        <div class="sp-a-outline-tools">
          <label>
            <input
              type="checkbox"
              checked={pendingOnly()}
              onChange={(event) => setPendingOnly(event.currentTarget.checked)}
            />{" "}
            Hide reviewed
          </label>
          <span>
            {reviewed().size} / {props.fixture.entities.length} reviewed
          </span>
        </div>
        <nav class="sp-a-tree" aria-label="Files and symbols">
          <For each={files()}>
            {(path) => {
              /** Reads the declarations currently assigned to this file. */
              const fileEntities = () =>
                props.fixture.entities.filter((item) => item.path === path);
              /** Starts branches at declarations without a same-file parent. */
              const roots = () =>
                fileEntities().filter(
                  (item) =>
                    item.parentId === null ||
                    expect(
                      props.fixture.entities.find(
                        (parent) => parent.id === item.parentId,
                      ),
                      "Parent entity must exist",
                    ).path !== path,
                );
              return (
                <Show when={fileEntities().some(shown)}>
                  <section class="sp-a-file">
                    <button
                      class="sp-a-file-heading"
                      aria-expanded={
                        search().trim() !== "" ||
                        !collapsed().has(`file:${path}`)
                      }
                      onClick={() => toggleBranch(`file:${path}`)}
                      title={path}
                    >
                      <span class="sp-a-file-chevron">
                        {search().trim() !== "" ||
                        !collapsed().has(`file:${path}`)
                          ? "⌄"
                          : "›"}
                      </span>
                      <span class="sp-a-file-path">{path}</span>
                      <span class="sp-a-file-count">
                        {fileEntities().length}
                      </span>
                    </button>
                    <Show
                      when={
                        search().trim() !== "" ||
                        !collapsed().has(`file:${path}`)
                      }
                    >
                      <For each={roots()}>
                        {(item) => <OutlineNode item={item} depth={0} />}
                      </For>
                    </Show>
                  </section>
                </Show>
              );
            }}
          </For>
          <Show when={visible().length === 0}>
            <div class="sp-a-empty">
              No symbols match.
              <button
                onClick={() => {
                  setSearch("");
                  setPendingOnly(false);
                }}
              >
                Show the full outline
              </button>
            </div>
          </Show>
        </nav>
        <div class="sp-a-outline-footer">
          <span>
            <i data-status="added">+</i> added
          </span>
          <span>
            <i data-status="modified">M</i> modified
          </span>
          <span>
            <i data-status="moved">↗</i> moved
          </span>
          <span>
            <i data-status="removed">−</i> removed
          </span>
        </div>
      </aside>
      <main class="sp-a-workbench">
        <Show when={entity()} keyed>
          {(current) => (
            <>
              <header class="sp-a-entity-header">
                <div class="sp-a-breadcrumb">
                  <span>{current.path}</span>
                  <Show when={current.parentId !== null}>
                    <span class="sp-a-breadcrumb-divider">/</span>
                    <button
                      onClick={() =>
                        setSelected(
                          expect(current.parentId, "Parent id is required"),
                        )
                      }
                    >
                      {
                        expect(
                          props.fixture.entities.find(
                            (parent) => parent.id === current.parentId,
                          ),
                          "Parent entity must exist",
                        ).name
                      }
                    </button>
                  </Show>
                  <span class="sp-a-breadcrumb-divider">/</span>
                  <span>{current.kind}</span>
                </div>
                <div class="sp-a-title-row">
                  <div class="sp-a-title">
                    <span class="sp-a-entity-kind">
                      {current.kind === "function"
                        ? "ƒ"
                        : current.kind === "method"
                          ? "m"
                          : current.kind === "class"
                            ? "C"
                            : "≡"}
                    </span>
                    <h1>{current.name}</h1>
                    <span class="sp-a-status" data-status={current.status}>
                      {current.status}
                    </span>
                  </div>
                  <button
                    class="sp-a-review-button"
                    classList={{
                      "sp-a-review-button-done": reviewed().has(current.id),
                    }}
                    aria-pressed={reviewed().has(current.id)}
                    onClick={() =>
                      setReviewed((previous) => {
                        const next = new Set(previous);
                        if (next.has(current.id)) {
                          next.delete(current.id);
                        } else {
                          next.add(current.id);
                        }
                        return next;
                      })
                    }
                  >
                    {reviewed().has(current.id)
                      ? "✓ Reviewed"
                      : "Mark reviewed"}
                  </button>
                </div>
                <p class="sp-a-summary">{current.summary}</p>
                <Show
                  when={current.oldPath !== null || current.oldName !== null}
                >
                  <div class="sp-a-provenance">
                    <span class="sp-a-provenance-label">Moved / renamed</span>
                    <code>
                      {current.oldPath ?? current.path}
                      <Show when={current.oldName !== null}>
                        {" "}
                        · {current.oldName}
                      </Show>
                    </code>
                    <span class="sp-a-provenance-arrow">→</span>
                    <code>
                      {current.path} · {current.name}
                    </code>
                  </div>
                </Show>
                <Show when={current.match !== null}>
                  <div class="sp-a-evidence">
                    <span class="sp-a-evidence-label">
                      Pairing evidence <small>fixture</small>
                    </span>
                    <span>{current.match}</span>
                  </div>
                </Show>
              </header>
              <div class="sp-a-code-scroll">
                <div class="sp-a-source-heading">
                  <span>Source diff</span>
                  <span>
                    Fixture source ·{" "}
                    {props.view === "split" ? "side by side" : "inline"}
                  </span>
                </div>
                <PrototypeCode entity={current} view={props.view} />
                <Show
                  when={props.fixture.relations.some(
                    (relation) =>
                      relation.from === current.id ||
                      relation.to === current.id,
                  )}
                >
                  <div class="sp-a-related">
                    <span class="sp-a-eyebrow">Related changes</span>
                    <For
                      each={props.fixture.relations.filter(
                        (relation) =>
                          relation.from === current.id ||
                          relation.to === current.id,
                      )}
                    >
                      {(relation) => {
                        const otherId =
                          relation.from === current.id
                            ? relation.to
                            : relation.from;
                        const other = expect(
                          props.fixture.entities.find(
                            (item) => item.id === otherId,
                          ),
                          "Related entity must exist",
                        );
                        return (
                          <button onClick={() => setSelected(other.id)}>
                            <strong>
                              {relation.from === current.id
                                ? current.name
                                : other.name}
                            </strong>
                            <span class="sp-a-relation-direction">
                              {relation.label} →
                            </span>
                            <strong>
                              {relation.to === current.id
                                ? current.name
                                : other.name}
                            </strong>
                            <code>{other.path}</code>
                            <span aria-hidden="true">↗</span>
                          </button>
                        );
                      }}
                    </For>
                  </div>
                </Show>
                <details class="sp-local-state sp-a-state">
                  <summary>Interaction state</summary>
                  <pre>
                    {JSON.stringify(
                      {
                        selected: selected(),
                        search: search(),
                        pendingOnly: pendingOnly(),
                        collapsed: [...collapsed()],
                        reviewed: [...reviewed()],
                      },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              </div>
              <footer class="sp-a-workbench-footer">
                <span>
                  {current.kind} in <strong>{current.path}</strong>
                </span>
                <span>
                  {reviewed().has(current.id)
                    ? "Reviewed in this prototype"
                    : "Awaiting review"}
                </span>
              </footer>
            </>
          )}
        </Show>
      </main>
    </div>
  );
}

/**
 * Tests a whole-patch entity ledger with code expanded inside its table.
 * Search, operation and review filters, grouping, expanded rows, and reviewed
 * identities live only for this mounted prototype; fixture data stays untouched.
 */
function PrototypeB(props: PrototypeVariantProps): JSX.Element {
  const [search, setSearch] = createSignal("");
  const [operation, setOperation] = createSignal("all");
  const [groupBy, setGroupBy] = createSignal("operation");
  const [unreviewedOnly, setUnreviewedOnly] = createSignal(false);
  const [expanded, setExpanded] = createSignal(new Set<string>());
  const [reviewed, setReviewed] = createSignal(new Set<string>());
  const operations = [
    "moved",
    "renamed",
    "modified",
    "added",
    "removed",
    "unchanged",
    "context",
  ];
  const visible = createMemo(() => {
    const query = search().trim().toLowerCase();
    return props.fixture.entities.filter(
      (entity) =>
        (operation() === "all" || entity.status === operation()) &&
        (!unreviewedOnly() || !reviewed().has(entity.id)) &&
        [
          entity.name,
          entity.oldName,
          entity.path,
          entity.oldPath,
          entity.kind,
          entity.summary,
        ].some(
          (value) => value !== null && value.toLowerCase().includes(query),
        ),
    );
  });
  const groups = createMemo(() => {
    const keys =
      groupBy() === "operation"
        ? operations.filter((status) =>
            visible().some((entity) => entity.status === status),
          )
        : [...new Set(visible().map((entity) => entity.path))].sort();
    return keys.map((key) => ({
      key,
      entities: visible()
        .filter(
          (entity) =>
            (groupBy() === "operation" ? entity.status : entity.path) === key,
        )
        .sort((a, b) => a.name.localeCompare(b.name)),
    }));
  });

  return (
    <main class="sp-b">
      <header class="sp-b-heading">
        <div>
          <div class="sp-b-eyebrow">Whole patch · entity index</div>
          <h1>Change ledger</h1>
          <p>Scan the changes. Open code where you need it.</p>
        </div>
        <div class="sp-b-progress">
          <strong>
            {reviewed().size}
            <span> / {props.fixture.entities.length}</span>
          </strong>
          <span>entities reviewed</span>
          <div class="sp-b-progress-track">
            <div
              style={{
                width: `${props.fixture.entities.length === 0 ? 0 : (reviewed().size / props.fixture.entities.length) * 100}%`,
              }}
            />
          </div>
        </div>
      </header>
      <section class="sp-b-controls" aria-label="Ledger controls">
        <label class="sp-b-search">
          <span aria-hidden="true">⌕</span>
          <input
            type="search"
            aria-label="Search entities and paths"
            placeholder="Search entities, paths, or changes…"
            value={search()}
            onInput={(event) => setSearch(event.currentTarget.value)}
          />
        </label>
        <label class="sp-b-group-control">
          Group by
          <select
            aria-label="Group ledger by"
            value={groupBy()}
            onChange={(event) => setGroupBy(event.currentTarget.value)}
          >
            <option value="operation">Operation</option>
            <option value="path">File path</option>
          </select>
        </label>
        <label class="sp-b-unreviewed">
          <input
            type="checkbox"
            checked={unreviewedOnly()}
            onChange={(event) => setUnreviewedOnly(event.currentTarget.checked)}
          />
          Unreviewed only
        </label>
      </section>
      <div class="sp-b-filters" aria-label="Filter operation">
        <button
          classList={{ "sp-b-active": operation() === "all" }}
          aria-pressed={operation() === "all"}
          onClick={() => setOperation("all")}
        >
          All entities <span>{props.fixture.entities.length}</span>
        </button>
        <For
          each={operations.filter((status) =>
            props.fixture.entities.some((entity) => entity.status === status),
          )}
        >
          {(status) => (
            <button
              classList={{ "sp-b-active": operation() === status }}
              aria-pressed={operation() === status}
              onClick={() => setOperation(status)}
            >
              <i class={`sp-b-dot sp-b-${status}`} />
              <span class="sp-b-operation-name">{status}</span>
              <span>
                {
                  props.fixture.entities.filter(
                    (entity) => entity.status === status,
                  ).length
                }
              </span>
            </button>
          )}
        </For>
        <div class="sp-b-open-actions">
          <span>{visible().length} shown</span>
          <button
            disabled={expanded().size === 0}
            onClick={() => setExpanded(new Set<string>())}
          >
            Close code <span>{expanded().size}</span>
          </button>
        </div>
      </div>
      <div class="sp-b-table-wrap">
        <table class="sp-b-ledger">
          <colgroup>
            <col class="sp-b-col-open" />
            <col class="sp-b-col-operation" />
            <col class="sp-b-col-entity" />
            <col class="sp-b-col-source" />
            <col class="sp-b-col-summary" />
            <col class="sp-b-col-review" />
          </colgroup>
          <thead>
            <tr>
              <th aria-label="Expand code" />
              <th>Operation</th>
              <th>Entity</th>
              <th>Source · before → after</th>
              <th>Change</th>
              <th>Reviewed</th>
            </tr>
          </thead>
          <For each={groups()}>
            {(group) => (
              <tbody>
                <tr class="sp-b-section">
                  <th colSpan={6}>
                    <span
                      classList={{
                        "sp-b-section-status": groupBy() === "operation",
                      }}
                    >
                      {group.key}
                    </span>
                    <span class="sp-b-section-count">
                      {group.entities.length}{" "}
                      {group.entities.length === 1 ? "entity" : "entities"}
                    </span>
                  </th>
                </tr>
                <For each={group.entities}>
                  {(entity) => (
                    <>
                      <tr
                        class="sp-b-entity-row"
                        classList={{
                          "sp-b-is-open": expanded().has(entity.id),
                          "sp-b-is-reviewed": reviewed().has(entity.id),
                        }}
                      >
                        <td class="sp-b-disclosure">
                          <button
                            aria-label={`${expanded().has(entity.id) ? "Close" : "Open"} code for ${entity.name}`}
                            aria-expanded={expanded().has(entity.id)}
                            onClick={() =>
                              setExpanded((previous) => {
                                const next = new Set(previous);
                                if (next.has(entity.id)) {
                                  next.delete(entity.id);
                                } else {
                                  next.add(entity.id);
                                }
                                return next;
                              })
                            }
                          >
                            {expanded().has(entity.id) ? "⌄" : "›"}
                          </button>
                        </td>
                        <td>
                          <span class={`sp-b-status sp-b-${entity.status}`}>
                            {entity.status}
                          </span>
                        </td>
                        <td class="sp-b-identity">
                          <button
                            onClick={() =>
                              setExpanded((previous) => {
                                const next = new Set(previous);
                                if (next.has(entity.id)) {
                                  next.delete(entity.id);
                                } else {
                                  next.add(entity.id);
                                }
                                return next;
                              })
                            }
                            aria-expanded={expanded().has(entity.id)}
                          >
                            <Show
                              when={
                                entity.oldName !== null &&
                                entity.oldName !== entity.name
                              }
                            >
                              <span class="sp-b-old-name">
                                {entity.oldName} <span>→</span>
                              </span>
                            </Show>
                            <strong>{entity.name}</strong>
                          </button>
                          <span class="sp-b-kind">{entity.kind}</span>
                        </td>
                        <td class="sp-b-path">
                          <Show
                            when={
                              entity.oldPath !== null &&
                              entity.oldPath !== entity.path
                            }
                          >
                            <span class="sp-b-old-path">
                              {entity.oldPath}
                              <span class="sp-b-path-arrow">↓</span>
                            </span>
                          </Show>
                          <span>{entity.path}</span>
                        </td>
                        <td class="sp-b-summary">{entity.summary}</td>
                        <td class="sp-b-review">
                          <input
                            type="checkbox"
                            aria-label={`Mark ${entity.name} reviewed`}
                            checked={reviewed().has(entity.id)}
                            onChange={() =>
                              setReviewed((previous) => {
                                const next = new Set(previous);
                                if (next.has(entity.id)) {
                                  next.delete(entity.id);
                                } else {
                                  next.add(entity.id);
                                }
                                return next;
                              })
                            }
                          />
                        </td>
                      </tr>
                      <Show when={expanded().has(entity.id)}>
                        <tr class="sp-b-code-row">
                          <td colSpan={6}>
                            <div class="sp-b-code-caption">
                              <div>
                                <strong>{entity.name}</strong>
                                <span>Source diff</span>
                              </div>
                              <span>
                                {props.view === "split"
                                  ? "Before / after"
                                  : "Inline"}
                              </span>
                            </div>
                            <Show when={entity.match !== null}>
                              <div class="sp-b-match">
                                <span>Pairing note</span>
                                {entity.match}
                              </div>
                            </Show>
                            <PrototypeCode entity={entity} view={props.view} />
                            <div class="sp-b-code-bottom">
                              <span>
                                {entity.kind} · {entity.status}
                              </span>
                              <button
                                onClick={() =>
                                  setReviewed((previous) => {
                                    const next = new Set(previous);
                                    if (next.has(entity.id)) {
                                      next.delete(entity.id);
                                    } else {
                                      next.add(entity.id);
                                    }
                                    return next;
                                  })
                                }
                              >
                                {reviewed().has(entity.id)
                                  ? "✓ Reviewed · undo"
                                  : "Mark reviewed"}
                              </button>
                            </div>
                          </td>
                        </tr>
                      </Show>
                    </>
                  )}
                </For>
              </tbody>
            )}
          </For>
        </table>
        <Show when={visible().length === 0}>
          <div class="sp-b-empty">
            <strong>No matching entities</strong>
            <span>Change the search or filters to see more of the patch.</span>
          </div>
        </Show>
      </div>
      <footer class="sp-b-footer">
        <span>Open several rows to compare changes in place.</span>
        <span>Review marks live in this prototype.</span>
      </footer>
      <details class="sp-local-state">
        <summary>Interaction state</summary>
        <pre>
          {JSON.stringify(
            {
              search: search(),
              operation: operation(),
              groupBy: groupBy(),
              unreviewedOnly: unreviewedOnly(),
              expanded: [...expanded()],
              reviewed: [...reviewed()],
            },
            null,
            2,
          )}
        </pre>
      </details>
    </main>
  );
}

/**
 * Experiments with relationship-led review: an entity-centered map sits above
 * a full-width source diff. Selection, search, relationship pages, and review
 * marks belong to this mounted throwaway view and never alter fixture data.
 */
function PrototypeC(props: PrototypeVariantProps): JSX.Element {
  assert(
    props.fixture.entities.length > 0,
    "Relationship prototype needs entities",
  );
  // Begin with the entity that has the most supplied connections so each fixture
  // opens on its relationships. Equal counts preserve the fixture's entity order.
  const initialEntity = props.fixture.entities.reduce((best, entity) => {
    const entityConnections = props.fixture.relations.filter(
      (relation) => relation.from === entity.id || relation.to === entity.id,
    ).length;
    const bestConnections = props.fixture.relations.filter(
      (relation) => relation.from === best.id || relation.to === best.id,
    ).length;
    return entityConnections > bestConnections ? entity : best;
  });
  const [selectedId, setSelectedId] = createSignal(initialEntity.id);
  const [query, setQuery] = createSignal("");
  const [page, setPage] = createSignal(0);
  const [reviewed, setReviewed] = createSignal<Set<string>>(new Set());
  const [connectedOnly, setConnectedOnly] = createSignal(
    props.fixture.id === semCapture.fixture.id,
  );
  // The mounted fixture is immutable; this index only drives the explicit list filter.
  const connectedIds = new Set(
    props.fixture.relations.flatMap((relation) => [relation.from, relation.to]),
  );
  const selected = createMemo(() =>
    expect(
      props.fixture.entities.find((entity) => entity.id === selectedId()),
      "Selected entity must exist",
    ),
  );
  const incoming = createMemo(() =>
    props.fixture.relations.filter((relation) => relation.to === selectedId()),
  );
  const outgoing = createMemo(() =>
    props.fixture.relations.filter(
      (relation) => relation.from === selectedId(),
    ),
  );
  const pageCount = createMemo(() =>
    Math.max(1, Math.ceil(Math.max(incoming().length, outgoing().length) / 3)),
  );
  const mapHeight = createMemo(
    () =>
      Math.max(
        1,
        Math.min(
          3,
          Math.max(incoming().length, outgoing().length) - page() * 3,
        ),
      ) *
        74 +
      48,
  );
  const visibleEntities = createMemo(() =>
    props.fixture.entities.filter(
      (entity) =>
        (!connectedOnly() || connectedIds.has(entity.id)) &&
        `${entity.name} ${entity.path} ${entity.status} ${entity.kind}`
          .toLowerCase()
          .includes(query().toLowerCase()),
    ),
  );

  return (
    <section class="sp-c">
      <div class="sp-c-heading">
        <h1>Entity connections</h1>
        <div class="sp-c-progress">
          <strong>{reviewed().size}</strong> / {props.fixture.entities.length}{" "}
          reviewed
        </div>
      </div>
      <div class="sp-c-index">
        <label class="sp-c-search">
          <span>Find an entity</span>
          <input
            aria-label="Find an entity"
            placeholder="Name, file, kind…"
            value={query()}
            onInput={(event) => setQuery(event.currentTarget.value)}
          />
        </label>
        <label class="sp-c-connected-filter">
          <input
            type="checkbox"
            checked={connectedOnly()}
            onChange={(event) => setConnectedOnly(event.currentTarget.checked)}
          />{" "}
          Connected only <span>{connectedIds.size}</span>
        </label>
        <div class="sp-c-entity-strip" aria-label="All fixture entities">
          <For each={visibleEntities()}>
            {(entity) => (
              <button
                class="sp-c-index-item"
                aria-pressed={selectedId() === entity.id}
                classList={{
                  "sp-c-active": selectedId() === entity.id,
                  "sp-c-reviewed": reviewed().has(entity.id),
                }}
                onClick={() => {
                  setSelectedId(entity.id);
                  setPage(0);
                }}
                title={`${entity.name} · ${entity.path}`}
              >
                <span class="sp-c-index-dot" data-status={entity.status} />
                <span>{entity.name}</span>
                <Show when={reviewed().has(entity.id)}>
                  <span class="sp-c-check">✓</span>
                </Show>
              </button>
            )}
          </For>
          <Show when={visibleEntities().length === 0}>
            <span class="sp-c-no-results">No entities match this search.</span>
          </Show>
        </div>
      </div>
      <div class="sp-c-map-bar">
        <span>
          {props.fixture.id === semCapture.fixture.id
            ? "sem 0.25.0 · after-graph relationships · original labels"
            : "Relationships supplied by this fixture"}
        </span>
        <div>
          <Show when={pageCount() > 1}>
            <button
              aria-label="Previous connections"
              disabled={page() === 0}
              onClick={() => setPage(page() - 1)}
            >
              ‹
            </button>
            <span>
              {page() + 1} / {pageCount()}
            </span>
            <button
              aria-label="Next connections"
              disabled={page() === pageCount() - 1}
              onClick={() => setPage(page() + 1)}
            >
              ›
            </button>
          </Show>
          <span class="sp-c-map-hint">Select a node to inspect its code</span>
        </div>
      </div>
      <div
        class="sp-c-map"
        style={{ height: `${mapHeight()}px` }}
        aria-label="Selected entity and its fixture relationships"
      >
        <div class="sp-c-column-label sp-c-left-label">
          Incoming · {incoming().length}
        </div>
        <div class="sp-c-column-label sp-c-center-label">Inspecting</div>
        <div class="sp-c-column-label sp-c-right-label">
          Outgoing · {outgoing().length}
        </div>
        <svg
          class="sp-c-edges"
          viewBox={`0 0 1080 ${mapHeight()}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <defs>
            <marker
              id="sp-c-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#999185" />
            </marker>
          </defs>
          <For each={incoming().slice(page() * 3, page() * 3 + 3)}>
            {(relation, index) => (
              <>
                <path
                  d={`M 294 ${66 + index() * 74} C 340 ${66 + index() * 74}, 340 ${mapHeight() / 2}, 396 ${mapHeight() / 2}`}
                  marker-end="url(#sp-c-arrow)"
                />
                <text x="344" y={50 + index() * 74} text-anchor="middle">
                  {relation.label}
                </text>
              </>
            )}
          </For>
          <For each={outgoing().slice(page() * 3, page() * 3 + 3)}>
            {(relation, index) => (
              <>
                <path
                  d={`M 666 ${mapHeight() / 2} C 736 ${mapHeight() / 2}, 736 ${66 + index() * 74}, 786 ${66 + index() * 74}`}
                  marker-end="url(#sp-c-arrow)"
                />
                <text x="737" y={50 + index() * 74} text-anchor="middle">
                  {relation.label}
                </text>
              </>
            )}
          </For>
        </svg>
        <For each={incoming().slice(page() * 3, page() * 3 + 3)}>
          {(relation, index) => {
            const entity = expect(
              props.fixture.entities.find((item) => item.id === relation.from),
              "Relationship source must exist",
            );
            return (
              <button
                class="sp-c-node sp-c-left-node"
                style={{ top: `${34 + index() * 74}px` }}
                onClick={() => {
                  setSelectedId(entity.id);
                  setPage(0);
                }}
                title={entity.name}
              >
                <span class="sp-c-node-kind">
                  {entity.kind}
                  <span data-status={entity.status}>{entity.status}</span>
                </span>
                <strong>{entity.name}</strong>
                <small title={entity.path}>{entity.path}</small>
              </button>
            );
          }}
        </For>
        <div
          class="sp-c-node sp-c-selected-node"
          style={{ top: `${(mapHeight() - 64) / 2}px` }}
        >
          <span class="sp-c-node-kind">
            {selected().kind}
            <span data-status={selected().status}>{selected().status}</span>
          </span>
          <strong title={selected().name}>{selected().name}</strong>
          <small title={selected().path}>{selected().path}</small>
        </div>
        <For each={outgoing().slice(page() * 3, page() * 3 + 3)}>
          {(relation, index) => {
            const entity = expect(
              props.fixture.entities.find((item) => item.id === relation.to),
              "Relationship target must exist",
            );
            return (
              <button
                class="sp-c-node sp-c-right-node"
                style={{ top: `${34 + index() * 74}px` }}
                onClick={() => {
                  setSelectedId(entity.id);
                  setPage(0);
                }}
                title={entity.name}
              >
                <span class="sp-c-node-kind">
                  {entity.kind}
                  <span data-status={entity.status}>{entity.status}</span>
                </span>
                <strong>{entity.name}</strong>
                <small title={entity.path}>{entity.path}</small>
              </button>
            );
          }}
        </For>
        <Show when={incoming().length === 0}>
          <div
            class="sp-c-empty-side sp-c-empty-left"
            style={{ top: `${mapHeight() / 2 - 14}px` }}
          >
            No incoming relationships
            <br />
            <small>in this fixture</small>
          </div>
        </Show>
        <Show when={outgoing().length === 0}>
          <div
            class="sp-c-empty-side sp-c-empty-right"
            style={{ top: `${mapHeight() / 2 - 14}px` }}
          >
            No outgoing relationships
            <br />
            <small>in this fixture</small>
          </div>
        </Show>
      </div>
      <Show when={selected()} keyed>
        {(entity) => (
          <article class="sp-c-detail">
            <div class="sp-c-detail-heading">
              <div>
                <span class="sp-c-eyebrow">
                  {entity.kind} · {entity.status}
                </span>
                <h2>{entity.name}</h2>
                <Show when={entity.oldName !== null}>
                  <span class="sp-c-old-name">Previously {entity.oldName}</span>
                </Show>
              </div>
              <button
                class="sp-c-review-button"
                aria-pressed={reviewed().has(entity.id)}
                classList={{ "sp-c-is-reviewed": reviewed().has(entity.id) }}
                onClick={() =>
                  setReviewed((previous) => {
                    const next = new Set(previous);
                    if (next.has(entity.id)) {
                      next.delete(entity.id);
                    } else {
                      next.add(entity.id);
                    }
                    return next;
                  })
                }
              >
                {reviewed().has(entity.id) ? "✓ Reviewed" : "Mark reviewed"}
              </button>
            </div>
            <div class="sp-c-context">
              <p>{entity.summary}</p>
              <Show
                when={entity.oldPath !== null && entity.oldPath !== entity.path}
              >
                <p class="sp-c-origin">
                  Previous file <code>{entity.oldPath}</code>
                </p>
              </Show>
              <Show when={entity.match !== null}>
                <p class="sp-c-match">
                  <span>Matching explanation</span>
                  {entity.match}
                </p>
              </Show>
            </div>
            <PrototypeCode entity={entity} view={props.view} />
          </article>
        )}
      </Show>
      <details class="sp-local-state">
        <summary>Interaction state</summary>
        <pre>
          {JSON.stringify(
            {
              selected: selectedId(),
              query: query(),
              connectionPage: page(),
              connectedOnly: connectedOnly(),
              reviewed: [...reviewed()],
            },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  );
}

/**
 * Throwaway guided queue: fixture groups define the chapter order, while one
 * entity's source remains primary. Reviewed, skipped, selection, and the end
 * screen live only in this mounted variant; no action writes review data.
 */
function PrototypeD(props: PrototypeVariantProps): JSX.Element {
  const queue = createMemo(() =>
    props.fixture.groups.flatMap((group) => group.entityIds),
  );
  const [position, setPosition] = createSignal(0);
  const [reviewed, setReviewed] = createSignal<Set<string>>(new Set());
  const [skipped, setSkipped] = createSignal<Set<string>>(new Set());
  const [atEnd, setAtEnd] = createSignal(false);
  const selected = createMemo(() =>
    expect(
      props.fixture.entities.find(
        (entity) => entity.id === queue()[position()],
      ),
      "Queue entity must exist",
    ),
  );
  const group = createMemo(() =>
    expect(
      props.fixture.groups.find((chapter) =>
        chapter.entityIds.includes(selected().id),
      ),
      "Every queue entity belongs to a fixture group",
    ),
  );
  const related = createMemo(() =>
    props.fixture.relations.filter(
      (relation) =>
        relation.from === selected().id || relation.to === selected().id,
    ),
  );
  const pending = createMemo(() => queue().filter((id) => !reviewed().has(id)));

  /** Advance past visited items; an exhausted pass shows its actual outstanding work. */
  function advance(): void {
    const next = queue().findIndex(
      (id, index) =>
        index > position() && !reviewed().has(id) && !skipped().has(id),
    );
    if (next >= 0) {
      setPosition(next);
    } else {
      const earlier = queue().findIndex(
        (id) => !reviewed().has(id) && !skipped().has(id),
      );
      if (earlier >= 0) {
        setPosition(earlier);
      } else {
        setAtEnd(true);
      }
    }
  }

  return (
    <div class="sp-d">
      <aside class="sp-d-chapters" aria-label="Review chapters">
        <div class="sp-d-rail-heading">
          <span class="sp-d-eyebrow">Guided review</span>
          <h2>Review queue</h2>
          <p>
            {reviewed().size} of {queue().length} reviewed
          </p>
          <progress value={reviewed().size} max={queue().length} />
        </div>
        <div class="sp-d-chapter-list">
          <For each={props.fixture.groups}>
            {(chapter, chapterIndex) => (
              <section
                class="sp-d-chapter"
                classList={{
                  "sp-d-active-chapter": !atEnd() && group().id === chapter.id,
                }}
              >
                <button
                  class="sp-d-chapter-button"
                  onClick={() => {
                    setPosition(
                      queue().indexOf(
                        expect(
                          chapter.entityIds[0],
                          "Chapter requires an entity",
                        ),
                      ),
                    );
                    setAtEnd(false);
                  }}
                >
                  <span class="sp-d-chapter-number">
                    {String(chapterIndex() + 1).padStart(2, "0")}
                  </span>
                  <span>
                    <strong>{chapter.title}</strong>
                    <small>
                      {
                        chapter.entityIds.filter((id) => reviewed().has(id))
                          .length
                      }{" "}
                      / {chapter.entityIds.length} reviewed
                    </small>
                  </span>
                  <span class="sp-d-chapter-chevron">→</span>
                </button>
              </section>
            )}
          </For>
        </div>
        <details class="sp-local-state sp-d-state">
          <summary>Interaction state</summary>
          <pre>
            {JSON.stringify(
              {
                position: position(),
                selected: selected().id,
                reviewed: [...reviewed()],
                skipped: [...skipped()],
                atEnd: atEnd(),
              },
              null,
              2,
            )}
          </pre>
        </details>
      </aside>
      <Show when={!atEnd()}>
        <nav class="sp-d-chapter-items" aria-label="Changes in current chapter">
          <span class="sp-d-steps-label">In this chapter</span>
          <For each={group().entityIds}>
            {(id) => {
              const entity = expect(
                props.fixture.entities.find((item) => item.id === id),
                "Chapter entity must exist",
              );
              return (
                <button
                  class="sp-d-queue-item"
                  classList={{
                    "sp-d-selected": selected().id === id,
                    "sp-d-reviewed": reviewed().has(id),
                  }}
                  onClick={() => {
                    setPosition(queue().indexOf(id));
                    setAtEnd(false);
                  }}
                  title={`${entity.kind} · ${entity.path}`}
                >
                  <span class="sp-d-item-mark">
                    {reviewed().has(id) ? "✓" : skipped().has(id) ? "↷" : "·"}
                  </span>
                  <span class="sp-d-item-name">{entity.name}</span>
                  <span
                    class={`sp-d-status-dot sp-d-status-${entity.status}`}
                    title={entity.status}
                  />
                </button>
              );
            }}
          </For>
        </nav>
      </Show>
      <main class="sp-d-main">
        <Show
          when={!atEnd()}
          fallback={
            <section class="sp-d-end">
              <span class="sp-d-eyebrow">Review queue</span>
              <h1>
                {pending().length === 0
                  ? "Every change reviewed"
                  : "End of this pass"}
              </h1>
              <p>
                {pending().length === 0
                  ? `You marked all ${queue().length} entities reviewed across ${props.fixture.groups.length} chapters.`
                  : `${reviewed().size} entities reviewed. ${pending().length} still need review.`}
              </p>
              <Show when={pending().length > 0}>
                <div class="sp-d-pending-list">
                  <For each={pending()}>
                    {(id) => {
                      const entity = expect(
                        props.fixture.entities.find((item) => item.id === id),
                        "Pending entity must exist",
                      );
                      return (
                        <button
                          onClick={() => {
                            setPosition(queue().indexOf(id));
                            setAtEnd(false);
                          }}
                        >
                          <span>
                            <strong>{entity.name}</strong>
                            <small>{entity.path}</small>
                          </span>
                          <span>
                            {skipped().has(id) ? "Skipped" : "Unreviewed"} →
                          </span>
                        </button>
                      );
                    }}
                  </For>
                </div>
                <button
                  class="sp-d-primary"
                  onClick={() => {
                    setPosition(
                      queue().indexOf(
                        expect(pending()[0], "Resume needs pending work"),
                      ),
                    );
                    setSkipped(new Set<string>());
                    setAtEnd(false);
                  }}
                >
                  Resume unreviewed changes →
                </button>
              </Show>
              <Show when={pending().length === 0}>
                <button
                  onClick={() => {
                    setPosition(0);
                    setAtEnd(false);
                  }}
                >
                  Revisit the first chapter
                </button>
              </Show>
              <p class="sp-d-memory-note">
                Review marks are kept in memory for this prototype.
              </p>
            </section>
          }
        >
          <div class="sp-d-context">
            <div>
              <span class="sp-d-eyebrow">
                Chapter {props.fixture.groups.indexOf(group()) + 1} /{" "}
                {props.fixture.groups.length}
              </span>
              <h2>{group().title}</h2>
              <p>{group().description}</p>
            </div>
            <span class="sp-d-item-count">
              Change {position() + 1} of {queue().length}
            </span>
          </div>
          <div class="sp-d-source-scroll">
            <div class="sp-d-entity-header">
              <div>
                <div class="sp-d-entity-kicker">
                  <span>{selected().kind}</span>
                  <span class={`sp-d-status sp-d-status-${selected().status}`}>
                    {selected().status}
                  </span>
                  <Show when={reviewed().has(selected().id)}>
                    <span class="sp-d-review-mark">✓ Reviewed</span>
                  </Show>
                  <Show
                    when={
                      skipped().has(selected().id) &&
                      !reviewed().has(selected().id)
                    }
                  >
                    <span>Skipped this pass</span>
                  </Show>
                </div>
                <h1>{selected().name}</h1>
                <p>{selected().summary}</p>
              </div>
            </div>
            <Show when={selected().match !== null}>
              <details class="sp-d-pairing">
                <summary>Why these source ranges are paired</summary>
                <p>{selected().match}</p>
              </details>
            </Show>
            <div class="sp-d-code">
              <PrototypeCode entity={selected()} view={props.view} />
            </div>
            <div class="sp-d-related">
              <div class="sp-d-related-heading">
                <h3>Related changes</h3>
                <span>Fixture relationships</span>
              </div>
              <Show
                when={related().length > 0}
                fallback={
                  <p class="sp-d-no-relations">
                    No relationships supplied for this entity. Other changes
                    remain in the chapter index.
                  </p>
                }
              >
                <div class="sp-d-relations">
                  <For each={related()}>
                    {(relation) => {
                      const otherId =
                        relation.from === selected().id
                          ? relation.to
                          : relation.from;
                      const other = expect(
                        props.fixture.entities.find(
                          (entity) => entity.id === otherId,
                        ),
                        "Related entity must exist",
                      );
                      return (
                        <button
                          class="sp-d-related-link"
                          onClick={() => {
                            setPosition(queue().indexOf(otherId));
                            setAtEnd(false);
                          }}
                        >
                          <span class="sp-d-relation-caption">
                            {relation.from === selected().id
                              ? `Outgoing · ${relation.label}`
                              : `Incoming · ${relation.label}`}
                          </span>
                          <strong>{other.name}</strong>
                          <small>{other.path}</small>
                          <span class="sp-d-related-arrow">↗</span>
                        </button>
                      );
                    }}
                  </For>
                </div>
              </Show>
            </div>
          </div>
          <footer class="sp-d-actions">
            <div class="sp-d-step-buttons">
              <button
                disabled={position() === 0}
                onClick={() => setPosition(position() - 1)}
              >
                ← Previous
              </button>
              <button
                onClick={() => {
                  if (position() + 1 < queue().length) {
                    setPosition(position() + 1);
                  } else {
                    setAtEnd(true);
                  }
                }}
              >
                Next →
              </button>
            </div>
            <div class="sp-d-review-actions">
              <Show when={reviewed().has(selected().id)}>
                <button
                  onClick={() =>
                    setReviewed((values) => {
                      const next = new Set(values);
                      next.delete(selected().id);
                      return next;
                    })
                  }
                >
                  Mark unreviewed
                </button>
              </Show>
              <button
                disabled={reviewed().has(selected().id)}
                onClick={() => {
                  setSkipped((values) => new Set([...values, selected().id]));
                  advance();
                }}
              >
                Skip
              </button>
              <button
                class="sp-d-primary"
                onClick={() => {
                  setReviewed((values) => new Set([...values, selected().id]));
                  setSkipped((values) => {
                    const next = new Set(values);
                    next.delete(selected().id);
                    return next;
                  });
                  advance();
                }}
              >
                {reviewed().has(selected().id)
                  ? "Continue review →"
                  : "Mark reviewed & next →"}
              </button>
            </div>
          </footer>
        </Show>
      </main>
    </div>
  );
}
