import pytest


@pytest.fixture(autouse=True)
def _no_host_env_file(tmp_path, monkeypatch):
    """Never read the developer's ~/.config/aichat/aichat.env in tests."""
    monkeypatch.setenv("AICHAT_ENV_FILE", str(tmp_path / "no-aichat.env"))
