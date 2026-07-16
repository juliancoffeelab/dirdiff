/**
 * Defines the domain-independent state-owning autocomplete input.
 *
 * The module owns live text, edited status, popup visibility, local filtering,
 * highlighted choice, dismissal, and confirmation. Callers supply realtime seed
 * data and choices and receive only edit notifications and completed values. It
 * does not copy live input into caller state or know any dirdiff domain terms.
 */
import {
  For,
  Show,
  createMemo,
  createSignal,
  onCleanup,
  type JSX,
} from "solid-js";

/**
 * Describes one immutable autocomplete choice.
 *
 * `value` is returned on confirmation, `label` is visible, `description` is null
 * when no secondary copy exists, and `group` provides the visible section label.
 */
type AutocompleteChoice = {
  value: string;
  label: string;
  description: string | null;
  group: string;
};

/**
 * Defines the complete public contract of AutocompleteInput.
 *
 * `seed` and `choices` are realtime. A changed seed may fill only an untouched
 * input; choices may change at any time. Prefix, field action, and panel action
 * and edit notification are explicit nullable slots. All interaction state remains
 * private; null notification means no metadata owner exists to warm.
 */
type AutocompleteInputProps = {
  class: string;
  label: string;
  seed: string;
  placeholder: string;
  choices: readonly AutocompleteChoice[];
  inputVisible: boolean;
  inputPrefix: JSX.Element | null;
  fieldAction: JSX.Element | null;
  panelAction: JSX.Element | null;
  onEditNotification: (() => void) | null;
  onDone: (value: string) => void;
};

/**
 * Renders one autocomplete whose user-owned text survives realtime data changes.
 *
 * Focus or pointer activation opens suggestions. Editing permanently protects
 * the current text from later seeds for this mount. Enter confirms the highlighted
 * choice or current text, Escape dismisses, and choice activation preserves focus.
 */
