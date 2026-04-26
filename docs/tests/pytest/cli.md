# Pytest CLI Tests

Source:

- [`tests/test_cli.py`](/Users/illiadenysenko/Workspace/lab/dirdiff/tests/test_cli.py)

## Why This Layer Exists

These tests cover CLI startup behavior that should stay reliable even when a local port is already occupied.

## How These Tests Work

- They bind a temporary localhost socket first.
- They call the CLI server-creation helper directly.
- They verify that startup picks another port instead of failing immediately.

## Covered Test

`test_create_server_uses_next_port_when_requested_port_is_busy`

- What it tests: startup falls forward to a later localhost port when the requested one is busy.
- How it tests it: occupies an ephemeral port, then asks `create_server()` to bind there.
- Why it exists: protects `uv run dirdiff` from dying with an immediate socket-in-use error.
