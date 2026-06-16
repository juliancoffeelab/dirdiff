type ControlsState = { mode: string };
type RefChoices = { builtins: string[] };
type DiffEngine = "dirdiff" | "git" | "difftastic";
type RepoId = number;

declare function setEngine(engine: DiffEngine): void;
declare function setControls(controls: ControlsState): void;
declare function refChoices(): RefChoices;
declare function buildRequest(
  controls: ControlsState,
  choices: RefChoices,
  engine: DiffEngine,
  repoId: RepoId,
): string | object;
declare function setStatus(status: "error"): void;

export function loadControls(
  nextControls: ControlsState,
  nextEngine: DiffEngine,
  repoId: RepoId,
) {
  setEngine(nextEngine);
  setControls(nextControls);
  const nextRequest = buildRequest(
    nextControls,
    refChoices(),
    nextEngine,
    repoId,
  );

  if (typeof nextRequest === "string") {
    setStatus("error");
  }
}
