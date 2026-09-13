
.PHONY: git-word git-moved git-moved-indent

git-word:
	git --no-pager diff --no-index --color=always --no-color-moved --word-diff=color $(OLD) $(NEW)

git-moved:
	git --no-pager diff --no-index --color=always --color-moved=zebra --color-moved-ws=no $(OLD) $(NEW)

git-moved-indent:
	git --no-pager diff --no-index --color=always --color-moved=zebra --color-moved-ws=allow-indentation-change $(OLD) $(NEW)
