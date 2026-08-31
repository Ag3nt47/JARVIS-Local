"""FastAPI application and local JSON API."""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, model_validator

from . import __version__
from .angerona import AngeronaAdapter
from .angerona_control import (
    AngeronaControlError,
    AngeronaControlService,
    AngeronaTacticService,
)
from .audit import AuditLog
from .builtin_memory import DEFERRED_UPGRADES_MEMORY
from .chat import ChatService
from .communications import (
    DELIVERY_PROVIDERS,
    CommunicationRateLimitError,
    CommunicationSecretStorageError,
    CommunicationsService,
    ProviderId,
)
from .config import Settings, load_settings
from .database import Database
from .federation import (
    AuthenticationError,
    EnvelopeValidationError,
    FederationDependencyError,
    IdempotencyConflictError,
    ReplayError,
    ScopeDeniedError,
)
from .federation_service import (
    FEDERATION_ADMIN_APPROVAL_TOOL,
    FEDERATION_ENVELOPE_HEADER,
    FederationCapabilityError,
    FederationDisabledError,
    FederationRequestStateError,
    FederationService,
)
from .orchestration import OrchestrationService, TurnCancelled
from .phone_voice import (
    PhoneVoiceError,
    PhoneVoiceService,
    twiml_gather,
    twiml_say,
)
from .preferences import PreferencesService, PreferencesUpdate
from .providers import OllamaError, OllamaProvider
from .security import SecurityMiddleware, SecurityMonitor
from .services import (
    ApprovalService,
    MemoryService,
    NodeService,
    NotificationService,
    ProjectService,
    Scheduler,
    TaskService,
    TelemetryService,
)
from .skills import SkillRegistry
from .system_health import SystemHealthService
from .tools import ToolError, ToolRegistry


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChatMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class ChatRequest(ApiModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    skill_id: str = Field(default="executive_assistant", min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    use_tools: bool = True
    session_id: str | None = Field(default=None, min_length=36, max_length=36)
    channel: Literal["text", "voice"] = "text"


class TaskCreate(ApiModel):
    title: str = Field(min_length=1, max_length=300)
    details: str = Field(default="", max_length=10_000)
    priority: int = Field(default=2, ge=1, le=5)
    due_at: datetime | None = None
    dedupe_key: str | None = Field(default=None, max_length=200)


class TaskUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    details: str | None = Field(default=None, max_length=10_000)
    state: Literal["open", "in_progress", "blocked", "done", "cancelled"] | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    due_at: datetime | None = None
    dedupe_key: str | None = Field(default=None, max_length=200)


ProjectStatus = Literal["planned", "active", "paused", "completed", "archived"]
ProjectAccent = Literal[
    "slate", "blue", "cyan", "green", "amber", "orange", "red", "violet", "pink"
]
ProjectTag = Annotated[
    str,
    Field(
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,38}[A-Za-z0-9])?$",
    ),
]
ProjectId = Annotated[
    str,
    Path(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    ),
]


class ProjectCreate(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=10_000)
    status: ProjectStatus = "planned"
    priority: int = Field(default=2, ge=1, le=5)
    progress: int = Field(default=0, ge=0, le=100)
    next_action: str = Field(default="", max_length=500)
    tags: list[ProjectTag] = Field(default_factory=list, max_length=20)
    accent: ProjectAccent = "cyan"
    pinned: bool = False


class ProjectUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=10_000)
    status: ProjectStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    progress: int | None = Field(default=None, ge=0, le=100)
    next_action: str | None = Field(default=None, max_length=500)
    tags: list[ProjectTag] | None = Field(default=None, max_length=20)
    accent: ProjectAccent | None = None
    pinned: bool | None = None

    @model_validator(mode="after")
    def reject_empty_or_null_patch(self) -> Self:
        """Keep PATCH semantics explicit: each supplied field must contain a value."""

        if not self.model_fields_set:
            raise ValueError("At least one project field is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Project fields cannot be null")
        return self


