I found three constraints we can settle from the repo before touching the
feature. `export/table.py` already emits CSV in chunks and writes a header
before any rows, so that is the local pattern to reuse if CSV is the chosen
format. `test/test_export.py` fixes one acceptance criterion: even an empty
export must contain `id,label` and a line ending; the new route currently
misses that case. `docs/adr/0001-bounded-exports.md` rules out building the
whole dataset in memory or waiting to dump it synchronously. A streamed
response or bounded background job is still available. I have not changed
source files.

I need two product decisions before choosing the route. Which format does the
customer need—CSV, another format, or a choice—and does it have to preserve
the current columns? That determines whether the existing CSV helper can
serve the new route. Must `/api/export.csv` keep working for existing clients,
including its path and response shape? That determines whether we extend it
or add a separate route. One more question: how large can a typical export
get, and should clients be able to cancel it? That could decide between a
streamed request and an asynchronous job. Once those answers are available,
I would select the bounded path, fix the empty case, and test it before
implementing the rest of the feature.
