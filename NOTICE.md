# NOTICE

## Attribution

Zagrosi Forge is a Codex-native redesign inspired by Pierce Lamb's MIT-licensed
Deep Trilogy Claude Code plugins:

| Upstream Project | Repository |
|------------------|------------|
| Deep Project | https://github.com/piercelamb/deep-project |
| Deep Plan | https://github.com/piercelamb/deep-plan |
| Deep Implement | https://github.com/piercelamb/deep-implement |

## Adapted Ideas

The following high-level ideas informed this package:

- decomposing broad project briefs into smaller planning units
- generating implementation plans before code changes
- sectionizing work into implementation-ready markdown files
- encouraging test-first implementation and code review
- preserving durable workflow artifacts on disk

## Zagrosi Forge Changes

This package is not a direct copy of those projects. It replaces
Claude-specific workflow assumptions with Codex-native behavior:

- Codex skill packaging under `skills/`
- resumable state inferred from files on disk
- deterministic Python helper commands in `scripts/zagrosi_skills.py`
- Forge metadata via `FORGE_META`
- quality profiles, strict gates, SARIF/JSONL export, trace exports, and
  dependency-aware section helpers
- backward-compatible parsing and helper aliases for migrated `deep-*` artifacts

## License

Zagrosi Forge is distributed under the MIT License. See [LICENSE](LICENSE).

## Vendored dependency

Zagrosi Forge vendors the selected runtime tree from TOML Kit 0.15.0, licensed
under MIT. The verified upstream source artifact is
`https://files.pythonhosted.org/packages/51/db/03eaf4331631ef6b27d6e3c9b68c54dc6f0d63d87201fed600cc409307fd/tomlkit-0.15.0.tar.gz`
with SHA-256
`7d1a9ecba3086638211b13814ea79c90dd54dd11993564376f3aa92271f5c7a3`.
Its copied license has SHA-256
`f2f9b460ba719da6626add264d3782f275a4ff7aab677beda08b330911e23adb`.
See `src/zagrosi_forge/_vendor/vendor-receipt.json` for the exact selected-file
hashes and deterministic import transformation.
