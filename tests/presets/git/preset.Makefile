
.PHONY: git-word git-word-lang git-moved git-moved-indent

git-word:
	git --no-pager diff --no-index --color=always --no-color-moved --word-diff=color $(OLD) $(NEW)

# Same drivers dirdiff's Language word boundary selects, in the byte locale
# Git's built-in word rules were written for.
git-word-lang:
	attrs=$$(mktemp); \
	printf '%s\n' \
		'*.adb diff=ada' \
		'*.ads diff=ada' \
		'*.sh diff=bash' \
		'*.bash diff=bash' \
		'*.bib diff=bibtex' \
		'*.c diff=cpp' \
		'*.h diff=cpp' \
		'*.cc diff=cpp' \
		'*.cpp diff=cpp' \
		'*.cxx diff=cpp' \
		'*.hh diff=cpp' \
		'*.hpp diff=cpp' \
		'*.hxx diff=cpp' \
		'*.cs diff=csharp' \
		'*.css diff=css' \
		'*.dts diff=dts' \
		'*.dtsi diff=dts' \
		'*.ex diff=elixir' \
		'*.exs diff=elixir' \
		'*.f diff=fortran' \
		'*.f90 diff=fortran' \
		'*.f95 diff=fortran' \
		'*.for diff=fortran' \
		'*.fountain diff=fountain' \
		'*.go diff=golang' \
		'*.html diff=html' \
		'*.htm diff=html' \
		'*.xhtml diff=html' \
		'*.java diff=java' \
		'*.kt diff=kotlin' \
		'*.kts diff=kotlin' \
		'*.md diff=markdown' \
		'*.markdown diff=markdown' \
		'*.m diff=matlab' \
		'*.mm diff=objc' \
		'*.pas diff=pascal' \
		'*.pp diff=pascal' \
		'*.pl diff=perl' \
		'*.pm diff=perl' \
		'*.t diff=perl' \
		'*.php diff=php' \
		'*.py diff=python' \
		'*.pyi diff=python' \
		'*.rb diff=ruby' \
		'*.rs diff=rust' \
		'*.scm diff=scheme' \
		'*.ss diff=scheme' \
		'*.tex diff=tex' \
		'*.sty diff=tex' \
		'*.cls diff=tex' > $$attrs; \
	LC_ALL=C git -c core.attributesFile=$$attrs --no-pager diff --no-index --color=always --no-color-moved --word-diff=color $(OLD) $(NEW); \
	status=$$?; rm -f $$attrs; exit $$status

git-moved:
	git --no-pager diff --no-index --color=always --color-moved=zebra --color-moved-ws=no $(OLD) $(NEW)

git-moved-indent:
	git --no-pager diff --no-index --color=always --color-moved=zebra --color-moved-ws=allow-indentation-change $(OLD) $(NEW)
