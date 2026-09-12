
.PHONY: git-moved git-moved-indent

git-moved:
	git --no-pager diff --no-index --color=always --color-moved=zebra --color-moved-ws=no $(OLD) $(NEW)

git-moved-indent:
	git --no-pager diff --no-index --color=always --color-moved=zebra --color-moved-ws=allow-indentation-change $(OLD) $(NEW)
