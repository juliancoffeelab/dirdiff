checkFormatJs:
	npm --prefix frontend run format:check

checkFormatPython:
	.venv/bin/ruff format --check

format:
	.venv/bin/ruff format
	npm --prefix frontend run format
