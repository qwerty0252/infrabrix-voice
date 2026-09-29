"""A simulated production cloud for one demo project.

The agent, policies, approvals, and voice bridge in this repository are real;
the infrastructure they act on is not. Each project gets a small, stateful,
deterministic cloud: three services, a release history, a bad deploy that
started an incident, metrics and logs that reflect it, and a rollback that
actually changes the state after a short deployment delay.

Swap this module for real provider calls and nothing above it changes.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import utcnow
from app.models import Deployment, DeploymentStatus, Project
from app.repositories import DeploymentRepository
from app.settings import get_settings

INCIDENT_SERVICE = "checkout-api"
BAD_VERSION = "v42"
GOOD_VERSION = "v41"


def _ago(now: datetime, minutes: float) -> str:
    return (now - timedelta(minutes=minutes)).isoformat()


def seed_state(now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    return {
        "region": "us-east-1",
        "services": {
            "checkout-api": {
                "kind": "Container service (ECS Fargate, 4 tasks)",
                "current_version": BAD_VERSION,
                "status": "degraded",
            },
            "storefront": {
                "kind": "Static site (S3 + CloudFront)",
                "current_version": "v18",
                "status": "healthy",
            },
            "orders-db": {
                "kind": "PostgreSQL 16 (RDS, Multi-AZ)",
                "current_version": "16.4",
                "status": "healthy",
            },
        },
        "releases": {
            "checkout-api": [
                {
                    "version": "v40",
                    "deployed_at": _ago(now, 60 * 72),
                    "commit": "4be0d17",
                    "message": "Add idempotency keys to order submission",
                    "author": "maya",
                },
                {
                    "version": GOOD_VERSION,
                    "deployed_at": _ago(now, 60 * 26),
                    "commit": "c77a2e9",
                    "message": "Tune checkout retry budget",
                    "author": "dev",
                },
                {
                    "version": BAD_VERSION,
                    "deployed_at": _ago(now, 18),
                    "commit": "a91f3c2",
                    "message": "Switch payment client to pooled async HTTP",
                    "author": "ci-bot",
                },
            ],
            "storefront": [
                {
                    "version": "v18",
                    "deployed_at": _ago(now, 60 * 50),
                    "commit": "9d01b55",
                    "message": "Holiday banner",
                    "author": "maya",
                },
            ],
        },
        "incident": {
            "service": INCIDENT_SERVICE,
            "version": BAD_VERSION,
            "started_at": _ago(now, 16),
            "resolved_at": None,
        },
    }


# --- reads -------------------------------------------------------------------


def _incident_active(state: dict[str, Any]) -> bool:
    incident = state.get("incident") or {}
    return incident.get("resolved_at") is None


def _broken(state: dict[str, Any], service: str) -> bool:
    return (
        _incident_active(state)
        and service == INCIDENT_SERVICE
        and state["services"][service]["current_version"] == BAD_VERSION
    )


def overview(state: dict[str, Any]) -> dict[str, Any]:
    services = []
    for name, svc in state["services"].items():
        services.append({"name": name, **svc, **_headline_metrics(state, name)})
    degraded = [s["name"] for s in services if s["status"] != "healthy"]
    return {
        "region": state["region"],
        "services": services,
        "degraded": degraded,
        "headline": (
            f"{', '.join(degraded)} {'is' if len(degraded) == 1 else 'are'} unhealthy; "
            "everything else looks normal."
            if degraded
            else "All services are healthy."
        ),
    }


def _headline_metrics(state: dict[str, Any], service: str) -> dict[str, Any]:
    if service == "orders-db":
        return {"cpu_percent": 31, "connections": 88}
    if _broken(state, service):
        return {"error_rate_percent": 14.2, "p95_latency_ms": 2840, "requests_per_min": 1920}
    if service == INCIDENT_SERVICE:
        return {"error_rate_percent": 0.3, "p95_latency_ms": 310, "requests_per_min": 1880}
    return {"error_rate_percent": 0.0, "p95_latency_ms": 45, "requests_per_min": 5400}


def service_metrics(state: dict[str, Any], service: str) -> dict[str, Any] | None:
    if service not in state["services"]:
        return None
    current = _headline_metrics(state, service)
    series = []
    if service == INCIDENT_SERVICE:
        # Six five-minute buckets. The spike begins right after the v42 deploy.
        spike = _broken(state, service)
        for i, minutes in enumerate([30, 25, 20, 15, 10, 5]):
            bad = spike and minutes <= 15
            series.append(
                {
                    "minutes_ago": minutes,
                    "error_rate_percent": [0.3, 0.2, 0.4, 11.8, 14.9, 14.2][i] if bad else 0.3,
                    "p95_latency_ms": [300, 310, 305, 2610, 2920, 2840][i] if bad else 310,
                }
            )
    return {
        "service": service,
        "current": current,
        "last_30_minutes": series,
        "baseline": {"error_rate_percent": 0.3, "p95_latency_ms": 310}
        if service == INCIDENT_SERVICE
        else None,
    }


def recent_errors(state: dict[str, Any], service: str) -> list[dict[str, Any]]:
    if not _broken(state, service):
        return []
    return [
        {
            "count": 1284,
            "message": "PoolTimeout: timed out acquiring connection from payment client pool",
            "first_seen_minutes_ago": 15,
        },
        {
            "count": 212,
            "message": "HTTP 504 from upstream payments-gateway after 2500ms",
            "first_seen_minutes_ago": 15,
        },
    ]


def releases(state: dict[str, Any], service: str) -> list[dict[str, Any]] | None:
    rows = state["releases"].get(service)
    if rows is None:
        return None
    current = state["services"][service]["current_version"]
    return [{**r, "live": r["version"] == current} for r in reversed(rows)]


def diagnose(state: dict[str, Any], service: str | None = None) -> dict[str, Any]:
    """Correlate an error-rate onset with the release deployed just before it."""
    candidates = [service] if service else list(state["services"])
    for name in candidates:
        metrics = service_metrics(state, name)
        if metrics is None or not metrics["last_30_minutes"]:
            continue
        onset = next((p for p in metrics["last_30_minutes"] if p["error_rate_percent"] >= 5), None)
        if onset is None:
            continue
        onset_at = utcnow() - timedelta(minutes=onset["minutes_ago"])
        history = state["releases"].get(name, [])
        suspect = None
        for idx, rel in enumerate(history):
            deployed = datetime.fromisoformat(rel["deployed_at"])
            if timedelta(0) <= onset_at - deployed <= timedelta(minutes=10):
                suspect = (rel, history[idx - 1] if idx > 0 else None)
        if suspect is None:
            return {
                "service": name,
                "finding": "Error rate is elevated but no release lines up with the onset.",
                "recommendation": None,
                "headline": f"{name} is failing, but it does not line up with a deploy.",
            }
        bad, previous = suspect
        return {
            "service": name,
            "finding": (
                f"Errors jumped from 0.3% to {metrics['current']['error_rate_percent']}% "
                f"about {onset['minutes_ago']} minutes ago, right after {bad['version']} "
                f'("{bad["message"]}") was deployed. Top error: payment client pool timeouts.'
            ),
            "suspect_release": bad,
            "recommendation": {
                "action": "rollback_release",
                "service": name,
                "to_version": previous["version"] if previous else None,
                "why": f"{previous['version'] if previous else 'the previous release'} "
                "was healthy for over a day.",
            },
            "confidence": "high",
            "headline": (
                f"{name} broke after the {bad['version']} deploy. "
                f"Rolling back to {previous['version'] if previous else 'the previous version'} "
                "should fix it."
            ),
        }
    return {"finding": "No active incident.", "headline": "I don't see an active incident."}


def cost_summary() -> dict[str, Any]:
    return {
        "currency": "USD",
        "month_to_date": 412.37,
        "forecast_month_end": 781.0,
        "last_month": 702.15,
        "top_services": [
            {"name": "ECS Fargate (checkout-api)", "month_to_date": 148.20},
            {"name": "RDS (orders-db)", "month_to_date": 121.66},
            {"name": "NAT Gateway", "month_to_date": 74.90},
            {"name": "CloudFront + S3 (storefront)", "month_to_date": 22.41},
        ],
        "anomaly": "NAT Gateway data processing is up 38% week over week.",
        "headline": (
            "You've spent about $412 this month and are on track for $781, "
            "roughly 11% over last month, mostly from NAT Gateway traffic."
        ),
    }


# --- writes ------------------------------------------------------------------


async def start_rollback(
    session: AsyncSession, project: Project, *, service: str, to_version: str, user_id: uuid.UUID
) -> Deployment:
    state = copy.deepcopy(project.cloud_state)
    svc = state["services"][service]
    deployment = Deployment(
        project_id=project.id,
        service=service,
        from_version=svc["current_version"],
        to_version=to_version,
        requested_by=user_id,
    )
    session.add(deployment)
    svc["status"] = "rolling_back"
    project.cloud_state = state
    await session.flush()
    return deployment


async def in_progress(session: AsyncSession, project: Project, service: str) -> bool:
    rows = await DeploymentRepository(session).for_project(project.id)
    return any(d.service == service and d.status is DeploymentStatus.IN_PROGRESS for d in rows)


async def advance(session: AsyncSession, project: Project) -> None:
    """Finish simulated deployments whose rollout time has elapsed."""
    delay = timedelta(seconds=get_settings().demo_rollback_seconds)
    rows = await DeploymentRepository(session).for_project(project.id)
    pending = [d for d in rows if d.status is DeploymentStatus.IN_PROGRESS]
    if not pending:
        return
    now = utcnow()
    state = copy.deepcopy(project.cloud_state)
    changed = False
    for deployment in sorted(pending, key=lambda d: d.created_at):
        if now - deployment.created_at < delay:
            continue
        deployment.status = DeploymentStatus.SUCCEEDED
        deployment.finished_at = now
        svc = state["services"][deployment.service]
        svc["current_version"] = deployment.to_version
        svc["status"] = "healthy" if deployment.to_version != BAD_VERSION else "degraded"
        incident = state.get("incident") or {}
        if incident.get("service") == deployment.service and deployment.to_version != BAD_VERSION:
            incident["resolved_at"] = now.isoformat()
        changed = True
    if changed:
        project.cloud_state = state
        await session.flush()
