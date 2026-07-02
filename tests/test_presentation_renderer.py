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
                            "tags": ["nbis", "growth", "cloud", "revenue", "risk"],
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
                        "comment_sentiment_counts": {"bullish": 1, "bearish": 2, "mixed": 0, "neutral": 1, "unclear": 0},
                        "sentiment": "bearish",
                        "tags": ["mstr", "bitcoin", "leverage", "risk", "sentiment"],
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
    }


def _house_response() -> dict:
    response = _grouped_response()
    response["house_ptr"] = [
        {
            "doc_id": "20024228",
            "year": 2026,
            "name": "Jane Doe",
            "status": "Member",
            "state": "CA",
            "filing_date": "2026-06-30",
            "pdf_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf",
            "asset": "AAPL",
            "transaction_type": "Purchase",
            "transaction_date": "2026-06-20",
            "amount": "$1,001 - $15,000",
            "raw_cells": ["AAPL", "Purchase", "2026-06-20", "$1,001 - $15,000"],
        },
        {
            "doc_id": "20024229",
            "year": 2026,
            "name": "John Doe",
            "status": "Member",
            "state": "NY",
            "filing_date": "2026-06-29",
            "pdf_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024229.pdf",
            "asset": "MSFT",
            "transaction_type": "S (partial)",
            "transaction_date": "2026-06-19",
            "amount": "$15,001 - $50,000",
            "raw_cells": ["MSFT", "S (partial)", "2026-06-19", "$15,001 - $50,000"],
        }
    ]
    response["summary"]["house_ptr"] = response["house_ptr"]
    return response


def test_html_renderer_outputs_all_sections_and_escapes_html() -> None:
    response = _response()
    response["summary"]["x_signals"][0]["claim"] = "<script>alert(1)</script>"

    rendered = PresentationRenderer().render(response, mode="html", detail="full")

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
    rendered = PresentationRenderer().render(_response(), mode="markdown", detail="full")

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
    rendered = PresentationRenderer().render(_grouped_response(), mode="discord", detail="full")

    assert rendered.startswith("**Market Social Digest**")
    assert "**Social Media Sentiment**" in rendered
    assert "__Medium Importance__" in rendered
    assert "\n---\n- **NBIS growth post**" in rendered
    assert "**NBIS growth post**" in rendered
    assert "> **Summary:** Compares NBIS to HOOD growth." in rendered
    assert "**Takeaway:** _High-conviction but speculative._" in rendered
    assert "**Tags:** `nbis` `growth` `cloud` `revenue` `risk`" in rendered
    assert "[Read source](https://x.com/aleabitoreddit/status/1)" in rendered
    assert "[Image 1](https://cdn.example/x-chart.jpg)" in rendered
    assert "| Politician |" not in rendered
    assert "x1" not in rendered
    assert "m1" not in rendered


def test_text_renderer_preserves_refs_and_urls() -> None:
    rendered = PresentationRenderer().render(_response(), mode="text", detail="full")

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

    assert "No social signals at this detail level." in rendered


def test_renderer_handles_missing_sections() -> None:
    rendered = PresentationRenderer().render({"summary": {"executive_summary": ["only summary"]}}, mode="html")

    assert "only summary" not in rendered
    assert "No social signals at this detail level." in rendered


def test_renderer_rejects_unknown_mode() -> None:
    with pytest.raises(PresentationRenderError):
        PresentationRenderer().render(_response(), mode="pdf")


def test_renderer_detail_minimum_includes_high_only() -> None:
    response = _response()
    response["summary"]["x_signals"][0]["confidence"] = "high"

    rendered = PresentationRenderer().render(response, mode="markdown")

    assert "### High Importance" in rendered
    assert "X claim" in rendered
    assert "Reddit claim" not in rendered
    assert "### Medium Importance" not in rendered
    assert "### Low Importance" not in rendered


def test_renderer_detail_medium_includes_high_and_medium() -> None:
    response = _response()
    response["summary"]["x_signals"][0]["confidence"] = "high"

    rendered = PresentationRenderer().render(response, mode="discord", detail="medium")

    assert "__High Importance__" in rendered
    assert "__Medium Importance__" in rendered
    assert "X claim" in rendered
    assert "Reddit claim" in rendered
    assert "__Low Importance__" not in rendered


