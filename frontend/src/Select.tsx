import {
  For,
  Show,
  createEffect,
  createSignal,
  onCleanup,
  type JSX,
} from "solid-js";

export type SelectOption = {
  value: string;
  label: string;
};

export function Select(props: {
  class?: string;
  label: string;
  valueLabel: string;
  options: readonly SelectOption[];
  selectedValue: string;
  onChange: (value: string) => void;
  onOpen?: () => void;
  optionAction?: (option: SelectOption) => JSX.Element;
}) {
  let root: HTMLDivElement | undefined;
  let trigger: HTMLButtonElement | undefined;
  const [open, setOpen] = createSignal(false);

  createEffect(() => {
    if (!open()) {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (root !== undefined) {
        if (root.contains(target)) {
          return;
        }
      }
      setOpen(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") {
        return;
      }
      setOpen(false);
      trigger?.focus();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);

    onCleanup(() => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    });
  });

  const select = (value: string) => {
    setOpen(false);
    if (value !== props.selectedValue) {
      props.onChange(value);
    }
    trigger?.focus();
  };

  const toggleOpen = () => {
    const nextOpen = !open();
    setOpen(nextOpen);
    if (nextOpen) {
      if (props.onOpen !== undefined) {
        props.onOpen();
      }
    }
  };

  return (
    <div
      ref={root}
      class={`ui-select ${props.class ?? ""}`.trim()}
      data-open={open() ? "true" : "false"}
    >
      <button
        ref={trigger}
        type="button"
        class="ui-select-trigger"
        aria-haspopup="listbox"
        aria-expanded={open() ? "true" : "false"}
        onClick={toggleOpen}
      >
        <span class="ui-select-label">{props.label}</span>
        <span class="ui-select-value">{props.valueLabel}</span>
      </button>
      <Show when={open()}>
        <div class="ui-select-menu" role="listbox" aria-label={props.label}>
          <For each={props.options}>
            {(option) => (
              <div
                class={
                  props.optionAction === undefined
                    ? "ui-select-option-row ui-select-option-row-plain"
                    : "ui-select-option-row"
                }
              >
                <button
                  type="button"
                  class="ui-select-option"
                  data-selected={
                    option.value === props.selectedValue ? "true" : "false"
                  }
                  role="option"
                  aria-selected={
                    option.value === props.selectedValue ? "true" : "false"
                  }
                  onClick={() => select(option.value)}
                >
                  {option.label}
                </button>
                <Show when={props.optionAction !== undefined}>
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
