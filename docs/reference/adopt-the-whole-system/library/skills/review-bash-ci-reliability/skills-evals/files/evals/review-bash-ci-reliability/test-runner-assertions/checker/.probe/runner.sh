#!/usr/bin/env bash
# Break exactly one of the suite's five tests in a named way, run the suite
# the workflow runs, and exit 0 only when the runner recorded that one FAIL,
# still ran every test after it, printed its Results line and exited
# non-zero (or, for the green case, passed everything).
set -uo pipefail

case_name=${1:?usage: runner.sh CASE}

case "$case_name" in
    grep-miss-recorded)
        grep -v '^Usage: ' README.md > README.md.new && mv README.md.new README.md
        broken=readme-usage ;;
    cmp-difference-recorded)
        printf '%s\n' 'golden-path' > test/golden/golden-path.txt
        broken=golden-path ;;
    crash-recorded)
        cat > scripts/slugify.sh <<'SLUG'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in *:*) echo "slugify: cannot handle ':'" >&2; exit 3 ;; esac
slug=$(printf '%s' "$*" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')
slug=${slug#-}
printf '%s\n' "${slug%-}"
SLUG
        broken=punctuation ;;
    mismatch-recorded)
        cat > scripts/slugify.sh <<'SLUG'
#!/usr/bin/env bash
set -euo pipefail
[[ "$*" != "Hello World" ]] || { echo "hello_world"; exit 0; }
slug=$(printf '%s' "$*" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')
slug=${slug#-}
printf '%s\n' "${slug%-}"
SLUG
        broken=basic ;;
    green-run)
        broken= ;;
    *) echo "unknown case: ${case_name}" >&2; exit 2 ;;
esac

out=$(bash test/run-tests.sh 2>&1)
code=$?
has() { grep -qE -- "$1" <<<"$out"; }

if [[ -z "$broken" ]]; then
    [[ "$code" -eq 0 ]] && has '^Results: 5 passed, 0 failed'
    exit
fi

[[ "$code" -ne 0 ]] || exit 1
has '^Results: 4 passed, 1 failed' || exit 1
has "^FAIL: ${broken}( |$)" || exit 1
# Every test the case did not break was run and passed, the last one included.
for name in basic readme-usage punctuation golden-path trims-edges; do
    [[ "$name" == "$broken" ]] && continue
    has "^PASS: ${name}( |$)" || exit 1
done
