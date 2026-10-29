## Working habits

- Fix what was asked: no speculative features, premature abstractions or
  unused helpers. Prefer editing an existing file over creating one.
- A public interface change updates its tests; new behavior gets a test, a
  bug fix a regression test. Run the existing suite before calling a task
  complete and say what you ran.
- Tests are deterministic: no sleeps, network or wall-clock time.
- **Keep full numeric precision until the final output:** round or truncate
  only at the display or write.
- **A lint or check that reasons about code SHAPE** (which functions or
  `test()` blocks exist, what sits inside what, a call's arguments) **parses
  a real AST, never a regex or line scan** — regex cannot see a template-literal
  variable. Use `acorn`/`acorn-walk` for JS, `yaml` for YAML; regex only for a lexical token.
- Text to paste: give all of it, never edits.