def test_renderer_detail_full_includes_all_importance_levels() -> None:
    response = _response()
    response["summary"]["x_signals"].append(
        {
            "source_ref": "x2",
            "claim": "High claim",
            "confidence": "high",
            "urls": ["https://x.com/example/status/2"],
        }
    )

    rendered = PresentationRenderer().render(response, mode="text", detail="full")

    assert "HIGH IMPORTANCE" in rendered
    assert "MEDIUM IMPORTANCE" in rendered
    assert "LOW IMPORTANCE" in rendered
    assert "High claim" in rendered
    assert "Reddit claim" in rendered
    assert "X claim" in rendered


def test_renderer_minimum_empty_state_when_no_high_importance_items() -> None:
    rendered = PresentationRenderer().render(_grouped_response(), mode="html")

    assert "No social signals at this detail level." in rendered
    assert "NBIS growth post" not in rendered


def test_renderer_rejects_unknown_detail() -> None:
    with pytest.raises(PresentationRenderError):
        PresentationRenderer().render(_response(), mode="html", detail="verbose")


def test_grouped_markdown_renderer_uses_requested_heading_layout() -> None:
    rendered = PresentationRenderer().render(_grouped_response(), mode="markdown", detail="full")

    assert rendered.startswith("# Market Social Digest")
    assert "## Social Media Sentiment" in rendered
    assert "### Medium Importance" in rendered
    assert "#### NBIS growth post" in rendered
    assert "#### MSTR thread" in rendered
    assert "Comments:" in rendered
    assert "Comment stats:" not in rendered
    assert "- Stats: bullish: 1, bearish: 2, mixed: 0, neutral: 1, unclear: 0" in rendered
    assert "Tags:" in rendered
    assert rendered.index("- Stats: bullish: 1") < rendered.index("- Tags: mstr")
    assert rendered.index("- Tags: mstr") < rendered.index("- Source: [Read source](https://www.reddit.com/r/test/comments/1/)")
    assert "![Post image](https://cdn.example/x-chart.jpg)" in rendered
    assert "x1" not in rendered
    assert "r1" not in rendered
    assert "m1" not in rendered
    assert "## Media Observations" not in rendered
    assert "## Metadata" not in rendered


def test_discord_reddit_details_are_not_indented_or_duplicated() -> None:
    rendered = PresentationRenderer().render(_grouped_response(), mode="discord", detail="full")

    assert "\n**Comments:** Comments mostly agree with jokes." in rendered
    assert "\n**Stats:** `bullish 1` · `bearish 2` · `mixed 0` · `neutral 1` · `unclear 0`" in rendered
    assert "\n**Tags:** `mstr` `bitcoin` `leverage` `risk` `sentiment`" in rendered
    assert "\n**Takeaway:**" in rendered
    assert "\n**Links:** [Read source](https://www.reddit.com/r/test/comments/1/)" in rendered
    assert "Comment stats:" not in rendered
    assert rendered.index("**Stats:**") < rendered.index("**Tags:** `mstr`")
    assert rendered.index("**Tags:** `mstr`") < rendered.index("**Links:** [Read source](https://www.reddit.com/r/test/comments/1/)")
    assert "\n  Comments:" not in rendered
    assert "\n  Tags:" not in rendered


def test_discord_count_only_comments_are_not_duplicated() -> None:
    response = _grouped_response()
    response["summary"]["reddit_report"]["posts"][0]["comments_sentiment"] = "bullish: 1, bearish: 2, mixed: 0, neutral: 1, unclear: 0"

    rendered = PresentationRenderer().render(response, mode="discord", detail="full")

    assert "\n**Comments:** bullish: 1" not in rendered
    assert "\n**Stats:** `bullish 1` · `bearish 2` · `mixed 0` · `neutral 1` · `unclear 0`" in rendered


