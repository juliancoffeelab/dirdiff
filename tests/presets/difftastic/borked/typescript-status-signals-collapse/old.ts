type LoadState = "idle" | "loading" | "done" | "error";

export function createDiffResources() {
  const [loadingRevision, setLoadingRevision] = createSignal(0);
  const [status, setStatus] = createSignal<LoadState>("idle");
  const [statusText, setStatusText] = createSignal("Preparing diff...");

  let toastedManifestErrorIdentity = "";
  let activeLoadId = 0;

  return {
    loadingRevision,
    setLoadingRevision,
    status,
    setStatus,
    statusText,
    setStatusText,
    toastedManifestErrorIdentity,
    activeLoadId,
  };
}
