# ExampleCLI release notes

## v4.3.0

[https://github.com/example-vendor/example-cli/releases/tag/v4.3.0](https://github.com/example-vendor/example-cli/releases/tag/v4.3.0)

- `example-cli check` now exits 2 instead of 1 on configuration errors; scripts written against 4.2.0 or earlier that test for exit 1 need updating.
- Faster startup when many plugins are installed.
- Debug logs now include the session ID.

## v4.2.0

[https://github.com/example-vendor/example-cli/releases/tag/v4.2.0](https://github.com/example-vendor/example-cli/releases/tag/v4.2.0)

- Session-start hooks now report source "fork" for forked sessions; matchers written for v4.1.0 that list only startup|resume no longer fire for them.
- Terminal color palette tweaks for high-contrast themes.
- Fixed a Windows path-handling bug affecting network drives.

## v4.1.0

[https://github.com/example-vendor/example-cli/releases/tag/v4.1.0](https://github.com/example-vendor/example-cli/releases/tag/v4.1.0)

- The `--verbose` flag now prints resolved plugin paths.
- Onboarding wizard copy tweaks for first-run setup.
