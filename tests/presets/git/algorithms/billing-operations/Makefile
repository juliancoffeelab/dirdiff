OLD := $(firstword $(wildcard old.*))
NEW := $(firstword $(wildcard new.*))

.PHONY: difft git gum difft-json bat diffs

difft:
	difft $(OLD) $(NEW)

git:
	git --no-pager diff --no-index --color=always --no-color-moved $(OLD) $(NEW)

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

difft-json:
	DFT_UNSTABLE=yes difft --display json --context 100000000 $(OLD) $(NEW)

bat:
	bat $(OLD) $(NEW)

diffs:
	diffsitter --no-config $(OLD) $(NEW)

.PHONY: git-moved git-moved-indent

git-moved:
	git --no-pager diff --no-index --color=always --color-moved=zebra --color-moved-ws=no $(OLD) $(NEW)

git-moved-indent:
	git --no-pager diff --no-index --color=always --color-moved=zebra --color-moved-ws=allow-indentation-change $(OLD) $(NEW)
