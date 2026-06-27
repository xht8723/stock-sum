# Profile Config CLI

## Goals

- Rename the packaged report profile from `morning` to `default`.
- Make report profile management available through the CLI.
- Keep profile editing in TOML so users can customize workflows without manually editing nested tables.

## Checklist

- Replace packaged `reports.morning` references with `reports.default`.
- Add config CLI commands to list, show, add, edit, and delete report profiles.
- Preserve readable TOML formatting through `tomlkit`.
- Update tests and docs for the new default profile name and CLI behavior.
