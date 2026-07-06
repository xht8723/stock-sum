"""Presentation renderers for LLM summary responses."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Literal
import json

PresentationMode = Literal["html", "markdown", "discord", "text"]
SocialReportDetail = Literal["minimum", "medium", "full"]

SECTION_TITLES = {
    "executive_summary": "Executive Summary",
    "x_signals": "X Signals",
    "reddit_signals": "Reddit Signals",
    "media_observations": "Media Observations",
    "risks_or_uncertainties": "Risks And Uncertainties",
    "notable_sources": "Notable Sources",
    "metadata": "Metadata",
}


class PresentationRenderError(ValueError):
    """Raised when a presentation cannot be rendered."""


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
                '<header class="hero">',
                f"<h1>{escape(self.title)}</h1>",
                "</header>",
                *sections,
                "</main>",
                "</body>",
                "</html>",
            ]
        )

    def _render_markdown(self, response: dict[str, Any], summary: dict[str, Any], detail: SocialReportDetail) -> str:
        media_by_ref = _media_by_source_ref(response, summary)
        lines = [
            f"# {self.title}",
            "",
            _markdown_pipeline_warnings(response.get("pipeline_warnings")),
            _markdown_social_sentiment(summary, media_by_ref, detail),
        ]
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _render_trading_markdown(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            f"# {self.title}",
            "",
            _markdown_pipeline_warnings(response.get("pipeline_warnings")),
            _markdown_house_ptr(response, summary),
        ]
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _render_13f_markdown(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            f"# {self.title}",
            "",
            _markdown_pipeline_warnings(response.get("pipeline_warnings")),
            _markdown_sec_13f(response, summary),
        ]
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _render_trendings_markdown(self, response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
        lines = [
            f"# {self.title}",
            "",
            _markdown_trendings(response, summary, limit),
            _markdown_pipeline_warnings(response.get("pipeline_warnings")),
        ]
        return "\n".join(line for line in lines if line is not None).strip() + "\n"

    def _render_discord_markdown(self, response: dict[str, Any], summary: dict[str, Any], detail: SocialReportDetail) -> str:
        media_by_ref = _media_by_source_ref(response, summary)
        lines = [
            f"**{self.title}**",
            "",
            _discord_pipeline_warnings(response.get("pipeline_warnings")),
            _discord_social_sentiment(summary, media_by_ref, detail),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_trading_discord_markdown(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            f"**{self.title}**",
            "",
            _discord_pipeline_warnings(response.get("pipeline_warnings")),
            _discord_house_ptr(response, summary),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_13f_discord_markdown(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            f"**{self.title}**",
            "",
            _discord_pipeline_warnings(response.get("pipeline_warnings")),
            _discord_sec_13f(response, summary),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_trendings_discord_markdown(self, response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
        lines = [
            f"**{self.title}**",
            "",
            _discord_trendings(response, summary, limit),
            _discord_pipeline_warnings(response.get("pipeline_warnings")),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_text(self, response: dict[str, Any], summary: dict[str, Any], detail: SocialReportDetail) -> str:
        media_by_ref = _media_by_source_ref(response, summary)
        lines = [
            self.title.upper(),
            "",
            _text_pipeline_warnings(response.get("pipeline_warnings")),
            _text_social_sentiment(summary, media_by_ref, detail),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_trading_text(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            self.title.upper(),
            "",
            _text_pipeline_warnings(response.get("pipeline_warnings")),
            _text_house_ptr(response, summary),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_13f_text(self, response: dict[str, Any], summary: dict[str, Any]) -> str:
        lines = [
            self.title.upper(),
            "",
            _text_pipeline_warnings(response.get("pipeline_warnings")),
            _text_sec_13f(response, summary),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"

    def _render_trendings_text(self, response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
        lines = [
            self.title.upper(),
            "",
            _text_trendings(response, summary, limit),
            _text_pipeline_warnings(response.get("pipeline_warnings")),
        ]
        return "\n\n".join(line for line in lines if line).strip() + "\n"


def _summary_from_response(response: dict[str, Any]) -> dict[str, Any]:
    summary = response.get("summary")
    if isinstance(summary, dict):
        return summary
    summary_text = response.get("summary_text")
    if isinstance(summary_text, str) and summary_text.strip():
        try:
            parsed = json.loads(_strip_json_fence(summary_text.strip()))
        except ValueError:
            return {"executive_summary": [summary_text]}
        if isinstance(parsed, dict):
            return parsed
    return {"executive_summary": ["No structured summary was available."]}


def _has_grouped_layout(summary: dict[str, Any]) -> bool:
    return isinstance(summary.get("x_reports"), list) or isinstance(summary.get("reddit_report"), dict)


def _html_pipeline_warnings(value: Any) -> str:
    warnings = _pipeline_warnings(value)
    if not warnings:
        return ""
    items = "".join(
        f"<li><strong>{escape(_warning_title(warning))}</strong>: {escape(_warning_message(warning))}</li>"
        for warning in warnings
    )
    return _html_section("Unavailable Sections", f"<ul>{items}</ul>")


def _markdown_pipeline_warnings(value: Any) -> str:
    warnings = _pipeline_warnings(value)
    if not warnings:
        return ""
    lines = ["## Unavailable Sections", ""]
    lines.extend(f"- **{_warning_title(warning)}**: {_warning_message(warning)}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def _discord_pipeline_warnings(value: Any) -> str:
    warnings = _pipeline_warnings(value)
    if not warnings:
        return ""
    lines = ["**Unavailable Sections**"]
    lines.extend(f"- **{_warning_title(warning)}**: {_warning_message(warning)}" for warning in warnings)
    return "\n".join(lines)


def _text_pipeline_warnings(value: Any) -> str:
    warnings = _pipeline_warnings(value)
    if not warnings:
        return ""
    lines = ["UNAVAILABLE SECTIONS"]
    lines.extend(f"- {_warning_title(warning)}: {_warning_message(warning)}" for warning in warnings)
    return "\n".join(lines)


def _pipeline_warnings(value: Any) -> list[dict[str, Any]]:
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _warning_title(warning: dict[str, Any]) -> str:
    section = str(warning.get("section") or "section").replace("_", " ").title()
    source_id = warning.get("source_id")
    return f"{section} ({source_id})" if source_id else section


def _warning_message(warning: dict[str, Any]) -> str:
    return _stringify_item(warning.get("message") or "Unavailable.")


def _html_social_sentiment(summary: dict[str, Any], media_by_ref: dict[str, list[dict[str, Any]]], detail: SocialReportDetail) -> str:
    buckets = _filtered_social_buckets(summary, detail)
    groups = []
    for bucket, title in _importance_titles(detail):
        items = buckets.get(bucket, [])
        if not items:
            continue
        cards = [_html_social_card(item, media_by_ref) for item in items]
        groups.append(
            "\n".join(
                [
                    '<article class="importance-group">',
                    f"<h3>{escape(title)}</h3>",
                    '<div class="compact-grid">',
                    *cards,
                    "</div>",
                    "</article>",
                ]
            )
        )
    return _html_section("Social Media Sentiment", "\n".join(groups) or '<p class="empty">No social signals at this detail level.</p>')


def _html_social_card(item: dict[str, Any], media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    source_ref = str(item.get("source_ref") or "")
    title = _post_title(item, reddit=item.get("source_kind") == "reddit")
    lines = [
        '<article class="card signal compact">',
        _html_social_badges(item),
        f"<h4>{escape(title)}</h4>",
        _html_paragraph("Summary", item.get("post_summary") or item.get("claim") or item.get("summary")),
    ]
    comments = _comments_text(item)
    if comments:
        lines.append(_html_paragraph("Comments", comments))
    lines.extend(
        [
            _html_paragraph("Sentiment", item.get("sentiment")),
            _html_paragraph("Takeaway", item.get("interpretation") or item.get("reason")),
            _html_paragraph("Stats", _plain_sentiment_stats_text(item.get("comment_sentiment_counts"))),
            _html_links(item.get("urls") or item.get("url")),
            _html_linked_media(source_ref, media_by_ref),
            "</article>",
        ]
    )
    return "\n".join(lines)


def _html_house_ptr(response: dict[str, Any], summary: dict[str, Any]) -> str:
    rows = _house_ptr_items(response, summary)
    if not rows:
        return _html_section("Official Trading Disclosures", '<p class="empty">No official trading disclosures.</p>')
    body_rows = []
    for row in rows:
        pdf_url = row.get("pdf_url")
        link = ""
        if pdf_url:
            link = f'<a href="{escape(str(pdf_url), quote=True)}" rel="noreferrer">PDF</a>'
        body_rows.append(
            "<tr>"
            f"<td>{escape(_stringify_item(row.get('name') or 'Unknown'))}</td>"
            f"<td>{escape(_house_value(row.get('status')))}</td>"
            f"<td>{escape(_house_value(row.get('state')))}</td>"
            f"<td>{escape(_house_value(row.get('filing_date')))}</td>"
            f"<td>{escape(_house_value(row.get('asset') or _raw_cells_preview(row)))}</td>"
            f"<td>{escape(_house_asset_type(row))}</td>"
            f"<td>{escape(_house_value(row.get('stock_ticker')))}</td>"
            f"<td>{escape(_house_trade_action(row.get('transaction_action') or row.get('transaction_type')))}</td>"
            f"<td>{escape(_house_value(row.get('transaction_date')))}</td>"
            f"<td>{escape(_house_value(row.get('amount')))}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    table = (
        '<div class="table-wrap"><table class="disclosures"><thead><tr>'
        "<th>Filer</th><th>Status</th><th>State</th><th>Filed</th><th>Asset</th>"
        "<th>Type</th><th>Ticker</th><th>Action</th><th>Trade Date</th><th>Amount</th><th>Source</th>"
        "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>"
    )
    return _html_section("Official Trading Disclosures", table)


def _html_sec_13f(response: dict[str, Any], summary: dict[str, Any]) -> str:
    rows = _sec_13f_items(response, summary)
    if not rows:
        return _html_section("SEC 13F Holdings", '<p class="empty">No SEC 13F holdings matched this query.</p>')
    body_rows = []
    for row in rows:
        source = f'<a href="{escape(str(row.get("filing_url")), quote=True)}" rel="noreferrer">Filing</a>' if row.get("filing_url") else ""
        body_rows.append(
            "<tr>"
            f"<td>{escape(_stringify_item(row.get('manager_name') or 'Unknown'))}</td>"
            f"<td>{escape(_sec_value(row.get('period_of_report')))}</td>"
            f"<td>{escape(_sec_value(row.get('filing_date')))}</td>"
            f"<td>{escape(_sec_value(row.get('issuer')))}</td>"
            f"<td>{escape(_sec_value(row.get('title_of_class')))}</td>"
            f"<td>{escape(_sec_value(row.get('cusip')))}</td>"
            f"<td>{escape(_sec_value(row.get('figi')))}</td>"
            f"<td>{escape(_format_int(row.get('value')))}</td>"
            f"<td>{escape(_format_int(row.get('ssh_prn_amt')))} {escape(_sec_value(row.get('ssh_prn_type')))}</td>"
            f"<td>{escape(_sec_value(row.get('put_call')))}</td>"
            f"<td>{escape(_sec_value(row.get('investment_discretion')))}</td>"
            f"<td>{escape(_sec_voting(row))}</td>"
            f"<td>{escape(_sec_value(row.get('accession_number')))} {source}</td>"
            "</tr>"
        )
    table = (
        '<div class="table-wrap"><table class="disclosures"><thead><tr>'
        "<th>Manager</th><th>Period</th><th>Filed</th><th>Issuer</th><th>Class</th>"
        "<th>CUSIP</th><th>FIGI</th><th>Value</th><th>Shares/PRN</th><th>Put/Call</th>"
        "<th>Discretion</th><th>Voting</th><th>Accession</th>"
        "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>"
    )
    return _html_section("SEC 13F Holdings", table)


def _html_social_badges(item: dict[str, Any]) -> str:
    badges = []
    label = item.get("source_label")
    if label:
        badges.append(f'<span class="badge">{escape(str(label))}</span>')
    sentiment = item.get("sentiment")
    if sentiment:
        badges.append(f'<span class="badge sentiment">{escape(str(sentiment))}</span>')
    confidence = item.get("confidence")
    if confidence:
        badges.append(f'<span class="badge confidence">{escape(str(confidence))}</span>')
    return f'<div class="badges">{"".join(badges)}</div>' if badges else ""


def _markdown_social_sentiment(summary: dict[str, Any], media_by_ref: dict[str, list[dict[str, Any]]], detail: SocialReportDetail) -> str:
    buckets = _filtered_social_buckets(summary, detail)
    lines = ["## Social Media Sentiment", ""]
    if not any(buckets.values()):
        return "\n".join([*lines, "_No social signals at this detail level._", ""])
    for bucket, title in _importance_titles(detail):
        items = buckets.get(bucket, [])
        if not items:
            continue
        lines.extend([f"### {title}", ""])
        for item in items:
            lines.extend(_markdown_social_item(item, media_by_ref))
    return "\n".join(lines)


def _markdown_social_item(item: dict[str, Any], media_by_ref: dict[str, list[dict[str, Any]]]) -> list[str]:
    source_ref = str(item.get("source_ref") or "")
    title = _post_title(item, reddit=item.get("source_kind") == "reddit")
    labels = " / ".join(str(value) for value in (item.get("source_label"), item.get("sentiment"), item.get("confidence")) if value)
    lines = [f"#### {title}", ""]
    if labels:
        lines.append(f"- **{labels}**")
    if item.get("post_summary") or item.get("claim") or item.get("summary"):
        lines.append(f"- Summary: {_stringify_item(item.get('post_summary') or item.get('claim') or item.get('summary'))}")
    comments = _comments_text(item)
    if comments:
        lines.append(f"- Comments: {comments}")
    if item.get("interpretation") or item.get("reason"):
        lines.append(f"- Takeaway: {_stringify_item(item.get('interpretation') or item.get('reason'))}")
    if item.get("comment_sentiment_counts"):
        lines.append(f"- Stats: {_plain_sentiment_stats_text(item.get('comment_sentiment_counts'))}")
    for index, url in enumerate(_as_list(item.get("urls") or item.get("url")), start=1):
        lines.append(f"- Source: [{_source_link_label(index)}]({url})")
    lines.extend(_markdown_linked_media(source_ref, media_by_ref))
    lines.append("")
    return lines


def _markdown_house_ptr(response: dict[str, Any], summary: dict[str, Any]) -> str:
    rows = _house_ptr_items(response, summary)
    lines = ["## Official Trading Disclosures", ""]
    if not rows:
        return "\n".join([*lines, "_No official trading disclosures._", ""])
    for row in rows:
        title = " / ".join(str(value) for value in (row.get("name"), row.get("status"), row.get("state")) if value)
        lines.append(f"- **{title or 'Unknown filer'}**")
        lines.append(f"  - Filed: {_house_value(row.get('filing_date'))}")
        lines.append(f"  - Asset: {_house_value(row.get('asset') or _raw_cells_preview(row))}")
        if _house_asset_type(row):
            lines.append(f"  - Type: {_house_asset_type(row)}")
        if row.get("stock_ticker"):
            lines.append(f"  - Ticker: {_house_value(row.get('stock_ticker'))}")
        lines.append(f"  - Action: {_house_trade_action(row.get('transaction_action') or row.get('transaction_type'))}")
        lines.append(f"  - Trade date: {_house_value(row.get('transaction_date'))}")
        lines.append(f"  - Amount: {_house_value(row.get('amount'))}")
        if row.get("pdf_url"):
            lines.append(f"  - Source: [Read PDF]({row['pdf_url']})")
    lines.append("")
    return "\n".join(lines)


def _markdown_sec_13f(response: dict[str, Any], summary: dict[str, Any]) -> str:
    rows = _sec_13f_items(response, summary)
    lines = ["## SEC 13F Holdings", ""]
    if not rows:
        return "\n".join([*lines, "_No SEC 13F holdings matched this query._", ""])
    for row in rows:
        lines.append(f"- **{_sec_value(row.get('manager_name')) or 'Unknown manager'}**")
        lines.append(f"  - Holding: {_sec_value(row.get('issuer'))} / {_sec_value(row.get('title_of_class'))}")
        lines.append(f"  - Period/filed: {_sec_value(row.get('period_of_report'))} / {_sec_value(row.get('filing_date'))}")
        lines.append(f"  - CUSIP/FIGI: {_sec_value(row.get('cusip'))} / {_sec_value(row.get('figi'))}")
        lines.append(f"  - Value: {_format_int(row.get('value'))}; Shares/PRN: {_format_int(row.get('ssh_prn_amt'))} {_sec_value(row.get('ssh_prn_type'))}")
        if row.get("put_call") or row.get("investment_discretion"):
            lines.append(f"  - Put/Call and discretion: {_sec_value(row.get('put_call'))} / {_sec_value(row.get('investment_discretion'))}")
        lines.append(f"  - Voting: {_sec_voting(row)}")
        accession = _sec_value(row.get("accession_number"))
        if row.get("filing_url"):
            lines.append(f"  - Source: [{accession or 'SEC filing'}]({row['filing_url']})")
        elif accession:
            lines.append(f"  - Accession: {accession}")
    lines.append("")
    return "\n".join(lines)


def _discord_social_sentiment(summary: dict[str, Any], media_by_ref: dict[str, list[dict[str, Any]]], detail: SocialReportDetail) -> str:
    buckets = _filtered_social_buckets(summary, detail)
    lines = ["**Social Media Sentiment**"]
    if not any(buckets.values()):
        return "\n".join([*lines, "_No social signals at this detail level._"])
    for bucket, title in _importance_titles(detail):
        items = buckets.get(bucket, [])
        if not items:
            continue
        lines.extend(["", f"__{title}__"])
        for item in items:
            lines.extend(_discord_social_item(item, media_by_ref))
    return "\n".join(lines)


def _discord_social_item(item: dict[str, Any], media_by_ref: dict[str, list[dict[str, Any]]]) -> list[str]:
    source_ref = str(item.get("source_ref") or "")
    title = _post_title(item, reddit=item.get("source_kind") == "reddit")
    labels = " / ".join(str(value) for value in (item.get("source_label"), item.get("sentiment"), item.get("confidence")) if value)
    prefix = f"- **{title}**"
    if labels:
        prefix += f" _{labels}_"
    lines = ["---", prefix]
    if item.get("post_summary") or item.get("claim") or item.get("summary"):
        lines.append(f"> **Summary:** {_stringify_item(item.get('post_summary') or item.get('claim') or item.get('summary'))}")
    comments = _comments_text(item)
    if comments:
        lines.append(f"**Comments:** {comments}")
    if item.get("interpretation") or item.get("reason"):
        lines.append(f"**Takeaway:** _{_stringify_item(item.get('interpretation') or item.get('reason'))}_")
    if item.get("comment_sentiment_counts"):
        lines.append(f"**Stats:** {_discord_sentiment_stats_text(item.get('comment_sentiment_counts'))}")
    source_links = []
    for index, url in enumerate(_as_list(item.get("urls") or item.get("url")), start=1):
        source_links.append(f"[{_source_link_label(index)}]({url})")
    media_links = _discord_linked_media(source_ref, media_by_ref)
    links = [*source_links, *media_links]
    if links:
        lines.append("**Links:** " + " | ".join(links))
    return lines


def _discord_house_ptr(response: dict[str, Any], summary: dict[str, Any]) -> str:
    rows = _house_ptr_items(response, summary)
    lines = ["**Official Trading Disclosures**"]
    if not rows:
        return "\n".join([*lines, "_No official trading disclosures._"])
    for row in rows:
        title = " / ".join(str(value) for value in (row.get("name"), row.get("status"), row.get("state")) if value)
        detail = " · ".join(
            part
            for part in (
                f"Filed {_house_value(row.get('filing_date'))}" if row.get("filing_date") else "",
                f"Asset {_house_value(row.get('asset') or _raw_cells_preview(row))}",
                f"Type {_house_asset_type(row)}" if _house_asset_type(row) else "",
                f"Ticker {_house_value(row.get('stock_ticker'))}" if row.get("stock_ticker") else "",
                f"Action {_house_trade_action(row.get('transaction_action') or row.get('transaction_type'))}" if row.get("transaction_action") or row.get("transaction_type") else "",
                f"Date {_house_value(row.get('transaction_date'))}" if row.get("transaction_date") else "",
                f"Amount {_house_value(row.get('amount'))}" if row.get("amount") else "",
            )
            if part
        )
        source = f" [PDF]({row['pdf_url']})" if row.get("pdf_url") else ""
        lines.append(f"- **{title or 'Unknown filer'}**: {detail}{source}")
    return "\n".join(lines)


def _discord_sec_13f(response: dict[str, Any], summary: dict[str, Any]) -> str:
    rows = _sec_13f_items(response, summary)
    lines = ["**SEC 13F Holdings**"]
    if not rows:
        return "\n".join([*lines, "_No SEC 13F holdings matched this query._"])
    for row in rows:
        title = f"{_sec_value(row.get('manager_name')) or 'Unknown manager'} · {_sec_value(row.get('issuer'))}"
        details = " · ".join(
            part
            for part in (
                f"Period {_sec_value(row.get('period_of_report'))}" if row.get("period_of_report") else "",
                f"Filed {_sec_value(row.get('filing_date'))}" if row.get("filing_date") else "",
                f"CUSIP {_sec_value(row.get('cusip'))}" if row.get("cusip") else "",
                f"Value {_format_int(row.get('value'))}" if row.get("value") is not None else "",
                f"Shares {_format_int(row.get('ssh_prn_amt'))} {_sec_value(row.get('ssh_prn_type'))}" if row.get("ssh_prn_amt") is not None else "",
                f"Put/Call {_sec_value(row.get('put_call'))}" if row.get("put_call") else "",
            )
            if part
        )
        source = f" [Filing]({row['filing_url']})" if row.get("filing_url") else ""
        lines.append(f"- **{title}**: {details}{source}")
    return "\n".join(lines)


def _discord_linked_media(source_ref: str, media_by_ref: dict[str, list[dict[str, Any]]]) -> list[str]:
    links = []
    for index, media in enumerate(media_by_ref.get(source_ref, []), start=1):
        url = _media_url(media)
        if url:
            label = "Image" if _is_image_media(media) else "Media"
            links.append(f"[{label} {index}]({url})")
    return links


def _text_social_sentiment(summary: dict[str, Any], media_by_ref: dict[str, list[dict[str, Any]]], detail: SocialReportDetail) -> str:
    buckets = _filtered_social_buckets(summary, detail)
    lines = ["SOCIAL MEDIA SENTIMENT"]
    if not any(buckets.values()):
        return "\n".join([*lines, "  No social signals at this detail level."])
    for bucket, title in _importance_titles(detail):
        items = buckets.get(bucket, [])
        if not items:
            continue
        lines.extend(["", title.upper()])
        for item in items:
            lines.extend(_text_social_item(item, media_by_ref))
    return "\n".join(lines)


def _text_social_item(item: dict[str, Any], media_by_ref: dict[str, list[dict[str, Any]]]) -> list[str]:
    source_ref = str(item.get("source_ref") or "")
    title = _post_title(item, reddit=item.get("source_kind") == "reddit")
    labels = " / ".join(str(value) for value in (item.get("source_label"), item.get("sentiment"), item.get("confidence")) if value)
    lines = [f"- {title}" + (f" [{labels}]" if labels else "")]
    if item.get("post_summary") or item.get("claim") or item.get("summary"):
        lines.append(f"  Summary: {_stringify_item(item.get('post_summary') or item.get('claim') or item.get('summary'))}")
    comments = _comments_text(item)
    if comments:
        lines.append(f"  Comments: {comments}")
    if item.get("interpretation") or item.get("reason"):
        lines.append(f"  Takeaway: {_stringify_item(item.get('interpretation') or item.get('reason'))}")
    if item.get("comment_sentiment_counts"):
        lines.append(f"  Stats: {_plain_sentiment_stats_text(item.get('comment_sentiment_counts'))}")
    for url in _as_list(item.get("urls") or item.get("url")):
        lines.append(f"  Source: {url}")
    lines.extend(_text_linked_media(source_ref, media_by_ref))
    return lines


def _text_house_ptr(response: dict[str, Any], summary: dict[str, Any]) -> str:
    rows = _house_ptr_items(response, summary)
    lines = ["OFFICIAL TRADING DISCLOSURES"]
    if not rows:
        return "\n".join([*lines, "  No official trading disclosures."])
    for row in rows:
        title = " / ".join(str(value) for value in (row.get("name"), row.get("status"), row.get("state")) if value)
        lines.append(f"- {title or 'Unknown filer'}")
        lines.append(f"  Filed: {_house_value(row.get('filing_date'))}")
        lines.append(f"  Asset: {_house_value(row.get('asset') or _raw_cells_preview(row))}")
        if _house_asset_type(row):
            lines.append(f"  Type: {_house_asset_type(row)}")
        if row.get("stock_ticker"):
            lines.append(f"  Ticker: {_house_value(row.get('stock_ticker'))}")
        lines.append(f"  Action: {_house_trade_action(row.get('transaction_action') or row.get('transaction_type'))}")
        lines.append(f"  Trade date: {_house_value(row.get('transaction_date'))}")
        lines.append(f"  Amount: {_house_value(row.get('amount'))}")
        if row.get("pdf_url"):
            lines.append(f"  Source: {row['pdf_url']}")
    return "\n".join(lines)


def _text_sec_13f(response: dict[str, Any], summary: dict[str, Any]) -> str:
    rows = _sec_13f_items(response, summary)
    lines = ["SEC 13F HOLDINGS"]
    if not rows:
        return "\n".join([*lines, "  No SEC 13F holdings matched this query."])
    for row in rows:
        lines.append(f"- {_sec_value(row.get('manager_name')) or 'Unknown manager'}")
        lines.append(f"  Holding: {_sec_value(row.get('issuer'))} / {_sec_value(row.get('title_of_class'))}")
        lines.append(f"  Period/filed: {_sec_value(row.get('period_of_report'))} / {_sec_value(row.get('filing_date'))}")
        lines.append(f"  CUSIP/FIGI: {_sec_value(row.get('cusip'))} / {_sec_value(row.get('figi'))}")
        lines.append(f"  Value: {_format_int(row.get('value'))}; Shares/PRN: {_format_int(row.get('ssh_prn_amt'))} {_sec_value(row.get('ssh_prn_type'))}")
        if row.get("put_call") or row.get("investment_discretion"):
            lines.append(f"  Put/Call and discretion: {_sec_value(row.get('put_call'))} / {_sec_value(row.get('investment_discretion'))}")
        lines.append(f"  Voting: {_sec_voting(row)}")
        if row.get("filing_url"):
            lines.append(f"  Source: {row['filing_url']}")
    return "\n".join(lines)


def _html_trendings(response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
    if response.get("skipped"):
        return _html_section("Trending stocks", '<p class="empty">Adanos API key is not configured.</p>')
    stocks = _trendings_rows(summary, "stocks", limit)
    sectors = _trendings_rows(summary, "sectors", limit)
    return "\n".join(
        [
            _html_section("Trending stocks", "\n".join(_html_trendings_platform_rows(stocks, is_sector=False))),
            _html_section("Trending sectors", "\n".join(_html_trendings_platform_rows(sectors, is_sector=True))),
        ]
    )


def _html_trendings_platform_rows(grouped: dict[str, list[dict[str, Any]]], *, is_sector: bool) -> list[str]:
    parts: list[str] = []
    for platform, rows in grouped.items():
        parts.append(f"<h3>{escape(_platform_label(platform))}</h3>")
        if not rows:
            parts.append('<p class="empty">No rows.</p>')
            continue
        parts.append('<div class="items">')
        for row in rows:
            parts.append('<article class="item">')
            parts.append(f"<h4>{escape(_trendings_title(row, is_sector=is_sector))}</h4>")
            parts.append(f"<p>{escape(_trendings_stats(row))}</p>")
            parts.append("</article>")
        parts.append("</div>")
    return parts


def _markdown_trendings(response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
    return _plain_trendings(response, summary, limit, heading="##", bullet="-")


def _discord_trendings(response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
    return _plain_trendings(response, summary, limit, heading="**", bullet="•")


def _text_trendings(response: dict[str, Any], summary: dict[str, Any], limit: int) -> str:
    return _plain_trendings(response, summary, limit, heading="", bullet="-")


def _plain_trendings(response: dict[str, Any], summary: dict[str, Any], limit: int, *, heading: str, bullet: str) -> str:
    if response.get("skipped"):
        return "Trending stocks\nAdanos API key is not configured.\n\nTrending sectors\nAdanos API key is not configured."
    lines: list[str] = []
    for title, key, is_sector in (
        ("Trending stocks", "stocks", False),
        ("Trending sectors", "sectors", True),
    ):
        if heading == "**":
            lines.append(f"**{title}**")
        elif heading:
            lines.append(f"{heading} {title}")
        else:
            lines.append(title.upper())
        grouped = _trendings_rows(summary, key, limit)
        for platform, rows in grouped.items():
            lines.append(f"__{_platform_label(platform)}__" if heading == "**" else _platform_label(platform))
            if not rows:
                lines.append(f"{bullet} No rows.")
                continue
            for row in rows:
                lines.append(f"{bullet} {_trendings_title(row, is_sector=is_sector)}")
                lines.append(f"  {_trendings_stats(row)}")
        lines.append("")
    return "\n".join(lines).strip()


def _trendings_rows(summary: dict[str, Any], key: str, limit: int) -> dict[str, list[dict[str, Any]]]:
    rows = summary.get(key)
    if not isinstance(rows, list):
        rows = []
    grouped: dict[str, list[dict[str, Any]]] = {"reddit": [], "x": []}
    for row in rows:
        if not isinstance(row, dict):
            continue
        platform = str(row.get("platform") or "").lower()
        if platform in grouped and len(grouped[platform]) < limit:
            grouped[platform].append(row)
    return grouped


def _trendings_title(row: dict[str, Any], *, is_sector: bool) -> str:
    if is_sector:
        top_tickers = row.get("top_tickers")
        tickers = ", ".join(str(item) for item in top_tickers[:5]) if isinstance(top_tickers, list) else ""
        suffix = f" ({tickers})" if tickers else ""
        return f"{row.get('sector') or 'Unknown sector'}{suffix}"
    ticker = row.get("ticker") or "UNKNOWN"
    company = row.get("company_name")
    return f"{ticker} - {company}" if company else str(ticker)


def _trendings_stats(row: dict[str, Any]) -> str:
    return (
        f"trend: {_display_value(row.get('trend'))}; "
        f"mentions: {_display_value(row.get('mentions'))}; "
        f"bullish_pct: {_display_percent(row.get('bullish_pct'))}; "
        f"bearish_pct: {_display_percent(row.get('bearish_pct'))}"
    )


def _platform_label(platform: str) -> str:
    return {"reddit": "Reddit Stocks", "x": "X Stocks"}.get(platform, platform.title())


def _display_percent(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{value}%"


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    return str(value)


def _social_items_by_importance(summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {"high": [], "medium": [], "low": []}
    for item in _social_items(summary):
        buckets[_importance_bucket(item)].append(item)
    return buckets


def _filtered_social_buckets(summary: dict[str, Any], detail: SocialReportDetail) -> dict[str, list[dict[str, Any]]]:
    buckets = _social_items_by_importance(summary)
    allowed = {bucket for bucket, _title in _importance_titles(detail)}
    return {bucket: items if bucket in allowed else [] for bucket, items in buckets.items()}


def _social_items(summary: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for report in _as_list(summary.get("x_reports")):
        if not isinstance(report, dict):
            continue
        handle = str(report.get("handle") or "unknown")
        for post in _as_list(report.get("posts")):
            if isinstance(post, dict):
                items.append({**post, "source_kind": "x", "source_label": f"X @{handle}"})

    reddit_report = summary.get("reddit_report")
    if isinstance(reddit_report, dict):
        for post in _as_list(reddit_report.get("posts")):
            if isinstance(post, dict):
                subreddit = post.get("subreddit") or reddit_report.get("subreddit") or "Reddit"
                label = str(subreddit)
                if not label.startswith("r/") and label != "Reddit":
                    label = f"r/{label}"
                items.append({**post, "source_kind": "reddit", "source_label": label})

    for item in _as_list(summary.get("x_signals")):
        if isinstance(item, dict):
            items.append({**item, "source_kind": "x", "source_label": "X"})
    for item in _as_list(summary.get("reddit_signals")):
        if isinstance(item, dict):
            items.append({**item, "source_kind": "reddit", "source_label": "Reddit"})
    return items


def _house_ptr_items(response: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    value = response.get("house_ptr") or summary.get("house_ptr")
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _sec_13f_items(response: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    value = response.get("sec_13f") or summary.get("sec_13f")
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _raw_cells_preview(row: dict[str, Any]) -> str:
    cells = [str(item).strip() for item in _as_list(row.get("raw_cells")) if str(item).strip()]
    return " | ".join(cells[:4])


def _house_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return _stringify_item(value)


def _house_asset_type(row: dict[str, Any]) -> str:
    code = _house_value(row.get("asset_type_code"))
    label = _house_value(row.get("asset_type_label"))
    if code and label:
        return f"{label} ({code})"
    return label or code


def _house_trade_action(value: Any) -> str:
    text = _house_value(value)
    normalized = text.strip().lower()
    if normalized.startswith("p"):
        return "Purchase"
    if normalized.startswith("s"):
        if "partial" in normalized:
            return "Sell (partial)"
        return "Sell"
    return text


def _sec_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return _stringify_item(value)


def _format_int(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return _stringify_item(value)


def _sec_voting(row: dict[str, Any]) -> str:
    parts = []
    for label, key in (("sole", "voting_auth_sole"), ("shared", "voting_auth_shared"), ("none", "voting_auth_none")):
        value = row.get(key)
        if value not in (None, ""):
            parts.append(f"{label} {_format_int(value)}")
    return ", ".join(parts)


def _importance_bucket(item: dict[str, Any]) -> str:
    explicit = str(item.get("importance") or item.get("priority") or "").lower()
    if explicit.startswith("high"):
        return "high"
    if explicit.startswith("low"):
        return "low"
    if explicit.startswith("med"):
        return "medium"
    return "medium"


def _importance_titles(detail: SocialReportDetail = "full") -> list[tuple[str, str]]:
    titles = [("high", "High Importance"), ("medium", "Medium Importance"), ("low", "Low Importance")]
    if detail == "minimum":
        return titles[:1]
    if detail == "medium":
        return titles[:2]
    return titles


def _normalize_social_detail(value: str) -> SocialReportDetail:
    normalized = value.lower().strip()
    if normalized in {"minimum", "medium", "full"}:
        return normalized  # type: ignore[return-value]
    raise PresentationRenderError(f"Unsupported social report detail: {value}")


def _html_x_reports(value: Any, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    groups = []
    for report in _as_list(value):
        if not isinstance(report, dict):
            continue
        handle = str(report.get("handle") or "unknown")
        posts = [_html_grouped_post(post, media_by_ref) for post in _as_list(report.get("posts")) if isinstance(post, dict)]
        groups.append(
            "\n".join(
                [
                    '<article class="group">',
                    f"<h3>{escape(handle)}</h3>",
                    '<h4>Overall Summary</h4>',
                    _html_bullets(report.get("overall_summary")),
                    *posts,
                    "</article>",
                ]
            )
        )
    return _html_section("X Reports", "\n".join(groups) or '<p class="empty">No X reports.</p>')


def _html_reddit_report(value: Any, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    report = value if isinstance(value, dict) else {}
    posts = [_html_grouped_post(post, media_by_ref, reddit=True) for post in _as_list(report.get("posts")) if isinstance(post, dict)]
    body = "\n".join(
        [
            '<article class="group">',
            '<h3>Overall Summary</h3>',
            _html_bullets(report.get("overall_summary")),
            *posts,
            "</article>",
        ]
    )
    return _html_section("Reddit Reports", body)


def _html_grouped_post(post: dict[str, Any], media_by_ref: dict[str, list[dict[str, Any]]], *, reddit: bool = False) -> str:
    source_ref = str(post.get("source_ref") or "")
    lines = [
        '<article class="card signal nested">',
        _html_badges(post),
        f"<h4>{escape(_post_title(post, reddit=reddit))}</h4>",
        _html_paragraph("Post Summary", post.get("post_summary") or post.get("claim") or post.get("summary")),
    ]
    comments = _comments_text(post) if reddit else ""
    if comments:
        lines.append(_html_paragraph("Comments Sentiment", comments))
    lines.extend(
        [
            _html_paragraph("Sentiment", post.get("sentiment")),
            _html_paragraph("Interpretation", post.get("interpretation")),
            _html_paragraph("Stats", _plain_sentiment_stats_text(post.get("comment_sentiment_counts"))),
            _html_links(post.get("urls") or post.get("url")),
            _html_linked_media(source_ref, media_by_ref),
            _html_extra_fields(
                post,
                {
                    "source_ref",
                    "title",
                    "post_summary",
                    "claim",
                    "summary",
                    "comments_sentiment",
                    "comment_sentiment_counts",
                    "sentiment",
                    "tags",
                    "interpretation",
                    "confidence",
                    "urls",
                    "url",
                    "media_ids",
                    "comment_refs",
                },
            ),
            "</article>",
        ]
    )
    return "\n".join(lines)


def _html_bullets(value: Any) -> str:
    items = _as_list(value)
    if not items:
        return '<p class="empty">No summary.</p>'
    return "<ul>" + "".join(f"<li>{escape(_stringify_item(item))}</li>" for item in items) + "</ul>"


def _html_executive(value: Any) -> str:
    return _html_simple_list("Executive Summary", value)


def _html_signal_section(title: str, value: Any, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    cards = []
    for item in _as_list(value):
        if isinstance(item, dict):
            cards.append(
                "\n".join(
                    [
                        '<article class="card signal">',
                        _html_badges(item),
                        f"<h3>{escape(str(item.get('claim') or item.get('title') or 'Signal'))}</h3>",
                        _html_paragraph("Interpretation", item.get("interpretation")),
                        _html_paragraph("Reason", item.get("reason")),
                        _html_links(item.get("urls") or item.get("url")),
                        _html_linked_media(str(item.get("source_ref") or ""), media_by_ref),
                        _html_extra_fields(item, {"source_ref", "confidence", "claim", "title", "interpretation", "reason", "urls", "url", "media_ids"}),
                        "</article>",
                    ]
                )
            )
        else:
            cards.append(f'<article class="card"><p>{escape(str(item))}</p></article>')
    return _html_section(title, "\n".join(cards) or '<p class="empty">No items.</p>')


def _html_linked_media(source_ref: str, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    media_items = media_by_ref.get(source_ref, [])
    if not media_items:
        return ""
    cards = []
    for media in media_items:
        url = _media_url(media)
        image = ""
        if url and _is_image_media(media):
            safe_url = escape(url, quote=True)
            image = f'<a href="{safe_url}" rel="noreferrer"><img src="{safe_url}" alt="Post image"></a>'
        elif url:
            safe_url = escape(url, quote=True)
            image = f'<a href="{safe_url}" rel="noreferrer">Open media</a>'
        cards.append(
            "\n".join(
                [
                    '<div class="media-card">',
                    image,
                    "</div>",
                ]
            )
        )
    return f'<div class="linked-media">{"".join(cards)}</div>'


def _html_media(value: Any) -> str:
    cards = []
    for item in _as_list(value):
        if isinstance(item, dict):
            cards.append(
                "\n".join(
                    [
                        '<article class="card">',
                        _html_badges(item, media=True),
                        f"<p>{escape(str(item.get('observation') or item))}</p>",
                        "</article>",
                    ]
                )
            )
        else:
            cards.append(f'<article class="card"><p>{escape(str(item))}</p></article>')
    return _html_section("Media Observations", "\n".join(cards) or '<p class="empty">No media observations.</p>')


def _html_simple_list(title: str, value: Any) -> str:
    items = _as_list(value)
    if not items:
        return _html_section(title, '<p class="empty">No items.</p>')
    html_items = []
    for item in items:
        text = _stringify_item(item)
        html_items.append(f"<li>{escape(text)}</li>")
    return _html_section(title, f"<ul>{''.join(html_items)}</ul>")


def _html_notable(value: Any) -> str:
    cards = []
    for item in _as_list(value):
        if isinstance(item, dict):
            cards.append(
                "\n".join(
                    [
                        '<article class="card">',
                        _html_badges(item),
                        f"<p>{escape(str(item.get('reason') or item.get('claim') or item))}</p>",
                        _html_links(item.get("url") or item.get("urls")),
                        "</article>",
                    ]
                )
            )
        else:
            cards.append(f'<article class="card"><p>{escape(str(item))}</p></article>')
    return _html_section("Notable Sources", "\n".join(cards) or '<p class="empty">No notable sources.</p>')


def _html_metadata(response: dict[str, Any], metadata: Any) -> str:
    rows: dict[str, Any] = {}
    if isinstance(metadata, dict):
        rows.update(metadata)
    rows["provider"] = response.get("provider")
    rows["model"] = response.get("model")
    usage = _safe_usage(response)
    if usage:
        rows["tokens"] = usage
    return _html_section("Metadata", _html_kv(rows))


def _html_section(title: str, body: str) -> str:
    return f'<section class="section"><h2>{escape(title)}</h2><div class="content">{body}</div></section>'


def _html_badges(item: dict[str, Any], *, media: bool = False) -> str:
    badges = []
    ref = item.get("source_ref")
    if ref:
        badges.append(f'<span class="badge">{escape(str(ref))}</span>')
    media_id = item.get("media_id")
    if media and media_id:
        badges.append(f'<span class="badge media">{escape(str(media_id))}</span>')
    confidence = item.get("confidence")
    if confidence:
        badges.append(f'<span class="badge confidence">{escape(str(confidence))}</span>')
    return f'<div class="badges">{"".join(badges)}</div>' if badges else ""


def _html_paragraph(label: str, value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return f'<p><strong>{escape(label)}:</strong> {escape(_stringify_item(value))}</p>'


def _html_links(value: Any) -> str:
    links = [item for item in _as_list(value) if item]
    if not links:
        return ""
    rendered = []
    for index, url in enumerate(links, start=1):
        safe_url = escape(str(url), quote=True)
        rendered.append(f'<a href="{safe_url}" rel="noreferrer">{escape(_source_link_label(index))}</a>')
    return f'<div class="links">{"".join(rendered)}</div>'


def _html_extra_fields(item: dict[str, Any], known: set[str]) -> str:
    extras = {key: value for key, value in item.items() if key not in known and value not in (None, "", [], {})}
    return _html_kv(extras) if extras else ""


def _html_kv(rows: dict[str, Any]) -> str:
    items = []
    for key, value in rows.items():
        if value in (None, "", [], {}):
            continue
        items.append(f"<dt>{escape(str(key))}</dt><dd>{escape(_stringify_item(value))}</dd>")
    return f"<dl>{''.join(items)}</dl>" if items else '<p class="empty">No metadata.</p>'


def _html_response_meta(response: dict[str, Any]) -> str:
    parts = []
    provider = response.get("provider")
    model = response.get("model")
    if provider:
        parts.append(f"Provider: {escape(str(provider))}")
    if model:
        parts.append(f"Model: {escape(str(model))}")
    usage = _safe_usage(response)
    if usage:
        parts.append(f"Tokens: {escape(usage)}")
    return " | ".join(parts)


def _html_css() -> str:
    return """
