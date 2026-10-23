# Settings schema design

Read this when you design a config file's keys, a CLI flag, a schema field or
an API parameter. The fleet rule is a pointer in
[`agents-md/base.md`](../../agents-md/base.md); the rules and terms live here.

## Rules

1. **One meaning, one value.** A key never accepts two values that mean the
   same thing, whether of different types (`false` and `[]`, a string and an
   object) or the same type (`[]` and `["none"]`). Reject the others with an
   error that names the accepted value.
2. **Scope and access keys are required unless their default selects
   nothing.** A key that selects what a tool acts on or what access it grants
   is required unless its default selects nothing: omitting it must not widen
   either. Ask two questions:
   - Does the key select targets, scope or access?
   - Does its default select nothing?

   A `timeout` keeps a default. `manage`, or a workflow's `permissions`, does
   not.
3. **Other keys may have a default,** set in one place and documented with the
   key.

## Why

The owner's words:

> When I encounter different representations, I tend to go on a research expedition to figure out if they cause different behavior. I'd like to spare my future self or anyone else from such an ambiguity.

## Terminology

Use JSON terms, giving weight to GitHub's own docs. Write examples in JSON
syntax (`["all"]`, not `[all]`).

| Use | Not | Why |
|---|---|---|
| key | name | RFC 8259 says name; developers and GitHub docs say key ("Required keys") |
| value | | |
| object | map, mapping | JSON |
| array | list, sequence | JSON; GitHub's typed tables say `Array`; "array of strings" appears 253 times in github/docs against "list of strings" once |
| element | entry, item | RFC 8259; GitHub's form schema says "array of form elements"; "entry" is ambiguous (key-value pair? array element? record?) |
| type | shape | JSON types: object, array, string, number, boolean, null |
| required / optional / default | | GitHub docs and JSON Schema |
| omitted | absent, missing | GitHub docs: "If omitted...", "defaults to" |

## Worked example

repo-settings' `manage` key in `repo-settings/fleet.yml`; the redesign is in
progress and has not shipped.

- Today `manage` is a boolean that defaults to `true` when omitted. Omitting it
  widens what the tool acts on, which breaks rule 2.
- The redesign makes it an array of strings (`["all"]`, `["labels"]`, `[]`),
  required in each element of the `repos` array.
- It rejects booleans, `["none"]`, `"all"` combined with other values, and
  string elements of `repos`.
