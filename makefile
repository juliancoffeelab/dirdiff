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
	uv --no-cache run flake8 --jobs 1 --select SBT001 src

flake-human:
	uv --no-cache run flake8 --jobs 1 --select SBT002 src

humancheck: basedpyright flake-human eslint-human

ruff:
	.venv/bin/ruff check

cram:
	uv --no-cache run cram tests/cli-cram/*.t

pytest:
	# default subset
	uv --no-cache run pytest

gitpytest:
	# run all test, including git marks
	uv --no-cache run pytest -m 'git or not git'

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

fullcheck: checkFormatPython checkFormatJs ruff mypy tscheck eslint flake-sbt

fulltest: gitpytest cram

format:
	.venv/bin/ruff format
	bun run --cwd frontend format

reinstall:
	uv tool install -e . --reinstall
