## Go

- Always check returned errors — never assign to `_` without justification.
- Use `context.Context` as the first parameter for functions that do I/O or may be cancelled.
- Prefer returning errors over panicking; reserve `panic` for truly unrecoverable situations.
- Run `go vet` and `golangci-lint` before considering a change complete.
- Use table-driven tests.
- Keep interfaces small (1–3 methods) and define them where used, not where implemented.
- Use `defer` for cleanup to guarantee execution on all code paths.
