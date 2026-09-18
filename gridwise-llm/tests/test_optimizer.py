import pytest
from app.optimizer import solve_energy_plan, InfeasiblePlanException
from app.schemas import HourlyData, BatterySpec, DirectiveInterpretation, DirectiveType

@pytest.fixture
def standard_hours():
    return [
        HourlyData(hour=h, demand_kwh=10.0, solar_kwh=5.0, tariff_bdt_per_kwh=10.0)
        for h in range(24)
    ]

@pytest.fixture
def standard_battery():
    return BatterySpec(
        capacity_kwh=50.0,
        initial_energy_kwh=10.0,
        minimum_energy_kwh=5.0,
        max_charge_kwh_per_hour=10.0,
        max_discharge_kwh_per_hour=10.0
    )

def test_successful_optimization(standard_hours, standard_battery):
    directives = []
    hourly_plan, total_grid, total_cost, peak_grid, summary = solve_energy_plan(
        "test_scenario", standard_hours, standard_battery, directives
    )
    
    assert len(hourly_plan) == 24
    
    # Verify energy balance for hour 0
    h0 = hourly_plan[0]
    supply = h0.grid_kwh + h0.solar_used_kwh + (h0.battery_kwh if h0.battery_action == "discharge" else 0.0)
    used = standard_hours[0].demand_kwh + (h0.battery_kwh if h0.battery_action == "charge" else 0.0)
    assert abs(supply - used) < 1e-3
    
    # Verify end-of-day neutrality
    assert abs(hourly_plan[-1].battery_energy_after_kwh - standard_battery.initial_energy_kwh) < 1e-3

def test_infeasible_plan(standard_hours, standard_battery):
    zero_solar_hours = [
        HourlyData(hour=h, demand_kwh=100.0, solar_kwh=0.0, tariff_bdt_per_kwh=10.0)
        for h in range(24)
    ]
    
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.max_grid_window,
            structured_adjustment={"hours": list(range(24)), "max_grid_kwh": 0.0},
            explanation="No grid at all"
        )
    ]
    
    with pytest.raises(InfeasiblePlanException):
        solve_energy_plan("test_scenario", zero_solar_hours, standard_battery, directives)
