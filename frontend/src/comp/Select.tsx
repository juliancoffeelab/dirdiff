/**
 * Defines the domain-independent popup Select control.
 *
 * Select stores whether one labelled selection popup is open. It implements
 * dismissal, keyboard escape, option activation, and focus restoration. Callers
 * supply complete option data and interpret selected string values. It does not
 * know about repositories, engines, views, Tabs, or any backend entity.
 */
import {
  For,
  Show,
  createEffect,
  createSignal,
  on,
  onCleanup,
  type JSX,
} from "solid-js";

/**
 * Describes one immutable choice displayed by Select.
 *
 * `value` is returned to the caller and `label` is rendered to the user. The
 * control does not infer one field from the other or attach domain metadata.
 */
export type SelectOption = {
  value: string;
  label: string;
};

/**
 * Defines every input and callback required by Select.
 *
 * Callers provide a selected value, its display label, all options, and explicit
 * hooks for selection. `onOpen` is null when opening has no external consequence,
 * and `optionAction` is null when rows have no secondary action. `disabled` makes
 * the complete trigger unavailable without inventing selectable options; `class`
 * is the complete modifier class supplied by the caller.
 */
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

/**
 * Renders one state-owning labelled popup selection.
 *
 * The caller controls the selected value but never controls popup state. Opening
 * invokes `onOpen`, activation reports a changed value exactly once, and outside
 * pointer or Escape dismissal restores a stable closed control.
 */
export function Select(props: SelectProps): JSX.Element {
  let root!: HTMLDivElement;
  let trigger!: HTMLButtonElement;
  const [open, setOpen] = createSignal(false);

  /**
   * Synchronizes the popup's reactive open lifetime with document listeners.
   *
   * The effect explicitly tracks only `open`. Each false-to-true transition
   * installs one pointer and keyboard listener pair; closing or disposing Select
   * runs the registered cleanup before another pair can be installed. It stores no
   * derived selection state and has no work while the popup is closed.
   */
  createEffect(
    on(open, (isOpen) => {
      if (!isOpen) {
        return;
      }

      /**
       * Closes this popup when a pointer interaction occurs outside its root.
       *
       * The document supplies the event and this handler does not change selection.
       */
      function dismissOutside(event: PointerEvent): void {
        const target = event.target;
        if (target instanceof Node && !root.contains(target)) {
          setOpen(false);
        }
      }

      /**
       * Closes this popup on Escape and restores focus to its trigger.
       *
       * Other keyboard input remains available to native button and list behavior.
       */
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

  /**
   * Selects one exact option value and returns focus to the trigger.
   *
   * Re-selecting the existing value closes the popup without emitting a redundant
   * change. The caller remains responsible for storing the selected value.
   */
  function select(value: string): void {
    setOpen(false);
    if (value !== props.selectedValue) {
      props.onChange(value);
    }
    trigger.focus();
  }

  /**
   * Toggles this control's popup from direct trigger activation.
   *
   * The opening notification runs only for a transition to open. It may ask the
   * caller to warm a canonical backend query without exposing or changing popup state.
   */
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
                  {option.label}
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
