"""Typed tools over the simulated cloud.

Each tool declares a risk level. READ tools run inside the turn; the rollback
is WRITE_INFRASTRUCTURE, so the runtime parks it for human approval and it only
ever executes from the authenticated approval endpoint.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app import democloud
from app.agent.registry import Tool, ToolContext, ToolRegistry, ToolResult
from app.models import ToolRisk


class _NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ServiceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(description="Service name, e.g. ria-api")


class _OptionalServiceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str | None = Field(default=None, description="Limit to one service")


class _RollbackArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(description="Service to roll back")
    to_version: str | None = Field(
        default=None, description="Release to restore. Defaults to the previous release."
    )


async def _state(ctx: ToolContext) -> dict:
    await democloud.advance(ctx.session, ctx.project)
    return ctx.project.cloud_state


def _unknown(service: str, state: dict) -> ToolResult:
    names = ", ".join(state["services"])
    return ToolResult.failure(f"there is no service called {service}; services are {names}")


class GetProjectOverview(Tool):
    name = "get_project_overview"
    description = "Services in this environment with their version, health and key metrics."
    risk = ToolRisk.READ
    Args = _NoArgs

    async def run(self, args: _NoArgs, ctx: ToolContext) -> ToolResult:
        data = democloud.overview(await _state(ctx))
        return ToolResult.success(data, summary=data["headline"])


class GetServiceMetrics(Tool):
    name = "get_service_metrics"
    description = "Error rate, p95 latency and traffic for one service over the last 30 minutes."
    risk = ToolRisk.READ
    Args = _ServiceArgs

    async def run(self, args: _ServiceArgs, ctx: ToolContext) -> ToolResult:
        state = await _state(ctx)
        data = democloud.service_metrics(state, args.service)
        if data is None:
            return _unknown(args.service, state)
        current = data["current"]
        if "error_rate_percent" in current:
            headline = (
                f"{args.service} is at {current['error_rate_percent']}% errors "
                f"and {current['p95_latency_ms']} milliseconds p95 latency."
            )
        else:
            headline = f"{args.service} is at {current.get('cpu_percent')}% CPU."
        data["headline"] = headline
        return ToolResult.success(data, summary=headline)


class GetRecentErrors(Tool):
    name = "get_recent_errors"
    description = "The most frequent recent error log lines for one service."
    risk = ToolRisk.READ
    Args = _ServiceArgs

    async def run(self, args: _ServiceArgs, ctx: ToolContext) -> ToolResult:
        state = await _state(ctx)
        if args.service not in state["services"]:
            return _unknown(args.service, state)
        errors = democloud.recent_errors(state, args.service)
        headline = (
            f"The top error is: {errors[0]['message']}, about {errors[0]['count']} times."
            if errors
            else f"No recent errors for {args.service}."
        )
        return ToolResult.success({"errors": errors, "headline": headline}, summary=headline)


class ListReleases(Tool):
    name = "list_releases"
    description = "Release history for a service, newest first, with the live version marked."
    risk = ToolRisk.READ
    Args = _ServiceArgs

    async def run(self, args: _ServiceArgs, ctx: ToolContext) -> ToolResult:
        state = await _state(ctx)
        rows = democloud.releases(state, args.service)
        if rows is None:
            return _unknown(args.service, state)
        live = next(r for r in rows if r["live"])
        headline = (
            f"{args.service} is running {live['version']}: {live['message']}. "
            f"There are {len(rows)} releases on record."
        )
        return ToolResult.success({"releases": rows, "headline": headline}, summary=headline)


class DiagnoseIncident(Tool):
    name = "diagnose_incident"
    description = (
        "Find what is broken and why: correlates error-rate spikes with recent releases "
        "and recommends a fix."
    )
    risk = ToolRisk.READ
    Args = _OptionalServiceArgs

    async def run(self, args: _OptionalServiceArgs, ctx: ToolContext) -> ToolResult:
        state = await _state(ctx)
        if args.service and args.service not in state["services"]:
            return _unknown(args.service, state)
        data = democloud.diagnose(state, args.service)
        return ToolResult.success(data, summary=data["headline"])


class GetCostSummary(Tool):
    name = "get_cost_summary"
    description = "Month-to-date cloud spend, forecast, top services and any cost anomaly."
    risk = ToolRisk.READ
    Args = _NoArgs

    async def run(self, args: _NoArgs, ctx: ToolContext) -> ToolResult:
        data = democloud.cost_summary()
        return ToolResult.success(data, summary=data["headline"])


class RollbackRelease(Tool):
    name = "rollback_release"
    description = (
        "Redeploy an earlier release of a service. Requires human approval on screen; "
        "calling it prepares the rollback, it does not run it."
    )
    risk = ToolRisk.WRITE_INFRASTRUCTURE
    Args = _RollbackArgs

    async def run(self, args: _RollbackArgs, ctx: ToolContext) -> ToolResult:
        state = await _state(ctx)
        rows = democloud.releases(state, args.service)
        if rows is None:
            return _unknown(args.service, state)
        versions = [r["version"] for r in rows]  # newest first
        live = next(r["version"] for r in rows if r["live"])
        target = args.to_version or (
            versions[versions.index(live) + 1] if versions.index(live) + 1 < len(versions) else None
        )
        if target is None or target not in versions:
            return ToolResult.failure(f"there is no earlier release of {args.service} to restore")
        if target == live:
            return ToolResult.failure(f"{args.service} is already running {target}")
        if await democloud.in_progress(ctx.session, ctx.project, args.service):
            return ToolResult.failure(f"a deployment of {args.service} is already in progress")
        deployment = await democloud.start_rollback(
            ctx.session, ctx.project, service=args.service, to_version=target, user_id=ctx.user.id
        )
        return ToolResult.success(
            {"deployments": [str(deployment.id)], "service": args.service, "to_version": target},
            summary=f"Rolling {args.service} back from {live} to {target}",
        )


def build_default_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            GetProjectOverview(),
            DiagnoseIncident(),
            GetServiceMetrics(),
            GetRecentErrors(),
            ListReleases(),
            GetCostSummary(),
            RollbackRelease(),
        ]
    )
