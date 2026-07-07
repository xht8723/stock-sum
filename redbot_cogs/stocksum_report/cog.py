"""Red Discord Bot cog for requesting stock-sum reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import quote
import asyncio
import os
import re

try:  # pragma: no cover - exercised in a Redbot runtime, not the project venv.
    import discord
    from redbot.core import app_commands, commands
except ModuleNotFoundError:  # pragma: no cover - lets local tests import the HTTP client helpers.
    discord = None

    class _FallbackCog:
        pass

    class _FallbackCommands:
        Cog = _FallbackCog

    class _FallbackAppCommands:
        class Group:
            def __init__(self, *_args, **_kwargs):
                pass

            def command(self, *_args, **_kwargs):
                def decorator(func):
                    return func

                return decorator

        @staticmethod
        def command(*_args, **_kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def describe(**_kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def choices(**_kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def rename(**_kwargs):
            def decorator(func):
                return func

            return decorator

        class Choice:
            def __init__(self, *, name: str, value: str) -> None:
                self.name = name
                self.value = value

    commands = _FallbackCommands()
    app_commands = _FallbackAppCommands()


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_POLL_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 30 * 60
DISCORD_INLINE_LIMIT = 1900
DISCORD_FAILURE_LIMIT = 1900
SUPPORTED_FORMATS = {"discord", "html", "markdown", "text", "json"}
SUPPORTED_SOCIAL_DETAILS = {"minimum", "medium", "full"}
SUPPORTED_STATISTIC_MODES = {"social", "trading"}
SUPPORTED_STATISTIC_BUCKETS = {"auto", "day", "week", "month"}
SUPPORTED_STATISTIC_SOURCES = {"x", "reddit", "all"}
SUPPORTED_STATISTIC_SENTIMENTS = {"bullish", "bearish", "mixed", "neutral", "unclear", "all"}
SUPPORTED_STATISTIC_ACTIONS = {"purchase", "sell", "sell_partial", "all"}
SUPPORTED_PUT_CALL = {"PUT", "CALL"}
FUZZY_REACTION_OPTIONS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
FUZZY_REACTION_DIGITS = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4}
FUZZY_SELECTION_TIMEOUT_SECONDS = 60.0
MAX_DAYS_FILTER = 3650
KNOWN_HOUSE_ASSET_TYPES = {"ST", "GS", "OI", "CS", "OT", "HN", "OP", "PS", "VA", "CT", "OL", "RS", "AB"}

_X_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9_]{1,15}$")
_SUBREDDIT_RE = re.compile(r"^(?:r/)?[A-Za-z0-9_]{2,21}$", re.IGNORECASE)
_ASSET_TYPE_RE = re.compile(r"^[A-Z0-9]{1,8}$")
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")
_CUSIP_RE = re.compile(r"^[A-Z0-9]{1,12}$")
_FIGI_RE = re.compile(r"^[A-Z0-9]{1,24}$")
_CIK_RE = re.compile(r"^[0-9]{1,10}$")
_ACCESSION_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")


class StockSumCogError(Exception):
    """Base error for user-facing stock-sum cog failures."""


class StockSumConfigurationError(StockSumCogError):
    """Raised when required local configuration is missing."""


class StockSumRequestError(StockSumCogError):
    """Raised when stock-sum rejects or cannot complete a request."""


class _ClientResponse(Protocol):
    status: int
    headers: Any

    async def json(self) -> Any:
        ...

    async def text(self) -> str:
        ...

    async def read(self) -> bytes:
        ...


class _RequestContext(Protocol):
    async def __aenter__(self) -> _ClientResponse:
        ...

    async def __aexit__(self, exc_type, exc, tb) -> None:
        ...


class _ClientSession(Protocol):
    def post(self, url: str, **kwargs: Any) -> _RequestContext:
        ...

    def delete(self, url: str, **kwargs: Any) -> _RequestContext:
        ...

    def get(self, url: str, **kwargs: Any) -> _RequestContext:
        ...

    async def close(self) -> None:
        ...


@dataclass(frozen=True)
class StockSumArtifact:
    """Downloaded stock-sum report artifact."""

    job_id: str
    filename: str
    content_type: str
    content: bytes
    status: dict[str, Any]


class StockSumHttpClient:
    """Small async client for the stock-sum local HTTP job API."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        session: _ClientSession | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds

    @classmethod
    def from_env(cls) -> "StockSumHttpClient":
        """Build a client from Redbot process environment variables."""

        return cls(
            base_url=os.getenv("STOCK_SUM_BASE_URL", DEFAULT_BASE_URL),
        )

    async def run_social_report(
        self,
        *,
        output_format: str,
        detail: str = "minimum",
    ) -> StockSumArtifact:
        """Create, poll, and download one stock-sum report job."""

        if output_format not in SUPPORTED_FORMATS:
            raise StockSumRequestError(f"Unsupported report format: {output_format}")
        if detail not in {"minimum", "medium", "full"}:
            raise StockSumRequestError(f"Unsupported social report detail: {detail}")

        session, owns_session = await self._session()
        try:
            job = await self._create_social_report_job(
                session,
                output_format=output_format,
                payload={"detail": detail},
            )
            job_id = _required_string(job, "job_id")
            status_payload = await self._poll_until_done(session, job_id)
            content, content_type, filename = await self._download_artifact(session, job_id, output_format)
            return StockSumArtifact(
                job_id=job_id,
                filename=filename,
                content_type=content_type,
                content=content,
                status=status_payload,
            )
        finally:
            if owns_session:
                await session.close()

    async def run_trading_report(
        self,
        *,
        output_format: str,
        name: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        days: int | None = None,
        asset_type: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        force_refresh: bool = False,
    ) -> StockSumArtifact:
        """Create, poll, and download one stock-sum trading disclosure report job."""

        if output_format not in SUPPORTED_FORMATS:
            raise StockSumRequestError(f"Unsupported report format: {output_format}")
        if not any((name, start_date, end_date, days, asset_type, ticker)):
            raise StockSumRequestError("ptr_search requires at least one filter: name, start_date/end_date, days, asset_type, or ticker.")

        session, owns_session = await self._session()
        try:
            payload = {
                "name": name,
                "start_date": start_date,
                "end_date": end_date,
                "days": days,
                "asset_type": asset_type,
                "ticker": ticker,
                "limit": limit,
                "force_refresh": force_refresh,
            }
            job = await self._create_trading_report_job(
                session,
                output_format=output_format,
                payload={key: value for key, value in payload.items() if value is not None},
            )
            job_id = _required_string(job, "job_id")
            status_payload = await self._poll_until_done(session, job_id)
            content, content_type, filename = await self._download_artifact(session, job_id, output_format)
            return StockSumArtifact(
                job_id=job_id,
                filename=filename,
                content_type=content_type,
                content=content,
                status=status_payload,
            )
        finally:
            if owns_session:
                await session.close()

    async def run_13f_report(
        self,
        *,
        output_format: str,
        manager: str | None = None,
        cik: str | None = None,
        accession_number: str | None = None,
        issuer: str | None = None,
        cusip: str | None = None,
        figi: str | None = None,
        put_call: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        filing_start: str | None = None,
        filing_end: str | None = None,
        min_value: int | None = None,
        min_shares: int | None = None,
        limit: int | None = None,
        force_refresh: bool = False,
    ) -> StockSumArtifact:
        """Create, poll, and download one SEC 13F holdings report job."""

        if output_format not in SUPPORTED_FORMATS:
            raise StockSumRequestError(f"Unsupported report format: {output_format}")
        if not any((manager, cik, accession_number, issuer, cusip, figi, put_call, period_start, period_end, filing_start, filing_end, min_value is not None, min_shares is not None)):
            raise StockSumRequestError("13f_search requires at least one filter: manager, issuer, cik, accession_number, cusip, figi, put_call, dates, min_value, or min_shares.")

        session, owns_session = await self._session()
        try:
            payload = {
                "manager": manager,
                "cik": cik,
                "accession_number": accession_number,
                "issuer": issuer,
                "cusip": cusip,
                "figi": figi,
                "put_call": put_call,
                "period_start": period_start,
                "period_end": period_end,
                "filing_start": filing_start,
                "filing_end": filing_end,
                "min_value": min_value,
                "min_shares": min_shares,
                "limit": limit,
                "force_refresh": force_refresh,
            }
            job = await self._create_13f_report_job(
                session,
                output_format=output_format,
                payload={key: value for key, value in payload.items() if value is not None},
            )
            job_id = _required_string(job, "job_id")
            status_payload = await self._poll_until_done(session, job_id)
            content, content_type, filename = await self._download_artifact(session, job_id, output_format)
            return StockSumArtifact(
                job_id=job_id,
                filename=filename,
                content_type=content_type,
                content=content,
                status=status_payload,
            )
        finally:
            if owns_session:
                await session.close()

    async def run_trendings_report(
        self,
        *,
        output_format: str,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int | None = None,
    ) -> StockSumArtifact:
        """Create, poll, and download one Adanos trendings report job."""

        if output_format not in SUPPORTED_FORMATS:
            raise StockSumRequestError(f"Unsupported report format: {output_format}")

        session, owns_session = await self._session()
        try:
            payload = {
                "from": from_date,
                "to": to_date,
                "limit": limit,
            }
            job = await self._create_trendings_report_job(
                session,
                output_format=output_format,
                payload={key: value for key, value in payload.items() if value is not None},
            )
            job_id = _required_string(job, "job_id")
            status_payload = await self._poll_until_done(session, job_id)
            content, content_type, filename = await self._download_artifact(session, job_id, output_format)
            return StockSumArtifact(
                job_id=job_id,
                filename=filename,
                content_type=content_type,
                content=content,
                status=status_payload,
            )
        finally:
            if owns_session:
                await session.close()

    async def run_statistic(
        self,
        *,
        mode: str,
        ticker: str | None = None,
        fuzzy_tag: str | None = None,
        name: str | None = None,
        asset_name: str | None = None,
        asset_type: str | None = None,
        action: str = "all",
        source: str = "all",
        sentiment: str = "all",
        start_date: str | None = None,
        end_date: str | None = None,
        days: int | None = None,
        bucket: str = "auto",
    ) -> StockSumArtifact:
        """Create, poll, and download one stock-sum statistic PNG job."""

        if mode not in SUPPORTED_STATISTIC_MODES:
            raise StockSumRequestError(f"Unsupported plot mode: {mode}")
        if bucket not in SUPPORTED_STATISTIC_BUCKETS:
            raise StockSumRequestError(f"Unsupported statistic bucket: {bucket}")
        if not any((ticker, fuzzy_tag, name, asset_name, asset_type, days, start_date, end_date)):
            raise StockSumRequestError("plot requires at least one filter: ticker, fuzzy_tag, name, asset_name, asset_type, days, or date range.")

        session, owns_session = await self._session()
        try:
            payload = {
                "mode": mode,
                "ticker": ticker,
                "fuzzy_tag": fuzzy_tag,
                "name": name,
                "asset_name": asset_name,
                "asset_type": asset_type,
                "action": action,
                "source": source,
                "sentiment": sentiment,
                "start_date": start_date,
                "end_date": end_date,
                "days": days,
                "bucket": bucket,
            }
            job = await self._create_statistic_job(
                session,
                payload={key: value for key, value in payload.items() if value is not None},
            )
            job_id = _required_string(job, "job_id")
            status_payload = await self._poll_until_done(session, job_id)
            content, content_type, filename = await self._download_artifact(session, job_id, "png")
            return StockSumArtifact(
                job_id=job_id,
                filename=filename,
                content_type=content_type,
                content=content,
                status=status_payload,
            )
        finally:
            if owns_session:
                await session.close()

    async def statistic_fuzzy_matches(
        self,
        *,
        mode: str,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return statistic fuzzy-match candidates."""

        if mode not in SUPPORTED_STATISTIC_MODES:
            raise StockSumRequestError(f"Unsupported plot mode: {mode}")
        params = f"mode={quote(mode, safe='')}&q={quote(query, safe='')}&limit={max(1, min(5, limit))}"
        payload = await self.get_json(f"/v1/statistics/fuzzy-matches?{params}")
        matches = payload.get("matches")
        if not isinstance(matches, list):
            raise StockSumRequestError("stock-sum returned malformed fuzzy search matches.")
        return [item for item in matches if isinstance(item, dict)]

    async def get_json(self, path: str, *, session: _ClientSession | None = None) -> dict[str, Any]:
        return await self._request_json("get", path, session=session, expected_status=200)

    async def post_json(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        session: _ClientSession | None = None,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        return await self._request_json("post", path, payload=payload, session=session, expected_status=expected_status)

    async def delete_json(self, path: str) -> dict[str, Any]:
        return await self._request_json("delete", path, expected_status=200)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        session: _ClientSession | None = None,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        owns_session = False
        if session is None:
            session, owns_session = await self._session()
        url = f"{self.base_url}{path}"
        try:
            request = getattr(session, method)
            kwargs: dict[str, Any] = {"headers": self._headers()}
            if payload is not None:
                kwargs["json"] = payload
            async with request(url, **kwargs) as response:
                return await self._json_response(response, expected_status=expected_status)
        except StockSumCogError:
            raise
        except Exception as exc:
            raise StockSumRequestError(f"Could not reach stock-sum at {self.base_url}: {exc}") from exc
        finally:
            if owns_session:
                await session.close()

    async def _session(self) -> tuple[_ClientSession, bool]:
        if self.session is not None:
            return self.session, False
        try:
            import aiohttp
        except ModuleNotFoundError as exc:  # pragma: no cover - Redbot normally provides aiohttp.
            raise StockSumConfigurationError("aiohttp is required in the Redbot runtime.") from exc
        return aiohttp.ClientSession(), True

    async def _create_social_report_job(
        self,
        session: _ClientSession,
        *,
        output_format: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self.base_url}/v1/social-reports/jobs/{quote(output_format, safe='')}"
        try:
            async with session.post(url, json=payload, headers=self._headers()) as response:
                return await self._json_response(response, expected_status=202)
        except StockSumCogError:
            raise
        except Exception as exc:
            raise StockSumRequestError(f"Could not reach stock-sum at {self.base_url}: {exc}") from exc

    async def _create_trading_report_job(
        self,
        session: _ClientSession,
        *,
        output_format: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self.base_url}/v1/trading-reports/jobs/{quote(output_format, safe='')}"
        try:
            async with session.post(url, json=payload, headers=self._headers()) as response:
                return await self._json_response(response, expected_status=202)
        except StockSumCogError:
            raise
        except Exception as exc:
            raise StockSumRequestError(f"Could not reach stock-sum at {self.base_url}: {exc}") from exc

    async def _create_13f_report_job(
        self,
        session: _ClientSession,
        *,
        output_format: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self.base_url}/v1/13f-reports/jobs/{quote(output_format, safe='')}"
        try:
            async with session.post(url, json=payload, headers=self._headers()) as response:
                return await self._json_response(response, expected_status=202)
        except StockSumCogError:
            raise
        except Exception as exc:
            raise StockSumRequestError(f"Could not reach stock-sum at {self.base_url}: {exc}") from exc

    async def _create_trendings_report_job(
        self,
        session: _ClientSession,
        *,
        output_format: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self.base_url}/v1/trendings/jobs/{quote(output_format, safe='')}"
        try:
            async with session.post(url, json=payload, headers=self._headers()) as response:
                return await self._json_response(response, expected_status=202)
        except StockSumCogError:
            raise
        except Exception as exc:
            raise StockSumRequestError(f"Could not reach stock-sum at {self.base_url}: {exc}") from exc

    async def _create_statistic_job(
        self,
        session: _ClientSession,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self.base_url}/v1/statistics/jobs"
        try:
            async with session.post(url, json=payload, headers=self._headers()) as response:
                return await self._json_response(response, expected_status=202)
        except StockSumCogError:
            raise
        except Exception as exc:
            raise StockSumRequestError(f"Could not reach stock-sum at {self.base_url}: {exc}") from exc

    async def _poll_until_done(self, session: _ClientSession, job_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/v1/jobs/{quote(job_id, safe='')}"
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        last_poll_error: Exception | None = None
        while True:
            if asyncio.get_running_loop().time() >= deadline:
                message = f"Report job {job_id} timed out after {int(self.timeout_seconds)} seconds."
                if last_poll_error is not None:
                    message += f" Last poll error: {last_poll_error}"
                raise StockSumRequestError(message)
            try:
                async with session.get(url, headers=self._headers()) as response:
                    payload = await self._json_response(response, expected_status=200)
            except StockSumCogError:
                raise
            except Exception as exc:
                last_poll_error = exc
                await asyncio.sleep(self.poll_seconds)
                continue

            status = str(payload.get("status") or "")
            if status == "succeeded":
                return payload
            if status == "failed":
                error = payload.get("error") or "stock-sum job failed"
                raise StockSumRequestError(f"Report job {job_id} failed: {error}")
            await asyncio.sleep(self.poll_seconds)

    async def _download_artifact(
        self,
        session: _ClientSession,
        job_id: str,
        output_format: str,
    ) -> tuple[bytes, str, str]:
        url = f"{self.base_url}/v1/jobs/{quote(job_id, safe='')}/artifact"
        try:
            async with session.get(url, headers=self._headers()) as response:
                if response.status != 200:
                    await self._raise_http_error(response)
                content = await response.read()
                content_type = _header_value(response.headers, "content-type") or "application/octet-stream"
                filename = _filename_from_response(response.headers) or _default_filename(job_id, output_format)
                return content, content_type, filename
        except StockSumCogError:
            raise
        except Exception as exc:
            raise StockSumRequestError(f"Could not download stock-sum report artifact: {exc}") from exc

    async def _json_response(self, response: _ClientResponse, *, expected_status: int) -> dict[str, Any]:
        if response.status != expected_status:
            await self._raise_http_error(response)
        payload = await response.json()
        if not isinstance(payload, dict):
            raise StockSumRequestError("stock-sum returned a malformed JSON response.")
        return payload

    async def _raise_http_error(self, response: _ClientResponse) -> None:
        message = await _response_error_text(response)
        if response.status == 401:
            raise StockSumRequestError("stock-sum rejected the request.")
        if response.status == 403:
            raise StockSumRequestError("stock-sum refused this client IP because it is blacklisted.")
        if response.status == 404:
            raise StockSumRequestError(f"stock-sum could not find the requested resource: {message}")
        if response.status == 503:
            raise StockSumRequestError(f"stock-sum is not ready: {message}")
        raise StockSumRequestError(f"stock-sum HTTP {response.status}: {message}")

    def _headers(self) -> dict[str, str]:
        return {}


class StockSumReport(commands.Cog):
    """Request stock-sum reports from Discord."""

    settings = app_commands.Group(name="settings", description="Manage stock-sum social sources.")

    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="List available stock-sum slash commands.")
    async def stocksum_help(self, interaction) -> None:
        """Slash command handler for listing stock-sum commands."""

        await interaction.response.send_message(_format_help_message(), ephemeral=False)

    @app_commands.command(name="recent_posts", description="Generate a stock-sum social media market report.")
    @app_commands.describe(
        detail="how many social sentiment items to include",
    )
    @app_commands.choices(
        detail=[
            app_commands.Choice(name="Minimum", value="minimum"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="Full", value="full"),
        ]
    )
    async def recent_posts(
        self,
        interaction,
        detail: str = "minimum",
    ) -> None:
        """Slash command handler for social report generation."""

        if error := _validate_report_options(output_format="discord", detail=detail):
            await _send_validation_error(interaction, error)
            return

        await interaction.response.send_message(
            "Social report is being generated, please wait a few minutes.",
            ephemeral=False,
        )
        try:
            artifact = await StockSumHttpClient.from_env().run_social_report(
                output_format="discord",
                detail=detail,
            )
        except StockSumCogError as exc:
            await _send_report_output(interaction, _failure_message(exc), private=False)
            return

        if discord is None:
            await _send_report_output(
                interaction,
                "stock-sum report is ready, but discord.py is not available.",
                private=False,
            )
            return

        report_text = artifact.content.decode("utf-8", errors="replace").strip()
        for chunk in _split_discord_markdown(report_text):
            await _send_report_output(interaction, chunk, private=False)

    @app_commands.command(name="ptr_search", description="Generate House trading disclosures. Provide at least one filter.")
    @app_commands.describe(
        name="case-insensitive fuzzy filer name filter",
        start_date="transaction start date, YYYY-MM-DD",
        end_date="transaction end date, YYYY-MM-DD",
        days="transaction records from the last N days",
        asset_type="House asset type code, e.g. ST, GS, OI, CS, OT",
        ticker="stock ticker for ST rows, e.g. AMZN",
        limit="maximum rows to return; uses stock-sum default if omitted",
        force_refresh="force a House PTR refresh before querying",
    )
    async def ptr_search(
        self,
        interaction,
        name: str = "",
        start_date: str = "",
        end_date: str = "",
        days: int | None = None,
        asset_type: str = "",
        ticker: str = "",
        limit: int | None = None,
        force_refresh: bool = False,
    ) -> None:
        """Slash command handler for House PTR trading disclosure reports."""

        name_filter = name.strip() or None
        start_filter, end_filter, error = _validate_date_range(
            start_date,
            end_date,
            start_label="start_date",
            end_label="end_date",
        )
        if error:
            await _send_validation_error(interaction, error)
            return
        asset_type_filter = asset_type.strip().upper() or None
        ticker_filter = ticker.strip().upper() or None
        if error := _validate_positive_int(days, label="days", maximum=MAX_DAYS_FILTER):
            await _send_validation_error(interaction, error)
            return
        if error := _validate_positive_int(limit, label="limit"):
            await _send_validation_error(interaction, error)
            return
        if error := _validate_asset_type(asset_type_filter):
            await _send_validation_error(interaction, error)
            return
        if error := _validate_ticker(ticker_filter):
            await _send_validation_error(interaction, error)
            return
        if not any((name_filter, start_filter, end_filter, days, asset_type_filter, ticker_filter)):
            await _send_validation_error(
                interaction,
                "ptr_search requires at least one filter: name, start_date/end_date, days, asset_type, or ticker.",
            )
            return

        await interaction.response.send_message(
            "Trading disclosure report is being generated, please wait a few minutes.",
            ephemeral=False,
        )
        try:
            artifact = await StockSumHttpClient.from_env().run_trading_report(
                output_format="discord",
                name=name_filter,
                start_date=start_filter,
                end_date=end_filter,
                days=days,
                asset_type=asset_type_filter,
                ticker=ticker_filter,
                limit=limit,
                force_refresh=force_refresh,
            )
        except StockSumCogError as exc:
            await _send_report_output(interaction, _failure_message(exc), private=False)
            return

        if discord is None:
            await _send_report_output(
                interaction,
                "stock-sum report is ready, but discord.py is not available.",
                private=False,
            )
            return

        report_text = artifact.content.decode("utf-8", errors="replace").strip()
        for chunk in _split_discord_markdown(report_text):
            await _send_report_output(interaction, chunk, private=False)

    @app_commands.command(name="13f_search", description="Generate SEC 13F holdings. Provide manager, issuer, ID, dates, value, or shares.")
    @app_commands.describe(
        manager="case-insensitive filing manager name filter",
        issuer="case-insensitive issuer name filter",
        cik="manager CIK",
        accession_number="SEC accession number",
        cusip="security CUSIP",
        figi="security FIGI",
        put_call="PUT or CALL",
        period_start="period-of-report start date, YYYY-MM-DD",
        period_end="period-of-report end date, YYYY-MM-DD",
        filing_start="filing start date, YYYY-MM-DD",
        filing_end="filing end date, YYYY-MM-DD",
        min_value="minimum reported holding value",
        min_shares="minimum shares/principal amount",
        limit="maximum rows to return; uses stock-sum default if omitted",
        force_refresh="force latest SEC 13F dataset refresh before querying",
    )
    @app_commands.choices(
        put_call=[
            app_commands.Choice(name="PUT", value="PUT"),
            app_commands.Choice(name="CALL", value="CALL"),
        ],
    )
    async def thirteenf_search(
        self,
        interaction,
        manager: str = "",
        issuer: str = "",
        cik: str = "",
        accession_number: str = "",
        cusip: str = "",
        figi: str = "",
        put_call: str = "",
        period_start: str = "",
        period_end: str = "",
        filing_start: str = "",
        filing_end: str = "",
        min_value: int | None = None,
        min_shares: int | None = None,
        limit: int | None = None,
        force_refresh: bool = False,
    ) -> None:
        """Slash command handler for SEC 13F holdings reports."""

        manager_filter = manager.strip() or None
        issuer_filter = issuer.strip() or None
        cik_filter = cik.strip() or None
        accession_filter = accession_number.strip() or None
        cusip_filter = cusip.strip().upper() or None
        figi_filter = figi.strip().upper() or None
        put_call_filter = put_call.strip().upper() or None
        period_start_filter, period_end_filter, error = _validate_date_range(
            period_start,
            period_end,
            start_label="period_start",
            end_label="period_end",
        )
        if error:
            await _send_validation_error(interaction, error)
            return
        filing_start_filter, filing_end_filter, error = _validate_date_range(
            filing_start,
            filing_end,
            start_label="filing_start",
            end_label="filing_end",
        )
        if error:
            await _send_validation_error(interaction, error)
            return
        if put_call_filter and put_call_filter not in SUPPORTED_PUT_CALL:
            await _send_validation_error(interaction, "put_call must be PUT or CALL.")
            return
        for value, label, pattern in (
            (cik_filter, "cik", _CIK_RE),
            (accession_filter, "accession_number", _ACCESSION_RE),
            (cusip_filter, "cusip", _CUSIP_RE),
            (figi_filter, "figi", _FIGI_RE),
        ):
            if error := _validate_13f_identifier(value, label=label, pattern=pattern):
                await _send_validation_error(interaction, error)
                return
        if error := _validate_positive_int(min_value, label="min_value", allow_zero=True):
            await _send_validation_error(interaction, error)
            return
        if error := _validate_positive_int(min_shares, label="min_shares", allow_zero=True):
            await _send_validation_error(interaction, error)
            return
        if error := _validate_positive_int(limit, label="limit"):
            await _send_validation_error(interaction, error)
            return
        if not any((manager_filter, issuer_filter, cik_filter, accession_filter, cusip_filter, figi_filter, put_call_filter, period_start_filter, period_end_filter, filing_start_filter, filing_end_filter, min_value is not None, min_shares is not None)):
            await _send_validation_error(
                interaction,
                "13f_search requires at least one filter: manager, issuer, cik, accession_number, cusip, figi, put_call, dates, min_value, or min_shares.",
            )
            return

        await interaction.response.send_message(
            "SEC 13F report is being generated, please wait a few minutes.",
            ephemeral=False,
        )
        try:
            artifact = await StockSumHttpClient.from_env().run_13f_report(
                output_format="discord",
                manager=manager_filter,
                issuer=issuer_filter,
                cik=cik_filter,
                accession_number=accession_filter,
                cusip=cusip_filter,
                figi=figi_filter,
                put_call=put_call_filter,
                period_start=period_start_filter,
                period_end=period_end_filter,
                filing_start=filing_start_filter,
                filing_end=filing_end_filter,
                min_value=min_value,
                min_shares=min_shares,
                limit=limit,
                force_refresh=force_refresh,
            )
        except StockSumCogError as exc:
            await _send_report_output(interaction, _failure_message(exc), private=False)
            return

        if discord is None:
            await _send_report_output(
                interaction,
                "stock-sum report is ready, but discord.py is not available.",
                private=False,
            )
            return

        report_text = artifact.content.decode("utf-8", errors="replace").strip()
        for chunk in _split_discord_markdown(report_text):
            await _send_report_output(interaction, chunk, private=False)

    @app_commands.command(name="trendings", description="Generate Adanos trending stocks and sectors.")
    @app_commands.rename(from_date="from")
    @app_commands.describe(
        from_date="start date, YYYY-MM-DD; defaults to 7-day window",
        to_date="end date, YYYY-MM-DD; defaults to current UTC date",
        limit="rows to display per platform and section; stock-sum default if omitted",
    )
    async def trendings(
        self,
        interaction,
        from_date: str = "",
        to_date: str = "",
        limit: int | None = None,
    ) -> None:
        """Slash command handler for Adanos trendings reports."""

        from_filter, to_filter, error = _validate_date_range(
            from_date,
            to_date,
            start_label="from",
            end_label="to",
        )
        if error:
            await _send_validation_error(interaction, error)
            return
        if error := _validate_positive_int(limit, label="limit"):
            await _send_validation_error(interaction, error)
            return

        await interaction.response.send_message(
            "Trendings report is being generated, please wait a few minutes.",
            ephemeral=False,
        )
        try:
            artifact = await StockSumHttpClient.from_env().run_trendings_report(
                output_format="discord",
                from_date=from_filter,
                to_date=to_filter,
                limit=limit,
            )
        except StockSumCogError as exc:
            await _send_report_output(interaction, _failure_message(exc), private=False)
            return

        if discord is None:
            await _send_report_output(
                interaction,
                "stock-sum report is ready, but discord.py is not available.",
                private=False,
            )
            return

        report_text = artifact.content.decode("utf-8", errors="replace").strip()
        for chunk in _split_discord_markdown(report_text):
            await _send_report_output(interaction, chunk, private=False)

    @app_commands.command(name="plot", description="Generate a statistics chart. Provide ticker, fuzzy_search, name, asset_type, days, or dates.")
    @app_commands.describe(
        mode="plot mode",
        ticker="stock ticker filter, e.g. NVDA",
        fuzzy_search="fuzzy search text; social searches tags, trading searches assets",
        name="House filer name filter for trading mode",
        asset_type="House asset type code for trading mode, e.g. ST",
        action="House trading action filter",
        source="social source filter",
        sentiment="social sentiment filter",
        days="records within the last N days",
        start_date="start date, YYYY-MM-DD",
        end_date="end date, YYYY-MM-DD",
        bucket="time bucket",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Social media", value="social"),
            app_commands.Choice(name="Financial disclosures", value="trading"),
        ],
        action=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Purchase", value="purchase"),
            app_commands.Choice(name="Sell", value="sell"),
            app_commands.Choice(name="Sell partial", value="sell_partial"),
        ],
        source=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="X", value="x"),
            app_commands.Choice(name="Reddit", value="reddit"),
        ],
        sentiment=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Bullish", value="bullish"),
            app_commands.Choice(name="Bearish", value="bearish"),
            app_commands.Choice(name="Mixed", value="mixed"),
            app_commands.Choice(name="Neutral", value="neutral"),
            app_commands.Choice(name="Unclear", value="unclear"),
        ],
        bucket=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="Day", value="day"),
            app_commands.Choice(name="Week", value="week"),
            app_commands.Choice(name="Month", value="month"),
        ],
    )
    async def plot(
        self,
        interaction,
        mode: str,
        ticker: str = "",
        fuzzy_search: str = "",
        name: str = "",
        asset_type: str = "",
        action: str = "all",
        source: str = "all",
        sentiment: str = "all",
        days: int | None = None,
        start_date: str = "",
        end_date: str = "",
        bucket: str = "auto",
    ) -> None:
        """Slash command handler for statistic PNG charts."""

        mode_filter = mode.strip().lower()
        ticker_filter = ticker.strip().upper() or None
        fuzzy_search_filter = fuzzy_search.strip() or None
        name_filter = name.strip() or None
        asset_type_filter = asset_type.strip().upper() or None
        action_filter = action.strip().lower() or "all"
        source_filter = source.strip().lower() or "all"
        sentiment_filter = sentiment.strip().lower() or "all"
        bucket_filter = bucket.strip().lower() or "auto"
        start_filter, end_filter, error = _validate_date_range(
            start_date,
            end_date,
            start_label="start_date",
            end_label="end_date",
        )
        if error:
            await _send_validation_error(interaction, error)
            return
        if ticker_filter and fuzzy_search_filter:
            await _send_validation_error(interaction, "Use either ticker or fuzzy_search, not both.")
            return
        if error := _validate_statistic_options(
            mode=mode_filter,
            ticker=ticker_filter,
            fuzzy_tag=fuzzy_search_filter,
            name=name_filter,
            asset_name=None,
            asset_type=asset_type_filter,
            action=action_filter,
            source=source_filter,
            sentiment=sentiment_filter,
            days=days,
            start_date=start_filter,
            end_date=end_filter,
            bucket=bucket_filter,
        ):
            await _send_validation_error(interaction, error)
            return

        selected_filters: dict[str, Any] = {}
        if fuzzy_search_filter:
            try:
                selected_filters = await self._select_statistic_fuzzy_match(
                    interaction,
                    mode=mode_filter,
                    query=fuzzy_search_filter,
                )
            except StockSumCogError as exc:
                await _send_validation_error(interaction, str(exc))
                return
            except Exception as exc:
                await _send_report_output(interaction, _failure_message(exc), private=False)
                return
            if not selected_filters:
                return

        if selected_filters:
            await _send_report_output(
                interaction,
                "Statistic chart is being generated, please wait a few minutes.",
                private=False,
            )
        else:
            await interaction.response.send_message(
                "Statistic chart is being generated, please wait a few minutes.",
                ephemeral=False,
            )
        try:
            artifact = await StockSumHttpClient.from_env().run_statistic(
                mode=mode_filter,
                ticker=selected_filters.get("ticker") or ticker_filter,
                fuzzy_tag=selected_filters.get("fuzzy_tag"),
                name=name_filter,
                asset_name=selected_filters.get("asset_name"),
                asset_type=asset_type_filter or selected_filters.get("asset_type"),
                action=action_filter,
                source=source_filter,
                sentiment=sentiment_filter,
                days=days,
                start_date=start_filter,
                end_date=end_filter,
                bucket=bucket_filter,
            )
        except StockSumCogError as exc:
            await _send_report_output(interaction, _failure_message(exc), private=False)
            return

        if discord is None:
            await _send_report_output(
                interaction,
                "stock-sum statistic is ready, but discord.py is not available.",
                private=False,
            )
            return
        file = discord.File(BytesIO(artifact.content), filename=artifact.filename)
        await _send_report_output(interaction, "Statistic generated.", private=False, file=file)

    async def _select_statistic_fuzzy_match(
        self,
        interaction,
        *,
        mode: str,
        query: str,
    ) -> dict[str, Any]:
        matches = await StockSumHttpClient.from_env().statistic_fuzzy_matches(
            mode=mode,
            query=query,
            limit=len(FUZZY_REACTION_OPTIONS),
        )
        if not matches:
            raise StockSumRequestError(f"No fuzzy_search matches found for {query!r}.")
        message = await _send_fuzzy_selection_message(interaction, _format_fuzzy_match_prompt(query, matches))
        usable_reactions = FUZZY_REACTION_OPTIONS[: len(matches)]
        for emoji in usable_reactions:
            if hasattr(message, "add_reaction"):
                try:
                    await message.add_reaction(emoji)
                except Exception:
                    continue
        usable_indexes = set(range(len(usable_reactions)))

        def check(payload) -> bool:
            selected_index = _fuzzy_reaction_index(getattr(payload, "emoji", payload))
            return (
                getattr(payload, "message_id", None) == getattr(message, "id", None)
                and getattr(payload, "user_id", None) == getattr(interaction.user, "id", None)
                and selected_index in usable_indexes
            )

        try:
            payload = await self.bot.wait_for(
                "raw_reaction_add",
                timeout=FUZZY_SELECTION_TIMEOUT_SECONDS,
                check=check,
            )
        except asyncio.TimeoutError:
            await _edit_message_content(message, "Selection timed out. Run /plot again to retry.")
            return {}

        selected_index = _fuzzy_reaction_index(getattr(payload, "emoji", payload))
        if selected_index is None or selected_index >= len(matches):
            await _edit_message_content(message, "Selection timed out. Run /plot again to retry.")
            return {}
        selected = matches[selected_index]
        label = str(selected.get("label") or selected.get("match_value") or "selection")
        await _send_report_output(interaction, f"Selected: {label}. Generating statistic chart...", private=False)
        filters = selected.get("statistic_filters")
        return filters if isinstance(filters, dict) else {}

    @settings.command(name="list", description="List configured X and Reddit sources.")
    async def settings_list(self, interaction) -> None:
        try:
            response = await StockSumHttpClient.from_env().get_json("/v1/sources")
        except StockSumCogError as exc:
            await _send_command_output(interaction, _failure_message(exc), private=True)
            return
        await _send_command_output(interaction, _format_sources_message(response), private=False)

    @settings.command(name="add-x", description="Add an X user source, e.g. aleabitoreddit or @aleabitoreddit.")
    @app_commands.describe(handle="X handle, e.g. aleabitoreddit or @aleabitoreddit")
    async def settings_add_x(self, interaction, handle: str) -> None:
        if not await self._require_owner(interaction):
            return
        if error := _validate_x_handle(handle):
            await _send_validation_error(interaction, error)
            return
        await self._send_api_json(
            interaction,
            "/v1/sources/x-users",
            method="post",
            payload={"handle": handle},
            title=f"Added X source {handle}",
            private=True,
        )

    @settings.command(name="remove-x", description="Remove an X user source, e.g. aleabitoreddit or @aleabitoreddit.")
    @app_commands.describe(handle="X handle, e.g. aleabitoreddit or @aleabitoreddit")
    async def settings_remove_x(self, interaction, handle: str) -> None:
        if not await self._require_owner(interaction):
            return
        if error := _validate_x_handle(handle):
            await _send_validation_error(interaction, error)
            return
        path = f"/v1/sources/x-users/{quote(handle, safe='')}"
        await self._send_api_json(interaction, path, method="delete", title=f"Removed X source {handle}", private=True)

    @settings.command(name="add-reddit", description="Add a subreddit source, e.g. wallstreetbets or r/wallstreetbets.")
    @app_commands.describe(subreddit="Subreddit name, e.g. wallstreetbets or r/wallstreetbets")
    async def settings_add_reddit(self, interaction, subreddit: str) -> None:
        if not await self._require_owner(interaction):
            return
        if error := _validate_subreddit(subreddit):
            await _send_validation_error(interaction, error)
            return
        await self._send_api_json(
            interaction,
            "/v1/sources/subreddits",
            method="post",
            payload={"subreddit": subreddit},
            title=f"Added subreddit {subreddit}",
            private=True,
        )

    @settings.command(name="remove-reddit", description="Remove a subreddit source, e.g. wallstreetbets or r/wallstreetbets.")
    @app_commands.describe(subreddit="Subreddit name, e.g. wallstreetbets or r/wallstreetbets")
    async def settings_remove_reddit(self, interaction, subreddit: str) -> None:
        if not await self._require_owner(interaction):
            return
        if error := _validate_subreddit(subreddit):
            await _send_validation_error(interaction, error)
            return
        path = f"/v1/sources/subreddits/{quote(subreddit, safe='')}"
        await self._send_api_json(interaction, path, method="delete", title=f"Removed subreddit {subreddit}", private=True)

    async def _require_owner(self, interaction) -> bool:
        checker = getattr(self.bot, "is_owner", None)
        allowed = False
        if checker is not None:
            result = checker(getattr(interaction, "user", None))
            allowed = await result if hasattr(result, "__await__") else bool(result)
        if allowed:
            return True
        await _send_command_output(interaction, "Only Redbot owners can use this stock-sum command.", private=True)
        return False

    async def _send_api_json(
        self,
        interaction,
        path: str,
        *,
        method: str = "get",
        payload: dict[str, Any] | None = None,
        title: str,
        private: bool = False,
    ) -> None:
        try:
            client = StockSumHttpClient.from_env()
            if method == "get":
                response = await client.get_json(path)
            elif method == "post":
                response = await client.post_json(path, payload=payload)
            elif method == "delete":
                response = await client.delete_json(path)
            else:
                raise StockSumRequestError(f"Unsupported management method: {method}")
        except StockSumCogError as exc:
            await _send_command_output(interaction, _failure_message(exc), private=True)
            return
        await _send_command_output(interaction, _format_json_message(title, response), private=private)


async def _response_error_text(response: _ClientResponse) -> str:
    try:
        payload = await response.json()
    except Exception:
        text = await response.text()
        return text[:500]
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if detail is not None:
            return str(detail)
    return str(payload)[:500]


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise StockSumRequestError(f"stock-sum response is missing {key}.")
    return value


def _validate_report_options(*, output_format: str, detail: str | None = None) -> str | None:
    if output_format not in SUPPORTED_FORMATS:
        return f"Unsupported report format: {output_format}. Use one of: {', '.join(sorted(SUPPORTED_FORMATS))}."
    if detail is not None and detail not in SUPPORTED_SOCIAL_DETAILS:
        return f"Unsupported social report detail: {detail}. Use minimum, medium, or full."
    return None


def _validate_x_handle(value: str) -> str | None:
    if not _X_HANDLE_RE.fullmatch(value.strip()):
        return "X handle must be 1-15 characters using letters, numbers, or underscore, with optional @."
    return None


def _validate_subreddit(value: str) -> str | None:
    if not _SUBREDDIT_RE.fullmatch(value.strip()):
        return "Subreddit must be 2-21 characters using letters, numbers, or underscore, with optional r/ prefix."
    return None


def _validate_optional_date(value: str | None, *, label: str) -> tuple[str | None, date | None, str | None]:
    clean = value.strip() if isinstance(value, str) else value
    if not clean:
        return None, None, None
    try:
        parsed = datetime.strptime(clean, "%Y-%m-%d").date()
    except ValueError:
        return clean, None, f"{label} must be in YYYY-MM-DD format."
    return clean, parsed, None


def _validate_date_range(
    start: str | None,
    end: str | None,
    *,
    start_label: str,
    end_label: str,
) -> tuple[str | None, str | None, str | None]:
    clean_start, start_date, error = _validate_optional_date(start, label=start_label)
    if error:
        return clean_start, end, error
    clean_end, end_date, error = _validate_optional_date(end, label=end_label)
    if error:
        return clean_start, clean_end, error
    if start_date is not None and end_date is not None and start_date > end_date:
        return clean_start, clean_end, f"{start_label} must be on or before {end_label}."
    return clean_start, clean_end, None


def _validate_positive_int(value: int | None, *, label: str, maximum: int | None = None, allow_zero: bool = False) -> str | None:
    if value is None:
        return None
    minimum = 0 if allow_zero else 1
    if value < minimum:
        return f"{label} must be {'0 or greater' if allow_zero else '1 or greater'}."
    if maximum is not None and value > maximum:
        return f"{label} must be {maximum} or less."
    return None


def _validate_asset_type(value: str | None) -> str | None:
    if not value:
        return None
    if not _ASSET_TYPE_RE.fullmatch(value):
        return "asset_type must be a short alphanumeric House asset code, such as ST, GS, OI, CS, OT, HN, OP, PS, VA, CT, OL, RS, or AB."
    if value not in KNOWN_HOUSE_ASSET_TYPES:
        return f"asset_type {value} is not a known House asset code. Known codes: {', '.join(sorted(KNOWN_HOUSE_ASSET_TYPES))}."
    return None


def _validate_ticker(value: str | None) -> str | None:
    if value and not _TICKER_RE.fullmatch(value):
        return "ticker must be 1-16 characters using letters, numbers, dot, or dash."
    return None


def _validate_statistic_options(
    *,
    mode: str,
    ticker: str | None,
    fuzzy_tag: str | None,
    name: str | None,
    asset_name: str | None,
    asset_type: str | None,
    action: str,
    source: str,
    sentiment: str,
    days: int | None,
    start_date: str | None,
    end_date: str | None,
    bucket: str,
) -> str | None:
    if mode not in SUPPORTED_STATISTIC_MODES:
        return "plot mode must be social or trading."
    if error := _validate_ticker(ticker):
        return error
    if error := _validate_asset_type(asset_type):
        return error
    if action not in SUPPORTED_STATISTIC_ACTIONS:
        return "action must be purchase, sell, sell_partial, or all."
    if source not in SUPPORTED_STATISTIC_SOURCES:
        return "source must be x, reddit, or all."
    if sentiment not in SUPPORTED_STATISTIC_SENTIMENTS:
        return "sentiment must be bullish, bearish, mixed, neutral, unclear, or all."
    if bucket not in SUPPORTED_STATISTIC_BUCKETS:
        return "bucket must be auto, day, week, or month."
    if error := _validate_positive_int(days, label="days", maximum=MAX_DAYS_FILTER):
        return error
    if days is not None and (start_date or end_date):
        return "plot accepts either days or explicit start/end dates, not both."
    if not any((ticker, fuzzy_tag, name, asset_name, asset_type, days, start_date, end_date)):
        return "plot requires at least one filter: ticker, fuzzy_search, name, asset_type, days, or date range."
    return None


def _validate_13f_identifier(value: str | None, *, label: str, pattern: re.Pattern[str]) -> str | None:
    if value and not pattern.fullmatch(value):
        return f"{label} has an invalid format."
    return None


async def _send_validation_error(interaction, message: str) -> None:
    await _send_command_output(interaction, f"stock-sum report failed: {message}", private=True)


def _failure_message(exc: Exception) -> str:
    message = f"stock-sum report failed: {exc}"
    if len(message) <= DISCORD_FAILURE_LIMIT:
        return message
    return message[: DISCORD_FAILURE_LIMIT - 3].rstrip() + "..."


async def _send_command_output(interaction, content: str, *, private: bool, file: Any | None = None) -> None:
    response = getattr(interaction, "response", None)
    is_done = getattr(response, "is_done", None)
    if response is not None and hasattr(response, "send_message"):
        done = is_done() if callable(is_done) else False
        if not done:
            await response.send_message(content, ephemeral=private, file=file, suppress_embeds=True)
            return
    await _send_report_output(interaction, content, private=private, file=file)


async def _send_report_output(interaction, content: str, *, private: bool, file: Any | None = None) -> None:
    """Send final report output without replying to the acknowledgement when public."""

    if private:
        await interaction.followup.send(content, ephemeral=True, file=file, suppress_embeds=True)
        return

    channel = getattr(interaction, "channel", None)
    if channel is not None and hasattr(channel, "send"):
        if file is None:
            await channel.send(content, suppress_embeds=True)
        else:
            await channel.send(content, file=file, suppress_embeds=True)
        return

    await interaction.followup.send(content, ephemeral=False, file=file, suppress_embeds=True)


async def _send_public_interaction_message(interaction, content: str) -> Any:
    response = getattr(interaction, "response", None)
    is_done = getattr(response, "is_done", None)
    done = is_done() if callable(is_done) else False
    if response is not None and hasattr(response, "send_message") and not done:
        maybe_message = await response.send_message(content, ephemeral=False, suppress_embeds=True)
        if maybe_message is not None:
            return maybe_message
        original_response = getattr(interaction, "original_response", None)
        if callable(original_response):
            return await original_response()
    channel = getattr(interaction, "channel", None)
    if channel is not None and hasattr(channel, "send"):
        return await channel.send(content, suppress_embeds=True)
    await interaction.followup.send(content, ephemeral=False, suppress_embeds=True)
    original_response = getattr(interaction, "original_response", None)
    if callable(original_response):
        return await original_response()
    raise StockSumRequestError("Could not create fuzzy search selection message.")


async def _send_fuzzy_selection_message(interaction, content: str) -> Any:
    response = getattr(interaction, "response", None)
    is_done = getattr(response, "is_done", None)
    done = is_done() if callable(is_done) else False
    if response is not None and not done and hasattr(response, "defer"):
        try:
            await response.defer(ephemeral=False, thinking=True)
        except Exception:
            pass

    channel = getattr(interaction, "channel", None)
    if channel is not None and hasattr(channel, "send"):
        return await channel.send(content, suppress_embeds=True)

    followup = getattr(interaction, "followup", None)
    if followup is not None and hasattr(followup, "send"):
        try:
            maybe_message = await followup.send(content, ephemeral=False, suppress_embeds=True, wait=True)
        except TypeError:
            maybe_message = await followup.send(content, ephemeral=False, suppress_embeds=True)
        if maybe_message is not None:
            return maybe_message

    return await _send_public_interaction_message(interaction, content)


async def _edit_message_content(message: Any, content: str) -> None:
    if hasattr(message, "edit"):
        await message.edit(content=content)
        return
    channel = getattr(message, "channel", None)
    if channel is not None and hasattr(channel, "send"):
        await channel.send(content, suppress_embeds=True)


def _format_fuzzy_match_prompt(query: str, matches: list[dict[str, Any]]) -> str:
    lines = [f"Select a fuzzy_search match for `{query}`:"]
    for index, match in enumerate(matches[: len(FUZZY_REACTION_OPTIONS)], start=1):
        label = str(match.get("label") or match.get("match_value") or "Unknown")
        row_count = int(match.get("row_count") or 0)
        mode = str(match.get("mode") or "")
        if mode == "social":
            x_count = int(match.get("x_count") or 0)
            reddit_count = int(match.get("reddit_count") or 0)
            detail = f"{row_count} posts, X {x_count}, Reddit {reddit_count}"
        else:
            ticker = str(match.get("ticker") or "").strip()
            asset_type = str(match.get("asset_type_code") or "").strip()
            extras = ", ".join(item for item in (ticker, asset_type) if item)
            detail = f"{row_count} rows" + (f", {extras}" if extras else "")
        lines.append(f"{FUZZY_REACTION_OPTIONS[index - 1]} {label} - {detail}")
    lines.append("Click one of the numbered reactions below to choose.")
    return "\n".join(lines)


def _fuzzy_reaction_index(value: Any) -> int | None:
    emoji = str(value).strip().replace("\ufe0f", "").replace("\u20e3", "")
    return FUZZY_REACTION_DIGITS.get(emoji)


def _format_json_message(title: str, payload: dict[str, Any]) -> str:
    text = json_dumps_compact(payload)
    message = f"**{title}**\n```json\n{text}\n```"
    if len(message) <= DISCORD_INLINE_LIMIT:
        return message
    return f"**{title}**\n```json\n{text[: DISCORD_INLINE_LIMIT - len(title) - 24].rstrip()}...\n```"


def _format_help_message() -> str:
    lines = [
        "**Stock-Sum Commands**",
        "",
        "`/recent_posts` - Social media market report from configured X and Reddit sources.",
        "`/ptr_search` - Search official House PTR trading disclosures. Provide at least one filter such as name, ticker, asset_type, days, or dates.",
        "`/13f_search` - Search SEC 13F holdings. Provide at least one filter such as manager, issuer, CIK, security ID, dates, min_value, or min_shares.",
        "`/trendings` - Trending stocks and sectors from Adanos.",
        "`/plot` - Generate a sentiment or disclosure statistic chart. Provide mode plus ticker, fuzzy_search, name, asset_type, days, or dates.",
        "`/settings list` - List configured X users and subreddits.",
        "`/settings add-x` - Owner only. Add an X user source, e.g. `aleabitoreddit` or `@aleabitoreddit`.",
        "`/settings remove-x` - Owner only. Remove an X user source.",
        "`/settings add-reddit` - Owner only. Add a subreddit source, e.g. `wallstreetbets` or `r/wallstreetbets`.",
        "`/settings remove-reddit` - Owner only. Remove a subreddit source.",
        "`/help` - Show this command list.",
    ]
    return "\n".join(lines)


def _format_sources_message(payload: dict[str, Any]) -> str:
    x_users = payload.get("x_users")
    subreddits = payload.get("subreddits")
    lines = ["**Stock-Sum Sources**", "", "**X users**"]
    if isinstance(x_users, list) and x_users:
        for source in x_users:
            if isinstance(source, dict):
                handle = str(source.get("handle") or "").lstrip("@")
                state = "enabled" if source.get("enabled", True) else "disabled"
                limit = source.get("limit")
                lookback = source.get("lookback_hours")
                details = _compact_detail([state, _kv("fetch cap", limit), _kv("lookback", f"{lookback}h" if lookback is not None else None)])
                lines.append(f"- @{handle}{details}")
    else:
        lines.append("- none")

    lines.extend(["", "**Subreddits**"])
    if isinstance(subreddits, list) and subreddits:
        for source in subreddits:
            if isinstance(source, dict):
                subreddit = str(source.get("subreddit") or "").removeprefix("r/")
                state = "enabled" if source.get("enabled", True) else "disabled"
                limit = source.get("limit")
                lookback = source.get("lookback_hours")
                comments = source.get("comments_per_post")
                details = _compact_detail(
                    [
                        state,
                        _kv("fetch cap", limit),
                        _kv("lookback", f"{lookback}h" if lookback is not None else None),
                        _kv("comments", comments),
                    ]
                )
                lines.append(f"- r/{subreddit}{details}")
    else:
        lines.append("- none")
    return "\n".join(lines)


def _kv(label: str, value: Any | None) -> str | None:
    if value is None:
        return None
    return f"{label}: {value}"


def _compact_detail(parts: list[str | None]) -> str:
    clean = [part for part in parts if part]
    return f" ({', '.join(clean)})" if clean else ""


def json_dumps_compact(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, indent=2, ensure_ascii=False)


def _split_discord_markdown(content: str, *, limit: int = DISCORD_INLINE_LIMIT) -> list[str]:
    """Split Discord markdown into message-sized chunks, preserving readable boundaries."""

    clean = content.strip()
    if not clean:
        return ["Report generated, but it did not contain any text."]
    if len(clean) <= limit:
        return [clean]

    chunks: list[str] = []
    current = ""
    for block in clean.split("\n\n"):
        current = _append_segment(chunks, current, block, separator="\n\n", limit=limit)
    if current:
        chunks.append(current)
    return chunks


def _append_segment(chunks: list[str], current: str, segment: str, *, separator: str, limit: int) -> str:
    segment = segment.strip()
    if not segment:
        return current
    if len(segment) > limit:
        current = _flush_current(chunks, current)
        for line in segment.splitlines():
            current = _append_line(chunks, current, line, limit=limit)
        return current

    candidate = f"{current}{separator}{segment}" if current else segment
    if len(candidate) <= limit:
        return candidate
    chunks.append(current)
    return segment


def _append_line(chunks: list[str], current: str, line: str, *, limit: int) -> str:
    line = line.rstrip()
    if not line:
        return current
    if len(line) > limit:
        current = _flush_current(chunks, current)
        chunks.extend(line[index : index + limit] for index in range(0, len(line), limit))
        return ""
    candidate = f"{current}\n{line}" if current else line
    if len(candidate) <= limit:
        return candidate
    chunks.append(current)
    return line


def _flush_current(chunks: list[str], current: str) -> str:
    if current:
        chunks.append(current)
    return ""


def _header_value(headers: Any, key: str) -> str | None:
    if hasattr(headers, "get"):
        return headers.get(key) or headers.get(key.title())
    return None


def _filename_from_response(headers: Any) -> str | None:
    disposition = _header_value(headers, "content-disposition")
    if not disposition:
        return None
    for part in disposition.split(";"):
        clean = part.strip()
        if clean.startswith("filename="):
            return clean.split("=", 1)[1].strip('"')
    return None


def _default_filename(job_id: str, output_format: str) -> str:
    extension = {"discord": "md", "html": "html", "markdown": "md", "text": "txt", "json": "json", "png": "png"}.get(output_format, "bin")
    return f"stock-sum-report-{job_id}.{extension}"
