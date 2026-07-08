OLD := $(firstword $(wildcard old.*))
NEW := $(firstword $(wildcard new.*))

.PHONY: diff full gum json show

diff:
	difft $(OLD) $(NEW)

full:
	diff $(OLD) $(NEW)

gum:
	gumtree webdiff --port $${GUM_PORT:-4567} $(OLD) $(NEW) & \
	pid=$$!; \
	trap 'kill $$pid 2>/dev/null || true' INT TERM EXIT; \
	sleep 1; \
	url=http://127.0.0.1:$${GUM_PORT:-4567}/; \
	if command -v open >/dev/null 2>&1; then open $$url; \
	elif command -v xdg-open >/dev/null 2>&1; then xdg-open $$url; \
	else printf 'GumTree webdiff: %s\n' $$url; fi; \
	wait $$pid

json:
	@DFT_UNSTABLE=yes difft --display json --context 100000000 $(OLD) $(NEW)

show:
	bat $(OLD) $(NEW)
