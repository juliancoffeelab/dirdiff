# Tabs and preferences

## Tabs

- Each Tab retains one complete `DiffParams` and passes that same value to both
  `ChangeSet` and manifest, which receives it unchanged as one JSON `selection`
  query value; nothing builds a second selection shape.
- Repository-backed Tabs construct or report a selection only behind the
  `repoId` gate, so no repo-backed value exists without a repository.
- `engine` is a file-rendering choice independent of `DiffParams`; it takes no
  part in manifest or Room identity. Workspace URLs store only the engine name.
- `ChangeSetShell` observes no queries, stores no backend data, and never
  selects hunks, navigates, or owns file expansion.

## Preferences

- The active TanStack query is the sole reactive representation of
  preferences. Option values are not copied into workspace state.
- localStorage holds the preference values only, with no Profile identity or
  version. Profile reads write the database value into it; anonymous reads use
  it. Fields absent from an old anonymous value take the localStorage schema's
  explicit defaults; HTTP values still require every field.
- `savePreferences` is the single publish path. A save that completes after its
  Profile was left must not overwrite the current local cache.
- A Profile's preference row is created with initial values only when absent;
  saves replace all values.
- The custom Git word pattern persists whatever mode is selected, and NULL
  means none was entered. Neither the browser nor the HTTP boundary compiles
  it; Git does, and its rejection reaches the file card as the usual engine
  error.
