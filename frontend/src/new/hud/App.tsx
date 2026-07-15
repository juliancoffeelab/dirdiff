/**
 * Defines the top-level application component.
 *
 * The module composes the persistent application header with the active main
 * surface. It is the root of visible application UI, but it does not own the
 * providers mounted above it or low-level component behavior below it.
 */
import { AppHeader } from "./AppHeader";

/**
 * Renders the complete application shell.
 *
 * The caller supplies `onSwitchFrontend` for the AppHeader brand action. The
 * component owns only top-level composition and forwards that callback without
 * invoking it independently.
 */
export function App(props: { onSwitchFrontend: () => void }) {
  return (
    <main class="app-shell">
      <AppHeader onSwitchFrontend={props.onSwitchFrontend} />
      <section class="rewrite-under-construction">
        The rewritten frontend is under construction.
      </section>
    </main>
  );
}