def test_grouped_html_renderer_uses_user_and_post_sections() -> None:
    rendered = PresentationRenderer().render(_grouped_response(), mode="html", detail="full")

    assert "Social Media Sentiment" in rendered
    assert "Medium Importance" in rendered
    assert "aleabitoreddit" in rendered
    assert "NBIS growth post" in rendered
    assert "Comments" in rendered
    assert "nbis, growth, cloud, revenue, risk" in rendered
    assert "Comments mostly agree with jokes." in rendered
    assert "<strong>Stats:</strong> bullish: 1, bearish: 2, mixed: 0, neutral: 1, unclear: 0" in rendered
    assert "Comment Stats" not in rendered
    assert 'src="https://cdn.example/x-chart.jpg"' in rendered
    assert "Trading Info" not in rendered
    assert "Media Observations" not in rendered
    assert "Risks And Uncertainties" not in rendered


def test_grouped_text_renderer_is_social_only() -> None:
    rendered = PresentationRenderer().render(_grouped_response(), mode="text", detail="full")

    assert "TRADING INFO" not in rendered
    assert "NBIS growth post" in rendered
    assert "MSTR thread" in rendered
    assert "  Stats: bullish: 1, bearish: 2, mixed: 0, neutral: 1, unclear: 0" in rendered
    assert rendered.index("  Stats: bullish: 1") < rendered.index("  Tags: mstr")
    assert rendered.index("  Tags: mstr") < rendered.index("  Source: https://www.reddit.com/r/test/comments/1/")


def test_count_only_comments_are_not_duplicated_in_all_non_discord_modes() -> None:
    response = _grouped_response()
    response["summary"]["reddit_report"]["posts"][0]["comments_sentiment"] = "bullish: 1, bearish: 2, mixed: 0, neutral: 1, unclear: 0"

    markdown = PresentationRenderer().render(response, mode="markdown", detail="full")
    html = PresentationRenderer().render(response, mode="html", detail="full")
    text = PresentationRenderer().render(response, mode="text", detail="full")

    assert "- **comments_sentiment**: bullish: 1" not in markdown
    assert "Comments Sentiment" not in html
    assert "- comments_sentiment: bullish: 1" not in text
    assert "- Stats: bullish: 1, bearish: 2, mixed: 0, neutral: 1, unclear: 0" in markdown
    assert "<strong>Stats:</strong> bullish: 1, bearish: 2, mixed: 0, neutral: 1, unclear: 0" in html
    assert "  Stats: bullish: 1, bearish: 2, mixed: 0, neutral: 1, unclear: 0" in text


def test_renderer_outputs_pipeline_warnings_in_all_modes() -> None:
    response = _grouped_response()
    response["pipeline_warnings"] = [
        {
            "section": "optional_enrichment",
            "source_id": "future.source",
            "phase": "enrichment",
            "message": "Temporarily unavailable",
            "recoverable": True,
        }
    ]

    html = PresentationRenderer().render(response, mode="html")
    markdown = PresentationRenderer().render(response, mode="markdown")
    discord = PresentationRenderer().render(response, mode="discord")
    text = PresentationRenderer().render(response, mode="text")

    assert "Unavailable Sections" in html
    assert "Temporarily unavailable" in html
    assert "## Unavailable Sections" in markdown
    assert "**Unavailable Sections**" in discord
    assert "UNAVAILABLE SECTIONS" in text


def test_renderer_outputs_house_ptr_disclosures_in_all_modes() -> None:
    response = _house_response()

    html = PresentationRenderer().render_trading(response, mode="html")
    markdown = PresentationRenderer().render_trading(response, mode="markdown")
    discord = PresentationRenderer().render_trading(response, mode="discord")
    text = PresentationRenderer().render_trading(response, mode="text")

    assert "Official Trading Disclosures" in html
    assert "<td>Jane Doe</td>" in html
    assert "AAPL" in html
    assert ">Purchase<" in html
    assert ">Sell (partial)<" in html
    assert 'href="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf"' in html
    assert "## Official Trading Disclosures" in markdown
    assert "Action: Purchase" in markdown
    assert "Action: Sell (partial)" in markdown
    assert "[Read PDF](https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf)" in markdown
    assert "**Official Trading Disclosures**" in discord
    assert "Action Purchase" in discord
    assert "Action Sell (partial)" in discord
    assert "[PDF](https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf)" in discord
    assert "OFFICIAL TRADING DISCLOSURES" in text
    assert "Action: Purchase" in text
    assert "Action: Sell (partial)" in text
    assert "Source: https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf" in text
