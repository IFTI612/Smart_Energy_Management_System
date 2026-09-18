import hashlib
import asyncio
import httpx
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
- no_op: no additional fields.

Rules:
- 1-inclusive/end-exclusive hour rules: e.g., "1 PM to 4 PM" means hours [13, 14, 15].
- Produce exactly one directive per operator note.
- Map the note's index (0-based) to `note_index`.
- Set `applies` to true for valid directives, false for no_op.
- Output JSON format:
{
  "directives": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "...",
      "structured_adjustment": { ... },
      "explanation": "..."
    }
  ]
}

Few-shot examples:
Note: "Reduce solar to 20% from 10 AM to 1 PM"
-> hours: [10, 11, 12], factor: 0.20, type: solar_reduction

Note: "Do not charge the battery between 5 PM and 8 PM"
-> hours: [17, 18, 19], type: no_charge_window

Note: "Keep at least 50 kWh in the battery all day"
-> hours: [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23], minimum_energy_kwh: 50.0, type: minimum_battery_reserve
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
    for i, note in enumerate(notes):
        user_prompt += f"[{i}] {note}\n"
    user_prompt += f"Battery capacity: {capacity_kwh} kWh\n"
    
    llm_response_text = None
    
    try:
        async with asyncio.timeout(TOTAL_LLM_BUDGET):
            # 1. Primary: AsyncGroq
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
                            pass # Fallback will trigger
                            
            # 2. Fallback: Gemini REST API
            if not llm_response_text and GEMINI_API_KEY:
                try:
                    async with httpx.AsyncClient(timeout=FALLBACK_TIMEOUT) as client:
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
                        payload = {
                            "contents": [
                                {"role": "user", "parts": [{"text": _SYSTEM_PROMPT + "\n\n" + user_prompt}]}
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
                except Exception as e:
                    llm_response_text = None
    except (asyncio.TimeoutError, Exception):
        llm_response_text = None
            
    # Parse and Validate
    parsed = parse_raw_llm_json(llm_response_text) if llm_response_text else None
    valid_directives = validate_and_repair_directives(parsed, len(notes), capacity_kwh)
    
    # Write to cache
    for directive in valid_directives:
        key = _get_cache_key(notes[directive.note_index], capacity_kwh)
        _CACHE[key] = directive
        
    return valid_directives
