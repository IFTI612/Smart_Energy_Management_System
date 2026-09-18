from pydantic import BaseModel, Field, field_validator
from enum import Enum
from typing import List, Optional
import math

class DirectiveType(str, Enum):
    solar_reduction = "solar_reduction"
    minimum_battery_reserve = "minimum_battery_reserve"
    no_charge_window = "no_charge_window"
    no_discharge_window = "no_discharge_window"
    max_grid_window = "max_grid_window"
    no_op = "no_op"

class BatteryAction(str, Enum):
    charge = "charge"
    discharge = "discharge"
    idle = "idle"

class BatterySpec(BaseModel):
    capacity_kwh: float = Field(..., gt=0)
    initial_energy_kwh: float = Field(..., ge=0)
    minimum_energy_kwh: float = Field(..., ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)

    @field_validator('capacity_kwh', 'initial_energy_kwh', 'minimum_energy_kwh', 'max_charge_kwh_per_hour', 'max_discharge_kwh_per_hour')
    @classmethod
    def check_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Value must be a finite number")
        return v

class HourlyData(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0)
    solar_kwh: float = Field(..., ge=0)
    tariff_bdt_per_kwh: float = Field(..., ge=0)

    @field_validator('demand_kwh', 'solar_kwh', 'tariff_bdt_per_kwh')
    @classmethod
    def check_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Value must be a finite number")
        return v

class OptimizeRequest(BaseModel):
    scenario_id: str
    operator_notes: List[str] = Field(..., min_length=1, max_length=3)
    hours: List[HourlyData] = Field(..., min_length=24, max_length=24)
    battery: BatterySpec

    @field_validator('operator_notes')
    @classmethod
    def check_notes_not_empty(cls, v: List[str]) -> List[str]:
        for idx, note in enumerate(v):
            if not note or not note.strip():
                raise ValueError(f"Note at index {idx} cannot be empty.")
        return v

    @field_validator('hours')
    @classmethod
    def check_hours_unique(cls, v: List[HourlyData]) -> List[HourlyData]:
        hour_set = {h.hour for h in v}
        if len(hour_set) != 24:
            raise ValueError("Hours must uniquely cover 0..23")
        return v

class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[dict] = None
    explanation: str

class HourlyPlanItem(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: BatteryAction
    battery_kwh: float
    battery_energy_after_kwh: float

class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanItem]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
