import json
from unittest.mock import patch

from backend.app.config import DEFAULT_MODEL, DEFAULTS, load_config


def test_load_config_no_file(tmp_path):
    config_dir = tmp_path / ".bolodb"
    config_file = config_dir / "config.json"

    with (
        patch("backend.app.config.CONFIG_DIR", config_dir),
        patch("backend.app.config.CONFIG_FILE", config_file),
        patch.dict("os.environ", {"GEMINI_API_KEY": ""}),
    ):
        cfg = load_config()
        assert cfg == dict(DEFAULTS)
        assert config_dir.exists()
        assert not config_file.exists()


def test_load_config_with_valid_file(tmp_path):
    config_dir = tmp_path / ".bolodb"
    config_file = config_dir / "config.json"
    config_dir.mkdir()

    custom_data = {
        "provider": "gemini",
        "model": "gemini-2.5-pro",
        "api_keys": {"gemini": "AIza-test-123"},
    }
    config_file.write_text(json.dumps(custom_data))

    with (
        patch("backend.app.config.CONFIG_DIR", config_dir),
        patch("backend.app.config.CONFIG_FILE", config_file),
    ):
        cfg = load_config()
        assert cfg["provider"] == "gemini"
        assert cfg["model"] == "gemini-2.5-pro"
        assert cfg["api_keys"]["gemini"] == "AIza-test-123"


def test_load_config_migrates_old_provider_config(tmp_path):
    """Configs written before the Gemini-only switch are coerced to Gemini."""
    config_dir = tmp_path / ".bolodb"
    config_file = config_dir / "config.json"
    config_dir.mkdir()

    old_data = {
        "provider": "claude",
        "model": "claude-3-opus",
        "ollama_url": "http://localhost:11434",
        "api_keys": {"claude": "sk-ant-123", "openai": "sk-123"},
    }
    config_file.write_text(json.dumps(old_data))

    with (
        patch("backend.app.config.CONFIG_DIR", config_dir),
        patch("backend.app.config.CONFIG_FILE", config_file),
    ):
        cfg = load_config()
        assert cfg["provider"] == "gemini"
        assert cfg["model"] == DEFAULT_MODEL
        # old vendor keys are dropped, gemini key slot exists
        assert set(cfg["api_keys"]) == {"gemini"}


def test_load_config_env_var_fallback(tmp_path):
    config_dir = tmp_path / ".bolodb"
    config_file = config_dir / "config.json"

    with (
        patch("backend.app.config.CONFIG_DIR", config_dir),
        patch("backend.app.config.CONFIG_FILE", config_file),
        patch.dict("os.environ", {"GEMINI_API_KEY": "AIza-from-env"}),
    ):
        cfg = load_config()
        assert cfg["api_keys"]["gemini"] == "AIza-from-env"


def test_load_config_saved_key_beats_env_var(tmp_path):
    config_dir = tmp_path / ".bolodb"
    config_file = config_dir / "config.json"
    config_dir.mkdir()
    config_file.write_text(json.dumps({"api_keys": {"gemini": "AIza-saved"}}))

    with (
        patch("backend.app.config.CONFIG_DIR", config_dir),
        patch("backend.app.config.CONFIG_FILE", config_file),
        patch.dict("os.environ", {"GEMINI_API_KEY": "AIza-from-env"}),
    ):
        cfg = load_config()
        assert cfg["api_keys"]["gemini"] == "AIza-saved"


def test_load_config_invalid_json(tmp_path):
    config_dir = tmp_path / ".bolodb"
    config_file = config_dir / "config.json"
    config_dir.mkdir()

    config_file.write_text("invalid json {")

    with (
        patch("backend.app.config.CONFIG_DIR", config_dir),
        patch("backend.app.config.CONFIG_FILE", config_file),
        patch.dict("os.environ", {"GEMINI_API_KEY": ""}),
    ):
        cfg = load_config()
        assert cfg == dict(DEFAULTS)


def test_save_config(tmp_path):
    config_dir = tmp_path / ".bolodb"
    config_file = config_dir / "config.json"

    with (
        patch("backend.app.config.CONFIG_DIR", config_dir),
        patch("backend.app.config.CONFIG_FILE", config_file),
    ):
        from backend.app.config import save_config

        save_config({"test": "data"})

        assert config_dir.exists()
        assert config_file.exists()
        assert json.loads(config_file.read_text()) == {"test": "data"}


def test_public_config():
    from backend.app.config import public_config

    cfg = {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "api_keys": {"gemini": "AIza-secret"},
        "last_db_url": "sqlite:///test.db",
    }

    pub = public_config(cfg)
    assert pub["provider"] == "gemini"
    assert pub["model"] == "gemini-2.5-flash"
    assert pub["api_keys_set"]["gemini"] == "set"
    assert "AIza-secret" not in str(pub)
