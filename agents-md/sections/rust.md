## Rust

- Do not use `unsafe` without a `// SAFETY:` comment explaining the invariant being upheld.
- Run `cargo clippy -- -D warnings` and `cargo test` before considering a change complete.
- Keep `unwrap()` and `expect()` out of library code; they are acceptable only in tests and short-lived scripts.
