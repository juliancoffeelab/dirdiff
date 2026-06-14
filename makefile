checkFormatJs:
	npm --prefix frontend run format:check

checkFormatPython:
	.venv/bin/ruff format --check

tscheck:
	npm --prefix frontend run typecheck

mypy:
	.venv/bin/mypy .

format:
	.venv/bin/ruff format
	npm --prefix frontend run format
