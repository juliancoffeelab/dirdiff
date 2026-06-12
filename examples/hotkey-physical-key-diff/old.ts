function onKeyDown(event: KeyboardEvent) {
  if (shouldIgnoreHunkNavKeyEvent(event)) {
    return;
  }
  if (event.key === "n" && !event.shiftKey) {
    event.preventDefault();
    scrollHunk(1);
    return;
  }
  if (event.key === "N") {
    event.preventDefault();
    scrollHunk(-1);
    return;
  }
  if (event.key === "p") {
    event.preventDefault();
    scrollTop();
    return;
  }
  if (event.key === "t") {
    event.preventDefault();
    setFileTreeOpen((open) => !open);
    return;
  }
  if (event.key === "d") {
    event.preventDefault();
    setDebugMenuOpen((open) => !open);
    return;
  }
  if (event.key === "h") {
    event.preventDefault();
    setHelpOpen((open) => !open);
    return;
  }
}
