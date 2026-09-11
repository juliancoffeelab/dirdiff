# Git presets

Select **Git Presets** under **Preset type**, then choose **Algorithms**,
**Python**, or **Whitespace**. Use the Git engine and Split view.

## Algorithms

`billing-operations` moves the customer loader above the invoice loader and
replaces invoice deletion with archiving. Compare Myers with Patience: Myers
matches repeated error handling within the functions, while Patience keeps the
function movement together. Histogram matches Patience here; Minimal matches
Myers.

## Python

`free-shipping` adds an `if` around the shipping charge so orders of at least
100 get free shipping. The existing charge moves one indentation level deeper;
the new condition remains visible with Ignore space changes enabled.

`unconditional-shipping` moves the shipping charge out of `if member`, while
keeping the membership discount conditional. Nonmembers now pay shipping too.
This is an indentation-only edit with a behavior change: Ignore space changes
hides the entire edit.

## Whitespace

- `receipt-indentation` changes indentation from four spaces to two. With
  **Ignore space changes** enabled, every line is equal.
- `tax-rate-change` changes sales tax from 20% to 21% while removing alignment
  spaces. The tax edit stays visible when ignoring space changes. Spacing added
  around `+` in the receipt label also stays visible: Git's
  `--ignore-space-change` ignores changes in existing whitespace amount, but
  still counts whitespace introduced where there was none.

Preserve the deliberate formatting differences in these source pairs.
