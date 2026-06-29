"""Presentation renderer tests."""

from __future__ import annotations

import json

import pytest

from stock_sum.reports.presentation import PresentationRenderError, PresentationRenderer


def _response() -> dict:
    return {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "summary_text": "{}",
        "summary": {
            "executive_summary": ["Retail sentiment is mixed."],
            "x_signals": [
                {
                    "source_ref": "x1",
                    "claim": "X claim",
                    "interpretation": "X interpretation",
                    "confidence": "low",
                    "urls": ["https://x.com/example/status/1"],
                }
            ],
            "reddit_signals": [
                {
                    "source_ref": "r1",
                    "claim": "Reddit claim",
                    "interpretation": "Reddit interpretation",
                    "confidence": "medium",
                    "urls": ["https://www.reddit.com/r/test/comments/1/"],
                }
            ],
            "media_observations": [{"media_id": "m1", "source_ref": "r1", "observation": "Chart image."}],
            "risks_or_uncertainties": ["Unverified social signals."],
            "notable_sources": [
                {
                    "source_ref": "r1",
                    "url": "https://www.reddit.com/r/test/comments/1/",
                    "reason": "Most detailed thread.",
                }
            ],
            "metadata": {"not_financial_advice": True},
        },
        "input_media": {
            "m1": {
                "source": "reddit",
                "source_ref": "r1",
                "kind": "image",
                "remote_url": "https://cdn.example/chart.png",
            }
        },
        "metadata": {"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}},
    }


def _grouped_response() -> dict:
    return {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "summary": {
            "x_reports": [
                {
                    "handle": "aleabitoreddit",
                    "overall_summary": ["Bullish neocloud thesis."],
                    "posts": [
                        {
                            "source_ref": "x1",
                            "title": "NBIS growth post",
                            "post_summary": "Compares NBIS to HOOD growth.",
                            "sentiment": "bullish",
                            "interpretation": "High-conviction but speculative.",
                            "confidence": "medium",
                            "urls": ["https://x.com/aleabitoreddit/status/1"],
                        }
                    ],
                }
            ],
            "reddit_report": {
                "overall_summary": ["Mixed Reddit sentiment."],
                "posts": [
                    {
                        "source_ref": "r1",
                        "title": "MSTR thread",
                        "post_summary": "Main post is bearish.",
                        "comments_sentiment": "Comments mostly agree with jokes.",
                        "sentiment": "bearish",
                        "confidence": "medium",
                        "urls": ["https://www.reddit.com/r/test/comments/1/"],
                    }
                ],
            },
            "media_observations": [{"media_id": "m1", "source_ref": "x1", "observation": "Chart image."}],
            "risks_or_uncertainties": ["Social signals are noisy."],
            "notable_sources": [{"source_ref": "x1", "url": "https://x.com/aleabitoreddit/status/1", "reason": "Clear thesis."}],
            "metadata": {"not_financial_advice": True},
        },
        "input_media": {
            "m1": {
                "source": "x",
                "source_ref": "x1",
                "kind": "photo",
                "remote_url": "https://cdn.example/x-chart.jpg",
            }
        },
        "capitol_trades": {
            "source_url": "https://www.capitoltrades.com/trades?page=1",
            "cards": [{"label": "TRADES", "value": "36,776"}],
            "trades": [
                {
                    "politician": "Nancy Pelosi",
                    "party": "Democrat",
                    "chamber": "House",
                    "state": "CA",
                    "issuer": "Intel Corp",
                    "ticker": "INTC:US",
                    "published": "24 Jun 2026",
                    "traded": "28 May 2026",
                    "filed_after": "25 days",
                    "owner": "Spouse",
                    "transaction_type": "BUY*",
                    "size": "1M-5M",
                    "price": "N/A",
                }
            ],
        },
    }


def test_html_renderer_outputs_all_sections_and_escapes_html() -> None:
    response = _response()
    response["summary"]["x_signals"][0]["claim"] = "<script>alert(1)</script>"

    rendered = PresentationRenderer().render(response, mode="html")

    assert "<!doctype html>" in rendered
    assert "Social Media Sentiment" in rendered
    assert "Medium Importance" in rendered
    assert "Low Importance" in rendered
    assert "Executive Summary" not in rendered
    assert "Media Observations" not in rendered
    assert "Risks And Uncertainties" not in rendered
    assert "Notable Sources" not in rendered
    assert "Metadata" not in rendered
    assert "Tokens:" not in rendered
    assert 'src="https://cdn.example/chart.png"' in rendered
    assert ">Read source<" in rendered
    assert "m1" not in rendered
    assert "x1" not in rendered
    assert "r1" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered


def test_markdown_renderer_preserves_refs_and_urls() -> None:
    rendered = PresentationRenderer().render(_response(), mode="markdown")

    assert "# Market Social Digest" in rendered
    assert "## Social Media Sentiment" in rendered
    assert "### Low Importance" in rendered
    assert "**X / low**" in rendered
    assert "[Read source](https://x.com/example/status/1)" in rendered
    assert "**Reddit / medium**" in rendered
    assert "![Post image](https://cdn.example/chart.png)" in rendered
    assert "x1" not in rendered
    assert "r1" not in rendered
    assert "m1" not in rendered
    assert "not_financial_advice" not in rendered
    assert "Risks And Uncertainties" not in rendered


def test_discord_markdown_renderer_is_compact_and_clickable() -> None:
    rendered = PresentationRenderer().render(_grouped_response(), mode="discord")

    assert rendered.startswith("**Market Social Digest**")
    assert "**Social Media Sentiment**" in rendered
    assert "__Medium Importance__" in rendered
    assert "**NBIS growth post**" in rendered
    assert "[Read source](https://x.com/aleabitoreddit/status/1)" in rendered
    assert "[Image 1](https://cdn.example/x-chart.jpg)" in rendered
    assert "**Politician Trading Info**" in rendered
    assert "| Politician |" not in rendered
    assert "x1" not in rendered
    assert "m1" not in rendered


def test_text_renderer_preserves_refs_and_urls() -> None:
    rendered = PresentationRenderer().render(_response(), mode="text")

    assert "MARKET SOCIAL DIGEST" in rendered
    assert "SOCIAL MEDIA SENTIMENT" in rendered
    assert "X / low" in rendered
    assert "Reddit / medium" in rendered
    assert "Media: https://cdn.example/chart.png" in rendered
    assert "x1" not in rendered
    assert "r1" not in rendered
    assert "m1" not in rendered
    assert "https://www.reddit.com/r/test/comments/1/" in rendered
    assert "MEDIA OBSERVATIONS" not in rendered


def test_renderer_handles_missing_structured_summary_with_summary_text() -> None:
    response = {"provider": "deepseek", "model": "deepseek-v4-flash", "summary_text": '{"executive_summary":["fallback"]}'}

    rendered = PresentationRenderer().render(response, mode="text")

    assert "No social signals." in rendered


def test_renderer_handles_missing_sections() -> None:
    rendered = PresentationRenderer().render({"summary": {"executive_summary": ["only summary"]}}, mode="html")

    assert "only summary" not in rendered
    assert "No social signals." in rendered


def test_renderer_rejects_unknown_mode() -> None:
    with pytest.raises(PresentationRenderError):
        PresentationRenderer().render(_response(), mode="pdf")


def test_grouped_markdown_renderer_uses_requested_heading_layout() -> None:
    rendered = PresentationRenderer().render(_grouped_response(), mode="markdown")

    assert rendered.startswith("# Market Social Digest")
    assert "## Social Media Sentiment" in rendered
    assert "### Medium Importance" in rendered
    assert "#### NBIS growth post" in rendered
    assert "#### MSTR thread" in rendered
    assert "Comments:" in rendered
    assert "![Post image](https://cdn.example/x-chart.jpg)" in rendered
    assert "x1" not in rendered
    assert "r1" not in rendered
    assert "m1" not in rendered
    assert "## Media Observations" not in rendered
    assert "## Metadata" not in rendered


def test_grouped_html_renderer_uses_user_and_post_sections() -> None:
    rendered = PresentationRenderer().render(_grouped_response(), mode="html")

    assert "Social Media Sentiment" in rendered
    assert "Medium Importance" in rendered
    assert "aleabitoreddit" in rendered
    assert "NBIS growth post" in rendered
    assert "Comments" in rendered
    assert 'src="https://cdn.example/x-chart.jpg"' in rendered
    assert "Politician Trading Info" in rendered
    assert "Nancy Pelosi" in rendered
    assert "BUY*" in rendered
    assert "36,776" not in rendered
    assert "Media Observations" not in rendered
    assert "Risks And Uncertainties" not in rendered


def test_grouped_text_renderer_includes_capitol_trades() -> None:
    rendered = PresentationRenderer().render(_grouped_response(), mode="text")

    assert "POLITICIAN TRADING INFO" in rendered
    assert "Nancy Pelosi (Democrat | House | CA) BUY* Intel Corp" in rendered
    assert "TRADES: 36,776" not in rendered


def test_renderer_outputs_pipeline_warnings_in_all_modes() -> None:
    response = _grouped_response()
    response["pipeline_warnings"] = [
        {
            "section": "capitol_trades",
            "source_id": "https://www.capitoltrades.com/trades?page=1",
            "phase": "scraping_capitol_trades",
            "message": "Vercel checkpoint",
            "recoverable": True,
        }
    ]

    html = PresentationRenderer().render(response, mode="html")
    markdown = PresentationRenderer().render(response, mode="markdown")
    discord = PresentationRenderer().render(response, mode="discord")
    text = PresentationRenderer().render(response, mode="text")

    assert "Unavailable Sections" in html
    assert "Vercel checkpoint" in html
    assert "## Unavailable Sections" in markdown
    assert "**Unavailable Sections**" in discord
    assert "UNAVAILABLE SECTIONS" in text
