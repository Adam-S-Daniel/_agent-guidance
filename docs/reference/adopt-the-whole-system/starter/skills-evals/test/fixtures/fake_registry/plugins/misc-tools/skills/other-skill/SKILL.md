---
name: other-skill
description: Placeholder second skill, used to prove glob resolution picks the
  right skill dir when multiple bundles exist in the registry.
---

# other-skill

Placeholder skill content for eval-harness tests. Lives in the `misc-tools`
bundle, alongside `gha-tools`, so `test/run_tests.py` can assert that
resolving `fixture-primary-skill` doesn't accidentally match this bundle (or
vice versa).
