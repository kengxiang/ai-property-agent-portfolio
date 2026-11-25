"""Lead preview and confirmation API."""

from fastapi import APIRouter

from backend.schemas.lead import LeadConfirmRequest, LeadConfirmResponse, LeadDraft, LeadPrepareResponse
from backend.services.lead_service import confirm_lead, prepare_lead


router = APIRouter(prefix="/leads", tags=["Leads (Demo)"])


@router.post("/prepare", response_model=LeadPrepareResponse)
def prepare_lead_endpoint(request: LeadDraft) -> LeadPrepareResponse:
    return prepare_lead(request)


@router.post(
    "/confirm",
    response_model=LeadConfirmResponse,
    responses={
        404: {"description": "Property not found"},
        409: {"description": "Idempotency key already used with different details"},
        500: {"description": "Lead transaction failed"},
        503: {"description": "Lead database unavailable or transaction temporarily interrupted"},
    },
)
def confirm_lead_endpoint(request: LeadConfirmRequest) -> LeadConfirmResponse:
    """Confirm with explicit consent. An identical replay returns HTTP 200 and the original Lead ID."""
    return confirm_lead(request)
