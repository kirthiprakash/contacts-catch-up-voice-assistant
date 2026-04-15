# Feature: contacts-catch-up-voice-assistant, Property 20: Missing required environment variable causes startup failure

import os
import pytest
from unittest.mock import patch
from hypothesis import given, settings
from hypothesis import strategies as st

from app.config import ConfigurationError, get_settings, _REQUIRED_VARS

# Full set of valid env vars needed for a successful startup
_VALID_ENV = {
    "VAPI_API_KEY": "vapi-key",
    "VAPI_ASSISTANT_ID": "asst-id",
    "VAPI_PHONE_NUMBER_ID": "phone-id",
    "QDRANT_API_KEY": "qdrant-key",
    "QDRANT_ENDPOINT": "https://qdrant.example.com",
    "OPENAI_API_KEY": "openai-key",
    "OPENAI_BASE_URL": "https://api.openai.com/v1",
    "OPENAI_MODEL": "gpt-4o",
}


@given(missing_var=st.sampled_from(_REQUIRED_VARS))
@settings(max_examples=100)
def test_missing_required_env_var_raises_configuration_error(missing_var):
    """
    Property 20: For any required environment variable, if that variable is absent
    from the environment, get_settings() shall raise ConfigurationError and not proceed.
    Validates: Requirements 10.2
    """
    env_without_missing = {k: v for k, v in _VALID_ENV.items() if k != missing_var}

    # Patch os.environ completely so no real env vars or .env file can interfere
    with patch.dict(os.environ, env_without_missing, clear=True):
        with pytest.raises(ConfigurationError) as exc_info:
            get_settings()

    # The error message must mention the missing variable name
    assert missing_var in str(exc_info.value)
