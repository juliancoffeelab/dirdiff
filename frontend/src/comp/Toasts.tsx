/**
 * Presents application notifications and contained component failures.
 *
 * `ToastProvider` keeps the one application queue and installs the browser error
 * listeners for failures that have no smaller valid boundary. Local error panels
 * preserve the caller's original failure and invoke only the retry operation the
 * caller supplies. `presentError` gives every thrown value a renderable form.
 *
 * This module does not turn failures into domain state, choose a recovery action,
 * or clear the state that caused a local panel to remain mounted.
 */
import {
  ErrorBoundary,
  For,
  Show,
  createContext,
  createSignal,
  createUniqueId,
  onCleanup,
  onMount,
  useContext,
  type Accessor,
  type JSX,
} from "solid-js";
import { assert, expect } from "../utils";

/**
 * Lifetime of a timeout error Toast before its card requests dismissal.
 *
 * Timeout failures are the only error Toasts that expire. `ToastCard` owns the
 * timer for its mounted lifetime and clears it if the card leaves first.
 */
const TIMEOUT_TOAST_TTL_MS = 10_000;

/**
 * Stable text shown when an arbitrary thrown value cannot be inspected safely.
 *
 * Error formatting uses this instead of throwing a second error or inventing a
 * representation for cyclic, proxied, or otherwise hostile values.
 */
const UNDISPLAYABLE_THROWN_VALUE_MESSAGE =
  "Unable to display the thrown value.";

/**
 * Represents one immutable error notification owned by ToastProvider.
 *
 * Producers supply a title and thrown value through ToastCommands; the provider
 * assigns the identifier and formatted fields. Consumers may render every field
 * but must not manufacture records or use them for non-error notifications.
 */
export type ErrorToast = {
  /**
   * Monotonic identity assigned by the provider that created this Toast.
   *
   * Rendering uses it as the list key and dismissal uses it to remove only this
   * record. Producers never choose or reuse an ID.
   */
  id: number;
  /**
   * Short context supplied by the code reporting the failure.
   *
   * The viewport renders it separately from the formatted thrown value, so it
   * should name the failed operation rather than repeat the error message.
   */
  title: string;
  /**
   * Primary display-safe text produced by `presentError`.
   *
   * It is always renderable, even when the original thrown value was not an
   * `Error`, and remains distinct from the caller's title.
   */
  message: string;
  /**
   * Stack text that adds information beyond `message`.
   *
   * `null` means the viewport must omit the details control. It does not mean
   * formatting failed.
   */
  details: string | null;
  /**
   * Error lifetime selected from the thrown value's transport classification.
   *
   * `timeout` starts the fixed expiration timer. `other` remains visible until
   * the user dismisses it.
   */
  reason: "timeout" | "other";
};

/**
 * Represents one short non-error notification owned by ToastProvider.
 *
 * Producers provide complete visible text and a positive lifetime. The provider
 * assigns the identifier. Notices carry no Error, stack, retry operation, or
 * transport classification and must not be used to hide an application failure.
 */
export type TransientToast = {
  /**
   * Monotonic identity assigned by the provider that created this Toast.
   *
   * It has the same exact-dismissal role as an error Toast ID; callers never
   * supply it through `showTransient`.
   */
  id: number;
  /**
   * Short caller-supplied context rendered above the notice.
   *
   * The provider requires visible text and does not derive this from `message`.
   */
  title: string;
  /**
   * Complete user-visible informational text.
   *
   * This is not error prose and carries no hidden thrown value or retry action.
   */
  message: string;
  /**
   * Fixed absence of error stack details.
   *
   * The explicit `null` keeps Toast rendering uniform without pretending an
   * informational notice may have a traceback.
   */
  details: null;
  /**
   * Discriminant selecting informational presentation and caller-chosen expiry.
   *
   * It must remain `transient`; transport error classifications belong to
   * `ErrorToast`.
   */
  reason: "transient";
  /**
   * Positive finite display lifetime chosen by the producer, in milliseconds.
   *
   * `ToastCard` samples it at mount and requests dismissal when it elapses. The
   * provider rejects zero, negative, or non-finite durations.
   */
  durationMs: number;
};

