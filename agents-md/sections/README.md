# Optional guidance sections

A repo opts into extra sections by listing them in its own `.agents-sync.yml`
(see `.agents-sync.example.yml`); each `<name>.md` in this directory is
appended to that repo's managed `AGENTS.md` block.

The language sections (docker, dotnet, go, javascript, python, rust,
typescript) were removed on 2026-11-03 because no repo used them: `repos.yml`
has `default_sections: []` and no repo carries a `.agents-sync.yml`. Restore
any of them from git history (the commit before the removal) if a repo opts
in.
