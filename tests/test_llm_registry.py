"""LLM provider registry metadata tests."""

from stock_sum.llm.registry import get_llm_provider, list_llm_providers


def test_deepseek_provider_metadata() -> None:
    provider = get_llm_provider("deepseek")

    assert provider.provider_id == "deepseek"
    assert provider.display_name == "DeepSeek"
    assert provider.default_model == "deepseek-v4-flash"
    assert provider.api_key_env == "DEEPSEEK_API_KEY"
    assert provider.implemented is True
    assert provider in list_llm_providers()
