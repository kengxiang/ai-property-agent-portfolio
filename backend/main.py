from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from backend.routers.staff import router as staff_router
from backend.routers.admin import router as admin_router
from backend.routers.properties import router as properties_router
from backend.routers.agent_chat import router as agent_router
from backend.routers.leads import confirm_lead_endpoint, router as leads_router

app = FastAPI(
    title="AI Property Agent API",
    version="0.1.0"
)


@app.exception_handler(RequestValidationError)
async def handle_request_validation(request: Request, error: RequestValidationError):
    if request.scope.get("endpoint") is confirm_lead_endpoint or request.url.path.startswith(("/admin/", "/staff/")):
        details = [
            {"loc": issue["loc"], "msg": issue["msg"], "type": issue["type"]}
            for issue in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})
    return await request_validation_exception_handler(request, error)


@app.get("/")
def root():
    return {
        "message": "AI Property Agent API is running"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }


app.include_router(admin_router)
app.include_router(staff_router)
app.include_router(properties_router)
app.include_router(agent_router)
app.include_router(leads_router)
