import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@solidjs/testing-library";
import { afterEach, expect, test } from "vitest";

import { Select } from "../src/comp/Select";

// Dispose each Solid root and its document listeners before the next test.
afterEach(cleanup);

test("selects a different option and restores trigger focus", () => {
  const selectedValues: string[] = [];
  render(() => (
    <Select
      class=""
      label="Engine"
      valueLabel="Difftastic"
      options={[
        { value: "difftastic", label: "Difftastic", description: null },
        { value: "token", label: "Token diff", description: null },
      ]}
      selectedValue="difftastic"
      disabled={false}
      onChange={(value) => selectedValues.push(value)}
      onOpen={null}
      optionAction={null}
    />
  ));

  const trigger = screen.getByRole("button", {
    name: /Engine\s*Difftastic/,
  });
  fireEvent.click(trigger);
  expect(screen.getByRole("listbox", { name: "Engine" })).toBeVisible();

  fireEvent.click(screen.getByRole("option", { name: "Token diff" }));
  expect(selectedValues).toEqual(["token"]);
  expect(screen.queryByRole("listbox", { name: "Engine" })).toBeNull();
  expect(trigger).toHaveFocus();
});
