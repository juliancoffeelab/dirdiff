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
 * The control reports the stable machine value and presents the matching user
 * label without attaching domain metadata.
 */
export type SelectOption = {
  /**
   * Stable value passed unchanged to `onChange` and `optionAction`.
   *
   * Values must be unique within one Select. The component compares them with
   * `selectedValue` to mark selection and suppress a redundant change callback.
   */
  value: string;
  /**
   * User-visible text rendered by the option button.
   *
   * Select does not derive this label from the value. Callers may also use it
   * when labelling the optional action beside the same option.
   */
  label: string;
};

/**
 * Defines every input and callback required by Select.
 *
 * Callers provide the complete controlled selection and available choices.
 * Select keeps only the popup interaction state.
 */
type SelectProps = {
  /**
   * Complete modifier class appended to the control root.
   *
   * The base `ui-select` class is always present. An empty string requests no
   * caller-specific modifier.
   */
  class: string;
  /**
   * Caption identifying the setting controlled by this Select.
   *
   * It is rendered in the trigger and also labels the popup list for assistive
   * technology.
   */
  label: string;
  /**
   * User-visible text for the current controlled selection.
   *
   * Callers provide it separately because a temporarily unavailable or empty
   * selection may have no matching entry in `options`.
   */
  valueLabel: string;
  /**
   * Complete immutable choices rendered in display order each time the popup opens.
   *
   * Select stores no copy. Callers must keep each value unique and provide an
   * entry matching `selectedValue` whenever that value represents a real option.
   */
  options: readonly SelectOption[];
  /**
   * Caller-controlled value used to mark one option as selected.
   *
   * Activating that same value closes the popup without calling `onChange`.
   * Select never updates this prop itself after a different value is accepted.
   */
  selectedValue: string;
  /**
   * Whether the native trigger rejects pointer and keyboard activation.
   *
   * A disabled Select cannot open, so neither `onOpen` nor `onChange` runs.
   * Callers still provide the visible label explaining the unavailable state.
   */
  disabled: boolean;
  /**
   * Handles activation of an option different from `selectedValue`.
   *
   * `value` is the activated option's exact `SelectOption.value`. The callback
   * may update caller state, navigate, or perform another domain action. If
   * this `Select` remains mounted and should reflect the choice, pass the
   * accepted value back as `selectedValue`.
   *
   * After the callback completes, `Select` closes its options popup and
   * focuses its trigger button.
   */
  onChange: (value: string) => void;
  /**
   * Handles a direct trigger transition from closed to open.
   *
   * The callback runs after local popup state changes and may warm caller data.
   * It does not run when Select closes, when an already-open popup is dismissed,
   * or when the prop is `null`.
   */
  onOpen: (() => void) | null;
  /**
   * Renders a secondary action beside each option while the popup is rendered.
   *
   * The callback receives that row's complete immutable option and must return
   * the element for that option only. Select does not treat activating the
   * returned element as selection. `null` renders plain option rows.
   */
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
   * Re-selecting the existing value closes the popup without emitting a
   * redundant change.
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
