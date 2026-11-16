
# SEM names one executable. Set it explicitly if sem is not on PATH.
SEM ?= sem

.PHONY: sem sem-json sem-graph-old sem-graph-new sem-repo

sem:
	SEM_CLOUD=0 "$(SEM)" diff $(OLD) $(NEW) --verbose

sem-json:
	SEM_CLOUD=0 "$(SEM)" diff $(OLD) $(NEW) --json

# A directory outside this checkout prevents sem from selecting dirdiff's Git root.
# Use the same app filename on both sides and retain unchanged companion files.
sem-graph-old sem-graph-new:
	set -eu; \
	sem_case=$$(mktemp -d); \
	trap 'rm -rf "$$sem_case"' EXIT; \
	cp "$(if $(filter sem-graph-old,$@),$(OLD),$(NEW))" "$$sem_case/app$(suffix $(OLD))"; \
	if test -d context; then cp -R context/. "$$sem_case/"; fi; \
	SEM_CLOUD=0 "$(SEM)" graph "$$sem_case" --json --no-cache

# Explicitly keep an editable Git example with old committed and new in the worktree.
sem-repo:
	set -eu; \
	sem_case=$$(mktemp -d); \
	trap 'rm -rf "$$sem_case"' EXIT; \
	cp "$(OLD)" "$$sem_case/app$(suffix $(OLD))"; \
	if test -d context; then cp -R context/. "$$sem_case/"; fi; \
	git -C "$$sem_case" init; \
	git -C "$$sem_case" add .; \
	git -C "$$sem_case" -c user.name='Sem playground' -c user.email='sem@localhost' -c commit.gpgsign=false -c core.hooksPath=/dev/null commit -m 'Before'; \
	cp "$(NEW)" "$$sem_case/app$(suffix $(OLD))"; \
	trap - EXIT; \
	printf 'cd "%s"\n' "$$sem_case"
