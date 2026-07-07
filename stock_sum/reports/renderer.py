"""Presentation renderer dispatch class."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from stock_sum.reports.formatting import (
    PresentationMode,
    PresentationRenderError,
    SocialReportDetail,
    _discord_house_ptr,
    _discord_pipeline_warnings,
    _discord_sec_13f,
    _discord_social_sentiment,
    _discord_trendings,
    _html_css,
    _html_house_ptr,
    _html_pipeline_warnings,
    _html_sec_13f,
    _html_social_sentiment,
    _html_trendings,
    _markdown_house_ptr,
    _markdown_pipeline_warnings,
    _markdown_sec_13f,
    _markdown_social_sentiment,
    _markdown_trendings,
    _media_by_source_ref,
    _normalize_social_detail,
    _summary_from_response,
    _text_house_ptr,
    _text_pipeline_warnings,
    _text_sec_13f,
    _text_social_sentiment,
    _text_trendings,
)

@dataclass(frozen=True)
class PresentationRenderer:
    """Render LLM response JSON into final presentation artifacts."""

    title: str = "Market Social Digest"

    def render(self, response: dict[str, Any], *, mode: str, detail: str = "minimum") -> str:
        """Render a social report in html, markdown, discord, or text mode."""

        mode = mode.lower()
        detail = _normalize_social_detail(detail)
        summary = _summary_from_response(response)
        if mode == "html":
            return self._render_html(response, summary, detail)
        if mode == "markdown":
            return self._render_markdown(response, summary, detail)
        if mode in {"discord", "discord_markdown"}:
            return self._render_discord_markdown(response, summary, detail)
        if mode == "text":
            return self._render_text(response, summary, detail)
        raise PresentationRenderError(f"Unsupported presentation mode: {mode}")

    def render_trading(self, response: dict[str, Any], *, mode: str) -> str:
        """Render a House PTR trading report in html, markdown, discord, or text mode."""

        mode = mode.lower()
        summary = _summary_from_response(response)
        if mode == "html":
            return self._render_trading_html(response, summary)
        if mode == "markdown":
            return self._render_trading_markdown(response, summary)
        if mode in {"discord", "discord_markdown"}:
            return self._render_trading_discord_markdown(response, summary)
        if mode == "text":
            return self._render_trading_text(response, summary)
        raise PresentationRenderError(f"Unsupported presentation mode: {mode}")

    def render_13f(self, response: dict[str, Any], *, mode: str) -> str:
        """Render an SEC 13F holdings report in html, markdown, discord, or text mode."""

        mode = mode.lower()
        summary = _summary_from_response(response)
        if mode == "html":
            return self._render_13f_html(response, summary)
        if mode == "markdown":
            return self._render_13f_markdown(response, summary)
        if mode in {"discord", "discord_markdown"}:
            return self._render_13f_discord_markdown(response, summary)
        if mode == "text":
            return self._render_13f_text(response, summary)
        raise PresentationRenderError(f"Unsupported presentation mode: {mode}")

    def render_trendings(self, response: dict[str, Any], *, mode: str, limit: int = 5) -> str:
        """Render an Adanos trendings report in html, markdown, discord, or text mode."""

        mode = mode.lower()
        summary = _summary_from_response(response)
        if mode == "html":
            return self._render_trendings_html(response, summary, limit)
        if mode == "markdown":
            return self._render_trendings_markdown(response, summary, limit)
        if mode in {"discord", "discord_markdown"}:
            return self._render_trendings_discord_markdown(response, summary, limit)
        if mode == "text":
            return self._render_trendings_text(response, summary, limit)
        raise PresentationRenderError(f"Unsupported presentation mode: {mode}")

    def _render_html(self, response: dict[str, Any], summary: dict[str, Any], detail: SocialReportDetail) -> str:
        media_by_ref = _media_by_source_ref(response, summary)
        sections = [
            _html_pipeline_warnings(response.get("pipeline_warnings")),
            _html_social_sentiment(summary, media_by_ref, detail),
        ]
        return self._html_document(sections)

    def _render_trading_html(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        sections = [
            _html_pipeline_warnings(response.get("pipeline_warnings")),
            _html_house_ptr(response, summary),
        ]
        return self._html_document(sections)

    def _render_13f_html(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        sections = [
            _html_pipeline_warnings(response.get("pipeline_warnings")),
            _html_sec_13f(response, summary),
        ]
        return self._html_document(sections)

    def _render_trendings_html(self, response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
        sections = [
            _html_trendings(response, summary, limit),
            _html_pipeline_warnings(response.get("pipeline_warnings")),
        ]
        return self._html_document(sections)

    def _html_document(self, sections: list[str]) -> str:
        return "\n".join(
            [
                "<!doctype html>",
                '<html lang="en">',
                "<head>",
                '<meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                f"<title>{escape(self.title)}</title>",
                f"<style>{_html_css()}</style>",
                "</head>",
                "<body>",
                '<main class="page">',
                *sections,
                "</main>",
                "</body>",
                "</html>",
            ]
        )

    def _render_markdown(self, response: dict[str, Any], summary: dict[str, Any], detail: SocialReportDetail) -> str:
        media_by_ref = _media_by_source_ref(response, summary)
        lines = [
            _markdown_pipeline_warnings(response.get("pipeline_warnings")),
            _markdown_social_sentiment(summary, media_by_ref, detail),
        ]
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _render_trading_markdown(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            _markdown_pipeline_warnings(response.get("pipeline_warnings")),
            _markdown_house_ptr(response, summary),
        ]
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _render_13f_markdown(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            _markdown_pipeline_warnings(response.get("pipeline_warnings")),
            _markdown_sec_13f(response, summary),
        ]
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _render_trendings_markdown(self, response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
        lines = [
            _markdown_trendings(response, summary, limit),
            _markdown_pipeline_warnings(response.get("pipeline_warnings")),
        ]
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _render_discord_markdown(self, response: dict[str, Any], summary: dict[str, Any], detail: SocialReportDetail) -> str:
        media_by_ref = _media_by_source_ref(response, summary)
        lines = [
            _discord_pipeline_warnings(response.get("pipeline_warnings")),
            _discord_social_sentiment(summary, media_by_ref, detail),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_trading_discord_markdown(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            _discord_pipeline_warnings(response.get("pipeline_warnings")),
            _discord_house_ptr(response, summary),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_13f_discord_markdown(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            _discord_pipeline_warnings(response.get("pipeline_warnings")),
            _discord_sec_13f(response, summary),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_trendings_discord_markdown(self, response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
        lines = [
            _discord_trendings(response, summary, limit),
            _discord_pipeline_warnings(response.get("pipeline_warnings")),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_text(self, response: dict[str, Any], summary: dict[str, Any], detail: SocialReportDetail) -> str:
        media_by_ref = _media_by_source_ref(response, summary)
        lines = [
            _text_pipeline_warnings(response.get("pipeline_warnings")),
            _text_social_sentiment(summary, media_by_ref, detail),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_trading_text(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            _text_pipeline_warnings(response.get("pipeline_warnings")),
            _text_house_ptr(response, summary),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_13f_text(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            _text_pipeline_warnings(response.get("pipeline_warnings")),
            _text_sec_13f(response, summary),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_trendings_text(self, response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
        lines = [
            _text_trendings(response, summary, limit),
            _text_pipeline_warnings(response.get("pipeline_warnings")),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

