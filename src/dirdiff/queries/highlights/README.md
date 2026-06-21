These highlight queries are vendored from Helix:

https://github.com/helix-editor/helix/tree/master/runtime/queries

Helix is distributed under the Mozilla Public License 2.0. The copied license is
kept next to these files as `LICENSE-HELIX`.

They are used for JavaScript, JSX, TypeScript, and TSX because the highlight
queries bundled with the Python parser wheels are too small for dirdiff's UI.
Helix query inheritance comments such as `; inherits: ecma,_typescript` are
expanded by `dirdiff.rendering`.
