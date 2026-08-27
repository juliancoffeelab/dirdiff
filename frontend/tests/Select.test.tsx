/**
 * Contains jsdom rendering tests for Select's caller-visible contract.
 *
 * Tests exercise the real Solid component through accessible controls and
 * observable callbacks. Add cases for selection, dismissal, focus, and disabled
 * behavior. Do not inspect Solid state, duplicate the popup algorithm, or assert
 * generated markup and styling.
 */
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
        { value: "difftastic", label: "Difftastic" },
        { value: "token", label: "Token diff" },
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
