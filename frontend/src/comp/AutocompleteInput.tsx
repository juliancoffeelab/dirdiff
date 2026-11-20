import {
  For,
  Show,
  createMemo,
  createSignal,
  onCleanup,
  type JSX,
} from "solid-js";

type AutocompleteChoice = {
  value: string;
  label: string;
  description: string | null;
  group: string;
};

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

  // Blur dismissal may still be waiting when a caller removes the field. Clear
  // that one component-local timer so disposed input state is never updated.
  onCleanup(() => {
    if (blurTimer !== null) {
      window.clearTimeout(blurTimer);
    }
  });

  function keepOpen(): void {
    if (blurTimer !== null) {
      window.clearTimeout(blurTimer);
      blurTimer = null;
    }
  }

  function closeSoon(): void {
    props.onDone(value());
    blurTimer = window.setTimeout(() => {
      setOpen(false);
      setHighlighted(null);
      setQuery("");
      blurTimer = null;
    }, 120);
  }

  function edit(next: string): void {
    setEditedText(next);
    setQuery(next);
    setOpen(true);
    setHighlighted(null);
    if (props.onEditNotification !== null) {
      props.onEditNotification();
    }
  }

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
