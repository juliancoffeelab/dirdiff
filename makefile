checkFormatJs:
	npm --prefix frontend run format:check

checkFormatPython:
	uv run ruff format --check

format:
	uv run ruff format
	npm --prefix frontend run format
