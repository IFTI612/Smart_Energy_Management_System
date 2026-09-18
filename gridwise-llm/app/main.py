from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import asyncio
import json

from app.schemas import OptimizeRequest, OptimizeResponse
from app.llm_interpreter import interpret_operator_notes
from app.optimizer import solve_energy_plan, InfeasiblePlanException

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    if errors and errors[0].get("type") == "json_invalid":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Malformed JSON request"}
        )
    return JSONResponse(
        status_code=422,
        content={"detail": errors}
    )

@app.exception_handler(InfeasiblePlanException)
async def infeasible_plan_handler(request: Request, exc: InfeasiblePlanException):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal optimization or processing error"}
    )

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 400:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Malformed JSON request"}
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc.detail)}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, json.JSONDecodeError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Malformed JSON request"}
        )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal optimization or processing error"}
    )

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(req: OptimizeRequest):
    capacity_kwh = req.battery.capacity_kwh
    directives = await interpret_operator_notes(req.operator_notes, capacity_kwh)
    
    hourly_plan, total_grid, total_cost, peak_grid, summary = await asyncio.to_thread(
        solve_energy_plan,
        req.scenario_id,
        req.hours,
        req.battery,
        directives
    )
    
    return OptimizeResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary=summary
    )
