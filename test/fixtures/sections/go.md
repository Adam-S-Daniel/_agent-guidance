## Go

- Use `context.Context` as the first parameter for functions that do I/O or may be cancelled.
- Run `go vet` and `golangci-lint` before considering a change complete.
- Use table-driven tests.
- Keep interfaces small (1–3 methods) and define them at the point of use, not at the point of implementation.
