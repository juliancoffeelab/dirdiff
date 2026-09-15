/**
 * Defines the domain-independent controlled Checkbox control.
 *
 * Checkbox renders one labelled row whose native check state only ever mirrors
 * the caller's `checked` value. A click reports the requested next value through
 * `onToggle` and leaves the native state untouched until the caller changes
 * `checked`. It owns no state, and knows nothing about preferences, profiles,
 * engines, or any backend entity.
 */
import type { JSX } from "solid-js";

/**
 * Defines every input and callback required by Checkbox.
 *
 * The caller stores the checked value and decides when it changes. Checkbox
 * never stores a value of its own.
 */
type CheckboxProps = {
  /**
   * Complete modifier class appended to the control root.
   *
   * The base `ui-checkbox` class is always present. An empty string requests no
   * caller-specific modifier.
   */
  class: string;
  /** Bold title rendered beside the control. */
  label: string;
  /** Secondary explanation rendered under the title. */
  description: JSX.Element;
  /** Complete controlled value mirrored by the native input and indicator. */
  checked: boolean;
  /**
   * Whether a click must be ignored right now.
   *
   * The input stays focusable and reports `aria-disabled`, so keyboard focus
   * survives a pending save. A busy click changes nothing and reports nothing.
   */
  busy: boolean;
  /** Receives the value the user requested, which is the opposite of `checked`. */
  onToggle: (next: boolean) => void;
};

/**
 * Renders one controlled checkbox row.
 *
 * Callers pass the complete current value and receive toggle requests. The
 * native input follows `checked` alone, so a request that never becomes a new
 * `checked` value leaves the control unchanged.
 */
export function Checkbox(props: CheckboxProps): JSX.Element {
  return (
    <label class={`ui-checkbox ${props.class}`}>
      <span class="ui-checkbox-copy">
        <strong>{props.label}</strong>
        <span>{props.description}</span>
      </span>
      <span class="ui-checkbox-control">
        <input
          class="ui-checkbox-input"
          type="checkbox"
          checked={props.checked}
          aria-disabled={props.busy}
          onClick={(event) => {
            // The browser flipped the native check before dispatch. Write the
            // controlled value back instead of canceling the event: a canceled
            // click restores the old state after dispatch, which would undo a
            // Solid write that lands inside the same dispatch when the caller
            // changes `checked` from a microtask.
            event.currentTarget.checked = props.checked;
            if (props.busy) {
              return;
            }
            props.onToggle(!props.checked);
          }}
        />
        <span
          class="visibility-indicator large ui-checkbox-indicator"
          classList={{ visible: props.checked }}
          aria-hidden="true"
        />
      </span>
    </label>
  );
}
