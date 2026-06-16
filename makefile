checkFormatJs:
	bun run --cwd frontend format:check

checkFormatPython:
	.venv/bin/ruff format --check

tscheck:
	bun run --cwd frontend typecheck

eslint:
	bun run --cwd frontend lint

mypy:
	.venv/bin/mypy .

ruff:
	.venv/bin/ruff check

cram:
	uv --no-cache run cram tests/cli-cram/*.t

pytest:
	uv --no-cache run pytest

fullcheck: checkFormatPython checkFormatJs ruff mypy tscheck eslint pytest cram

format:
	.venv/bin/ruff format
	bun run --cwd frontend format

reinstall:
	uv tool install -e . --reinstall
