function onKeyDown(event: KeyboardEvent) {
  if (shouldIgnoreHunkNavKeyEvent(event)) {
    return;
  }
  if (event.code === "KeyN" && !event.shiftKey) {
    event.preventDefault();
    scrollHunk(1);
    return;
  }
  if (event.code === "KeyN" && event.shiftKey) {
    event.preventDefault();
    scrollHunk(-1);
    return;
  }
  if (event.code === "KeyP") {
    event.preventDefault();
    scrollTop();
    return;
  }
  if (event.code === "KeyT") {
    event.preventDefault();
    setFileTreeOpen((open) => !open);
    return;
  }
  if (event.code === "KeyD") {
    event.preventDefault();
    setDebugMenuOpen((open) => !open);
    return;
  }
  if (event.code === "KeyH") {
    event.preventDefault();
    setHelpOpen((open) => !open);
    return;
  }
}
