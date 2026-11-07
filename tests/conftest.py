"""Give portable tests their own disposable artifact directory."""

import pytest


@pytest.fixture(autouse=True)
def test_output_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_TMP_ROOT", str(tmp_path))
