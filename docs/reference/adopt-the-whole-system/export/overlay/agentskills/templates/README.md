# Templates

Inert starting points. Nothing here is loaded, linked, locked or listed: the
registry's checks, its lock generator and the plugins read only
`plugins/<plugin>/skills/<skill>/`.

## A new skill

`templates/skill/` holds a skeleton `SKILL.md` and `PURPOSE.md`. To add a skill:

1. Copy the folder to `plugins/<plugin>/skills/<your-skill>/`, set `name:` to
   the folder name, and replace every `<...>` placeholder. The folder name
   never changes once the skill has shipped: a consumer's `skills.lock` and
   the plugin's command (`/<plugin>:<your-skill>`) are keyed by it.
2. Add the name to that plugin's `skills` list in `scripts/plugin_layout.json`.
3. Run `python3 scripts/generate_readme_table.py` to refresh the README table,
   and re-pin `skills.lock` with `scripts/generate_skills_lock.py` once the
   skill is committed (its docstring says how).
4. Add an entry to `docs/skill-impact.md`, and an eval fixture in the
   skills-evals repo (`evals/<your-skill>/`). The existing skill,
   `workflow-path-audit`, is the worked example of all of these.