/**
 * Describes every notification rendered by the global Toast viewport.
 *
 * Error notifications retain their existing persistent or transport-timeout
 * lifetime. Transient notices are explicit non-error information with a required
 * caller-supplied duration; neither variant may be reinterpreted as the other.
 */
export type Toast = ErrorToast | TransientToast;

/**
 * Contains the display-safe representation of one arbitrary thrown value.
 *
 * It contains the safe copy needed to present a failure and choose its Toast
 * lifetime. It carries no retry action, notification identity, or provider state.
 */
export type PresentedError = {
  /**
   * Primary text safe to render for every thrown JavaScript value.
   *
   * JSON-shaped messages may be formatted for readability, while ordinary text
   * remains verbatim. Formatting failure produces the stable undisplayable text.
   */
  message: string;
  /**
   * Distinct stack text retained from an `Error`, if it adds information.
   *
   * `null` covers non-Errors, missing or empty stacks, and stacks identical to
   * the primary message.
   */
  details: string | null;
  /**
   * Stable lifetime classification consumed by Toast presentation.
   *
   * Only an exact structural `error_reason: "timeout"` becomes `timeout`; every
   * other thrown value is `other`.
   */
  reason: "timeout" | "other";
};

/**
 * Defines the command-only notification interface exposed by ToastProvider.
 *
 * Callers may provide a complete error title and thrown value or complete text
 * plus a positive duration for a transient notice. They cannot inspect, dismiss,
 * or mutate the provider-owned queue through this contract.
 */
export type ToastCommands = {
  /**
   * Presents one arbitrary thrown value as a global error Toast.
   *
   * `title` names the failed operation and `error` is passed unchanged to
   * `presentError`. The provider appends the resulting Toast synchronously.
   * Timeout-classified failures expire; every other failure waits for explicit
   * dismissal. Callers keep retry and local error state outside this command.
   *
   * @param title Short context for the failed operation.
   * @param error Original thrown value to format and present.
   */
  showError(title: string, error: unknown): void;
  /**
   * Presents complete non-error text for an explicit finite lifetime.
   *
   * `title` and `message` must both be non-empty. `durationMs` must be positive
   * and finite. The provider appends the notice synchronously and its mounted
   * card later requests dismissal; callers must use `showError` for failures.
   *
   * @param title Short context for the notice.
   * @param message Complete user-visible informational text.
   * @param durationMs Positive finite display lifetime in milliseconds.
   */
  showTransient(title: string, message: string, durationMs: number): void;
};

/**
 * Defines the complete compact-trigger error popover contract.
 *
 * Callers provide the visible trigger, its classes and accessible label, the
 * original error, and an explicit retry operation. The popover owns only native
 * top-layer presentation and never changes caller-owned failure state.
 */
export type ErrorPopoverProps = {
  /**
   * Context rendered above the formatted failure inside the popover.
   *
   * It should name the failed local operation and remains separate from the
   * compact trigger's accessible label.
   */
  title: string;
  /**
   * Original thrown value formatted whenever the popover panel renders.
   *
   * The component stores no copy and does not report the value to the global
   * Toast queue.
   */
  error: unknown;
  /**
   * Performs the caller's explicit recovery attempt.
   *
   * The callback runs only when the user activates the Retry button inside the
   * open popover. ErrorPopover neither closes the popover nor clears caller state
   * on its behalf; the caller's accepted state controls what remains mounted.
   */
  onRetry: () => void;
  /**
   * Compact caller-chosen content rendered unchanged inside the trigger button.
   *
   * It may be an icon or short status. Accessible naming comes from
   * `triggerLabel`, not from assumptions about this element.
   */
  trigger: JSX.Element;
  /**
   * Complete class string applied to the popover trigger button.
   *
   * The caller uses it to fit the compact failure control into local geometry;
   * the component adds no substitute modifier class.
   */
  triggerClass: string;
  /**
   * Accessible name and native hover text for the compact trigger.
   *
   * It must explain that activating the trigger reveals failure details; the
   * visible `trigger` content need not contain equivalent text.
   */
  triggerLabel: string;
};

