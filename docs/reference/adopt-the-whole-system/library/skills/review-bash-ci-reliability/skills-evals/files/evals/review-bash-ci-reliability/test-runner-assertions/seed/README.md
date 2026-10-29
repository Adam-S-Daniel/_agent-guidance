# example-slugs

A one-file title-to-slug helper used by the site's publishing scripts.

Usage: bash scripts/slugify.sh <title...>

`test/run-tests.sh` is the test suite, and `.github/workflows/test.yml` runs
it on every pull request and on every push to `main`. It prints a PASS or
FAIL line per test and one `Results:` line at the end, and exits non-zero
when any test failed.
