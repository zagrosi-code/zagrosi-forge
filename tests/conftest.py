"""Keep isolated verified runtimes alive for their complete test-module lifetime."""

import pytest

from runtime_support import runtime_scope


@pytest.fixture(scope="session", autouse=True)
def isolated_native_hosts(tmp_path_factory):
    """Keep host detection and installed-plugin reads independent of the developer's agent session."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))
        for name in ("CLAUDECODE", "CODEX_THREAD_ID", "CLAUDE_CODE_PLUGIN_CACHE_DIR"):
            patch.delenv(name, raising=False)
        yield


@pytest.fixture(scope="module", autouse=True)
def verified_runtime_scope():
    with runtime_scope():
        yield
