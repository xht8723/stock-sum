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

    def patch(self, url: str, **kwargs: Any) -> _RequestContext:
        ...

    def put(self, url: str, **kwargs: Any) -> _RequestContext:
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

    async def run_report(
        self,
        *,
        profile: str,
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
            job = await self._create_report_job(
                session,
                profile=profile,
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
        limit: int | None = None,
        force_refresh: bool = False,
    ) -> StockSumArtifact:
        """Create, poll, and download one stock-sum trading disclosure report job."""

        if output_format not in SUPPORTED_FORMATS:
            raise StockSumRequestError(f"Unsupported report format: {output_format}")
        if not any((name, start_date, end_date, days)):
            raise StockSumRequestError("tradingreport requires at least one filter: name, start_date/end_date, or days.")

        session, owns_session = await self._session()
        try:
            payload = {
                "name": name,
                "start_date": start_date,
                "end_date": end_date,
                "days": days,
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

    async def run_collect_profile(self, *, profile: str) -> dict[str, Any]:
        """Create and poll a collection-only job for one profile."""

        session, owns_session = await self._session()
        try:
            job = await self.post_json(f"/v1/collect/{quote(profile, safe='')}/jobs", session=session, expected_status=202)
            job_id = _required_string(job, "job_id")
            return await self._poll_until_done(session, job_id)
        finally:
            if owns_session:
                await session.close()

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

    async def patch_json(self, path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("patch", path, payload=payload, expected_status=200)

    async def put_json(self, path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request_json("put", path, payload=payload, expected_status=200)

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

    async def _create_report_job(
        self,
        session: _ClientSession,
        *,
        profile: str,
        output_format: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self.base_url}/v1/social-reports/{quote(profile, safe='')}/jobs/{quote(output_format, safe='')}"
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

    stocksum = app_commands.Group(name="stocksum", description="Manage stock-sum.")
    profiles = app_commands.Group(name="profiles", description="Manage report profiles.", parent=stocksum)
    sources = app_commands.Group(name="sources", description="Manage report sources.", parent=stocksum)
    llm = app_commands.Group(name="llm", description="Manage LLM settings.", parent=stocksum)
    secrets = app_commands.Group(name="secrets", description="Manage stock-sum API keys.", parent=stocksum)
    collect_group = app_commands.Group(name="collect", description="Run collection jobs.", parent=stocksum)
    setup = app_commands.Group(name="setup", description="Check stock-sum setup.", parent=stocksum)
    retention = app_commands.Group(name="retention", description="Inspect runtime data retention.", parent=stocksum)

    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="socialreport", description="Generate a stock-sum social media market report.")
    @app_commands.describe(
        profile="stock-sum report profile name",
        format="report artifact format",
        detail="how many social sentiment items to include",
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
    @app_commands.choices(
        detail=[
            app_commands.Choice(name="Minimum", value="minimum"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="Full", value="full"),
        ]
    )
    async def socialreport(
        self,
        interaction,
        profile: str = "default",
        format: str = "discord",
        detail: str = "minimum",
        private: bool = False,
    ) -> None:
        """Slash command handler for social report generation."""

        await interaction.response.send_message(
            "Social report is being generated, please wait a few minutes.",
            ephemeral=private,
        )
        try:
            artifact = await StockSumHttpClient.from_env().run_report(
                profile=profile,
                output_format=format,
                detail=detail,
            )
        except StockSumCogError as exc:
            await _send_report_output(interaction, _failure_message(exc), private=private)
            return

        if discord is None:
            await _send_report_output(
                interaction,
                "stock-sum report is ready, but discord.py is not available.",
                private=private,
            )
            return

        if format == "discord":
            report_text = artifact.content.decode("utf-8", errors="replace").strip()
            for chunk in _split_discord_markdown(report_text):
                await _send_report_output(interaction, chunk, private=private)
            return

        file = discord.File(BytesIO(artifact.content), filename=artifact.filename)
        await _send_report_output(interaction, "Report generated.", private=private, file=file)

    @app_commands.command(name="tradingreport", description="Generate an official House trading disclosure report.")
    @app_commands.describe(
        name="case-insensitive fuzzy filer name filter",
        start_date="transaction start date, YYYY-MM-DD",
        end_date="transaction end date, YYYY-MM-DD",
        days="transaction records from the last N days",
        limit="optional maximum rows to return",
        format="report artifact format",
        private="send the response only to you",
        force_refresh="force a House PTR refresh before querying",
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
    async def tradingreport(
        self,
        interaction,
        name: str = "",
        start_date: str = "",
        end_date: str = "",
        days: int | None = None,
        limit: int | None = None,
        format: str = "discord",
        private: bool = False,
        force_refresh: bool = False,
    ) -> None:
        """Slash command handler for House PTR trading disclosure reports."""

        name_filter = name.strip() or None
        start_filter = start_date.strip() or None
        end_filter = end_date.strip() or None
        if not any((name_filter, start_filter, end_filter, days)):
            await _send_report_output(
                interaction,
                "stock-sum report failed: tradingreport requires at least one filter: name, start_date/end_date, or days.",
                private=True,
            )
            return

        await interaction.response.send_message(
            "Trading disclosure report is being generated, please wait a few minutes.",
            ephemeral=private,
        )
        try:
            artifact = await StockSumHttpClient.from_env().run_trading_report(
                output_format=format,
                name=name_filter,
                start_date=start_filter,
                end_date=end_filter,
                days=days,
                limit=limit if limit is None else max(1, limit),
                force_refresh=force_refresh,
            )
        except StockSumCogError as exc:
            await _send_report_output(interaction, _failure_message(exc), private=private)
            return

        if discord is None:
            await _send_report_output(
                interaction,
                "stock-sum report is ready, but discord.py is not available.",
                private=private,
            )
            return

        if format == "discord":
            report_text = artifact.content.decode("utf-8", errors="replace").strip()
            for chunk in _split_discord_markdown(report_text):
                await _send_report_output(interaction, chunk, private=private)
            return

        file = discord.File(BytesIO(artifact.content), filename=artifact.filename)
        await _send_report_output(interaction, "Report generated.", private=private, file=file)

    @profiles.command(name="list", description="List stock-sum profiles.")
    async def profiles_list(self, interaction) -> None:
        await self._send_api_json(interaction, "/v1/profiles", title="Profiles")

    @profiles.command(name="show", description="Show one stock-sum profile.")
    async def profiles_show(self, interaction, name: str = "default") -> None:
        await self._send_api_json(interaction, f"/v1/profiles/{quote(name, safe='')}", title=f"Profile {name}")

    @profiles.command(name="add", description="Add a stock-sum profile.")
    async def profiles_add(
        self,
        interaction,
        name: str,
        collectors: str = "",
        schedule: str = "0 8 * * *",
        timezone: str = "UTC",
    ) -> None:
        if not await self._require_owner(interaction):
            return
        payload = {
            "name": name,
            "collector_ids": _csv(collectors),
            "delivery_ids": [],
            "schedule": schedule,
            "timezone": timezone,
        }
        await self._send_api_json(interaction, "/v1/profiles", method="post", payload=payload, title=f"Added profile {name}", private=True)

    @profiles.command(name="edit", description="Edit a stock-sum profile collector list.")
    async def profiles_edit(self, interaction, name: str, collectors: str) -> None:
        if not await self._require_owner(interaction):
            return
        await self._send_api_json(
            interaction,
            f"/v1/profiles/{quote(name, safe='')}",
            method="patch",
            payload={"collector_ids": _csv(collectors)},
            title=f"Updated profile {name}",
            private=True,
        )

    @profiles.command(name="delete", description="Delete a stock-sum profile.")
    async def profiles_delete(self, interaction, name: str) -> None:
        if not await self._require_owner(interaction):
            return
        await self._send_api_json(interaction, f"/v1/profiles/{quote(name, safe='')}", method="delete", title=f"Deleted profile {name}", private=True)

    @sources.command(name="list", description="List configured report sources.")
    async def sources_list(self, interaction) -> None:
        await self._send_api_json(interaction, "/v1/sources", title="Sources")

    @sources.command(name="add-x", description="Add an X user source.")
    async def sources_add_x(
        self,
        interaction,
        handle: str,
        profile: str = "default",
        limit: int = 100,
        lookback_hours: int = 24,
        enabled: bool = True,
    ) -> None:
        if not await self._require_owner(interaction):
            return
        payload = {"handle": handle, "profile": profile, "limit": limit, "lookback_hours": lookback_hours, "enabled": enabled}
        await self._send_api_json(interaction, "/v1/sources/x-users", method="post", payload=payload, title=f"Added X source {handle}", private=True)

    @sources.command(name="delete-x", description="Delete an X user source.")
    async def sources_delete_x(self, interaction, handle: str, profile: str = "default") -> None:
        if not await self._require_owner(interaction):
            return
        path = f"/v1/sources/x-users/{quote(handle, safe='')}?profile={quote(profile, safe='')}"
        await self._send_api_json(interaction, path, method="delete", title=f"Deleted X source {handle}", private=True)

    @sources.command(name="add-reddit", description="Add a subreddit source.")
    async def sources_add_reddit(
        self,
        interaction,
        subreddit: str,
        profile: str = "default",
        limit: int = 100,
        lookback_hours: int = 24,
        include_comments: bool = True,
        comments_per_post: int = 10,
    ) -> None:
        if not await self._require_owner(interaction):
            return
        payload = {
            "subreddit": subreddit,
            "profile": profile,
            "limit": limit,
            "lookback_hours": lookback_hours,
            "include_comments": include_comments,
            "comments_per_post": comments_per_post,
        }
        await self._send_api_json(interaction, "/v1/sources/subreddits", method="post", payload=payload, title=f"Added subreddit {subreddit}", private=True)

    @sources.command(name="delete-reddit", description="Delete a subreddit source.")
    async def sources_delete_reddit(self, interaction, subreddit: str, profile: str = "default") -> None:
        if not await self._require_owner(interaction):
            return
        path = f"/v1/sources/subreddits/{quote(subreddit, safe='')}?profile={quote(profile, safe='')}"
        await self._send_api_json(interaction, path, method="delete", title=f"Deleted subreddit {subreddit}", private=True)

    @sources.command(name="house-show", description="Show the House PTR disclosure source.")
    async def sources_house_show(self, interaction) -> None:
        await self._send_api_json(interaction, "/v1/sources/house-ptr", title="House PTR Source")

    @sources.command(name="house-set", description="Configure the House PTR disclosure source.")
    async def sources_house_set(
        self,
        interaction,
        profile: str = "default",
        enabled: bool = True,
        year: int = 0,
        render_limit: int = 20,
        refresh_ttl_seconds: int = 21600,
        download_concurrency: int = 4,
        parse_concurrency: int = 2,
    ) -> None:
        if not await self._require_owner(interaction):
            return
        payload = {
            "profile": profile,
            "enabled": enabled,
            "year": year,
            "render_limit": render_limit,
            "refresh_ttl_seconds": refresh_ttl_seconds,
            "download_concurrency": download_concurrency,
            "parse_concurrency": parse_concurrency,
        }
        await self._send_api_json(interaction, "/v1/sources/house-ptr", method="patch", payload=payload, title="Updated House PTR source", private=True)

    @llm.command(name="providers", description="List supported LLM providers.")
    async def llm_providers(self, interaction) -> None:
        await self._send_api_json(interaction, "/v1/llm/providers", title="LLM Providers")

    @llm.command(name="show", description="Show current LLM config.")
    async def llm_show(self, interaction) -> None:
        await self._send_api_json(interaction, "/v1/llm/config", title="LLM Config")

    @llm.command(name="select", description="Select the LLM provider/model.")
    async def llm_select(self, interaction, provider: str = "deepseek", model: str = "") -> None:
        if not await self._require_owner(interaction):
            return
        payload = {"provider": provider}
        if model:
            payload["model"] = model
        await self._send_api_json(interaction, "/v1/llm/config", method="patch", payload=payload, title="Updated LLM config", private=True)

    @secrets.command(name="list", description="List configured secret names.")
    async def secrets_list(self, interaction) -> None:
        if not await self._require_owner(interaction):
            return
        await self._send_api_json(interaction, "/v1/secrets", title="Secrets", private=True)

    @secrets.command(name="set", description="Set a stock-sum secret value.")
    async def secrets_set(self, interaction, name: str, value: str) -> None:
        if not await self._require_owner(interaction):
            return
        await self._send_api_json(
            interaction,
            f"/v1/secrets/{quote(name, safe='')}",
            method="put",
            payload={"value": value},
            title=f"Set secret {name}",
            private=True,
        )

    @secrets.command(name="remove", description="Remove a stock-sum secret.")
    async def secrets_remove(self, interaction, name: str) -> None:
        if not await self._require_owner(interaction):
            return
        await self._send_api_json(interaction, f"/v1/secrets/{quote(name, safe='')}", method="delete", title=f"Removed secret {name}", private=True)

    @collect_group.command(name="profile", description="Run collection for one profile.")
    async def collect_profile(self, interaction, profile: str = "default") -> None:
        await interaction.response.send_message("Collection is running, please wait.", ephemeral=True)
        try:
            payload = await StockSumHttpClient.from_env().run_collect_profile(profile=profile)
        except StockSumCogError as exc:
            await interaction.followup.send(_failure_message(exc), ephemeral=True, suppress_embeds=True)
            return
        await interaction.followup.send(_format_json_message("Collection complete", payload), ephemeral=True, suppress_embeds=True)

    @setup.command(name="check", description="Check stock-sum setup.")
    async def setup_check(self, interaction) -> None:
        await self._send_api_json(interaction, "/v1/setup/check", title="Setup Check", private=True)

    @retention.command(name="status", description="Show runtime data usage.")
    async def retention_status(self, interaction) -> None:
        await self._send_api_json(interaction, "/v1/retention/status", title="Retention Status")

    @retention.command(name="prune", description="Prune runtime data.")
    async def retention_prune(self, interaction, dry_run: bool = True) -> None:
        if not await self._require_owner(interaction):
            return
        await self._send_api_json(
            interaction,
            "/v1/retention/prune",
            method="post",
            payload={"dry_run": dry_run},
            title="Retention Prune",
            private=True,
        )

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
            elif method == "patch":
                response = await client.patch_json(path, payload=payload or {})
            elif method == "put":
                response = await client.put_json(path, payload=payload or {})
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


def _format_json_message(title: str, payload: dict[str, Any]) -> str:
    text = json_dumps_compact(payload)
    message = f"**{title}**\n```json\n{text}\n```"
    if len(message) <= DISCORD_INLINE_LIMIT:
        return message
    return f"**{title}**\n```json\n{text[: DISCORD_INLINE_LIMIT - len(title) - 24].rstrip()}...\n```"


def json_dumps_compact(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, indent=2, ensure_ascii=False)


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


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
    extension = {"discord": "md", "html": "html", "markdown": "md", "text": "txt", "json": "json"}.get(output_format, "bin")
    return f"stock-sum-report-{job_id}.{extension}"
