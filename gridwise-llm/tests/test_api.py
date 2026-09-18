import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from app.main import app
from app.schemas import DirectiveInterpretation, DirectiveType

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_malformed_json():
    response = client.post(
        "/optimize-energy",
        content="this is not valid json",
        headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert "detail" in response.json()

def test_invalid_schema():
    response = client.post("/optimize-energy", json={"scenario_id": "bad", "operator_notes": ["note"]})
    assert response.status_code == 422

@patch("app.main.interpret_operator_notes", new_callable=AsyncMock)
def test_optimize_energy_success(mock_interpret):
    mock_interpret.return_value = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.no_charge_window,
            structured_adjustment={"hours": [17, 18, 19]},
            explanation="mocked"
        )
    ]
    
    payload = {
        "scenario_id": "SAMPLE-01",
        "operator_notes": ["Do not charge the battery between 5 PM and 8 PM"],
        "battery": {
            "capacity_kwh": 100.0,
            "initial_energy_kwh": 20.0,
            "minimum_energy_kwh": 10.0,
            "max_charge_kwh_per_hour": 30.0,
            "max_discharge_kwh_per_hour": 30.0
        },
        "hours": [
            {"hour": h, "demand_kwh": 50.0, "solar_kwh": 20.0, "tariff_bdt_per_kwh": 10.0}
            for h in range(24)
        ]
    }
    
    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_id"] == "SAMPLE-01"
    assert len(data["hourly_plan"]) == 24
    assert data["total_cost_bdt"] > 0
