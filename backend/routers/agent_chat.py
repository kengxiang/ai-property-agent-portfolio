from contextlib import nullcontext
import json
import logging
import os
import re
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from backend.database.connection import get_engine
from backend.services import admin_ingestion, model_settings
from backend.routers.admin import database_call
from llm.gemini_metrics import GeminiUsage, error_metadata, failure as gemini_failure
from llm.gemini_rate_limit import GeminiRateWait, GeminiTaskStop, track_gemini_http
from agent.property_evidence import guard_transaction_terms
from agent.graph import (
    build_agent,
    create_local_model,
    create_cloud_model,
    extract_text,
    is_argument_error,
    TOOL_ARGUMENT_FAILURE,
)


router = APIRouter(tags=["AI Agent"])
logger = logging.getLogger(__name__)
PROPERTY_ID = re.compile(r"\bP\d{3,}[A-Z0-9-]*\b", re.IGNORECASE)
DOCUMENT_POLICY = re.compile(
    r"\b(?:house rules?|pet polic(?:y|ies)|pets? (?:are )?(?:allowed|permitted)|"
    r"quiet hours?|visitor rules?|parking policy)\b|房规|房規|宠物|寵物",
    re.IGNORECASE,
)


class ChatTurn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1)
    provider: Literal["local", "cloud"] | None = None
    cloud_consent: bool = False
    session_id: str | None = None  # Legacy API field. Context is supplied with each request.
    history: list[ChatTurn] = Field(default_factory=list, max_length=6)


class ChatResponse(BaseModel):
    answer: str
    tool_calls: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    model: str
    latency_ms: float
    provider_usage: dict[str, Any] | None = None
    gemini_http_requests: int | None = None
    tool_argument_failures: int | None = None


def extract_sources(messages) -> list[dict[str, Any]]:
    """Extract verified retrieval sources from RAG tool results."""

    sources = []
    seen = set()

    for message in messages:

        if not isinstance(message, ToolMessage):
            continue

        if message.name != "search_documents":
            continue

        if getattr(message, "status", "success") != "success":
            continue

        # An empty result is valid when no documents match.
        if message.content == []:
            records = []
        elif isinstance(message.content, str):
            try:
                records = json.loads(message.content)
            except json.JSONDecodeError as error:
                raise HTTPException(
                    status_code=502,
                    detail="Invalid document search result",
                ) from error
        else:
            raise HTTPException(
                status_code=502,
                detail="Invalid document search result",
            )

        if not isinstance(records, list):
            raise HTTPException(
                status_code=502,
                detail="Invalid document search result",
            )

        for item in records:

            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=502,
                    detail="Invalid document source metadata",
                )

            property_id = item.get("property_id")
            document_id = item.get("document_id")
            filename = item.get("filename")
            page = item.get("page")

            if (
                not isinstance(property_id, str)
                or not isinstance(document_id, str)
                or not isinstance(filename, str)
                or not isinstance(page, int)
                or isinstance(page, bool)
                or page < 1
                or not property_id
                or not document_id
                or not filename
            ):
                raise HTTPException(
                    status_code=502,
                    detail="Invalid document source metadata",
                )

            key = (property_id, filename, page)

            if key in seen:
                continue

            seen.add(key)

            sources.append({
                "property_id": property_id,
                "document_id": document_id,
                "filename": filename,
                "page": page,
            })

    return sources

def require_provider_override(token):
    if not token or os.getenv("ADMIN_INGESTION_ENABLED") != "1":
        raise HTTPException(403, "Only administrators can override the provider")
    try:
        engine = get_engine()
    except (RuntimeError, ValueError):
        raise HTTPException(503, "Model settings are unavailable") from None
    try:
        database_call(admin_ingestion.authorize, engine, token)
    finally:
        engine.dispose()


@router.get("/agent/provider")
def public_provider():
    return model_settings.current_status()


CONTACT_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+|(?<![\w])\+?\d(?:[ .()-]*\d){7,}(?![\w])")


def resolve_provider(request, http_request):
    status = model_settings.current_status()
    provider = status["active_provider"]
    if request.provider is not None and request.provider != provider:
        kind, _, token = http_request.headers.get("Authorization", "").partition(" ")
        require_provider_override(token if kind.lower() == "bearer" else "")
        provider = request.provider
    if provider == "cloud":
        if not request.cloud_consent:
            raise HTTPException(422, "Explicit consent is required for Gemini. Keep personal details out of chat.")
        texts = [request.message, *(part for turn in request.history for part in (turn.question, turn.answer))]
        if any(CONTACT_PATTERN.search(text) for text in texts):
            raise HTTPException(422, "Remove contact details before sending chat to Gemini")
    return provider


