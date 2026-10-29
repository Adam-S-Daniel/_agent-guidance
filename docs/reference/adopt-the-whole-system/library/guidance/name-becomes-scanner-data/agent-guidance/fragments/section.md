## A name you choose becomes data a scanner reads

gitleaks' `generic-api-key` rule fires on a keyword next to a
high-entropy value:

```
access  auth  api  credential  creds  key  passwd  password  secret  token
```

Any generated file serializing such a `name: value` beside a hash looks like
a leak: **`example-site-platform-secrets`** in `skills.lock` turned both consumer sites
red on every push (PR lane scans `base..head`, push lane full history; the
name outlives a rename).

- **Check a name against that list** whenever it lands in a generated
  artifact; name the purpose (`consumer-repo-provisioning`), not the
  sensitive noun.
- **Fix it at the source, not with an allowlist.** A `.gitleaksignore`
  fingerprint is `<commit>:<file>:<rule>:<line>` — repo-unique, unpropagable.
- **Do not lean on a scanner's internals.** `sha256:<hex>` dodges the rule
  only because `:` is outside its capture class.
- **Suppress by value, never by path.** A `paths` entry skips the file before
  any rule runs (cms-platform#260).
