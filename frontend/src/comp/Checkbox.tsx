import type { JSX } from "solid-js";

type CheckboxProps = {
  class: string;
  label: string;
  description: JSX.Element;
  checked: boolean;
  busy: boolean;
  onToggle: (next: boolean) => void;
};

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
