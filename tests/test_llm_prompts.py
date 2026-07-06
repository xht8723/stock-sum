"""LLM prompt builder tests."""

from stock_sum.llm.prompts import build_trading_summary_messages


def test_trading_summary_prompt_preserves_source_separation() -> None:
    messages = build_trading_summary_messages(
        {
            "report_type": "social",
            "sources": {
                "x": [{"handle": "aleabitoreddit", "posts": [{"id": "x1", "text": "x text"}]}],
                "reddit": [{"subreddit": "wallstreetbets", "posts": [{"id": "r1", "title": "post"}]}],
            },
            "media": {"m1": {"source_ref": "r1", "remote_url": "https://example.com/image.jpg"}},
        },
        instructions="Focus on market-moving items.",
    )

    assert messages[0]["role"] == "system"
    assert "Return valid JSON only" in messages[0]["content"]
    assert "Additional instructions: Focus on market-moving items." in messages[1]["content"]
    assert '"x"' in messages[1]["content"]
    assert '"reddit"' in messages[1]["content"]
    assert "DEEPSEEK_API_KEY" not in messages[1]["content"]


def test_trading_summary_prompt_requests_expected_sections() -> None:
    messages = build_trading_summary_messages({"sources": {"x": [], "reddit": []}})

    user_prompt = messages[1]["content"]
    assert "X reports grouped by user" in user_prompt
    assert "x_reports" in user_prompt
    assert "reddit_report" in user_prompt
    assert "overall_summary" in user_prompt
    assert "comments_sentiment" in user_prompt
    assert "media_observations" in user_prompt
    assert "risks_or_uncertainties" in user_prompt
    assert "notable_sources" in user_prompt
