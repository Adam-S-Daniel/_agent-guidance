#!/usr/bin/env bash
# Run every test below in order and print one Results line. Exits non-zero
# when any test failed.
set -euo pipefail

cd "$(dirname "$0")/.."

passed=0
failed=0
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

pass() { echo "PASS: $1"; passed=$((passed + 1)); }
fail() { echo "FAIL: $1 -- $2"; failed=$((failed + 1)); }

# assert_slug NAME EXPECTED TITLE: slugify.sh prints EXPECTED for TITLE.
assert_slug() {
    local name=$1 expected=$2 title=$3 actual
    actual=$(bash scripts/slugify.sh "$title")
    if [[ "$actual" == "$expected" ]]; then
        pass "$name"
    else
        fail "$name" "expected '${expected}', got '${actual}'"
    fi
}

# assert_line NAME FILE PATTERN: FILE has a line matching PATTERN.
assert_line() {
    local name=$1 file=$2 pattern=$3 line
    line=$(grep -n -- "$pattern" "$file" | head -1 | cut -d: -f1)
    if [[ -n "$line" ]]; then
        pass "$name"
    else
        fail "$name" "no line matching '${pattern}' in ${file}"
    fi
}

# assert_golden NAME GOLDEN TITLE: slugify.sh output equals the GOLDEN file.
assert_golden() {
    local name=$1 golden=$2 title=$3 delta
    bash scripts/slugify.sh "$title" > "$tmp/${name}.out"
    delta=$(cmp "$golden" "$tmp/${name}.out")
    if [[ -z "$delta" ]]; then
        pass "$name"
    else
        fail "$name" "$delta"
    fi
}

assert_slug basic "hello-world" "Hello World"
assert_line readme-usage README.md '^Usage: bash scripts/slugify.sh'
assert_slug punctuation "release-notes-v2" "Release Notes: v2!"
assert_golden golden-path test/golden/golden-path.txt "The Golden Path"
assert_slug trims-edges "edge-case" "  Edge case  "

echo "Results: ${passed} passed, ${failed} failed"
[[ "$failed" -eq 0 ]]