:root { color-scheme: light; font-family: Inter, Segoe UI, Arial, sans-serif; background: #f5f7f9; color: #172026; }
body { margin: 0; }
.page { max-width: 1080px; margin: 0 auto; padding: 24px 18px 40px; }
.hero { border-bottom: 1px solid #d9e0e7; padding-bottom: 14px; margin-bottom: 18px; }
h1 { margin: 0; font-size: 30px; letter-spacing: 0; }
h2 { font-size: 20px; margin: 0 0 12px; }
h3 { font-size: 16px; margin: 8px 0; }
.subtitle { color: #52616f; margin: 8px 0; }
.meta { color: #64717f; font-size: 13px; }
.section { margin: 18px 0; }
.content { display: grid; gap: 12px; }
.group { display: grid; gap: 12px; }
.group h3 { font-size: 18px; margin: 4px 0 0; color: #263747; }
.group h4 { font-size: 15px; margin: 4px 0; color: #3b4856; }
.importance-group { display: grid; gap: 8px; }
.importance-group h3 { color: #263747; margin: 2px 0; }
.compact-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 10px; }
.card { background: #fff; border: 1px solid #dde4eb; border-radius: 8px; padding: 12px; box-shadow: 0 1px 2px rgba(20, 35, 50, 0.04); }
.card.nested { margin-left: 10px; }
.card.compact p { margin: 7px 0; font-size: 13px; line-height: 1.38; }
.card.compact h4 { margin: 8px 0 6px; color: #1d3d5c; font-size: 15px; }
.signal h3 { color: #1d3d5c; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; }
.badge { display: inline-flex; align-items: center; border-radius: 999px; background: #e8f0f8; color: #234361; padding: 3px 8px; font-size: 12px; font-weight: 600; }
.badge.confidence { background: #eef6e9; color: #315a26; }
.badge.sentiment { background: #f3efe4; color: #624708; }
.badge.media { background: #fff4d8; color: #6b4a00; }
.links { display: grid; gap: 4px; margin-top: 8px; font-size: 12px; }
.table-wrap { overflow-x: auto; }
table.disclosures { width: 100%; border-collapse: collapse; font-size: 12px; background: #fff; border: 1px solid #dde4eb; }
table.disclosures th, table.disclosures td { border-bottom: 1px solid #e7edf3; padding: 7px 8px; text-align: left; vertical-align: top; }
table.disclosures th { background: #eef4f9; color: #33485c; font-weight: 700; }
table.disclosures td { overflow-wrap: anywhere; }
.linked-media { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin-top: 10px; }
.media-card { border: 1px solid #e4ebf1; border-radius: 8px; padding: 8px; background: #f8fafc; }
.media-card img { width: 100%; max-height: 180px; object-fit: contain; border-radius: 6px; background: #fff; }
.media-card p { margin: 6px 0 0; font-size: 13px; color: #465563; }
.media-title { font-size: 12px; font-weight: 700; color: #566575; margin-bottom: 6px; }
a { color: #0b66c3; overflow-wrap: anywhere; }
ul { margin: 0; padding-left: 22px; }
li { margin: 7px 0; }
dl { display: grid; grid-template-columns: minmax(120px, 220px) 1fr; gap: 8px 14px; margin: 0; }
dt { font-weight: 700; color: #3b4856; }
dd { margin: 0; overflow-wrap: anywhere; }
.empty { color: #7c8792; font-style: italic; }
@media (max-width: 680px) { .page { padding: 20px 12px 36px; } h1 { font-size: 28px; } dl { grid-template-columns: 1fr; } }
"""


def _markdown_executive(value: Any) -> str:
    return _markdown_simple_list("Executive Summary", value)


def _markdown_x_reports(value: Any, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    lines = ["# X Reports", ""]
    reports = [report for report in _as_list(value) if isinstance(report, dict)]
    if not reports:
        return "\n".join([*lines, "_No X reports._", ""])
    for report in reports:
        lines.extend([f"## {report.get('handle') or 'unknown'}", "", "### Overall Summary", ""])
        lines.extend(_markdown_bullets(report.get("overall_summary")))
        for post in _as_list(report.get("posts")):
            if isinstance(post, dict):
                lines.extend(_markdown_grouped_post(post, media_by_ref))
    lines.append("")
    return "\n".join(lines)


def _markdown_reddit_report(value: Any, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    report = value if isinstance(value, dict) else {}
    lines = ["# Reddit Reports", "", "## Overall Summary", ""]
    lines.extend(_markdown_bullets(report.get("overall_summary")))
    for post in _as_list(report.get("posts")):
        if isinstance(post, dict):
            lines.extend(_markdown_grouped_post(post, media_by_ref, reddit=True, heading="###"))
    lines.append("")
    return "\n".join(lines)


def _markdown_grouped_post(
    post: dict[str, Any],
    media_by_ref: dict[str, list[dict[str, Any]]],
    *,
    reddit: bool = False,
    heading: str = "####",
) -> list[str]:
    source_ref = str(post.get("source_ref") or "")
    title = _post_title(post, reddit=reddit)
    lines = [f"{heading} {title}", ""]
    if source_ref:
        lines.append(f"- **source_ref**: {source_ref}")
    if post.get("confidence"):
        lines.append(f"- **confidence**: {post['confidence']}")
    if post.get("post_summary") or post.get("claim") or post.get("summary"):
        lines.append(f"- **post_summary**: {_stringify_item(post.get('post_summary') or post.get('claim') or post.get('summary'))}")
    comments = _comments_text(post) if reddit else ""
    if comments:
        lines.append(f"- **comments_sentiment**: {comments}")
    if post.get("sentiment"):
        lines.append(f"- **sentiment**: {_stringify_item(post['sentiment'])}")
    if post.get("interpretation"):
        lines.append(f"- **interpretation**: {_stringify_item(post['interpretation'])}")
    if reddit and post.get("comment_sentiment_counts"):
        lines.append(f"- **stats**: {_plain_sentiment_stats_text(post.get('comment_sentiment_counts'))}")
    for index, url in enumerate(_as_list(post.get("urls") or post.get("url")), start=1):
        lines.append(f"- **source**: [{_source_link_label(index)}]({url})")
    lines.extend(_markdown_linked_media(source_ref, media_by_ref))
    lines.append("")
    return lines


def _markdown_bullets(value: Any) -> list[str]:
    items = _as_list(value)
    if not items:
        return ["_No summary._", ""]
    return [f"- {_stringify_item(item)}" for item in items] + [""]


def _markdown_signal_section(title: str, value: Any, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    lines = [f"## {title}", ""]
    items = _as_list(value)
    if not items:
        return "\n".join([*lines, "_No items._", ""])
    for item in items:
        if isinstance(item, dict):
            label = item.get("source_ref") or "signal"
            confidence = f" [{item['confidence']}]" if item.get("confidence") else ""
            lines.append(f"- **{label}{confidence}**: {_stringify_item(item.get('claim') or item.get('title') or 'Signal')}")
            if item.get("interpretation"):
                lines.append(f"  Interpretation: {_stringify_item(item['interpretation'])}")
            if item.get("reason"):
                lines.append(f"  Reason: {_stringify_item(item['reason'])}")
            for index, url in enumerate(_as_list(item.get("urls") or item.get("url")), start=1):
                lines.append(f"  Source: [{_source_link_label(index)}]({url})")
            lines.extend(_markdown_linked_media(str(label), media_by_ref))
        else:
            lines.append(f"- {_stringify_item(item)}")
    lines.append("")
    return "\n".join(lines)


def _markdown_linked_media(source_ref: str, media_by_ref: dict[str, list[dict[str, Any]]]) -> list[str]:
    lines: list[str] = []
    for media in media_by_ref.get(source_ref, []):
        url = _media_url(media)
        if url and _is_image_media(media):
            lines.append(f"  ![Post image]({url})")
        elif url:
            lines.append(f"  [Open media]({url})")
    return lines


def _markdown_media(value: Any) -> str:
    lines = ["## Media Observations", ""]
    items = _as_list(value)
    if not items:
        return "\n".join([*lines, "_No media observations._", ""])
    for item in items:
        if isinstance(item, dict):
            label = " / ".join(str(part) for part in (item.get("media_id"), item.get("source_ref")) if part)
            lines.append(f"- **{label or 'media'}**: {_stringify_item(item.get('observation') or item)}")
        else:
            lines.append(f"- {_stringify_item(item)}")
    lines.append("")
    return "\n".join(lines)


def _markdown_simple_list(title: str, value: Any) -> str:
    lines = [f"## {title}", ""]
    items = _as_list(value)
    if not items:
        return "\n".join([*lines, "_No items._", ""])
    lines.extend(f"- {_stringify_item(item)}" for item in items)
    lines.append("")
    return "\n".join(lines)


def _markdown_notable(value: Any) -> str:
    lines = ["## Notable Sources", ""]
    items = _as_list(value)
    if not items:
        return "\n".join([*lines, "_No notable sources._", ""])
    for item in items:
        if isinstance(item, dict):
            ref = item.get("source_ref") or "source"
            reason = _stringify_item(item.get("reason") or item.get("claim") or item)
            url = item.get("url")
            lines.append(f"- **{ref}**: {reason}")
            if url:
                lines.append(f"  Source: [Read source]({url})")
        else:
            lines.append(f"- {_stringify_item(item)}")
    lines.append("")
    return "\n".join(lines)


def _markdown_metadata(value: Any) -> str:
    lines = ["## Metadata", ""]
    if not isinstance(value, dict) or not value:
        return "\n".join([*lines, "_No metadata._", ""])
    lines.extend(f"- **{key}**: {_stringify_item(item)}" for key, item in value.items())
    lines.append("")
    return "\n".join(lines)


def _markdown_response_meta(response: dict[str, Any]) -> str:
    parts = []
    for key in ("provider", "model"):
        if response.get(key):
            parts.append(f"**{key}**: {response[key]}")
    usage = _safe_usage(response)
    if usage:
        parts.append(f"**tokens**: {usage}")
    return " | ".join(parts)


def _text_executive(value: Any) -> str:
    return _text_simple_list("EXECUTIVE SUMMARY", value)


def _text_x_reports(value: Any, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    lines = ["X REPORTS"]
    reports = [report for report in _as_list(value) if isinstance(report, dict)]
    if not reports:
        return "\n".join([*lines, "  No X reports."])
    for report in reports:
        lines.extend(["", f"USER: {report.get('handle') or 'unknown'}", "OVERALL SUMMARY"])
        lines.extend(f"- {_stringify_item(item)}" for item in _as_list(report.get("overall_summary")) or ["No summary."])
        for post in _as_list(report.get("posts")):
            if isinstance(post, dict):
                lines.extend(_text_grouped_post(post, media_by_ref))
    return "\n".join(lines)


def _text_reddit_report(value: Any, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    report = value if isinstance(value, dict) else {}
    lines = ["REDDIT REPORTS", "OVERALL SUMMARY"]
    lines.extend(f"- {_stringify_item(item)}" for item in _as_list(report.get("overall_summary")) or ["No summary."])
    for post in _as_list(report.get("posts")):
        if isinstance(post, dict):
            lines.extend(_text_grouped_post(post, media_by_ref, reddit=True))
    return "\n".join(lines)


def _text_grouped_post(
    post: dict[str, Any],
    media_by_ref: dict[str, list[dict[str, Any]]],
    *,
    reddit: bool = False,
) -> list[str]:
    source_ref = str(post.get("source_ref") or "")
    lines = ["", _post_title(post, reddit=reddit)]
    if source_ref:
        lines.append(f"- source_ref: {source_ref}")
    if post.get("confidence"):
        lines.append(f"- confidence: {post['confidence']}")
    if post.get("post_summary") or post.get("claim") or post.get("summary"):
        lines.append(f"- post_summary: {_stringify_item(post.get('post_summary') or post.get('claim') or post.get('summary'))}")
    comments = _comments_text(post) if reddit else ""
    if comments:
        lines.append(f"- comments_sentiment: {comments}")
    if post.get("sentiment"):
        lines.append(f"- sentiment: {_stringify_item(post['sentiment'])}")
    if post.get("interpretation"):
        lines.append(f"- interpretation: {_stringify_item(post['interpretation'])}")
    if reddit and post.get("comment_sentiment_counts"):
        lines.append(f"- stats: {_plain_sentiment_stats_text(post.get('comment_sentiment_counts'))}")
    for url in _as_list(post.get("urls") or post.get("url")):
        lines.append(f"- source: {url}")
    lines.extend(_text_linked_media(source_ref, media_by_ref))
    return lines


def _text_signal_section(title: str, value: Any, media_by_ref: dict[str, list[dict[str, Any]]]) -> str:
    lines = [title]
    items = _as_list(value)
    if not items:
        return "\n".join([*lines, "  No items."])
    for item in items:
        if isinstance(item, dict):
            label = item.get("source_ref") or "signal"
            confidence = f" [{item['confidence']}]" if item.get("confidence") else ""
            lines.append(f"- {label}{confidence}: {_stringify_item(item.get('claim') or item.get('title') or 'Signal')}")
            if item.get("interpretation"):
                lines.append(f"  Interpretation: {_stringify_item(item['interpretation'])}")
            if item.get("reason"):
                lines.append(f"  Reason: {_stringify_item(item['reason'])}")
            for url in _as_list(item.get("urls") or item.get("url")):
                lines.append(f"  Source: {url}")
            lines.extend(_text_linked_media(str(label), media_by_ref))
        else:
            lines.append(f"- {_stringify_item(item)}")
    return "\n".join(lines)


def _text_linked_media(source_ref: str, media_by_ref: dict[str, list[dict[str, Any]]]) -> list[str]:
    lines: list[str] = []
    for media in media_by_ref.get(source_ref, []):
        url = _media_url(media)
        if url:
            lines.append(f"  Media: {url}")
    return lines


def _text_media(value: Any) -> str:
    lines = ["MEDIA OBSERVATIONS"]
    items = _as_list(value)
    if not items:
        return "\n".join([*lines, "  No media observations."])
    for item in items:
        if isinstance(item, dict):
            label = " / ".join(str(part) for part in (item.get("media_id"), item.get("source_ref")) if part)
            lines.append(f"- {label or 'media'}: {_stringify_item(item.get('observation') or item)}")
        else:
            lines.append(f"- {_stringify_item(item)}")
    return "\n".join(lines)


def _text_simple_list(title: str, value: Any) -> str:
    lines = [title]
    items = _as_list(value)
    if not items:
        return "\n".join([*lines, "  No items."])
    lines.extend(f"- {_stringify_item(item)}" for item in items)
    return "\n".join(lines)


def _text_notable(value: Any) -> str:
    lines = ["NOTABLE SOURCES"]
    items = _as_list(value)
    if not items:
        return "\n".join([*lines, "  No notable sources."])
    for item in items:
        if isinstance(item, dict):
            ref = item.get("source_ref") or "source"
            lines.append(f"- {ref}: {_stringify_item(item.get('reason') or item.get('claim') or item)}")
            if item.get("url"):
                lines.append(f"  Source: {item['url']}")
        else:
            lines.append(f"- {_stringify_item(item)}")
    return "\n".join(lines)


def _text_metadata(value: Any) -> str:
    lines = ["METADATA"]
    if not isinstance(value, dict) or not value:
        return "\n".join([*lines, "  No metadata."])
    lines.extend(f"- {key}: {_stringify_item(item)}" for key, item in value.items())
    return "\n".join(lines)


def _text_response_meta(response: dict[str, Any]) -> str:
    parts = []
    for key in ("provider", "model"):
        if response.get(key):
            parts.append(f"{key}: {response[key]}")
    usage = _safe_usage(response)
    if usage:
        parts.append(f"tokens: {usage}")
    return " | ".join(parts)


def _safe_usage(response: dict[str, Any]) -> str:
    metadata = response.get("metadata")
    usage = metadata.get("usage") if isinstance(metadata, dict) else None
    if not isinstance(usage, dict):
        return ""
    total = usage.get("total_tokens")
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    parts = []
    if total is not None:
        parts.append(f"total {total}")
    if prompt is not None:
        parts.append(f"prompt {prompt}")
    if completion is not None:
        parts.append(f"completion {completion}")
    return ", ".join(parts)


def _post_title(post: dict[str, Any], *, reddit: bool) -> str:
    fallback = "Post And Comments Sentiment" if reddit else "Post"
    title = post.get("title") or post.get("claim") or post.get("post_summary") or fallback
    return _stringify_item(title)


def _source_link_label(index: int) -> str:
    return "Read source" if index == 1 else f"Read source {index}"


def _media_by_source_ref(response: dict[str, Any], summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    observations_by_id: dict[str, dict[str, Any]] = {}
    observations_without_media: list[dict[str, Any]] = []
    for item in _as_list(summary.get("media_observations")):
        if not isinstance(item, dict):
            continue
        media_id = item.get("media_id")
        if media_id:
            observations_by_id[str(media_id)] = item
        else:
            observations_without_media.append(item)

    grouped: dict[str, list[dict[str, Any]]] = {}
    input_media = response.get("input_media")
    if isinstance(input_media, dict):
        for media_id, media in input_media.items():
            if not isinstance(media, dict):
                continue
            source_ref = media.get("source_ref")
            if not source_ref:
                continue
            observation = observations_by_id.get(str(media_id), {})
            merged = {
                **media,
                **{key: value for key, value in observation.items() if value not in (None, "", [], {})},
                "media_id": str(media_id),
            }
            grouped.setdefault(str(source_ref), []).append(merged)

    for media_id, observation in observations_by_id.items():
        source_ref = observation.get("source_ref")
        if not source_ref:
            continue
        existing = grouped.setdefault(str(source_ref), [])
        if not any(item.get("media_id") == media_id for item in existing):
            existing.append({"media_id": media_id, **observation})

    for observation in observations_without_media:
        source_ref = observation.get("source_ref")
        if source_ref:
            grouped.setdefault(str(source_ref), []).append(observation)
    return grouped


def _media_url(media: dict[str, Any]) -> str:
    value = media.get("remote_url") or media.get("local_path") or media.get("url")
    return str(value) if value else ""


def _is_image_media(media: dict[str, Any]) -> bool:
    kind = str(media.get("kind") or media.get("media_type") or "").lower()
    url = _media_url(media).lower()
    return kind in {"image", "photo", "thumbnail", "gif"} or url.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))


def _as_list(value: Any) -> list[Any]:
    if value in (None, "", {}, []):
        return []
    if isinstance(value, list):
        return value
    return [value]


def _stringify_item(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _discord_sentiment_stats_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = []
    for sentiment in ("bullish", "bearish", "mixed", "neutral", "unclear"):
        count = value.get(sentiment, 0)
        parts.append(f"`{sentiment} {count}`")
    return " · ".join(parts)


def _plain_sentiment_stats_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return ", ".join(f"{sentiment}: {value.get(sentiment, 0)}" for sentiment in ("bullish", "bearish", "mixed", "neutral", "unclear"))


def _comments_text(item: dict[str, Any]) -> str:
    comments = _stringify_item(item["comments_sentiment"]) if item.get("comments_sentiment") else ""
    stats = _plain_sentiment_stats_text(item.get("comment_sentiment_counts"))
    if comments and _normalized_text(comments) != _normalized_text(stats):
        return comments
    return ""


def _normalized_text(value: str) -> str:
    return "".join(str(value).lower().split())


def _strip_json_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