/**
 * Defines the private rendering inputs required by ToastViewport.
 *
 * The provider supplies a read-only queue accessor and exact-ID dismissal
 * operation. This contract must not expose the queue setter or ID allocation.
 */
type ToastViewportProps = {
  /**
   * Read-only accessor for the provider's live Toast queue.
   *
   * The viewport calls it reactively and renders records in array order. It must
   * return the authoritative queue rather than a copied local store.
   */
  toasts: Accessor<Toast[]>;
  /**
   * Handles dismissal of one exact provider-assigned Toast identity.
   *
   * `id` comes from the rendered record. The callback may safely receive the
   * same ID more than once and must leave every other Toast unchanged.
   */
  onDismiss(id: number): void;
};

/**
 * Describes an arbitrary object whose transport contract may classify an error.
 *
 * The field remains unknown until the formatter compares it with the supported
 * timeout value. This structural view must not imply that every error has a
 * reason or that other reason values receive special behavior.
 */
type ErrorWithReason = {
  /**
   * Unvalidated value exposed under the transport error classification name.
   *
   * Callers may compare it with the exact supported timeout string only. Its
   * presence does not establish a broader error interface.
   */
  error_reason: unknown;
};

/**
 * Narrows an arbitrary object to one exposing transport classification data.
 *
 * The check proves property presence only. Callers must still compare the unknown
 * value with the exact supported reason before changing presentation lifetime.
 */
function isErrorWithReason(error: object): error is ErrorWithReason {
  return "error_reason" in error;
}

/**
 * Describes an arbitrary object that may expose validation issues.
 *
 * The field remains unknown until runtime validation proves it is an array.
 * This structural view must not be used as a complete validation-error model.
 */
type ErrorWithIssues = {
  /**
   * Unvalidated value exposed under the validation issue collection name.
   *
   * Formatting gives it issue precedence only after `Array.isArray` succeeds;
   * no element shape is assumed.
   */
  issues: unknown;
};

/**
 * Narrows an arbitrary object to one exposing possible validation issues.
 *
 * The check proves property presence only. Callers separately require an array
 * before giving issue formatting precedence.
 */
function isErrorWithIssues(error: object): error is ErrorWithIssues {
  return "issues" in error;
}

/**
 * Represents the complete result of attempting to parse one JSON document.
 *
 * The discriminant separates parse failure from every valid JSON value,
 * including `null`. Callers may read `value` only from the successful variant.
 */
type ParsedJson =
  | {
      /**
       * Marks text that was not a complete JSON document.
       *
       * This arm deliberately has no value field, so parse failure cannot be
       * confused with successfully parsing JSON `null`.
       */
      parsed: false;
    }
  | {
      /**
       * Marks a successful parse of the complete input text.
       *
       * The true arm remains distinct even when `value` is `null`.
       */
      parsed: true;
      /**
       * Exact value returned by `JSON.parse` for the input text.
       *
       * Callers may read it only from the successful discriminant arm and pass
       * it to display-safe serialization without narrowing its JSON shape.
       */
      value: unknown;
    };

/**
 * Command-only notification channel provided at application lifetime.
 *
 * Consumers obtain it through `useToasts`. An absent value is a broken provider
 * invariant, not a state in which failures may be silently ignored.
 */
const ToastContext = createContext<ToastCommands>();

/**
 * Converts an arbitrary thrown value into complete display-safe error data.
 *
 * Callers may supply any JavaScript value, including cyclic objects, proxies,
 * and malformed Error-like values. The function returns a primary message,
 * optional distinct Error stack, and expiration reason without propagating a
 * formatting failure.
 */
