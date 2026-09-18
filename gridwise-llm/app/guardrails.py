import json
import re
from typing import Optional, List, Dict, Any
from app.schemas import DirectiveInterpretation, DirectiveType

def parse_raw_llm_json(raw_text: str) -> Optional[List[Dict]]:
    text = raw_text.strip()
    # Strip markdown code blocks
    text = re.sub(r'^```[a-zA-Z]*\n', '', text, flags=re.MULTILINE)
    text = re.sub(r'```$', '', text, flags=re.MULTILINE)
    text = text.strip()
    
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
            return parsed
        elif isinstance(parsed, dict):
            # Handle Groq json_object which must be an object
            for key in parsed:
                if isinstance(parsed[key], list) and all(isinstance(item, dict) for item in parsed[key]):
                    return parsed[key]
        return None
    except json.JSONDecodeError:
        return None

def _create_no_op(index: int, explanation: str = "fallback: could not resolve") -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=index,
        applies=False,
        directive_type=DirectiveType.no_op,
        structured_adjustment=None,
        explanation=explanation
    )

def validate_and_repair_directives(parsed_json: Optional[List[Dict]], note_count: int, capacity_kwh: float) -> List[DirectiveInterpretation]:
    results: List[DirectiveInterpretation] = []
    
    if parsed_json is None:
        parsed_json = []

    # Map by index, keeping first occurrence
    parsed_map = {}
    for item in parsed_json:
        if isinstance(item, dict) and "note_index" in item:
            idx = item["note_index"]
            if isinstance(idx, int) and 0 <= idx < note_count:
                if idx not in parsed_map:
                    parsed_map[idx] = item

    for idx in range(note_count):
        if idx not in parsed_map:
            results.append(_create_no_op(idx))
            continue
            
        item = parsed_map[idx]
        raw_type = item.get("directive_type")
        raw_applies = item.get("applies", False)
        raw_explanation = str(item.get("explanation", ""))
        raw_adjustment = item.get("structured_adjustment")
        
        # Determine valid type
        try:
            d_type = DirectiveType(raw_type)
        except ValueError:
            d_type = DirectiveType.no_op
            
        if d_type == DirectiveType.no_op or not raw_applies or not isinstance(raw_adjustment, dict):
            results.append(_create_no_op(idx, raw_explanation))
            continue
            
        # Extract hours array and sanitize
        raw_hours = raw_adjustment.get("hours", [])
        if not isinstance(raw_hours, list):
            results.append(_create_no_op(idx, raw_explanation))
            continue
            
        hours = sorted(list(set([h for h in raw_hours if isinstance(h, int) and 0 <= h <= 23])))
        if not hours:
            results.append(_create_no_op(idx, raw_explanation))
            continue
            
        # Valid adjustment dict
        adj = {"hours": hours}
        
        if d_type == DirectiveType.solar_reduction:
            factor = raw_adjustment.get("factor", 0.0)
            if not isinstance(factor, (int, float)):
                factor = 0.0
            factor = max(0.0, min(1.0, float(factor)))
            adj["factor"] = factor
            
        elif d_type == DirectiveType.minimum_battery_reserve:
            min_energy = raw_adjustment.get("minimum_energy_kwh", 0.0)
            if not isinstance(min_energy, (int, float)):
                min_energy = 0.0
            min_energy = max(0.0, min(float(capacity_kwh), float(min_energy)))
            adj["minimum_energy_kwh"] = min_energy
            
        elif d_type == DirectiveType.max_grid_window:
            max_grid = raw_adjustment.get("max_grid_kwh", 0.0)
            if not isinstance(max_grid, (int, float)):
                max_grid = 0.0
            max_grid = max(0.0, float(max_grid))
            adj["max_grid_kwh"] = max_grid
            
        results.append(DirectiveInterpretation(
            note_index=idx,
            applies=True,
            directive_type=d_type,
            structured_adjustment=adj,
            explanation=raw_explanation
        ))
        
    return results
