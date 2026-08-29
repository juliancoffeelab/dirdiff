/**
 * Defines the domain-independent state-owning autocomplete input.
 *
 * AutocompleteInput stores live text, edited status, popup visibility, and the
 * highlighted choice. It implements local filtering, dismissal, and confirmation.
 * Callers supply realtime seed data and choices and receive only edit notifications
 * and completed values. It does not copy live input into caller state or know any
 * dirdiff domain terms.
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
 * Each choice has one stable submitted value and the text needed to place it in
 * the suggestions popup.
 */
type AutocompleteChoice = {
  /**
   * Stable value submitted when the user confirms this choice.
   *
   * The value may differ from the visible label, as it does for structured Git
   * refs. Callers must not depend on it being unique across different groups.
   */
  value: string;
  /**
   * Primary text rendered for the choice and searched case-insensitively.
   *
   * Filtering uses only this label. Hidden domain identifiers belong in `value`,
   * not in special behavior inside AutocompleteInput.
   */
  label: string;
  /**
   * Secondary explanation rendered below the label.
   *
   * `null` omits the description element; an empty string is still explicit
   * visible content and should not be used as a substitute for absence.
   */
  description: string | null;
  /**
   * User-visible section label used to group suggestions.
   *
   * Choices sharing the exact string appear together in first-seen group order,
   * while their order inside the group remains the caller's order.
   */
  group: string;
};

/**
 * Defines the complete public contract of AutocompleteInput.
 *
 * `seed` and `choices` are realtime. A changed seed may fill only an untouched
 * input; choices may change at any time. Prefix, field action, panel action, and
 * edit notification are explicit nullable slots. All interaction state remains
 * private; null notification means this caller needs no edit notification.
 */
type AutocompleteInputProps = {
  /**
   * Complete modifier class appended to the field root.
   *
   * The base field and autocomplete classes are always present. An empty string
   * requests no caller-specific modifier.
   */
  class: string;
  /**
   * User-visible caption for the input.
   *
   * It is rendered inside the enclosing label and must name the value the caller
   * expects from `onDone`.
   */
  label: string;
  /**
   * Realtime text displayed until the user directly edits this mounted input.
   *
   * Later seed changes replace untouched text but never overwrite local edited
   * text. Remounting starts a new untouched lifetime.
   */
  seed: string;
  /**
   * Hint rendered by the native input when its current text is empty.
   *
   * It is presentation only and is never submitted through `onDone`.
   */
  placeholder: string;
  /**
   * Realtime immutable suggestions searched by their visible labels.
   *
   * The component reads the latest array whenever filtering runs and stores no
   * copy. Replacing choices does not replace the current text.
   */
  choices: readonly AutocompleteChoice[];
  /**
   * Whether the editable input is present.
   *
   * A false value supports controls whose prefix performs the current mode's
   * interaction. Confirming a popup choice then does not attempt to focus the
   * absent input.
   */
  inputVisible: boolean;
  /**
   * Caller element rendered before the editable input.
   *
   * `null` selects the plain-input layout. The component renders the supplied
   * element unchanged and assigns it no completion behavior.
   */
  inputPrefix: JSX.Element | null;
  /**
   * Caller action rendered beside the field caption.
   *
   * AutocompleteInput does not invoke or disable it. `null` leaves that slot
   * absent instead of rendering an empty action container.
   */
  fieldAction: JSX.Element | null;
  /**
   * Caller action rendered after the grouped suggestions.
   *
   * Pointer interaction with popup content cancels delayed blur dismissal so the
   * supplied action may run before the panel closes. `null` omits the slot.
   */
  panelAction: JSX.Element | null;
  /**
   * Reports each direct text edit before any later completion.
   *
   * The callback receives no value because callers use it only to warm realtime
   * metadata; completed text arrives through `onDone`. It does not run for seed
   * changes or choice activation, and `null` disables the notification.
   */
  onEditNotification: (() => void) | null;
  /**
   * Accepts the complete value when the user confirms a choice, presses Enter,
   * or leaves the input.
   *
   * Choice confirmation passes the exact choice value; free-form completion
   * passes current text. Choice or Enter confirmation first stores the accepted
   * text and closes the popup, then invokes the callback before restoring input
   * focus. Blur invokes it synchronously before scheduling delayed dismissal, so
   * a subsequently clicked caller action can observe accepted state. The input
   * owns its accepted text for this mount; the caller need not feed the value
   * back through `seed`.
   */
  onDone: (value: string) => void;
};

/**
 * Renders one autocomplete whose user input survives realtime data changes.
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
  /**
   * Returns the text currently presented and confirmed by this component.
   *
   * Before the first user edit, consumers receive the latest realtime seed.
   * Afterwards they receive local edited text, even if the caller changes seed.
   */
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
      /**
       * Exact group label rendered once above these choices.
       *
       * Its first occurrence fixes this group's position for the current filter.
       */
      label: string;
      /**
       * Matching choices collected for this label in caller-supplied order.
       *
       * The array is built fresh inside the memo and never escapes as state.
       */
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

  // Blur dismissal may still be waiting when a caller removes the field. Clear
  // that one component-local timer so disposed input state is never updated.
  onCleanup(() => {
    if (blurTimer !== null) {
      window.clearTimeout(blurTimer);
    }
  });

  /**
   * Cancels delayed blur dismissal while the user interacts with popup content.
   *
   * It changes neither focus nor value; the subsequent popup action decides both.
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
   * Implements the component-local keyboard interaction for suggestions.
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