class MemoryAdd(ApiModel):
    content: str = Field(min_length=1, max_length=100_000)
    source: str = Field(default="user", min_length=1, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearch(ApiModel):
    query: str = Field(min_length=1, max_length=10_000)
    limit: int = Field(default=8, ge=1, le=50)
    min_score: float = Field(default=-1.0, ge=-1.0, le=1.0)


class ApprovalDecision(ApiModel):
    actor: str = Field(default="user", min_length=1, max_length=100)


class NodeHeartbeat(ApiModel):
    id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    capabilities: list[str] = Field(default_factory=list, max_length=100)


class LocalNotification(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    level: Literal["info", "warning", "error"] = "info"
    dedupe_key: str | None = Field(default=None, max_length=200)


class PartialCommunicationRoute(ApiModel):
    """Reject empty and null route patches before they reach provider policy."""

    @model_validator(mode="after")
    def reject_empty_or_null_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one communications route field is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Communications route fields cannot be null")
        return self


class SignalRouteUpdate(PartialCommunicationRoute):
    enabled: bool | None = None
    account: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    recipient: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")


class TelegramRouteUpdate(PartialCommunicationRoute):
    enabled: bool | None = None
    chat_id: str | None = Field(default=None, pattern=r"^-?[1-9][0-9]{4,19}$")


class DiscordRouteUpdate(PartialCommunicationRoute):
    enabled: bool | None = None
    mode: Literal["webhook", "bot"] | None = None
    channel_id: str | None = Field(default=None, pattern=r"^[1-9][0-9]{16,19}$")


class WebAppRouteUpdate(PartialCommunicationRoute):
    enabled: bool | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=300)
    auth_enabled: bool | None = None


class CommunicationOrderUpdate(ApiModel):
    provider_order: list[Literal["signal", "telegram", "discord"]] = Field(
        min_length=3, max_length=3
    )

    @model_validator(mode="after")
    def require_each_delivery_provider_once(self) -> Self:
        if set(self.provider_order) != set(DELIVERY_PROVIDERS):
            raise ValueError("Provider order must contain Signal, Telegram, and Discord once")
        return self


class CommunicationFailoverUpdate(ApiModel):
    failover_enabled: bool


class CommunicationCredentialReplace(ApiModel):
    credential: SecretStr


class HealthThresholdUpdate(ApiModel):
    enabled: bool | None = None
    threshold: float | None = None

    @model_validator(mode="after")
    def reject_empty_or_null_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one threshold field is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Threshold fields cannot be null")
        return self


class HealthThresholdsUpdate(ApiModel):
    cpu_percent: HealthThresholdUpdate | None = None
    memory_percent: HealthThresholdUpdate | None = None
    disk_percent: HealthThresholdUpdate | None = None
    temperature_celsius: HealthThresholdUpdate | None = None

    @model_validator(mode="after")
    def reject_empty_or_null_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one health metric threshold is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Health metric threshold updates cannot be null")
        return self


class SystemHealthSettingsUpdate(ApiModel):
    monitor_enabled: bool | None = None
    online_notifications_enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=10, le=3600)
    cooldown_seconds: int | None = Field(default=None, ge=60, le=86_400)
    thresholds: HealthThresholdsUpdate | None = None
    notification_routes: list[Literal["telegram", "discord"]] | None = Field(
        default=None, max_length=2
    )

    @model_validator(mode="after")
    def reject_empty_null_or_duplicate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one system health setting is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("System health settings cannot be null")
        if "notification_routes" in self.model_fields_set and len(
            set(self.notification_routes or [])
        ) != len(self.notification_routes or []):
            raise ValueError("Health notification routes cannot contain duplicates")
        return self


class ActionRequest(ApiModel):
    action: Literal["list_windows", "read_clipboard", "click_control", "run_command"]
    arguments: dict[str, Any] = Field(default_factory=dict)


class FileFindRequest(ApiModel):
    path: str = Field(min_length=1, max_length=4096)
    query: str = Field(default="", max_length=300)
    extension: str | None = Field(default=None, pattern=r"^\.?[A-Za-z0-9_-]{1,20}$")
    max_results: int = Field(default=60, ge=1, le=100)


class AngeronaInspectRequest(ApiModel):
    window_title: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=150, ge=1, le=300)


class AngeronaActionRequest(ApiModel):
    window_title: str = Field(min_length=1, max_length=300)
    action: Literal["focus", "move_to_control", "invoke", "set_text", "send_keys", "scroll"]
    control_name: str | None = Field(default=None, min_length=1, max_length=300)
    automation_id: str | None = Field(default=None, min_length=1, max_length=300)
    control_type: str | None = Field(default=None, min_length=1, max_length=100)
    text: str | None = Field(default=None, max_length=2000)
    keys: str | None = Field(default=None, min_length=1, max_length=30)
    scroll_steps: int = Field(default=0, ge=-20, le=20)


