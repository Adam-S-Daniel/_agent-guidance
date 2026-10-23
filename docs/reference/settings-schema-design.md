# Settings schema design

Read this when you design settings in any format: config file keys (JSON,
YAML, TOML, INI, HCL), CLI flags, environment variables or API parameters.
The fleet rule is a pointer in [`agents-md/base.md`](../../agents-md/base.md);
the rules and terms live here.

## Rules

A setting is one configurable thing; it takes a value.

1. **One meaning, one value.** A setting accepts only its documented values
   and rejects everything else; it never accepts two values that mean the
   same thing. Define the accepted values once and build the error message
   from that definition, so it names what is accepted without enumerating
   what is not.
2. **A setting that selects what a tool acts on, or what access it grants, is
   required unless its default selects nothing.** Omitting it must not widen
   either. The two-question test:
   - Does the setting select targets, scope or access?
   - Does its default select nothing?

   A timeout keeps a default. repo-settings' `manage`, or a GitHub workflow's
   `permissions`, does not.
3. **Other settings may have a default,** defined in one place and documented
   with the setting.

"Omitted" means the setting was not specified in that format's way: a key
left out, a flag not passed, an environment variable unset.

## Why

The owner's words:

> When I encounter different representations, I tend to go on a research expedition to figure out if they cause different behavior. I'd like to spare my future self or anyone else from such an ambiguity.

## Terminology

Within one document, use one format's terms consistently. Where a format's
spec and its ecosystem disagree, follow the ecosystem, giving weight to
GitHub's own docs (github/docs). For example, GitHub's docs describe YAML
config with key, map and array: their typed tables say `Map` and `Array`, and
"mapping" and "sequence" are rare.

| Concept | JSON | YAML (spec / GitHub's docs) | TOML | INI | Env vars | CLI | HCL (Terraform) |
|---|---|---|---|---|---|---|---|
| Setting's identifier | key | key | key | key (in a section) | variable name | option / flag | argument |
| Key–value collection | object | mapping / **map** | table | section | — | — | map / object / block |
| Ordered collection | array | sequence / **array** | array | — (delimited string) | — (delimited string) | repeated flag or delimited value | list / tuple |
| Item in it | element | item / element | value | — | — | — | element |
| Not specified | omitted | omitted | omitted | omitted | unset (≠ empty) | not passed | omitted / `null` |

Evidence: JSON's own term is "name" (RFC 8259), but developers and GitHub's
docs say key. github/docs says "array of strings" 253 times and "list of
strings" once. "Entry" is avoided because it is ambiguous: a key–value pair,
an array element or a record.

## Formats that create synonyms on their own

Each of these is a rule-1 trap.

- **YAML 1.1 parsers.** PyYAML is one, and repo-settings reads `fleet.yml`
  with it. `yaml.safe_load('[no, on, yes, off, all]')` returns
  `[False, True, True, False, 'all']`, and `010` loads as `8`. So
  `manage: [no]` loads as `[False]`, an array holding a boolean. Validation
  that accepts only the documented values catches it; quoting, or a YAML 1.2
  parser, avoids it.
- **A single value vs a one-element array.** A GitHub workflow accepts both
  `on: push` and `on: [push]`. When you consume a format, you live with it;
  in a format you design, pick one.
- **Environment variables and INI.** `1`, `true`, `yes` and `on` are all read
  as true by some tools; an unset variable and an empty string differ.
- **CLI.** `--x`, `--x=true` and `--no-x`; `--tag a --tag b` vs `--tag a,b`.
- **Case.** `True` vs `true`, and keys that some formats treat as
  case-insensitive (often INI).

## Validation, by format

The approach is the same everywhere: rule 1.

- **JSON Schema** covers JSON, YAML (editor support through
  yaml-language-server's `# yaml-language-server: $schema=` comment) and TOML
  (through Taplo). Prefer it when a file has human editors or more than a
  handful of keys.
- **CLI parsers** declare it, for example Python argparse's `choices=` and
  `required=True`.
- **Environment variables** need an explicit parse against the accepted
  values.
- Hand checks are fine for a few settings.
- No validation at all is not acceptable for a tool that changes shared
  state, such as settings across many repos, because typos and synonyms fail
  silently.

## Worked example

repo-settings' `manage` key in
[`repo-settings/fleet.yml`](https://github.com/Adam-S-Daniel/repo-settings/blob/main/repo-settings/fleet.yml),
in GitHub's YAML terms. It shipped in
[repo-settings#98](https://github.com/Adam-S-Daniel/repo-settings/pull/98); the
decision is
[repo-settings ADR 0011](https://github.com/Adam-S-Daniel/repo-settings/blob/main/docs/decisions/0011-make-manage-a-required-array-of-surfaces.md)
and the reference is
[`repo-settings/schema.md`](https://github.com/Adam-S-Daniel/repo-settings/blob/main/repo-settings/schema.md),
"`manage`".

- Before, `manage` was a boolean that defaulted to `true` when omitted.
  Omitting it widened what the tool acts on, which breaks rule 2.
- Now it is an array of strings (`["all"]`, `["labels"]`, `[]`), required in
  each element of the `repos` array. `["all"]` includes surfaces the engine
  adds later.
- The accepted surface names are one constant, and every error message is
  built from it (rule 1). A list must use their canonical, alphabetical
  order, so one selection has one spelling.
- It rejects booleans, `[no]` (PyYAML's `[False]`), `["none"]`, `"all"`
  alongside other elements, a list naming every surface, and string elements
  of `repos`. Unknown keys and duplicate keys are rejected too, and a
  rejected config exits 2.
