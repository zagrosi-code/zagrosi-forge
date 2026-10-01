"""Keep isolated verified runtimes alive for their complete test-module lifetime."""

import pytest

from runtime_support import runtime_scope


@pytest.fixture(scope="module", autouse=True)
def verified_runtime_scope():
    with runtime_scope():
        yield
