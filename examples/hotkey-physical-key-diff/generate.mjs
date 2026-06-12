import { readFile, writeFile } from "node:fs/promises";

const oldPath = new URL("old.ts", import.meta.url);
const newPath = new URL("new.ts", import.meta.url);
const outputPath = new URL("case.json", import.meta.url);

const fixture = {
  name: "hotkey-physical-key-diff",
  description:
    "Line matcher case where event.key hotkeys changed to physical event.code keys.",
  old: await readFile(oldPath, "utf8"),
  new: await readFile(newPath, "utf8"),
};

await writeFile(outputPath, `${JSON.stringify(fixture, null, 2)}\n`);
