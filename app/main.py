from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi import Request as FastAPIRequest
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlmodel import Session, select

from app import admin, budget, gateway, metrics, quality, router
from app import cache as cache_module
from app.auth import Principal, authenticate
from app.config import get_routing_config
from app.db import get_session, init_db
from app.models import Request
from app.pricing import cost_usd

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="LLM Cost Autopilot", lifespan=lifespan)
app.include_router(admin.router)


@app.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(
    request: FastAPIRequest,
    background: BackgroundTasks,
    principal: Principal = Depends(authenticate),
    session: Session = Depends(get_session),
) -> Any:
    payload = await request.json()
    headers = dict(request.headers)

    if payload.get("stream"):
        status = gateway.enforce_budget(principal, session, background)
        _, decision = gateway.plan(payload, headers, status.over_soft_cap)
        return StreamingResponse(
            gateway.stream_completion(payload, principal, headers, status.over_soft_cap),
            media_type="text/event-stream",
            headers={
                gateway.MODEL_HEADER: decision.primary,
                "x-autopilot-tier": decision.tier,
                "x-autopilot-complexity": f"{decision.score:.4f}",
            },
        )

    result = await gateway.handle_completion(payload, principal, session, headers, background)
    return JSONResponse(content=result.body, headers=result.headers)


@app.post("/v1/autopilot/explain")
async def explain(
    request: FastAPIRequest,
    principal: Principal = Depends(authenticate),
) -> dict[str, Any]:
    """Dry run: show how a request would be routed, without calling any provider."""
    payload = await request.json()
    headers = dict(request.headers)
    messages = payload.get("messages", [])
    tools = payload.get("tools")

    status = budget.check(principal)
    requested_model, decision = gateway.plan(payload, headers, status.over_soft_cap)
    breakdown = router.explain_complexity(messages, tools)
    tokens = router.estimate_tokens(" ".join(router.message_text(m) for m in messages))

    return {
        "budget": {
            "state": status.state,
            "daily_spend_usd": status.daily_spend,
            "daily_budget_usd": status.daily_budget,
            "monthly_spend_usd": status.monthly_spend,
            "monthly_budget_usd": status.monthly_budget,
        },
        "cacheable": cache_module.is_cacheable(payload),
        "score": breakdown["score"],
        "components": breakdown["components"],
        "tier": decision.tier,
        "mode": decision.mode,
        "escalated": decision.escalated,
        "override": decision.override,
        "chain": decision.models,
        "requested_model": requested_model,
        "estimated_prompt_tokens": tokens,
        "estimated_cost_usd": cost_usd(decision.primary, tokens, 0),
        "baseline_cost_usd": cost_usd(requested_model, tokens, 0),
        "thresholds": {
            name: spec["max_score"] for name, spec in get_routing_config().tiers.items()
        },
    }


@app.get("/v1/autopilot/recent")
async def recent_requests(
    limit: int = 25,
    principal: Principal = Depends(authenticate),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = session.exec(
        select(Request)
        .where(Request.tenant_id == principal.tenant_id)
        .order_by(Request.id.desc())
        .limit(min(max(limit, 1), 100))
    ).all()
    return {
        "requests": [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "requested_model": r.requested_model,
                "routed_model": r.routed_model,
                "tier": r.tier,
                "complexity": r.complexity_score,
                "tokens": r.prompt_tokens + r.completion_tokens,
                "cost_usd": r.cost_usd,
                "baseline_cost_usd": r.baseline_cost_usd,
                "latency_ms": r.latency_ms,
                "cache_hit": r.cache_hit,
                "status": r.status,
            }
            for r in rows
        ]
    }


@app.get("/ui", include_in_schema=False)
async def ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/v1/autopilot/quality")
async def quality_report(
    principal: Principal = Depends(authenticate),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return quality.pass_rate_by_tier(session)
