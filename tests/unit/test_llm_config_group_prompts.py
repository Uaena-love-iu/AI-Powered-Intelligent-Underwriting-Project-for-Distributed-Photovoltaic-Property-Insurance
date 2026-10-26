from app.api.llm_config_routes import get_llm_config


def test_group_aisle_prompt_is_separate_and_populated():
    config = get_llm_config()
    assert config["prompts"]["aisle_group"]
    assert "【组图通道阻塞观察】" in config["prompts"]["aisle_group"]
    assert "【组图通道阻塞观察】" not in config["prompts"]["installation"]
    assert "aisle_localization" not in config["prompts"]
    assert "【模拟图片证据边界】" in config["prompts"]["installation"]
    assert "【模拟图片证据边界】" in config["prompts"]["aisle_group"]
