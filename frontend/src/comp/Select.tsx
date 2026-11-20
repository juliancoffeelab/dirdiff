import {
  For,
  Show,
  createEffect,
  createSignal,
  on,
  onCleanup,
  type JSX,
} from "solid-js";

export type SelectOption = {
  value: string;
  label: string;
  description: string | null;
};

type SelectProps = {
  class: string;
  label: string;
  valueLabel: string;
  options: readonly SelectOption[];
  selectedValue: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onOpen: (() => void) | null;
  optionAction: ((option: SelectOption) => JSX.Element) | null;
};

export function Select(props: SelectProps): JSX.Element {
  let root!: HTMLDivElement;
  let trigger!: HTMLButtonElement;
  const [open, setOpen] = createSignal(false);

  createEffect(
    on(open, (isOpen) => {
      if (!isOpen) {
        return;
      }

      function dismissOutside(event: PointerEvent): void {
        const target = event.target;
        if (target instanceof Node && !root.contains(target)) {
          setOpen(false);
        }
      }

      function dismissWithKeyboard(event: KeyboardEvent): void {
        if (event.key === "Escape") {
          setOpen(false);
          trigger.focus();
        }
      }

      document.addEventListener("pointerdown", dismissOutside);
      document.addEventListener("keydown", dismissWithKeyboard);
      onCleanup(() => {
        document.removeEventListener("pointerdown", dismissOutside);
        document.removeEventListener("keydown", dismissWithKeyboard);
      });
    }),
  );

  function select(value: string): void {
    setOpen(false);
    if (value !== props.selectedValue) {
      props.onChange(value);
    }
    trigger.focus();
  }

  function toggle(): void {
    const next = !open();
    setOpen(next);
    if (next && props.onOpen !== null) {
      props.onOpen();
    }
  }

  return (
    <div
      ref={root}
      class={`ui-select ${props.class}`.trim()}
      data-open={open() ? "true" : "false"}
    >
      <button
        ref={trigger}
        type="button"
        class="ui-select-trigger"
        aria-haspopup="listbox"
        aria-expanded={open() ? "true" : "false"}
        disabled={props.disabled}
        onClick={toggle}
      >
        <span class="ui-select-label">{props.label}</span>
        <span class="ui-select-value">{props.valueLabel}</span>
      </button>
      <Show when={open()}>
        <div class="ui-select-menu" role="listbox" aria-label={props.label}>
          <For each={props.options}>
            {(option) => (
              <div
                classList={{
                  "ui-select-option-row": true,
                  "ui-select-option-row-plain": props.optionAction === null,
                }}
              >
                <button
                  type="button"
                  class="ui-select-option"
                  data-selected={
                    option.value === props.selectedValue ? "true" : "false"
                  }
                  role="option"
                  aria-selected={option.value === props.selectedValue}
                  onClick={() => select(option.value)}
                >
                  <span class="ui-select-option-label">{option.label}</span>
                  <Show when={option.description !== null}>
                    <span class="ui-select-option-desc">
                      {option.description}
                    </span>
                  </Show>
                </button>
                <Show when={props.optionAction !== null}>
                  {props.optionAction?.(option)}
                </Show>
              </div>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}
