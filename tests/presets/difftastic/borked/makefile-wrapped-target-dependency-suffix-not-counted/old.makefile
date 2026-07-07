basedpyright:
	.venv/bin/basedpyright -p pyrightcheck.json

flake-sbt:
	uv --no-cache run flake8 --jobs 1 --select SBT001 src

flake-human:
	uv --no-cache run flake8 --jobs 1 --select SBT002 src

humancheck: basedpyright flake-human eslint-human
