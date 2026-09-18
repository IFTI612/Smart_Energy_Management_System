import pytest
from app.guardrails import parse_raw_llm_json, validate_and_repair_directives
from app.schemas import DirectiveType

def test_parse_raw_llm_json():
    # Valid JSON
    assert parse_raw_llm_json('[{"note_index": 0}]') == [{"note_index": 0}]
    
    # Valid JSON wrapped in Groq object
    assert parse_raw_llm_json('{"directives": [{"note_index": 0}]}') == [{"note_index": 0}]
    
    # JSON with Markdown fences
    text = "```json\n[{\"note_index\": 1}]\n```"
    assert parse_raw_llm_json(text) == [{"note_index": 1}]
    
    # Malformed JSON
    assert parse_raw_llm_json('[{"note_index": 0}') is None
    
def test_repair_unknown_directive_and_missing_index():
    parsed = [
        {"note_index": 1, "applies": True, "directive_type": "some_unknown_type", "structured_adjustment": {"hours": [1,2,3]}}
    ]
    results = validate_and_repair_directives(parsed, 2, 100.0)
    
    assert len(results) == 2
    assert results[0].note_index == 0
    assert results[0].directive_type == DirectiveType.no_op
    assert results[0].applies is False
    
    assert results[1].note_index == 1
    assert results[1].directive_type == DirectiveType.no_op
    
def test_clamping():
    parsed = [
        {
            "note_index": 0, "applies": True, "directive_type": "solar_reduction", 
            "structured_adjustment": {"hours": [10], "factor": 1.5, "extra_key": "bad"}
        },
        {
            "note_index": 1, "applies": True, "directive_type": "minimum_battery_reserve", 
            "structured_adjustment": {"hours": [10], "minimum_energy_kwh": 200.0}
        },
        {
            "note_index": 2, "applies": True, "directive_type": "max_grid_window", 
            "structured_adjustment": {"hours": [10], "max_grid_kwh": -50.0}
        }
    ]
    
    results = validate_and_repair_directives(parsed, 3, 100.0)
    
    assert results[0].structured_adjustment["factor"] == 1.0
    assert "extra_key" not in results[0].structured_adjustment
    assert results[1].structured_adjustment["minimum_energy_kwh"] == 100.0
    assert results[2].structured_adjustment["max_grid_kwh"] == 0.0
