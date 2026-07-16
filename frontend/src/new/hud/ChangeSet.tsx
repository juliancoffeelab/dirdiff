/**
 * Defines the stable owner boundary for one selected ChangeSet.
 *
 * The module exports the component lifetime that Tabs mount from complete
 * DiffParams. This public boundary owns identity and lifetime while keeping
 * manifest, file, rendering, expansion, and navigation details private. It does
 * not let Tabs reach into or mutate any ChangeSet-owned implementation state.
 */
import type { JSX } from "solid-js";
import type { DiffParams } from "../api/api";
import type { DiffViewMode } from "./App";
import type { AppHeaderOutlets } from "./AppHeader";

/**
 * Defines the complete inputs needed to identify and activate one ChangeSet.
 *
 * `params` is always a complete selected backend input, `view` is the reactive
 * global presentation, and `active` controls the expensive inner lifetime. No
 * field may represent live control input.
 */
type ChangeSetProps = {
  active: boolean;
  params: DiffParams;
  view: DiffViewMode;
  appHeaderOutlets: AppHeaderOutlets;
};

/**
 * Establishes one stable ChangeSet lifetime for a Tab's selected parameters.
 *
 * Callers mount it only after selection is complete and keep it mounted across
 * ordinary Tab switches and view changes. Reading `view` reactively updates only
 * dependent representation DOM; it does not replace this ChangeSet owner. The
 * public component adds no wrapper DOM and exposes no internal operations.
 */
export function ChangeSet(props: ChangeSetProps): JSX.Element {
  // Reading both required inputs keeps this boundary honest while it has no
  // active content: later implementation replaces this with the private owner.
  void props.active;
  void props.params;
  void props.view;
  void props.appHeaderOutlets;
  return <></>;
}
