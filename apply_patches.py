import os

base_dir = r"e:\Hackathon\BUP CSE Fest 26\Smart Energy Management System\Smart_Energy_Management_System\gridwise-llm"

files = {
    "app/llm_interpreter.py": """import hashlib
import asyncio
import httpx
import logging
from groq import AsyncGroq
from app.config import (
    GROQ_API_KEY, GEMINI_API_KEY, GROQ_MODEL, GEMINI_MODEL,
    PRIMARY_TIMEOUT, PRIMARY_RETRY_TIMEOUT, FALLBACK_TIMEOUT, TOTAL_LLM_BUDGET
)
from app.schemas import DirectiveInterpretation
from app.guardrails import parse_raw_llm_json, validate_and_repair_directives
from typing import List

_CACHE = {}

def _get_cache_key(note: str, capacity_kwh: float) -> str:
    s = f"{note.strip().lower()}|{capacity_kwh}"
    return hashlib.sha256(s.encode('utf-8')).hexdigest()

_SYSTEM_PROMPT = \"\"\"You are an expert energy management interpreter.
You must output a JSON object containing a list of directives under the key "directives".

Available schemas (DirectiveType):
- solar_reduction: hours (list of ints), factor (float 0.0-1.0, e.g. 80% reduction -> 0.20 remaining)
- minimum_battery_reserve: hours (list of ints), minimum_energy_kwh (float)
- no_charge_window: hours (list of ints)
- no_discharge_window: hours (list of ints)
- max_grid_window: hours (list of ints), max_grid_kwh (float)
- no_op: structured_adjustment must be null.

Rules:
- 1-inclusive/end-exclusive hour rules: e.g., "1 PM to 4 PM" means hours [13, 14, 15].
- Produce exactly one directive per operator note.
- Map the note's index (0-based) to `note_index`.
- Set `applies` to true for valid directives, false for no_op.
- If a note defines a percentage for reserve, calculate the absolute kWh using the provided battery capacity.
- CRITICAL: You must nest the parameters inside "structured_adjustment".

Example Interaction:

User:
Operator Notes:
[0] Expect an 80% reduction in rooftop solar between 10 AM and 1 PM.
[1] Do not charge the battery between 5 PM and 8 PM.
[2] The cafeteria menu changes tomorrow.
Battery capacity: 200 kWh

Assistant:
{
  "directives": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [10, 11, 12], "factor": 0.20},
      "explanation": "80% reduction leaves 20% remaining usable solar."
    },
    {
      "note_index": 1,
      "applies": true,
      "directive_type": "no_charge_window",
      "structured_adjustment": {"hours": [17, 18, 19]},
      "explanation": "Charging is disabled during this window."
    },
    {
      "note_index": 2,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "This note does not affect today's energy schedule."
    }
  ]
}
\"\"\"

async def interpret_operator_notes(notes: List[str], capacity_kwh: float) -> List[DirectiveInterpretation]:
    cached_results = []
    uncached_indices = []
    
    for i, note in enumerate(notes):
        key = _get_cache_key(note, capacity_kwh)
        if key in _CACHE:
            cached_item = _CACHE[key].model_copy()
            cached_item.note_index = i
            cached_results.append(cached_item)
        else:
            uncached_indices.append(i)
            
    if not uncached_indices:
        return sorted(cached_results, key=lambda x: x.note_index)
        
    user_prompt = "Operator Notes:\\n"
    for i in uncached_indices:
        user_prompt += f"[{i}] {notes[i]}\\n"
    user_prompt += f"Battery capacity: {capacity_kwh} kWh\\n"
    
    llm_response_text = None
    
    try:
        async with asyncio.timeout(TOTAL_LLM_BUDGET):
            if GROQ_API_KEY:
                client = AsyncGroq(api_key=GROQ_API_KEY)
                for attempt in range(2): 
                    try:
                        req_timeout = PRIMARY_TIMEOUT if attempt == 0 else PRIMARY_RETRY_TIMEOUT
                        resp = await client.chat.completions.create(
                            model=GROQ_MODEL,
                            messages=[
                                {"role": "system", "content": _SYSTEM_PROMPT},
                                {"role": "user", "content": user_prompt}
                            ],
                            response_format={"type": "json_object"},
                            temperature=0,
                            timeout=req_timeout
                        )
                        llm_response_text = resp.choices[0].message.content
                        break
                    except Exception as e:
                        if attempt == 1:
                            pass
                            
            if not llm_response_text and GEMINI_API_KEY:
                try:
                    async with httpx.AsyncClient(timeout=FALLBACK_TIMEOUT) as client:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
                        payload = {
                            "contents": [
                                {"role": "user", "parts": [{"text": _SYSTEM_PROMPT + "\\n\\n" + user_prompt}]}
                            ],
                            "generationConfig": {
                                "responseMimeType": "application/json",
                                "temperature": 0.0
                            }
                        }
                        resp = await client.post(url, json=payload)
                        resp.raise_for_status()
                        data = resp.json()
                        llm_response_text = data["candidates"][0]["content"]["parts"][0]["text"]
                except Exception:
                    pass
    except Exception as e:
        logging.error(f"Overall LLM failure: {e}")
        llm_response_text = None
            
    if llm_response_text is None:
        logging.error("LLM failed to return a valid response, falling back to no_op")
        
    parsed = parse_raw_llm_json(llm_response_text) if llm_response_text else None
    valid_directives = validate_and_repair_directives(parsed, len(notes), capacity_kwh)
    
    for cached_item in cached_results:
        valid_directives[cached_item.note_index] = cached_item
        
    for i in uncached_indices:
        key = _get_cache_key(notes[i], capacity_kwh)
        _CACHE[key] = valid_directives[i]
        
    return valid_directives
""",

    "app/guardrails.py": """import re
import json
import math
from typing import List, Dict, Optional, Any
from app.schemas import DirectiveInterpretation, DirectiveType

def parse_raw_llm_json(raw_text: str) -> Optional[List[Dict]]:
    text = raw_text.strip()
    text = re.sub(r'^```[a-zA-Z]*\\n', '', text, flags=re.MULTILINE)
    text = re.sub(r'```$', '', text, flags=re.MULTILINE)
    text = text.strip()
    
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
            return parsed
        elif isinstance(parsed, dict):
            for key in parsed:
                if isinstance(parsed[key], list) and all(isinstance(item, dict) for item in parsed[key]):
                    return parsed[key]
        return None
    except json.JSONDecodeError:
        return None

def _is_valid_numeric(val: Any) -> bool:
    return isinstance(val, (int, float)) and math.isfinite(val)

def validate_and_repair_directives(raw_list: Optional[List[Dict]], num_notes: int, capacity_kwh: float) -> List[DirectiveInterpretation]:
    results = {i: None for i in range(num_notes)}
    
    if raw_list is None:
        raw_list = []
        
    for raw in raw_list:
        idx = raw.get("note_index")
        if not isinstance(idx, int) or idx < 0 or idx >= num_notes:
            continue
            
        if results[idx] is not None:
            continue
            
        raw_type = raw.get("directive_type")
        try:
            dtype = DirectiveType(raw_type)
        except ValueError:
            dtype = DirectiveType.no_op
            
        raw_adj = raw.get("structured_adjustment")
        if not isinstance(raw_adj, dict):
            raw_adj = {}
            
        valid_adj = {}
        if dtype != DirectiveType.no_op:
            raw_hours = raw_adj.get("hours", [])
            if isinstance(raw_hours, list):
                valid_hours = []
                for h in raw_hours:
                    if isinstance(h, (int, float)) and float(h).is_integer():
                        h_int = int(h)
                        if 0 <= h_int <= 23:
                            valid_hours.append(h_int)
                valid_adj["hours"] = sorted(list(set(valid_hours)))
            else:
                valid_adj["hours"] = []
                
            if dtype == DirectiveType.solar_reduction:
                if "factor" in raw_adj and _is_valid_numeric(raw_adj["factor"]):
                    valid_adj["factor"] = max(0.0, min(1.0, float(raw_adj["factor"])))
                else:
                    dtype = DirectiveType.no_op
                    valid_adj = None
            elif dtype == DirectiveType.minimum_battery_reserve:
                if "minimum_energy_kwh" in raw_adj and _is_valid_numeric(raw_adj["minimum_energy_kwh"]):
                    valid_adj["minimum_energy_kwh"] = max(0.0, min(capacity_kwh, float(raw_adj["minimum_energy_kwh"])))
                else:
                    dtype = DirectiveType.no_op
                    valid_adj = None
            elif dtype == DirectiveType.max_grid_window:
                if "max_grid_kwh" in raw_adj and _is_valid_numeric(raw_adj["max_grid_kwh"]):
                    valid_adj["max_grid_kwh"] = max(0.0, float(raw_adj["max_grid_kwh"]))
                else:
                    dtype = DirectiveType.no_op
                    valid_adj = None
        else:
            valid_adj = None
            
        results[idx] = DirectiveInterpretation(
            note_index=idx,
            applies=(dtype != DirectiveType.no_op),
            directive_type=dtype,
            structured_adjustment=valid_adj,
            explanation=raw.get("explanation", "Recovered from output.")
        )
        
    final_list = []
    for i in range(num_notes):
        if results[i] is None:
            final_list.append(DirectiveInterpretation(
                note_index=i,
                applies=False,
                directive_type=DirectiveType.no_op,
                structured_adjustment=None,
                explanation="System fallback for missing or invalid directive."
            ))
        else:
            final_list.append(results[i])
            
    return final_list
""",

    "app/optimizer.py": """import pulp
import logging
from typing import List, Tuple, Dict, Any
from app.schemas import HourlyData, BatterySpec, DirectiveInterpretation, DirectiveType

class InfeasiblePlanException(Exception):
    pass

def solve_energy_plan(scenario_id: str,
                     hours: List[HourlyData],
                     battery: BatterySpec,
                     directives: List[DirectiveInterpretation]) -> Tuple[List[Dict[str, Any]], float, float, float, Dict[str, Any]]:
    prob = pulp.LpProblem("Energy_Optimization", pulp.LpMinimize)
    
    grid = {i: pulp.LpVariable(f"grid_{i}", lowBound=0) for i in range(24)}
    solar_used = {i: pulp.LpVariable(f"solar_used_{i}", lowBound=0) for i in range(24)}
    charge = {i: pulp.LpVariable(f"charge_{i}", lowBound=0) for i in range(24)}
    discharge = {i: pulp.LpVariable(f"discharge_{i}", lowBound=0) for i in range(24)}
    energy_after = {i: pulp.LpVariable(f"energy_after_{i}", lowBound=0) for i in range(24)}
    
    solar_factors = {h: 1.0 for h in range(24)}
    min_reserves = {h: battery.minimum_energy_kwh for h in range(24)}
    can_charge = {h: True for h in range(24)}
    can_discharge = {h: True for h in range(24)}
    max_grids = {h: None for h in range(24)}
    
    for d in directives:
        if not d.applies or d.structured_adjustment is None:
            continue
            
        target_hours = d.structured_adjustment.get("hours", [])
        if d.directive_type == DirectiveType.solar_reduction:
            f = d.structured_adjustment["factor"]
            for h in target_hours:
                solar_factors[h] = min(solar_factors[h], f)
        elif d.directive_type == DirectiveType.minimum_battery_reserve:
            r = d.structured_adjustment["minimum_energy_kwh"]
            for h in target_hours:
                min_reserves[h] = max(min_reserves[h], r)
        elif d.directive_type == DirectiveType.no_charge_window:
            for h in target_hours:
                can_charge[h] = False
        elif d.directive_type == DirectiveType.no_discharge_window:
            for h in target_hours:
                can_discharge[h] = False
        elif d.directive_type == DirectiveType.max_grid_window:
            mg = d.structured_adjustment["max_grid_kwh"]
            for h in target_hours:
                if max_grids[h] is None:
                    max_grids[h] = mg
                else:
                    max_grids[h] = min(max_grids[h], mg)
                    
    tariff_dict = {h.hour: h.tariff_bdt_per_kwh for h in hours}
    demand_dict = {h.hour: h.demand_kwh for h in hours}
    solar_dict = {h.hour: h.solar_kwh for h in hours}
    
    for h in range(24):
        avail_solar = solar_dict[h] * solar_factors[h]
        prob += solar_used[h] <= avail_solar
        
        c_upper = battery.max_charge_kwh_per_hour if can_charge[h] else 0.0
        d_upper = battery.max_discharge_kwh_per_hour if can_discharge[h] else 0.0
        prob += charge[h] <= c_upper
        prob += discharge[h] <= d_upper
        
        if max_grids[h] is not None:
            prob += grid[h] <= max_grids[h]
            
        prob += energy_after[h] >= min_reserves[h]
        prob += energy_after[h] <= battery.capacity_kwh
        
        if h == 0:
            prob += energy_after[h] == battery.initial_energy_kwh + charge[h] - discharge[h]
        else:
            prob += energy_after[h] == energy_after[h-1] + charge[h] - discharge[h]
            
        prob += grid[h] + solar_used[h] + discharge[h] == demand_dict[h] + charge[h]
        
    prob += energy_after[23] == battery.initial_energy_kwh
    
    prob += pulp.lpSum([grid[h] * tariff_dict[h] for h in range(24)]), "Total_Cost"
    
    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)
    
    if pulp.LpStatus[prob.status] != "Optimal":
        logging.error(f"Infeasible plan for scenario {scenario_id}. Solver status: {pulp.LpStatus[prob.status]}")
        raise InfeasiblePlanException()
        
    hourly_plan = []
    total_grid_kwh = 0.0
    total_cost_bdt = 0.0
    peak_grid_kwh = 0.0
    
    for h in range(24):
        g_val = grid[h].varValue or 0.0
        s_val = solar_used[h].varValue or 0.0
        c_val = charge[h].varValue or 0.0
        d_val = discharge[h].varValue or 0.0
        e_val = energy_after[h].varValue or 0.0
        
        action = "idle"
        if c_val > 1e-4:
            action = "charge"
        elif d_val > 1e-4:
            action = "discharge"
            
        total_grid_kwh += g_val
        total_cost_bdt += g_val * tariff_dict[h]
        if g_val > peak_grid_kwh:
            peak_grid_kwh = g_val
            
        hourly_plan.append({
            "hour": h,
            "grid_kwh": round(g_val, 4),
            "solar_used_kwh": round(s_val, 4),
            "battery_action": action,
            "battery_kwh": round(c_val if action == "charge" else d_val, 4),
            "battery_energy_after_kwh": round(e_val, 4)
        })
        
    summary = {
        "scenario_id": scenario_id,
        "total_cost_bdt": round(total_cost_bdt, 2),
        "total_grid_kwh": round(total_grid_kwh, 4),
        "peak_grid_kwh": round(peak_grid_kwh, 4)
    }
    
    return hourly_plan, total_grid_kwh, total_cost_bdt, peak_grid_kwh, summary
""",

    "app/main.py": """import json
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
    
    return OptimizeResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=valid_directives,
        hourly_plan=hourly_plan,
        total_cost_bdt=summary["total_cost_bdt"],
        summary=summary
    )
"""
}

for rel_path, content in files.items():
    full_path = os.path.join(base_dir, rel_path)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

print("Patch applied successfully.")

