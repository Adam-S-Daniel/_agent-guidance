---
name: fixture-solo-skill
description: Placeholder skill used to exercise legacy one-skill-per-plugin resolution.
---

# fixture-solo-skill

Placeholder skill content for eval-harness tests. Deliberately a synthetic
name that no real registry ships, so this fixture can never be confused with —
or accidentally shadow — a skill in the `agentskills` repo.

The plugin dir and the skill dir share this name on purpose: that is exactly
the legacy layout (`plugins/<skill>/skills/<skill>/SKILL.md`) this fixture
exists to keep resolvable.