@router.post(
    "/agent/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True,
    responses={
        502: {"description": "Agent tool execution failed or AI returned an empty answer"},
        503: {"description": "AI provider or agent is unavailable"},
    },
)
def agent_chat(request: ChatRequest, http_request: Request):

    provider = resolve_provider(request, http_request)
    start = time.perf_counter()

    context = [
        message
        for turn in request.history
        for message in (HumanMessage(content=turn.question), AIMessage(content=turn.answer))
    ]

    usage = GeminiUsage() if provider == "cloud" else None
    http_usage = None
    config = {"recursion_limit": 10}
    if usage is not None:
        config["callbacks"] = [usage]
    try:
        if provider == "local":
            model = create_local_model()
        else:
            model = create_cloud_model()

        agent = build_agent(model)

        with (track_gemini_http() if usage is not None else nullcontext()) as http_usage:
            result = agent.invoke(
                {
                    "messages": [*context, HumanMessage(content=request.message)]
                },
                config=config,
            )

    except Exception as error:
        if usage is not None:
            if isinstance(error, GeminiTaskStop):
                raise HTTPException(503, detail={"code": "provider_configuration",
                                                 "provider_usage": usage.summary(),
                                                 "gemini_http_requests": (http_usage or {}).get("requests", 0)}) from None
            if isinstance(error, GeminiRateWait):
                raise HTTPException(429, detail={"code": "rate_limit", "provider_usage": usage.summary(),
                                                 "gemini_http_requests": (http_usage or {}).get("requests", 0),
                                                 "retry_after_seconds": error.retry_after_seconds}) from None
            status, code = gemini_failure(error)
            raise HTTPException(status, detail={"code": code, "provider_usage": usage.summary(),
                                                "gemini_http_requests": (http_usage or {}).get("requests", 0),
                                                **error_metadata(error)}) from None
        raise HTTPException(
            status_code=503,
            detail="AI provider or agent is unavailable"
        ) from error

    # Only report tool calls and citations generated for this request.
    messages = result["messages"][len(context):]

    failed_ids = set()
    unresolved = {}
    batch = 0
    successful_results = []
    for message in messages:
        if isinstance(message, AIMessage):
            batch += 1
        if not isinstance(message, ToolMessage):
            continue
        if getattr(message, "status", "success") == "error":
            if not is_argument_error(message):
                logger.warning("Agent tool %s failed for provider %s", message.name, provider)
                raise HTTPException(status_code=502, detail="Agent tool execution failed")
            failed_ids.add(message.tool_call_id)
            unresolved[message.name] = batch
        else:
            successful_results.append(message)
            if message.name in unresolved and unresolved[message.name] < batch:
                unresolved.pop(message.name)

    tool_calls = [
        {"name": call["name"], "args": call["args"]}
        for message in messages if isinstance(message, AIMessage)
        for call in message.tool_calls if call["id"] not in failed_ids
    ]

    sources = extract_sources(messages)
    answer = extract_text(messages[-1].content) if messages else ""

    if unresolved:
        answer = TOOL_ARGUMENT_FAILURE
        sources = []
        tool_calls = []
    elif successful_results and all(
        item.name == "get_property_details" and item.content == "null"
        for item in successful_results
    ):
        ids = list(dict.fromkeys(str(call["args"]["property_id"]) for call in tool_calls))
        answer = f"I could not find the requested property ({', '.join(ids)}) in the listings."
    elif successful_results and all(
        item.name == "search_properties" and item.content in ([], "[]")
        for item in successful_results
    ):
        answer = "No properties matched the requested criteria."
    elif not sources and (
        (tool_calls and all(call["name"] == "search_documents" for call in tool_calls))
        or (not tool_calls and PROPERTY_ID.search(request.message) and DOCUMENT_POLICY.search(request.message))
    ):
        answer = "I cannot confirm that information. No supporting property documents were found."

    elif answer:
        answer = guard_transaction_terms(request.message, answer, successful_results)

    if not answer:
        logger.warning("Agent returned no visible answer for provider %s", provider)
        raise HTTPException(
            status_code=502,
            detail="AI returned an empty answer"
        )

    latency_ms = round(
        (time.perf_counter() - start) * 1000, 2
    )

    return ChatResponse(
        answer=answer,
        tool_calls=tool_calls,
        sources=sources,
        model=getattr(model, "model", "unknown"),
        latency_ms=latency_ms,
        provider_usage=usage.summary() if usage is not None else None,
        gemini_http_requests=http_usage["requests"] if http_usage is not None else None,
        tool_argument_failures=len(failed_ids) if usage is not None else None,
    )