export function AutocompleteInput(props: AutocompleteInputProps): JSX.Element {
  let input!: HTMLInputElement;
  let blurTimer: number | null = null;
  const [editedText, setEditedText] = createSignal<string | null>(null);
  const [query, setQuery] = createSignal("");
  const [open, setOpen] = createSignal(false);
  const [highlighted, setHighlighted] = createSignal<AutocompleteChoice | null>(
    null,
  );
  const value = () => editedText() ?? props.seed;

  const filteredChoices = createMemo(() => {
    const needle = query().trim().toLowerCase();
    if (needle.length === 0) {
      return props.choices;
    }
    return props.choices.filter((choice) =>
      choice.label.toLowerCase().includes(needle),
    );
  });

  const groupedChoices = createMemo(() => {
    const groups: Array<{
      label: string;
      choices: AutocompleteChoice[];
    }> = [];
    for (const choice of filteredChoices()) {
      const existing = groups.find((group) => group.label === choice.group);
      if (existing === undefined) {
        groups.push({ label: choice.group, choices: [choice] });
      } else {
        existing.choices.push(choice);
      }
    }
    return groups;
  });

  onCleanup(() => {
    if (blurTimer !== null) {
      window.clearTimeout(blurTimer);
    }
  });

  /**
   * Cancels delayed blur dismissal while the user interacts with popup content.
   *
   * It owns no focus or value change; the subsequent popup action decides both.
   */
  function keepOpen(): void {
    if (blurTimer !== null) {
      window.clearTimeout(blurTimer);
      blurTimer = null;
    }
  }

  /**
   * Completes current text and schedules dismissal after native blur.
   *
   * Completion is synchronous so a subsequently clicked form action observes the
   * selected value. The short delay still permits popup mouse activation.
   */
  function closeSoon(): void {
    props.onDone(value());
    blurTimer = window.setTimeout(() => {
      setOpen(false);
      setHighlighted(null);
      setQuery("");
      blurTimer = null;
    }, 120);
  }

  /**
   * Marks and stores a direct user edit while keeping suggestions visible.
   *
   * Even an empty value marks the component edited, protecting it from later seed
   * replacement. The caller receives notification only for metadata warmup.
   */
  function edit(next: string): void {
    setEditedText(next);
    setQuery(next);
    setOpen(true);
    setHighlighted(null);
    if (props.onEditNotification !== null) {
      props.onEditNotification();
    }
  }

  /**
   * Confirms one complete value and closes this popup.
   *
   * The confirmed value becomes the component's live text and is returned through
   * `onDone`; callers may retain that meaningful selection but not live edits.
   */
  function finish(next: string): void {
    setEditedText(next);
    setQuery("");
    setOpen(false);
    setHighlighted(null);
    props.onDone(next);
    if (props.inputVisible) {
      input.focus();
    }
  }

  /**
   * Implements the component-owned keyboard interaction for suggestions.
   *
   * Arrow keys move a bounded highlight, Enter confirms a choice or current text,
   * and Escape dismisses without changing or confirming the input.
   */
  function handleKeyDown(event: KeyboardEvent): void {
    const choices = filteredChoices();
    if (event.key === "Escape") {
      setOpen(false);
      setHighlighted(null);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (choices.length === 0) {
        return;
      }
      event.preventDefault();
      const current = highlighted();
      const currentIndex = choices.findIndex((choice) => choice === current);
      const delta = event.key === "ArrowDown" ? 1 : -1;
      const next =
        currentIndex === -1
          ? delta === 1
            ? 0
            : choices.length - 1
          : currentIndex + delta;
      setHighlighted(choices[(next + choices.length) % choices.length]);
      setOpen(true);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const current = highlighted();
      const highlightedChoice = choices.find((choice) => choice === current);
      finish(
        highlightedChoice === undefined ? value() : highlightedChoice.value,
      );
    }
  }

  return (
    <label class={`field autocomplete-host ${props.class}`.trim()}>
      <span>{props.label}</span>
      <div
        class="autocomplete-input-control"
        classList={{
          "autocomplete-input-control-plain": props.inputPrefix === null,
        }}
      >
        <Show when={props.inputPrefix !== null}>{props.inputPrefix}</Show>
        <input
          ref={input}
          hidden={!props.inputVisible}
          value={value()}
          placeholder={props.placeholder}
          spellcheck={false}
          autocomplete="off"
          onFocus={() => {
            setQuery("");
            setOpen(true);
          }}
          onBlur={closeSoon}
          onClick={() => {
            setQuery("");
            setOpen(true);
          }}
          onPointerDown={() => {
            setQuery("");
            setOpen(true);
          }}
          onInput={(event) => edit(event.currentTarget.value)}
          onKeyDown={handleKeyDown}
        />
      </div>
      <Show when={props.fieldAction !== null}>{props.fieldAction}</Show>
      <Show
        when={
          open() && (filteredChoices().length > 0 || props.panelAction !== null)
        }
      >
        <div
          class="autocomplete-panel"
          classList={{
            "autocomplete-panel-has-action": props.panelAction !== null,
          }}
          onMouseDown={keepOpen}
        >
          <Show when={props.panelAction !== null}>
            <div
              class="autocomplete-panel-action"
              onMouseDown={(event) => {
                event.preventDefault();
                keepOpen();
              }}
            >
              {props.panelAction}
            </div>
          </Show>
          <For each={groupedChoices()}>
            {(group) => (
              <div class="autocomplete-section">
                <div class="autocomplete-section-label">{group.label}</div>
                <For each={group.choices}>
                  {(choice) => (
                    <button
                      type="button"
                      class="autocomplete-option"
                      classList={{
                        "is-highlighted": highlighted() === choice,
                      }}
                      onMouseDown={(event) => {
                        event.preventDefault();
                        finish(choice.value);
                      }}
                    >
                      <span class="autocomplete-option-label">
                        {choice.label}
                      </span>
                      <Show when={choice.description !== null}>
                        <span class="autocomplete-option-description">
                          {choice.description}
                        </span>
                      </Show>
                    </button>
                  )}
                </For>
              </div>
            )}
          </For>
        </div>
      </Show>
    </label>
  );
}