class AngeronaTacticSave(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    preconditions: list[str] = Field(default_factory=list, max_length=20)
    success_evidence: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = False


class Services:
    """Application dependency container."""

    def __init__(self, settings: Settings, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("jarvis")
        self.settings = settings
        self.database = Database(settings.database_path)
        self.database.initialize()
        self.audit = AuditLog(self.database)
        self.orchestration = OrchestrationService(self.database, self.audit)
        self.provider = OllamaProvider(
            settings.ollama_base_url,
            settings.ollama_chat_model,
            settings.ollama_embed_model,
            settings.ollama_timeout_seconds,
            settings.temperature,
            settings.ollama_context_window,
        )
        self.skills = SkillRegistry()
        self.preferences = PreferencesService(settings.data_dir / "settings.json")
        self.angerona = AngeronaAdapter()
        self.angerona_control = AngeronaControlService()
        self.approvals = ApprovalService(self.database, self.audit, settings.approval_ttl_seconds)
        self.federation = FederationService.from_environment(
            settings,
            self.database,
            self.provider,
            self.approvals,
            self.audit,
        )
        self.tasks = TaskService(self.database, self.audit)
        self.projects = ProjectService(self.database, self.audit)
        self.communications = CommunicationsService(self.database, self.audit, settings)
        self.system_health = SystemHealthService(
            self.database, self.audit, settings, self.communications
        )
        self.notifications = NotificationService(self.database, settings, self.logger)
        self.telemetry = TelemetryService(settings)
        self.memory = MemoryService(self.database, self.provider)
        self.memory.pin(
            "jarvis-deferred-upgrades",
            DEFERRED_UPGRADES_MEMORY,
            source="system:pinned",
            metadata={"status": "resumed", "operator_editable": False},
        )
        self.nodes = NodeService(self.database)
        self.angerona_tactics = AngeronaTacticService(self.database, self.audit)
        self.phone_voice = PhoneVoiceService.from_environment(self.database, self.audit)
        self.tools = ToolRegistry(
            settings,
            self.audit,
            self.approvals,
            self.memory,
            self.tasks,
            self.notifications,
            self.telemetry,
            self.angerona_control,
            self.angerona_tactics,
        )
        self.chat = ChatService(
            settings,
            self.provider,
            self.skills,
            self.tools,
            self.audit,
            self.memory,
            self.preferences,
            self.orchestration,
        )
        self.scheduler = Scheduler(settings, self.tasks, self.notifications, self.logger)


def _new_app_logger() -> logging.Logger:
    logger = logging.getLogger(f"jarvis.instance.{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _configure_logging(settings: Settings, logger: logging.Logger) -> list[logging.Handler]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers: list[logging.Handler] = [logging.FileHandler(settings.log_path, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.insert(0, logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return handlers


def _close_logging(logger: logging.Logger, handlers: list[logging.Handler]) -> None:
    for handler in handlers:
        logger.removeHandler(handler)
        handler.flush()
        handler.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create a fully isolated JARVIS application instance."""
    runtime_settings = settings or load_settings()
    app_logger = _new_app_logger()
    services = Services(runtime_settings, app_logger)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        handlers: list[logging.Handler] = []
        started = False
        try:
            handlers = _configure_logging(runtime_settings, app_logger)
            services.audit.append(
                "system", "server.started", "server", {"host": runtime_settings.host}
            )
            services.scheduler.start()
            services.system_health.start()
            started = True
            yield
        finally:
            await services.system_health.stop()
            await services.scheduler.stop()
            if started:
                services.audit.append("system", "server.stopped", "server", {})
            await services.communications.close()
            await services.provider.close()
            services.federation.close()
            services.database.close()
            _close_logging(app_logger, handlers)

    app = FastAPI(
        title="JARVIS Local API",
        version=__version__,
        description="Secure, local-first assistant API powered by Ollama.",
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.settings = runtime_settings
    app.state.logger = app_logger
    security_monitor = SecurityMonitor(runtime_settings, services.audit)
    app.state.security = security_monitor
    app.add_middleware(SecurityMiddleware, monitor=security_monitor)

    @app.middleware("http")
    async def prevent_stale_dashboard_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in {"/", "/index.html", "/app.js", "/styles.css"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    if runtime_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(runtime_settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type"],
        )

    @app.exception_handler(OllamaError)
    async def ollama_error(_: Request, exc: OllamaError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc), "service": "ollama"})

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Request validation failed"})

    @app.exception_handler(ToolError)
    async def tool_error(_: Request, exc: ToolError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(TurnCancelled)
    async def turn_cancelled(_: Request, exc: TurnCancelled) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "cancelled": True})

    @app.exception_handler(AngeronaControlError)
    async def angerona_control_error(_: Request, exc: AngeronaControlError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(PhoneVoiceError)
    async def phone_voice_error(_: Request, exc: PhoneVoiceError) -> Response:
        return Response(
            twiml_say(str(exc), hangup=True),
            status_code=exc.status_code,
            media_type="application/xml",
        )

    @app.exception_handler(CommunicationRateLimitError)
    async def communication_rate_limit(
        _: Request, exc: CommunicationRateLimitError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": str(exc), "retry_after": exc.retry_after},
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(CommunicationSecretStorageError)
    async def communication_secret_storage(
        _: Request, __: CommunicationSecretStorageError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Protected local credential storage is unavailable"},
        )

    @app.exception_handler(KeyError)
    async def key_error(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(FederationDisabledError)
    async def federation_disabled(_: Request, exc: FederationDisabledError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(FederationCapabilityError)
    async def federation_capability(_: Request, exc: FederationCapabilityError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(FederationRequestStateError)
    async def federation_request_state(
        _: Request, exc: FederationRequestStateError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ScopeDeniedError)
    async def federation_scope_denied(_: Request, __: ScopeDeniedError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "federation scope denied"})

    @app.exception_handler(ReplayError)
    @app.exception_handler(IdempotencyConflictError)
    async def federation_replay_conflict(_: Request, __: AuthenticationError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "federation replay rejected"})

    @app.exception_handler(AuthenticationError)
    async def federation_authentication(_: Request, __: AuthenticationError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": "federation authentication failed"})

    @app.exception_handler(EnvelopeValidationError)
    async def federation_envelope_validation(
        _: Request, exc: EnvelopeValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(FederationDependencyError)
    async def federation_dependency(_: Request, __: FederationDependencyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "federation cryptography dependency is unavailable"},
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        ollama_ready = await services.provider.health()
        return {
            "status": "healthy" if ollama_ready else "degraded",
            "local_only": True,
            "ollama": {
                "ready": ollama_ready,
                "base_url": runtime_settings.ollama_base_url,
                "chat_model": runtime_settings.ollama_chat_model,
                "embed_model": runtime_settings.ollama_embed_model,
            },
            "database": {"ready": True, "path": str(runtime_settings.database_path)},
            "audit": services.audit.verify(),
            "telemetry": services.telemetry.snapshot(),
            "coding": services.tools.coding_status(),
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {
            "models": await services.provider.list_models(),
            "defaults": {
                "chat": runtime_settings.ollama_chat_model,
                "embedding": runtime_settings.ollama_embed_model,
            },
        }

    @app.get("/api/settings")
    async def get_settings() -> dict[str, object]:
        angerona = await services.angerona.status()
        return services.preferences.public(
            ollama_ready=await services.provider.health(),
            integration_status={"angerona": angerona["mcp_ready"]},
            federation_status=services.federation.status(),
        )

    @app.get("/api/federation/status")
    async def federation_status() -> dict[str, Any]:
        return services.federation.status()

    @app.post("/api/federation/v1/capabilities")
    async def federation_capability_request(request: Request) -> dict[str, Any]:
        if not services.federation.enabled:
            raise FederationDisabledError("federation is disabled or not safely configured")
        raw_body = await request.body()
        envelope = services.federation.parse_envelope_header(
            request.headers.get(FEDERATION_ENVELOPE_HEADER)
        )
        path_query = request.url.path
        if request.url.query:
            path_query = f"{path_query}?{request.url.query}"
        return await services.federation.handle(
            envelope,
            method=request.method,
            path_query=path_query,
            raw_body=raw_body,
        )

    @app.get("/api/security/posture")
    async def security_posture() -> dict[str, object]:
        angerona_status = await services.angerona.status()
        preference_payload = services.preferences.public(
            ollama_ready=False,
            integration_status={"angerona": angerona_status["mcp_ready"]},
            federation_status=services.federation.status(),
        )
        federation = preference_payload["settings"]["federation"]
        return security_monitor.posture(
            federation=federation,
            audit_valid=bool(services.audit.verify()["valid"]),
            angerona=angerona_status,
        )

    @app.get("/api/security/angerona")
    async def angerona_status() -> dict[str, Any]:
        return await services.angerona.status()

    @app.get("/api/security/angerona/events")
    async def angerona_events(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        return {"events": await services.angerona.recent_events(limit)}

    @app.post("/api/security/angerona/inspect")
    async def inspect_angerona(body: AngeronaInspectRequest) -> dict[str, Any]:
        return await services.tools.execute(
            "angerona_inspect", body.model_dump(), actor="user:hud", risk_tier="guarded"
        )

    @app.post("/api/security/angerona/actions")
    async def request_angerona_action(body: AngeronaActionRequest) -> dict[str, Any]:
        return await services.tools.execute(
            "angerona_action", body.model_dump(), actor="user:hud", risk_tier="guarded"
        )

    @app.post("/api/security/angerona/emergency-stop")
    async def stop_angerona_input() -> dict[str, Any]:
        outcome = services.angerona_control.emergency_stop()
        services.audit.append("user:hud", "angerona.emergency_stopped", "angerona:ui", {})
        return outcome

    @app.post("/api/security/angerona/resume")
    async def resume_angerona_input() -> dict[str, Any]:
        outcome = services.angerona_control.resume()
        services.audit.append("user:hud", "angerona.input_resumed", "angerona:ui", {})
        return outcome

    @app.get("/api/security/angerona/tactics")
    async def list_angerona_tactics(
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> dict[str, Any]:
        return {"tactics": services.angerona_tactics.list(limit)}

    @app.post("/api/security/angerona/tactics")
    async def save_angerona_tactic(body: AngeronaTacticSave) -> dict[str, Any]:
        return await services.tools.execute(
            "angerona_save_tactic",
            body.model_dump(),
            actor="user:hud",
            risk_tier="guarded",
        )

    @app.patch("/api/settings")
    async def update_settings(body: PreferencesUpdate) -> dict[str, object]:
        services.preferences.update(body)
        services.audit.append(
            "user",
            "settings.updated",
            "settings:federation",
            {"fields": sorted(body.model_dump(exclude_unset=True))},
        )
        angerona = await services.angerona.status()
        return services.preferences.public(
            ollama_ready=await services.provider.health(),
            integration_status={"angerona": angerona["mcp_ready"]},
            federation_status=services.federation.status(),
        )

    @app.post("/api/chat")
    async def chat(body: ChatRequest) -> dict[str, Any]:
        return await services.chat.complete(
            [message.model_dump() for message in body.messages],
            body.skill_id,
            model=body.model,
            use_tools=body.use_tools,
            session_id=body.session_id,
            channel=body.channel,
        )

    @app.get("/api/turns/{turn_id}")
    async def get_turn(turn_id: str) -> dict[str, Any]:
        turn = services.orchestration.get(turn_id)
        if turn is None:
            raise HTTPException(status_code=404, detail="Assistant turn not found")
        return {"turn": turn}

    @app.get("/api/turns")
    async def list_turns(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
        return {
            "turns": services.orchestration.recent(limit),
            "metrics": services.orchestration.metrics(),
        }

    @app.post("/api/turns/{turn_id}/cancel")
    async def cancel_turn(turn_id: str) -> dict[str, Any]:
        return {"turn": services.orchestration.request_cancel(turn_id)}

    @app.get("/api/voice/capabilities")
    async def voice_capabilities() -> dict[str, Any]:
        return {
            "transport": "ndjson",
            "streaming_text": True,
            "cancellable_turns": True,
            "local_stt": False,
            "local_tts": False,
            "wake_word": False,
            "note": "Audio remains explicit browser/WebView capture until local STT/TTS adapters are installed.",
        }

    @app.post("/api/chat/stream")
    async def stream_chat(body: ChatRequest) -> StreamingResponse:
        if body.use_tools:
            raise HTTPException(
                status_code=409,
                detail="Streaming tool execution is not enabled; use /api/chat for guarded actions",
            )
        turn = services.orchestration.begin(
            body.skill_id, session_id=body.session_id, channel=body.channel
        )
        services.orchestration.transition(turn["id"], "thinking")
        conversation = [
            {"role": "system", "content": services.skills.system_prompt(body.skill_id)},
            *[message.model_dump() for message in body.messages],
        ]

        async def fragments():
            collected: list[str] = []
            try:
                async for fragment in services.provider.stream_chat(conversation, model=body.model):
                    services.orchestration.checkpoint(turn["id"])
                    collected.append(fragment["content"])
                    yield (
                        json.dumps(
                            {**fragment, "turn_id": turn["id"], "session_id": turn["session_id"]}
                        )
                        + "\n"
                    )
                result = {"message": "".join(collected), "streamed": True}
                services.orchestration.transition(
                    turn["id"], "speaking", event_type="turn.response_ready"
                )
                services.orchestration.transition(turn["id"], "completed", result=result)
            except TurnCancelled:
                yield json.dumps({"turn_id": turn["id"], "cancelled": True, "done": True}) + "\n"
            except Exception as exc:  # noqa: BLE001 - stream must close with a safe terminal event
                services.orchestration.transition(
                    turn["id"], "failed", error_code=type(exc).__name__
                )
                yield (
                    json.dumps({"turn_id": turn["id"], "error": "stream_failed", "done": True})
                    + "\n"
                )

        return StreamingResponse(
            fragments(),
            media_type="application/x-ndjson",
            headers={"X-JARVIS-Turn-ID": turn["id"], "X-JARVIS-Session-ID": turn["session_id"]},
        )

    @app.get("/api/skills")
    async def skills() -> dict[str, Any]:
        return {"skills": services.skills.list()}

    @app.get("/api/coding/status")
    async def coding_status() -> dict[str, Any]:
        return services.tools.coding_status()

    @app.get("/api/live/status")
    async def live_briefing_status() -> dict[str, Any]:
        return services.tools.live.status()

    @app.get("/api/actions/catalog")
    async def action_catalog() -> dict[str, Any]:
        return {"actions": services.tools.catalog(), "shell_enabled": False}

    @app.post("/api/actions/requests")
    async def request_action(body: ActionRequest) -> dict[str, Any]:
        return await services.tools.execute(
            body.action,
            body.arguments,
            actor="user:hud",
            risk_tier="guarded",
        )

    @app.post("/api/files/find")
    async def find_files(body: FileFindRequest) -> dict[str, Any]:
        return await services.tools.execute(
            "find_files", body.model_dump(), actor="user:hud", risk_tier="low"
        )

    @app.post("/api/files/reveal")
    async def reveal_file(body: FileFindRequest) -> dict[str, Any]:
        return await services.tools.execute(
            "reveal_file", {"path": body.path}, actor="user:hud", risk_tier="guarded"
        )

    @app.get("/api/tasks")
    async def list_tasks(
        state: Literal["open", "in_progress", "blocked", "done", "cancelled"] | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"tasks": services.tasks.list(state, limit)}

    @app.post("/api/tasks", status_code=201)
    async def create_task(body: TaskCreate) -> dict[str, Any]:
        task, created = services.tasks.create(
            body.title,
            body.details,
            body.priority,
            body.due_at.isoformat() if body.due_at else None,
            body.dedupe_key,
        )
        return {"task": task, "created": created}

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        task = services.tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return {"task": task}

    @app.patch("/api/tasks/{task_id}")
    async def update_task(task_id: str, body: TaskUpdate) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True)
        if "due_at" in changes and changes["due_at"] is not None:
            changes["due_at"] = changes["due_at"].isoformat()
        return {"task": services.tasks.update(task_id, changes)}

    @app.get("/api/projects")
    async def list_projects(
        status: ProjectStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"projects": services.projects.list(status, limit)}

    @app.post("/api/projects", status_code=201)
    async def create_project(body: ProjectCreate) -> dict[str, Any]:
        return {
            "project": services.projects.create(
                body.title,
                body.summary,
                body.status,
                body.priority,
                body.progress,
                body.next_action,
                body.tags,
                body.accent,
                body.pinned,
                actor="user:hud",
            )
        }

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: ProjectId) -> dict[str, Any]:
        project = services.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return {"project": project}

    @app.patch("/api/projects/{project_id}")
    async def update_project(project_id: ProjectId, body: ProjectUpdate) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True)
        return {"project": services.projects.update(project_id, changes, actor="user:hud")}

    @app.get("/api/system-health")
    async def system_health() -> dict[str, Any]:
        """Return a read-only live snapshot plus persisted monitor state."""

        return services.system_health.snapshot()

    @app.get("/api/system-health/settings")
    async def system_health_settings() -> dict[str, Any]:
        return {
            "settings": services.system_health.get_settings(),
            "delivery": services.system_health.delivery_status(),
        }

    @app.patch("/api/system-health/settings")
    async def update_system_health_settings(
        body: SystemHealthSettingsUpdate,
    ) -> dict[str, Any]:
        settings = services.system_health.update_settings(
            body.model_dump(exclude_unset=True), actor="user:hud"
        )
        return {"settings": settings, "delivery": services.system_health.delivery_status()}

    @app.get("/api/system-health/alerts")
    async def system_health_alerts(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        metric: Literal["cpu_percent", "memory_percent", "disk_percent", "temperature_celsius"]
        | None = None,
        event_type: Literal["triggered", "ongoing", "recovered"] | None = None,
    ) -> dict[str, Any]:
        return {"alerts": services.system_health.list_alerts(limit, metric, event_type)}

    @app.get("/api/system-health/alerts/state")
    async def system_health_alert_state() -> dict[str, Any]:
        return services.system_health.alert_state()

    @app.get("/api/communications")
    async def communications() -> dict[str, Any]:
        return services.communications.snapshot()

    @app.get("/api/communications/status")
    async def communications_status() -> dict[str, Any]:
        return services.communications.snapshot()

    @app.get("/api/communications/routes/{provider}")
    async def communication_route(provider: ProviderId) -> dict[str, Any]:
        routes = services.communications.snapshot()["routes"]
        return {"route": next(route for route in routes if route["provider"] == provider)}

    @app.patch("/api/communications/routes/signal")
    async def update_signal_route(body: SignalRouteUpdate) -> dict[str, Any]:
        return {
            "route": services.communications.update_route(
                "signal", body.model_dump(exclude_unset=True)
            )
        }

    @app.patch("/api/communications/routes/telegram")
    async def update_telegram_route(body: TelegramRouteUpdate) -> dict[str, Any]:
        return {
            "route": services.communications.update_route(
                "telegram", body.model_dump(exclude_unset=True)
            )
        }

    @app.patch("/api/communications/routes/discord")
    async def update_discord_route(body: DiscordRouteUpdate) -> dict[str, Any]:
        return {
            "route": services.communications.update_route(
                "discord", body.model_dump(exclude_unset=True)
            )
        }

    @app.patch("/api/communications/routes/web_app")
    async def update_web_app_route(body: WebAppRouteUpdate) -> dict[str, Any]:
        return {
            "route": services.communications.update_route(
                "web_app", body.model_dump(exclude_unset=True)
            )
        }

    @app.post("/api/communications/routes/{provider}/test")
    async def test_communication_route(provider: ProviderId) -> dict[str, Any]:
        return {"test": await services.communications.test_route(provider)}

    @app.post("/api/communications/routes/{provider}/credential")
    async def replace_communication_credential(
        provider: Literal["telegram", "discord", "web_app"],
        body: CommunicationCredentialReplace,
    ) -> dict[str, Any]:
        return {
            "route": services.communications.replace_credential(
                provider, body.credential.get_secret_value()
            )
        }

    @app.delete("/api/communications/routes/{provider}/credential")
    async def clear_communication_credential(
        provider: Literal["telegram", "discord", "web_app"],
    ) -> dict[str, Any]:
        return {"route": services.communications.clear_credential(provider)}

    @app.patch("/api/communications/order")
    async def update_communication_order(body: CommunicationOrderUpdate) -> dict[str, Any]:
        return services.communications.reorder(body.provider_order)

    @app.patch("/api/communications/failover")
    async def update_communication_failover(
        body: CommunicationFailoverUpdate,
    ) -> dict[str, Any]:
        return services.communications.update_failover(body.failover_enabled)

    @app.get("/api/memory")
    async def list_memory(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {
            "items": services.memory.list(limit),
            "pinned": services.memory.list_pinned(min(limit, 20)),
        }

    @app.post("/api/memory", status_code=201)
    async def add_memory(body: MemoryAdd) -> dict[str, Any]:
        item = await services.memory.add(body.content, body.source, body.metadata)
        services.audit.append(
            "user", "memory.created", f"memory:{item['id']}", {"source": body.source}
        )
        return {"item": item}

    @app.post("/api/memory/search")
    async def search_memory(body: MemorySearch) -> dict[str, Any]:
        return {"results": await services.memory.search(body.query, body.limit, body.min_score)}

    @app.delete("/api/memory/{item_id}")
    async def delete_memory(item_id: str) -> dict[str, Any]:
        deleted = services.memory.delete(item_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory item not found")
        services.audit.append("user", "memory.deleted", f"memory:{item_id}", {})
        return {"deleted": True}

    @app.get("/api/approvals")
    async def list_approvals(
        status: Literal["pending", "approved", "denied", "expired", "failed"] | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"approvals": services.approvals.list(status, limit)}

    @app.post("/api/approvals/{request_id}/approve")
    async def approve(request_id: str, body: ApprovalDecision) -> dict[str, Any]:
        request = services.approvals.get(request_id)
        if request is not None and request.get("tool_name") == FEDERATION_ADMIN_APPROVAL_TOOL:
            outcome = services.federation.approve_admin_proposal(request_id, body.actor)
        else:
            outcome = await services.tools.execute_approval(request_id, body.actor)
        turn = services.orchestration.resume_approval(request_id, outcome, succeeded=True)
        return {"outcome": outcome, "approval": services.approvals.get(request_id), "turn": turn}

    @app.post("/api/approvals/{request_id}/deny")
    async def deny(request_id: str, body: ApprovalDecision) -> dict[str, Any]:
        approval = services.approvals.resolve(request_id, "denied", body.actor)
        turn = services.orchestration.find_by_approval(request_id)
        if turn is not None and turn["state"] == "awaiting_approval":
            turn = services.orchestration.transition(
                turn["id"],
                "cancelled",
                event_type="turn.approval_denied",
                details={"approval_id": request_id},
            )
        return {"approval": approval, "turn": turn}

    @app.get("/api/audit")
    async def list_audit(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before_id: Annotated[int | None, Query(ge=1)] = None,
    ) -> dict[str, Any]:
        return {"entries": services.audit.list(limit, before_id)}

    @app.get("/api/audit/verify")
    async def verify_audit() -> dict[str, Any]:
        return services.audit.verify()

    @app.get("/api/nodes")
    async def list_nodes() -> dict[str, Any]:
        return {"nodes": services.nodes.list(), "discovery_enabled": False}

    @app.post("/api/nodes/heartbeat")
    async def node_heartbeat(body: NodeHeartbeat) -> dict[str, Any]:
        node = services.nodes.heartbeat(body.id, body.name, str(body.url), body.capabilities)
        services.audit.append("node", "node.heartbeat", f"node:{body.id}", {})
        return {"node": node}

    @app.get("/api/notifications")
    async def list_notifications(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {"notifications": services.notifications.list(limit)}

    @app.post("/api/notifications", status_code=201)
    async def notify(body: LocalNotification) -> dict[str, Any]:
        return {"notification": services.notifications.send_local(**body.model_dump())}

    @app.post("/api/scheduler/run")
    async def run_scheduler() -> dict[str, Any]:
        return {"inactive_tasks": await services.scheduler.run_once()}

    async def verified_phone_parameters(request: Request) -> tuple[dict[str, str], bytes]:
        raw_body = await request.body()
        return (
            services.phone_voice.verify_webhook(
                path=request.url.path,
                raw_body=raw_body,
                supplied_signature=request.headers.get("X-Twilio-Signature"),
            ),
            raw_body,
        )

    @app.get("/api/phone/status")
    async def phone_status() -> dict[str, Any]:
        return services.phone_voice.status()

    @app.post("/api/phone/voice", response_class=Response)
    async def phone_voice(request: Request) -> Response:
        parameters, _ = await verified_phone_parameters(request)
        session = services.phone_voice.begin(
            parameters.get("CallSid", ""), parameters.get("From", "")
        )
        if bool(session.get("authenticated")):
            xml = twiml_gather(
                "JARVIS online. State one command, or say hang up.",
                action="/api/phone/command",
                input_modes="speech dtmf",
            )
        else:
            xml = twiml_gather(
                "JARVIS secure line. Enter your access PIN.",
                action="/api/phone/pin",
                input_modes="dtmf",
                num_digits=6,
            )
        return Response(xml, media_type="application/xml")

    @app.post("/api/phone/pin", response_class=Response)
    async def phone_pin(request: Request) -> Response:
        parameters, raw_body = await verified_phone_parameters(request)
        call_sid = parameters.get("CallSid", "")
        services.phone_voice.begin(call_sid, parameters.get("From", ""))
        if not services.phone_voice.claim_webhook_once(call_sid, request.url.path, raw_body):
            return Response(
                twiml_say("This request was already handled.", redirect="/api/phone/voice"),
                media_type="application/xml",
            )
        if not services.phone_voice.authenticate(call_sid, parameters.get("Digits", "")):
            return Response(
                twiml_say("Access denied.", redirect="/api/phone/voice"),
                media_type="application/xml",
            )
        return Response(
            twiml_gather(
                "Identity confirmed. State one command, or say hang up.",
                action="/api/phone/command",
                input_modes="speech dtmf",
            ),
            media_type="application/xml",
        )

    @app.post("/api/phone/command", response_class=Response)
    async def phone_command(request: Request) -> Response:
        parameters, raw_body = await verified_phone_parameters(request)
        call_sid = parameters.get("CallSid", "")
        services.phone_voice.begin(call_sid, parameters.get("From", ""))
        if not services.phone_voice.claim_webhook_once(call_sid, request.url.path, raw_body):
            return Response(
                twiml_say("That command was already handled.", redirect="/api/phone/voice"),
                media_type="application/xml",
            )
        services.phone_voice.accept_command(call_sid)
        command = (parameters.get("SpeechResult") or parameters.get("Digits") or "").strip()
        if not command:
            return Response(
                twiml_say("No command was received.", redirect="/api/phone/voice"),
                media_type="application/xml",
            )
        if command.casefold() in {"hang up", "goodbye", "end call", "9"}:
            services.phone_voice.end(call_sid)
            return Response(
                twiml_say("Secure session ended.", hangup=True),
                media_type="application/xml",
            )
        lowered = command.casefold()
        if any(word in lowered for word in ("angerona", "defense", "security", "threat")):
            skill_id = "angerona_defense"
        elif any(word in lowered for word in ("file", "folder", "locate", "search")):
            skill_id = "spatial_files"
        elif any(word in lowered for word in ("code", "program", "test", "build")):
            skill_id = "software_engineer"
        else:
            skill_id = "executive_assistant"
        outcome = await services.chat.complete(
            [{"role": "user", "content": command[:4000]}],
            skill_id,
            use_tools=True,
        )
        reply = str(outcome.get("message") or "Command received.")
        if outcome.get("approval_required"):
            reply += " This action is waiting for approval on the local JARVIS dashboard."
        services.audit.append(
            "phone:caller",
            "phone.command_completed",
            f"phone_call:{call_sid}",
            {"skill_id": skill_id, "approval_required": bool(outcome.get("approval_required"))},
        )
        return Response(
            twiml_say(reply, redirect="/api/phone/voice"),
            media_type="application/xml",
        )

    @app.get("/api")
    async def api_root() -> dict[str, str]:
        return {"name": "JARVIS Local API", "version": __version__}

    web_candidates = (
        runtime_settings.project_root / "web" / "dist",
        runtime_settings.project_root / "web",
    )
    static_root = next(
        (candidate for candidate in web_candidates if (candidate / "index.html").is_file()),
        None,
    )
    if static_root is not None:
        app.mount("/", StaticFiles(directory=static_root, html=True), name="web")

    return app
