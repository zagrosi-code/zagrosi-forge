# Releases

## 0.3.0 — shared quality workflows

- One entry skill and a dedicated cleanup skill, shared by Codex and Claude Code.
- Explicit verification outcomes and fresh integration evidence; failed checks cannot establish completion.
- Ownership-aware parallel work, portable bundled checks, and actionable context-budget recovery.
- Optional selected-provider reviews through existing CLI authentication.
- Native skill discovery and upgrade checks, plus broader behavioral evaluation fixtures.

`pyproject.toml` is the version source. Run `python3 tools/sync_release.py` after
changing it; CI rejects mismatched Codex or Claude manifests. Changes intended
for distribution require a new version: a marketplace refresh alone may leave
an installed same-version cache unchanged.

`python3 tools/native_plugin_smoke.py --require --upgrade` exercises both
installed CLIs with disposable settings and no model calls. It checks native
discovery, installed files, references, doctor, a version change, and whether a
same-version content change leaves stale bytes. Local-source upgrade checks do
not establish hosted marketplace propagation or model-led skill quality.

## 0.2.0 — initial shared host package

Compact project, plan and implementation workflows; Claude Code native metadata;
portable package, recovery and installer checks. Earlier Codex builds used a
timestamp suffix. Historical trial reports remain unchanged; they do not prove
general speed or code-quality gains.
