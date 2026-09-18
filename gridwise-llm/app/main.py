import json
import asyncio
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.schemas import OptimizeRequest, OptimizeResponse
from app.llm_interpreter import interpret_operator_notes
from app.optimizer import solve_energy_plan, InfeasiblePlanException

app = FastAPI(title="GridWise API", version="1.0.0")

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
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(req: OptimizeRequest):
    valid_directives = await interpret_operator_notes(req.operator_notes, req.battery.capacity_kwh)
    
    def run_opt():
        return solve_energy_plan(req.scenario_id, req.hours, req.battery, valid_directives)
        
    hourly_plan, total_grid, total_cost, peak_grid, summary = await asyncio.to_thread(run_opt)
    
    applied_types = [d.directive_type.value for d in valid_directives if d.applies]
    types_str = ", ".join(applied_types) if applied_types else "no adjustments"
    plan_summary_text = f"Optimized 24-hour schedule with {types_str}, achieving total cost {summary['total_cost_bdt']} BDT using {summary['total_grid_kwh']} kWh from grid."
    
    return OptimizeResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=valid_directives,
        hourly_plan=hourly_plan,
        total_cost_bdt=summary["total_cost_bdt"],
        total_grid_kwh=summary["total_grid_kwh"],
        peak_grid_kwh=summary["peak_grid_kwh"],
        plan_summary=plan_summary_text
    )
