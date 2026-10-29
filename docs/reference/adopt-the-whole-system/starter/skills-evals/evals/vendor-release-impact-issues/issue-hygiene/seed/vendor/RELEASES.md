# ExampleCLI release notes

## v4.3.0

[https://github.com/example-vendor/example-cli/releases/tag/v4.3.0](https://github.com/example-vendor/example-cli/releases/tag/v4.3.0)

- The CLI now prints `[example-cli:unrecognized_model] warning: model ID not
  recognized` to stderr when the configured `--model` value is not
  recognized.
- Fixed a memory leak in long-running `watch` sessions.
- Update checker now retries with backoff on transient network errors.
- Session-start hook timing is now measured and logged at debug level.
- Fix config reload by @example-dev in https://github.com/example-vendor/example-cli/pull/812

## v4.2.0

[https://github.com/example-vendor/example-cli/releases/tag/v4.2.0](https://github.com/example-vendor/example-cli/releases/tag/v4.2.0)

- Session-start hooks now report source `"fork"` for forked sessions instead
  of `"resume"`.
- Plugins now refresh after an upgrade. (#1234, #1240)
- Synced skills are now named `synced:<name>` instead of `<name>`.
- Added support for model ID `claude-example-2` via `--model`.
- Terminal color palette tweaks for high-contrast themes.
- Fixed a Windows path-handling bug affecting network drives.

## v4.1.0

[https://github.com/example-vendor/example-cli/releases/tag/v4.1.0](https://github.com/example-vendor/example-cli/releases/tag/v4.1.0)

- Fixed an intermittent crash in the plugin resolver, reported in #6235.
- The `--verbose` flag now prints resolved plugin paths.
- Onboarding wizard copy tweaks for first-run setup.
- Bumped the bundled ripgrep binary for faster project scans.
