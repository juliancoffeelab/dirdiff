type LoadState = "idle" | "loading" | "done" | "error";
type DiffStatus = {
  loadedFiles: {
    failed: number;
    loaded: number;
    total: number;
  } | null;
  placement: "inline" | "top";
  state: LoadState;
  text: string;
};

export function createDiffResources() {
  const [loadingRevision, setLoadingRevision] = createSignal(0);
  const [status, setStatus] = createSignal<DiffStatus>({
    loadedFiles: null,
    placement: "top",
    state: "idle",
    text: "Preparing diff...",
  });

  let toastedManifestErrorIdentity = "";
  let activeLoadId = 0;

  return {
    loadingRevision,
    setLoadingRevision,
    status,
    setStatus,
    toastedManifestErrorIdentity,
    activeLoadId,
  };
}
