import re
import json
import math
from typing import List, Dict, Optional, Any
from app.schemas import DirectiveInterpretation, DirectiveType

def parse_raw_llm_json(raw_text: str) -> Optional[List[Dict]]:
    text = raw_text.strip()
    text = re.sub(r'^```[a-zA-Z]*\n', '', text, flags=re.MULTILINE)
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
