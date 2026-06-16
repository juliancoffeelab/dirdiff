checkFormatJs:
	npm --prefix frontend run format:check

checkFormatPython:
	.venv/bin/ruff format --check

tscheck:
	npm --prefix frontend run typecheck

mypy:
	.venv/bin/mypy .

ruff:
	.venv/bin/ruff check

cram:
	uv --no-cache run cram tests/cli-cram/*.t

format:
	.venv/bin/ruff format
	npm --prefix frontend run format

reinstall:
	uv tool install -e . --reinstall
