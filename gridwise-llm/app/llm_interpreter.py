import hashlib
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

_SYSTEM_PROMPT = """You are an expert energy management interpreter.
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
[3] The battery charger will be isolated from 2 AM until 5 AM for electrical maintenance.
[4] Keep at least 30% battery reserve from 6 PM to 11 PM.
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
    },
    {
      "note_index": 3,
      "applies": true,
      "directive_type": "no_charge_window",
      "structured_adjustment": {"hours": [2, 3, 4]},
      "explanation": "Charger isolation implies battery charging is unavailable."
    },
    {
      "note_index": 4,
      "applies": true,
      "directive_type": "minimum_battery_reserve",
      "structured_adjustment": {"hours": [18, 19, 20, 21, 22], "minimum_energy_kwh": 60.0},
      "explanation": "30% of 200 kWh = 60 kWh minimum reserve."
    }
  ]
}
"""

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
        
    user_prompt = "Operator Notes:\n"
    for i in uncached_indices:
        user_prompt += f"[{i}] {notes[i]}\n"
    user_prompt += f"Battery capacity: {capacity_kwh} kWh\n"
    
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
                        if resp.choices and resp.choices[0].message.content:
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
                            "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
                            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0}
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