export function presentError(error: unknown): PresentedError {
  /**
   * Classifies the lifetime policy encoded by a transport error.
   *
   * Only the structural `error_reason: "timeout"` contract expires; every
   * other value remains persistent.
   *
   * # Returns
   *
   * - `"timeout"`: The value carries the recognized transport timeout marker,
   *   so the caller gives its Toast an expiration.
   * - `"other"`: Every other thrown value, for persistent presentation.
   */
  function errorReason(error: unknown): "timeout" | "other" {
    if (!isObject(error) || !isErrorWithReason(error)) {
      return "other";
    }

    return error.error_reason === "timeout" ? "timeout" : "other";
  }

  try {
    const message = primaryMessage(error);
    return {
      message,
      details: errorDetails(error, message),
      reason: errorReason(error),
    };
  } catch {
    return {
      message: UNDISPLAYABLE_THROWN_VALUE_MESSAGE,
      details: null,
      reason: "other",
    };
  }
}

/**
 * Establishes the single notification queue for an application subtree.
 *
 * Callers provide the complete subtree as `children` and mount one provider at
 * the application root. Descendants receive command-only access through
 * `useToasts`; queue contents, identifiers, dismissal, timers, and browser
 * listeners remain private to this boundary. The provider also renders the
 * viewport so notifications survive failures inside application boundaries.
 */
