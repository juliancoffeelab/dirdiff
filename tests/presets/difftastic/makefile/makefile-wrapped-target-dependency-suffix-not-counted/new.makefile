basedpyright:
	.venv/bin/basedpyright -p pyrightcheck.json

flake-sbt:
	uv --no-cache run flake8 --jobs 1 --select SBT001 src tests

flake-human:
	uv --no-cache run flake8 --jobs 1 --select SBT002 src tests

humancheck: basedpyright flake-human eslint-human
