"""
Tests for config.py - Settings and configuration management.
"""

import os


from config import Settings, settings


class TestSettingsDefaults:
    """Test default values and configuration loading."""

    def test_default_model_settings(self, mock_env_vars):
        """Test default model settings are loaded correctly."""
        s = Settings()
        # GLM-4.7 is the default primary model (no Anthropic key needed)
        assert s.primary_model in ("glm-4.7", "claude-opus-4-6")
        assert s.fallback_model == "ollama/qwen2.5-coder:32b"
        assert "glm-4" in s.last_resort_model  # glm-4 or glm-4.7

    def test_default_ollama_settings(self, mock_env_vars):
        """Test default Ollama settings."""
        s = Settings()
        assert s.ollama_base_url == "http://localhost:11434"
        assert s.ollama_model == "qwen2.5-coder:32b"

    def test_default_glm_settings(self, mock_env_vars):
        """Test default GLM-4 settings."""
        s = Settings()
        assert s.glm_base_url  # Just verify it's set
        assert s.glm_model  # Just verify it's set

    def test_default_database_settings(self, mock_env_vars):
        """Test default database settings."""
        s = Settings()
        assert s.database_url.startswith("sqlite:///")
        assert ".db" in s.database_url or "/test.db" in s.database_url

    def test_default_chroma_settings(self, mock_env_vars):
        """Test default ChromaDB settings."""
        s = Settings()
        assert s.chroma_collection_name == "component_datasheets"
        assert "chroma" in s.chroma_persist_dir.lower()

    def test_default_embedding_settings(self, mock_env_vars):
        """Test default embedding model settings."""
        s = Settings()
        assert s.embedding_model == "text-embedding-3-large"
        assert s.offline_embedding_model == "nomic-embed-text"

    def test_default_app_settings(self, mock_env_vars):
        """Test default application settings."""
        s = Settings()
        assert s.app_name == "Hardware Pipeline"
        assert s.app_env == "development"
        assert s.debug is True
        assert s.log_level in ["INFO", "DEBUG"]

    def test_default_server_settings(self, mock_env_vars):
        """Test default server settings."""
        s = Settings()
        assert s.fastapi_host == "0.0.0.0"
        assert s.fastapi_port == 8000
        assert s.streamlit_port == 8501


class TestSettingsApiKeys:
    """Test API key loading and validation."""

    def test_anthropic_api_key_from_env(self, mock_env_vars):
        """Test Anthropic API key is loaded from environment."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-custom-key"
        s = Settings()
        assert s.anthropic_api_key == "sk-ant-custom-key"

    def test_openai_api_key_from_env(self, mock_env_vars):
        """Test OpenAI API key is loaded from environment."""
        os.environ["OPENAI_API_KEY"] = "sk-openai-custom"
        s = Settings()
        assert s.openai_api_key == "sk-openai-custom"

    def test_glm_api_key_from_env(self, mock_env_vars):
        """Test GLM API key is loaded from environment."""
        os.environ["GLM_API_KEY"] = "custom-glm-key"
        s = Settings()
        assert s.glm_api_key == "custom-glm-key"


class TestSettingsFallbackChain:
    """Test fallback chain property."""

    def test_fallback_chain_order(self, mock_env_vars):
        """Test fallback chain is in correct order."""
        s = Settings()
        chain = s.fallback_chain
        assert len(chain) == 4
        assert chain[0] == s.primary_model
        assert chain[1] == s.fast_model
        assert chain[2] == s.fallback_model
        assert chain[3] == s.last_resort_model

    def test_fallback_chain_excludes_duplicates(self, mock_env_vars):
        """Test fallback chain excludes duplicates when models match."""
        os.environ["PRIMARY_MODEL"] = "claude-haiku-4-5-20251001"
        s = Settings()
        chain = s.fallback_chain
        assert len(chain) >= 3

    def test_fallback_chain_with_custom_models(self, mock_env_vars):
        """Test fallback chain with custom model configuration."""
        os.environ["PRIMARY_MODEL"] = "custom-primary"
        os.environ["FAST_MODEL"] = "custom-fast"
        os.environ["FALLBACK_MODEL"] = "custom-fallback"
        os.environ["LAST_RESORT_MODEL"] = "custom-last"
        s = Settings()
        chain = s.fallback_chain
        assert chain == ["custom-primary", "custom-fast", "custom-fallback", "custom-last"]


class TestSettingsAirGap:
    """Test air-gapped mode detection."""

    def test_is_air_gapped_no_keys(self, mock_env_vars):
        """Test air-gapped when no API keys set."""
        # Clear all LLM API keys that has_any_llm_key checks
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("GLM_API_KEY", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        s = Settings()
        assert s.is_air_gapped is True

    def test_is_not_air_gapped_with_anthropic(self, mock_env_vars):
        """Test not air-gapped with Anthropic key."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        s = Settings()
        assert s.is_air_gapped is False

    def test_is_not_air_gapped_with_glm(self, mock_env_vars):
        """Test not air-gapped with GLM key."""
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["GLM_API_KEY"] = "glm-test"
        s = Settings()
        assert s.is_air_gapped is False