export function ToastProvider(props: {
  /**
   * Application subtree receiving this provider's command channel.
   *
   * The provider renders the subtree before its Toast viewport, so contained
   * failures cannot replace notification presentation. Mount one provider around
   * the application rather than nesting independent queues.
   */
  children: JSX.Element;
}): JSX.Element {
  const [toasts, setToasts] = createSignal<Toast[]>([]);
  let nextToastId = 1;

  /**
   * Appends one formatted failure to this provider's queue.
   *
   * Internal producers supply the user-visible title and original failure. The
   * function assigns the next monotonic ID and preserves every existing Toast;
   * expiration and dismissal remain responsibilities of rendered Toast cards.
   *
   * @param title Short context for the failed operation.
   * @param error Original thrown value to format and retain as a failure.
   */
  function showError(title: string, error: unknown): void {
    const presented = presentError(error);
    const toast: ErrorToast = {
      id: nextToastId,
      title,
      ...presented,
    };
    nextToastId += 1;
    setToasts((current) => [...current, toast]);
  }

  /**
   * Appends one explicitly temporary non-error notice to this provider's queue.
   *
   * Internal producers must provide non-empty visible text and a positive finite
   * duration. The notice owns no failure data and expires after exactly that
   * duration unless the user dismisses it first.
   *
   * @param title Short context for the notice.
   * @param message Complete user-visible notice text.
   * @param durationMs Positive finite display lifetime in milliseconds.
   */
  function showTransient(
    title: string,
    message: string,
    durationMs: number,
  ): void {
    assert(
      title.length > 0 && message.length > 0,
      "Transient Toasts require visible title and message text.",
    );
    assert(
      Number.isFinite(durationMs) && durationMs > 0,
      "Transient Toasts require a positive finite duration.",
    );
    const toast: TransientToast = {
      id: nextToastId,
      title,
      message,
      details: null,
      reason: "transient",
      durationMs,
    };
    nextToastId += 1;
    setToasts((current) => [...current, toast]);
  }

  /**
   * Presents one browser ErrorEvent through the global queue.
   *
   * The browser supplies the event. The handler prefers its thrown value and
   * uses the browser message when no value exists; it deliberately leaves the
   * event's default console reporting untouched.
   */
  function onWindowError(event: ErrorEvent): void {
    // Runtime errors provide the thrown value and its stack, while resource and
    // cross-origin failures may provide only the browser's message.
    const error = event.error == null ? event.message : event.error;
    showError("Unexpected error", error);
  }

  /**
   * Presents one browser-level unhandled Promise rejection.
   *
   * The browser supplies the rejection event after ordinary query, mutation, and
   * component paths had the opportunity to handle it. The handler reports the
   * original reason without preventing native diagnostics.
   */
  function onUnhandledRejection(event: PromiseRejectionEvent): void {
    showError("Unhandled promise rejection", event.reason);
  }

  /**
   * Installs browser-level error reporting for exactly this provider lifetime.
   *
   * The non-tracking mount hook runs once after ToastProvider renders. Provider
   * cleanup removes both global listeners when the provider is disposed, so a
   * replacement provider cannot duplicate reports.
   */
  onMount(() => {
    window.addEventListener("error", onWindowError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    onCleanup(() => {
      window.removeEventListener("error", onWindowError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    });
  });

  const commands: ToastCommands = { showError, showTransient };

  return (
    <ToastContext.Provider value={commands}>
      {props.children}
      <ToastViewport
        toasts={toasts}
        onDismiss={(id) => {
          // Dismissal removes the exact ID and is safe to repeat.
          setToasts((current) => current.filter((toast) => toast.id !== id));
        }}
      />
    </ToastContext.Provider>
  );
}

/**
 * Returns the notification commands exposed by the nearest ToastProvider.
 *
 * Consumers may present an error Toast or the explicitly temporary notice, but
 * cannot inspect or mutate the queue. The function throws when no provider exists
 * so a missing application boundary cannot silently discard failures.
 */
export function useToasts(): ToastCommands {
  return expect(useContext(ToastContext), "useToasts requires ToastProvider.");
}

/**
 * Renders complete local damage while leaving retry policy with the caller.
 *
 * Callers provide a visible title, the original failure, and explicit action
 * elements as `children`. The panel always exposes the formatted message and
 * opens distinct stack details immediately. It never renders failed content,
 * dismisses itself, or invokes an action.
 */
export function ErrorPanel(props: {
  /**
   * Visible context rendered above the formatted failure.
   *
   * It should name the failed local operation. The panel keeps it distinct from
   * the error's own message and stack.
   */
  title: string;
  /**
   * Original thrown value formatted reactively by `presentError`.
   *
   * ErrorPanel owns no failure state and does not queue this value globally.
   * Replacing the prop replaces the presented local failure.
   */
  error: unknown;
  /**
   * Caller-supplied explicit actions rendered below the complete failure text.
   *
   * The panel invokes none of them itself. Callers may compose RetryButton or
   * another boundary-specific action without changing error presentation.
   */
  children: JSX.Element;
}): JSX.Element {
  /**
   * Formats the current error for the message and optional stack surfaces.
   *
   * JSX consumers call this accessor reactively from `props.error`. It retains
   * no second error value, and `presentError` guarantees formatting never hides
   * the original failure with another exception.
   */
  const presented = () => presentError(props.error);

  return (
    <section class="notice error recoverable-error-notice" role="alert">
      <strong>{props.title}</strong>
      <pre class="render-error-message">{presented().message}</pre>
      <Show when={presented().details !== null}>
        <details class="error-traceback" open>
          <summary>Stack</summary>
          <pre>{presented().details}</pre>
        </details>
      </Show>
      {props.children}
    </section>
  );
}

/**
 * Renders the shared explicit retry control.
 *
 * The caller must provide the complete retry operation. The callback runs only
 * in response to user activation and has no default or automatic behavior.
 */
export function RetryButton(props: {
  /**
   * Performs the caller's explicit recovery attempt.
   *
   * The callback runs only from native button activation. RetryButton does not
   * catch failures, clear caller state, disable itself, or repeat the operation;
   * the caller's accepted state determines what renders next.
   */
  onRetry: () => void;
}): JSX.Element {
  return (
    <button type="button" onClick={props.onRetry}>
      Try again
    </button>
  );
}

/**
 * Keeps a localized error compact until the user requests complete details.
 *
 * The trigger remains in the constrained caller layout. Activating it opens a
 * native top-layer popover containing the complete ErrorPanel and RetryButton,
 * so traceback visibility and user recovery never alter surrounding geometry.
 */
export function ErrorPopover(props: ErrorPopoverProps): JSX.Element {
  const popoverId = createUniqueId();

  return (
    <>
      <button
        type="button"
        class={props.triggerClass}
        aria-label={props.triggerLabel}
        title={props.triggerLabel}
        popovertarget={popoverId}
      >
        {props.trigger}
      </button>
      <div id={popoverId} class="error-popover" popover="auto">
        <ErrorPanel title={props.title} error={props.error}>
          <RetryButton onRetry={props.onRetry} />
        </ErrorPanel>
      </div>
    </>
  );
}

/**
 * Contains unexpected rendering or reactive failures for one trusted subtree.
 *
 * Callers provide a stable error title and the subtree whose correctness is
 * shared. A thrown failure replaces that subtree with complete local damage,
 * emits one notification for the failed mount, and offers only Solid's
 * caller-visible reset action. `retryOnR` explicitly exposes that same reset
 * through the unmodified `r` key when the failed subtree contained its normal
 * keyboard handler; it must remain false when another mounted handler survives.
 */
export function UnexpectedErrorBoundary(props: {
  /**
   * Stable context used by both local and global failure presentation.
   *
   * It should identify the contained subtree rather than repeat a thrown error's
   * message, and remains unchanged across boundary reset attempts.
   */
  title: string;
  /**
   * Whether failure presentation may claim unmodified `r` for retry.
   *
   * Set it only when this boundary replaced the subtree that normally handles
   * the key. The panel still ignores modified keys and editable targets.
   */
  retryOnR: boolean;
  /**
   * Trusted subtree whose rendering and reactive failures share one boundary.
   *
   * A thrown failure replaces these children with the local error panel until
   * the user invokes Solid's reset operation.
   */
  children: JSX.Element;
}): JSX.Element {
  return (
    <ErrorBoundary
      fallback={(error, reset) => (
        <UnexpectedErrorPanel
          title={props.title}
          error={error}
          onRetry={reset}
          retryOnR={props.retryOnR}
        />
      )}
    >
      {props.children}
    </ErrorBoundary>
  );
}

/**
 * Presents an uncaught application-root failure without replacing Toasts.
 *
 * The root ErrorBoundary supplies the original error and its reset callback.
 * Mounting reports one persistent application error, replaces the application
 * with complete full-page damage, and exposes reset only through RetryButton.
 */
export function ApplicationErrorPanel(props: {
  /**
   * Original failure caught by the application-root ErrorBoundary.
   *
   * The mounted panel reports it once through Toasts and also renders it as the
   * complete page failure. It stores no second error value.
   */
  error: unknown;
  /**
   * Solid ErrorBoundary reset operation exposed as the only recovery action.
   *
   * It runs only from the visible Retry button. The panel does not reload the
   * page or clear application state independently.
   */
  onRetry: () => void;
}): JSX.Element {
  const toast = useToasts();

  /**
   * Reports this root failure exactly once for the mounted failed attempt.
   *
   * Retry disposes this panel before a later failure can mount another one. The
   * hook is deliberately non-reactive: changing arbitrary error object internals
   * must not enqueue duplicate Toasts, and no external resource needs cleanup.
   */
  onMount(() => {
    toast.showError("Application error", props.error);
  });

  return (
    <main class="app-shell app-crash">
      <div class="app-crash-notice">
        <ErrorPanel title="Something broke." error={props.error}>
          <RetryButton onRetry={props.onRetry} />
        </ErrorPanel>
      </div>
    </main>
  );
}

/**
 * Renders the provider's queue while each Toast declares its own live semantics.
 *
 * The provider supplies a read-only queue and exact-ID dismissal command. This
 * component preserves insertion order without imposing one urgency on mixed
 * notifications and delegates each timer lifetime to its mounted ToastCard.
 */
function ToastViewport(props: ToastViewportProps): JSX.Element {
  return (
    <div class="toast-viewport">
      <For each={props.toasts()}>
        {(toast) => (
          <ToastCard
            toast={toast}
            onDismiss={() => props.onDismiss(toast.id)}
          />
        )}
      </For>
    </div>
  );
}

/**
 * Renders one notification and owns its optional expiration timer.
 *
 * The caller provides immutable Toast data and an exact dismissal operation.
 * Timeout errors request dismissal after ten seconds, transient notices use
 * their exact positive duration, and other errors remain until dismissal.
 */
function ToastCard(props: {
  /**
   * Immutable notification whose variant selects presentation and lifetime.
   *
   * The card samples its reason and duration at mount for timer setup and renders
   * the remaining fields directly. Producers replace the record rather than
   * mutating it in place.
   */
  toast: Toast;
  /**
   * Requests removal of this exact mounted Toast.
   *
   * The callback may run from the dismiss button or the card's optional timer.
   * It receives no argument because ToastViewport already binds the record ID;
   * repeated calls must remain harmless at the provider boundary.
   */
  onDismiss: () => void;
}): JSX.Element {
  /**
   * Starts expiration only for an expiring mounted Toast.
   *
   * The immutable Toast reason is sampled once at mount. Persistent errors own
   * no timer; every expiring Toast clears its timer on dismissal or disposal.
   */
  onMount(() => {
    if (props.toast.reason === "other") {
      return;
    }

    const durationMs =
      props.toast.reason === "transient"
        ? props.toast.durationMs
        : TIMEOUT_TOAST_TTL_MS;
    const timer = window.setTimeout(props.onDismiss, durationMs);
    onCleanup(() => {
      window.clearTimeout(timer);
    });
  });

  return (
    <section
      class="toast"
      classList={{
        "toast-error": props.toast.reason !== "transient",
        "toast-transient": props.toast.reason === "transient",
      }}
      role={props.toast.reason === "transient" ? "status" : "alert"}
    >
      <div class="toast-copy">
        <strong>{props.toast.title}</strong>
        <pre class="toast-message">{props.toast.message}</pre>
        <Show when={props.toast.details !== null}>
          <details class="toast-details">
            <summary>Stack</summary>
            <pre>{props.toast.details}</pre>
          </details>
        </Show>
      </div>
      <button
        class="toast-dismiss"
        type="button"
        aria-label="Dismiss notification"
        onClick={props.onDismiss}
      >
        x
      </button>
    </section>
  );
}

/**
 * Presents one unexpected subtree failure caught by an ErrorBoundary.
 *
 * The containing ErrorBoundary provides the original failure and reset operation.
 * Each mounted failed attempt emits its global notification exactly once and
 * retains complete local damage until the user explicitly retries. When the
 * caller explicitly enables `retryOnR`, the panel also routes that unmodified
 * key to the same reset operation as its visible RetryButton.
 */
function UnexpectedErrorPanel(props: {
  /**
   * Stable context shown locally and in the queued global Toast.
   *
   * It identifies the failed subtree and must not be derived by parsing the
   * thrown value.
   */
  title: string;
  /**
   * Original failure caught by the containing ErrorBoundary.
   *
   * The panel reports it once at mount and passes it unchanged to ErrorPanel for
   * local formatting.
   */
  error: unknown;
  /**
   * Solid boundary reset operation shared by the Retry button and optional key.
   *
   * The callback runs only from those explicit user actions. The panel performs
   * no automatic reset after reporting the Toast.
   */
  onRetry: () => void;
  /**
   * Whether this failed subtree may temporarily claim unmodified `r`.
   *
   * False installs no document listener. True installs one for the panel's
   * mounted lifetime and cleanup removes it before a replacement attempt.
   */
  retryOnR: boolean;
}): JSX.Element {
  const toast = useToasts();

  /**
   * Reports this localized failed attempt exactly once when its panel mounts.
   *
   * The containing ErrorBoundary disposes the panel on Retry. The hook intentionally
   * does not track props, preventing repeated Toasts for the same failed mount;
   * there is no external resource requiring cleanup.
   */
  onMount(() => {
    toast.showError(props.title, props.error);
    if (!props.retryOnR) {
      return;
    }

    /**
     * Invokes this panel's visible RetryButton operation from the `r` hotkey.
     *
     * The caller explicitly enables the shortcut only when the failed subtree
     * removed its ordinary hotkey listener. Modified browser shortcuts and
     * editable controls retain their native behavior.
     */
    function retryFromKeyboard(event: KeyboardEvent): void {
      if (
        event.defaultPrevented ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.code !== "KeyR"
      ) {
        return;
      }
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          target instanceof HTMLInputElement ||
          target instanceof HTMLTextAreaElement ||
          target instanceof HTMLSelectElement)
      ) {
        return;
      }
      event.preventDefault();
      props.onRetry();
    }

    document.addEventListener("keydown", retryFromKeyboard);
    onCleanup(() => {
      document.removeEventListener("keydown", retryFromKeyboard);
    });
  });

  return (
    <ErrorPanel title={props.title} error={props.error}>
      <RetryButton onRetry={props.onRetry} />
    </ErrorPanel>
  );
}

