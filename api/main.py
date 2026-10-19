from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal

app = FastAPI(
    title="GoogleSaada V8 Backend API",
    version="0.1.0",
    description=(
        "Versioned API foundation. "
        "Model and provider integrations remain gated."
    ),
)

API_PREFIX = "/api/v1"


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_key: str = Field(
        min_length=1, max_length=256
    )
    markets: list[str] = Field(
        default_factory=list,
        max_length=20
    )


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(min_length=10, max_length=10)
    limit: int = Field(default=5, ge=1, le=50)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        min_length=1, max_length=2000
    )


def not_integrated(service: str):
    """
    Fail closed. Never return fabricated data,
    fake predictions, or a false readiness flag.
    """
    raise HTTPException(
        status_code=503,
        detail={
            "code": "SERVICE_NOT_INTEGRATED",
            "service": service,
            "message": (
                "Verified backend service integration "
                "and access controls are not complete."
            )
        }
    )


@app.get(API_PREFIX + "/health/live")
def health_live():
    return {
        "status": "alive",
        "service": "googlesaada-v8-api",
    }


@app.get(API_PREFIX + "/health/ready")
def health_ready():
    # Readiness stays unavailable until all mandatory
    # production service checks are implemented.
    not_integrated("production_readiness")


@app.get(API_PREFIX + "/models/status")
def models_status():
    not_integrated("model_registry")


@app.get(API_PREFIX + "/fixtures")
def fixtures():
    not_integrated("fixtures_provider")


@app.get(API_PREFIX + "/scores/live")
def scores_live():
    not_integrated("live_scores_provider")


@app.get(API_PREFIX + "/scores/results")
def scores_results():
    not_integrated("results_provider")


@app.post(API_PREFIX + "/predictions")
def predictions(request: PredictionRequest):
    not_integrated("original10_prediction_service")


@app.post(API_PREFIX + "/recommendations")
def recommendations(request: RecommendationRequest):
    not_integrated("recommendation_service")


@app.post(API_PREFIX + "/agent/chat")
def agent_chat(request: AgentRequest):
    not_integrated("agent_orchestration_and_auth")


@app.get(API_PREFIX + "/user/quota")
def user_quota():
    not_integrated("user_authentication_and_quota")


@app.get(API_PREFIX + "/help")
def help_content():
    not_integrated("approved_help_content")



# GS_V8_STAGE4J3_RESEARCH_ROUTER
# Authentication and production approval are still pending.
# Retain the public prediction endpoint's existing 503 behavior.
from fastapi import APIRouter

gs_research_router = APIRouter(prefix="/api/v1")


@gs_research_router.get("/research/status")
def gs_research_status():
    return {
        "service": "original10_research",
        "integration": "offline_adapter",
        "public_predictions_enabled": False,
        "authenticated_access_ready": False,
        "historical_research_only": True,
    }


@gs_research_router.post("/research/day")
def gs_research_day_locked():
    raise HTTPException(
        status_code=503,
        detail={
            "code": "RESEARCH_ACCESS_NOT_ENABLED",
            "message": (
                "Authentication, provider trust and "
                "research-output access policy are not yet integrated."
            ),
        },
    )


app.include_router(gs_research_router)

# GS_V8_PUBLIC_FIXTURES_ROUTER_V1
from api.public_fixtures import router as _gs_public_fixtures_router
app.include_router(_gs_public_fixtures_router)

# GS_V8_PUBLIC_MATCH_SCORES_V1
from api.public_match_scores import router as _gs_public_match_scores_router
app.include_router(_gs_public_match_scores_router)

# GS_V8_PUBLIC_AGENT_RESEARCH_4M
from api.public_agent_research import router as _gs_4m_router
app.include_router(_gs_4m_router)

# GS_V8_STAGE5A_PUBLIC_SESSION_AGENT
from api.public_session_agent import router as _gs_v8_session_router
app.include_router(_gs_v8_session_router)
