"""Red Discord Bot cog for requesting stock-sum reports."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol
from urllib.parse import quote
import asyncio
import os

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

        class Choice:
            def __init__(self, *, name: str, value: str) -> None:
                self.name = name
                self.value = value

    commands = _FallbackCommands()
    app_commands = _FallbackAppCommands()


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 14 * 60
DISCORD_INLINE_LIMIT = 1900
SUPPORTED_FORMATS = {"discord", "html", "markdown", "text", "json"}


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

    async def run_report(
        self,
        *,
        profile: str,
        output_format: str,
        include_capitol_trades: bool,
    ) -> StockSumArtifact:
        """Create, poll, and download one stock-sum report job."""

        if output_format not in SUPPORTED_FORMATS:
            raise StockSumRequestError(f"Unsupported report format: {output_format}")

        session, owns_session = await self._session()
        try:
            job = await self._create_report_job(
                session,
                profile=profile,
                output_format=output_format,
                include_capitol_trades=include_capitol_trades,
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

    async def _session(self) -> tuple[_ClientSession, bool]:
        if self.session is not None:
            return self.session, False
        try:
            import aiohttp
        except ModuleNotFoundError as exc:  # pragma: no cover - Redbot normally provides aiohttp.
            raise StockSumConfigurationError("aiohttp is required in the Redbot runtime.") from exc
        return aiohttp.ClientSession(), True

    async def _create_report_job(
        self,
        session: _ClientSession,
        *,
        profile: str,
        output_format: str,
        include_capitol_trades: bool,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/v1/reports/{quote(profile, safe='')}/jobs/{quote(output_format, safe='')}"
        payload = {
            "include_capitol_trades": include_capitol_trades,
        }
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

    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="report", description="Generate a stock-sum market report.")
    @app_commands.describe(
        profile="stock-sum report profile name",
        format="report artifact format",
        include_capitol_trades="include Capitol Trades politician trading rows",
        private="send the response only to you",
    )
    @app_commands.choices(
        format=[
            app_commands.Choice(name="Discord Markdown", value="discord"),
            app_commands.Choice(name="HTML", value="html"),
            app_commands.Choice(name="Markdown", value="markdown"),
            app_commands.Choice(name="Text", value="text"),
            app_commands.Choice(name="JSON", value="json"),
        ]
    )
    async def report(
        self,
        interaction,
        profile: str = "default",
        format: str = "discord",
        include_capitol_trades: bool = True,
        private: bool = True,
    ) -> None:
        """Slash command handler for report generation."""

        await interaction.response.defer(ephemeral=private, thinking=True)
        try:
            artifact = await StockSumHttpClient.from_env().run_report(
                profile=profile,
                output_format=format,
                include_capitol_trades=include_capitol_trades,
            )
        except StockSumCogError as exc:
            await interaction.followup.send(f"stock-sum report failed: {exc}", ephemeral=private)
            return

        if discord is None:
            await interaction.followup.send("stock-sum report is ready, but discord.py is not available.", ephemeral=private)
            return

        message = (
            f"stock-sum report complete.\n"
            f"profile: `{profile}`\n"
            f"format: `{format}`\n"
            f"job: `{artifact.job_id}`"
        )
        if format == "discord":
            report_text = artifact.content.decode("utf-8", errors="replace").strip()
            inline_message = f"{message}\n\n{report_text}" if report_text else message
            if len(inline_message) <= DISCORD_INLINE_LIMIT:
                await interaction.followup.send(inline_message, ephemeral=private)
                return
            message += "\nReport was too long for one Discord message, so it is attached."

        file = discord.File(BytesIO(artifact.content), filename=artifact.filename)
        await interaction.followup.send(message, file=file, ephemeral=private)


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
    extension = {"discord": "md", "html": "html", "markdown": "md", "text": "txt", "json": "json"}.get(output_format, "bin")
    return f"stock-sum-report-{job_id}.{extension}"
