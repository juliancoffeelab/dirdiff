checkFormatJs:
	bun run --cwd frontend format:check

checkFormatPython:
	.venv/bin/ruff format --check

tscheck:
	bun run --cwd frontend typecheck

eslint:
	bun run --cwd frontend lint

eslint-human:
	bun run --cwd frontend lint:human

mypy:
	.venv/bin/mypy .

basedpyright:
	.venv/bin/basedpyright -p pyrightcheck.json

flake-sbt:
	uv --no-cache run flake8 --jobs 1 --select SBT001 src tests

flake-cst:
	uv --no-cache run flake8 --jobs 1 --select CST001 src tests lints

flake-hlp:
	uv --no-cache run flake8 --jobs 1 --select HLP002 src tests lints

pyflake: flake-sbt flake-cst flake-hlp

flake-human:
	uv --no-cache run flake8 --jobs 1 --select SBT002 src tests

flake-hlp-human:
	uv --no-cache run flake8 --jobs 1 --select HLP001 src tests lints

humancheck:
	$(MAKE) --keep-going basedpyright flake-human flake-hlp-human eslint-human

ruff:
	.venv/bin/ruff check

cram:
	uv --no-cache run cram tests/cli-cram/*.t

pytest:
	# default subset
	uv --no-cache run pytest

snapshot:
	uv --no-cache run pytest \
		tests/test_difftastic_golden.py \
		tests/test_fold_golden.py \
		tests/test_gumtree_golden.py \
		--snapshot-warn-unused

resnapshot:
	rm -rf tests/golden/*
	uv --no-cache run pytest \
		tests/test_difftastic_golden.py \
		tests/test_fold_golden.py \
		tests/test_gumtree_golden.py \
		--snapshot-update \
		--snapshot-warn-unused

fullcode: checkFormatPython checkFormatJs ruff mypy tscheck eslint flake-sbt flake-cst flake-hlp

fulltest: pytest cram

fullcheck: fullcode fulltest

format:
	.venv/bin/ruff format
	bun run --cwd frontend format

reinstall:
	uv tool install -e . --reinstall