/**
 * Selects the primary user-visible representation of a thrown value.
 *
 * The formatter gives array-valued validation issues precedence, then Error
 * and string messages, then arbitrary JSON values. Callers receive complete
 * text and must invoke this function only inside `presentError`, which provides
 * the outer no-throw boundary for hostile structural values.
 */
function primaryMessage(error: unknown): string {
  /**
   * Extracts validation issues when a thrown value exposes the required shape.
   *
   * Only an array-valued `issues` field participates in formatting precedence.
   *
   * # Returns
   *
   * - An array containing the exact validation issues exposed by the value.
   * - `null`: The value has no array-valued issues. The caller then formats the
   *   Error, string, or arbitrary value instead.
   */
  function errorIssues(error: unknown): unknown[] | null {
    if (!isObject(error) || !isErrorWithIssues(error)) {
      return null;
    }

    return Array.isArray(error.issues) ? error.issues : null;
  }

  /**
   * Attempts to parse one complete JSON document.
   *
   * The discriminant preserves valid `null` separately from parse failure.
   */
  function parseJson(text: string): ParsedJson {
    try {
      return { parsed: true, value: JSON.parse(text) };
    } catch {
      return { parsed: false };
    }
  }

  /**
   * Pretty-prints arbitrary data without assuming serialization succeeds.
   *
   * Unsupported values use the stable undisplayable-value diagnostic without
   * invoking conversion hooks.
   */
  function prettyJson(value: unknown): string {
    try {
      const formatted = JSON.stringify(value, null, 2);
      return formatted === undefined
        ? UNDISPLAYABLE_THROWN_VALUE_MESSAGE
        : formatted;
    } catch {
      return UNDISPLAYABLE_THROWN_VALUE_MESSAGE;
    }
  }

  /**
   * Formats complete JSON text while preserving ordinary text verbatim.
   *
   * Callers provide an Error message or thrown string. Valid JSON is expanded
   * for readability; failed parsing returns the original text without trimming
   * or substituting content.
   */
  function formattedText(text: string): string {
    const parsed = parseJson(text);
    return parsed.parsed ? prettyJson(parsed.value) : text;
  }

  const issues = errorIssues(error);
  if (issues !== null) {
    return prettyJson(issues);
  }

  if (error instanceof Error) {
    return formattedText(error.message);
  }

  if (typeof error === "string") {
    return formattedText(error);
  }

  return prettyJson(error);
}

/**
 * Returns stack details only when they add information beyond the message.
 *
 * Callers provide the original thrown value and its already-formatted primary
 * message. Non-Error values, absent stacks, empty stacks, and duplicate stacks
 * produce `null` so presentation does not render an empty details control.
 *
 * @param error Original thrown value.
 * @param message Primary text already selected for that value.
 * # Returns
 *
 * - A nonempty Error stack distinct from `message`.
 * - `null`: No separate details control should render because the value is not
 *   an Error, has no useful stack, or the stack duplicates `message`.
 */
function errorDetails(error: unknown, message: string): string | null {
  if (!(error instanceof Error)) {
    return null;
  }

  const stack = error.stack;
  if (stack === undefined || stack.length === 0 || stack === message) {
    return null;
  }
  return stack;
}

/**
 * Narrows values that may safely participate in structural property checks.
 *
 * This shared guard excludes primitives and `null`; callers remain responsible
 * for accessing properties inside `presentError`'s no-throw boundary.
 */
function isObject(value: unknown): value is object {
  return typeof value === "object" && value !== null;
}