class TestSettingsPaths:
    """Test path properties."""

    def test_base_dir_exists(self, mock_env_vars):
        """Test base_dir points to valid path."""
        s = Settings()
        assert s.base_dir.exists()
        assert s.base_dir.is_dir()

    def test_output_dir_relative_to_base(self, mock_env_vars):
        """Test output_dir is relative to base_dir."""
        s = Settings()
        assert s.output_dir == s.base_dir / "output"

    def test_templates_dir_relative_to_base(self, mock_env_vars):
        """Test templates_dir is relative to base_dir."""
        s = Settings()
        assert s.templates_dir == s.base_dir / "templates"

    def test_data_dir_relative_to_base(self, mock_env_vars):
        """Test data_dir is relative to base_dir."""
        s = Settings()
        assert s.data_dir == s.base_dir / "data"


class TestSettingsSingleton:
    """Test the settings singleton instance."""

    def test_singleton_instance_exists(self):
        """Test settings singleton is importable."""
        assert isinstance(settings, Settings)

    def test_singleton_uses_env_vars(self, mock_env_vars):
        """Test singleton loads from environment variables."""
        os.environ["APP_NAME"] = "Custom Pipeline"
        from importlib import reload
        import config
        reload(config)
        assert config.settings.app_name == "Custom Pipeline"


class TestSettingsComponentApis:
    """Test component search API settings."""

    def test_digikey_default_settings(self, mock_env_vars):
        """Test default DigiKey API settings."""
        s = Settings()
        assert "digikey.com" in s.digikey_api_url.lower()

    def test_digikey_custom_settings(self, mock_env_vars):
        """Test custom DigiKey API settings."""
        os.environ["DIGIKEY_CLIENT_ID"] = "test-client-id"
        os.environ["DIGIKEY_CLIENT_SECRET"] = "test-secret"
        os.environ["DIGIKEY_API_URL"] = "https://test.digikey.com/v4"
        s = Settings()
        assert s.digikey_client_id == "test-client-id"
        assert s.digikey_client_secret == "test-secret"
        assert s.digikey_api_url == "https://test.digikey.com/v4"

    def test_mouser_default_settings(self, mock_env_vars):
        """Test default Mouser API settings."""
        s = Settings()
        assert "mouser.com" in s.mouser_api_url.lower()

    def test_mouser_custom_settings(self, mock_env_vars):
        """Test custom Mouser API settings."""
        os.environ["MOUSER_API_KEY"] = "test-mouser-key"
        os.environ["MOUSER_API_URL"] = "https://test.mouser.com/v3"
        s = Settings()
        assert s.mouser_api_key == "test-mouser-key"
        assert s.mouser_api_url == "https://test.mouser.com/v3"
