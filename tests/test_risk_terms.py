"""Section risk estimates match whole risk words outside compatibility metadata."""

import pytest

from forge_test_helpers import ROOT
from runtime_support import load_runtime


COMPATIBILITY = (
    "## Compatibility\n```json\n"
    '{"version": 1, "mode": "required", "source_paths": ["src/labels.py"], '
    '"check_paths": ["tests/test_labels.py"], '
    '"check_provenance": {"source": "existing", "author": "Repository maintainers"}}\n'
    "```\n"
)


@pytest.fixture(scope="module")
def forge():
    return load_runtime(ROOT / "scripts/zagrosi_skills.py")


def metrics(forge, tmp_path, text):
    path = tmp_path / "section-01-labels.md"
    path.write_text(text)
    return forge.sections.section_metrics("section-01-labels", path, {})


def test_risk_terms_ignore_author_and_metadata(forge, tmp_path):
    text = "# section-01-labels\n\nGoal: keep file metadata and the author list.\n\n" + COMPATIBILITY
    assert metrics(forge, tmp_path, text)["risk"] == "low"


@pytest.mark.parametrize(("phrase", "terms"), [
    ("Rotate auth tokens.", ["auth", "tokens"]),
    ("Add authentication and authorization.", ["authentication", "authorization"]),
    ("Only authorized users; check permissions before the migration.", ["authorized", "migration", "permissions"]),
    ("Move data into the database.", ["data", "database"]),
    ("Keep secrets private for security and privacy.", ["privacy", "secrets", "security"]),
    ("Authenticate users via OAuth, hash passwords, encrypt credentials and migrate the table.",
     ["authenticate", "credentials", "encrypt", "migrate", "oauth", "passwords"]),
    ("Record each payment.", ["payment"]),
    ("Use OAuth2 and auth0 instead of insecure cookies.", ["auth0", "insecure", "oauth2"]),
])
def test_risk_terms_match_whole_words_and_plurals(forge, tmp_path, phrase, terms):
    assert forge.sections.risk_terms(phrase) == terms
    assert metrics(forge, tmp_path, f"# section-01-labels\n\nGoal: {phrase}\n")["risk"] == "medium"


def test_risk_terms_skip_partial_words(forge):
    assert forge.sections.risk_terms("The author tokenized metadata; authority and datapoints stay low.") == []
