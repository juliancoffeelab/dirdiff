OLD := $(firstword $(wildcard old.*))
NEW := $(firstword $(wildcard new.*))

.PHONY: diff full json show

diff:
	difft $(OLD) $(NEW)

full:
	diff $(OLD) $(NEW)

json:
	@DFT_UNSTABLE=yes difft --display json --context 100000000 $(OLD) $(NEW)

show:
	bat $(OLD) $(NEW)
