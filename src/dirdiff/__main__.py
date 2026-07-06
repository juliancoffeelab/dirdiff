"""Module execution entrypoint for `python -m dirdiff`."""

from dirdiff.cli import main

# Intentionally empty: `main` is imported here only so module execution can
# delegate to the real public entrypoint, `dirdiff.cli:main`.
__all__: list[str] = []

if __name__ == "__main__":
    main()
