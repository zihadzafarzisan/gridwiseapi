"""Pydantic v2 schemas for GridWise Smart Campus Energy Dispatch Optimization.

Defines input schemas for hourly load/solar/tariff, battery specifications,
structured operator directive interpretations, and output schemas for hourly
dispatch plans and optimization results.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class HourInput(BaseModel):
    """Input parameters for a single operating hour (0 to 23)."""

    model_config = ConfigDict(extra="ignore")

    hour: int = Field(..., ge=0, le=23, description="Hour of the day (0-23)")
    demand_kwh: float = Field(..., ge=0.0, description="Base campus demand in kWh")
    solar_kwh: float = Field(..., ge=0.0, description="Forecasted solar generation in kWh")
    tariff_bdt_per_kwh: float = Field(..., ge=0.0, description="Grid electricity tariff in BDT/kWh")


class BatterySpecs(BaseModel):
    """Static battery storage system specifications."""

    model_config = ConfigDict(extra="ignore")

    capacity_kwh: float = Field(..., gt=0.0, description="Total battery capacity in kWh")
    initial_energy_kwh: float = Field(..., ge=0.0, description="Battery energy level at start of day (hour 0)")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Standard minimum allowable reserve in kWh")
    max_charge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum charge rate per hour in kWh")
    max_discharge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum discharge rate per hour in kWh")


class StructuredAdjustment(BaseModel):
    """Structured adjustment parameters extracted from an operator note."""

    model_config = ConfigDict(extra="ignore")

    hours: Optional[List[int]] = Field(default=None, description="Affected hours (0-23)")
    factor: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Usable solar fraction remaining (e.g. 0.2 for 80% reduction)",
    )
    minimum_energy_kwh: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Directive-specified minimum reserve level in kWh",
    )
    max_grid_kwh: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Directive-specified maximum grid import cap in kWh",
    )


class DirectiveInterpretation(BaseModel):
    """Interpretation of a single natural language operator note."""

    model_config = ConfigDict(extra="ignore")

    note_index: int = Field(..., ge=0, description="Index of the operator note")
    applies: bool = Field(..., description="Whether this directive actively applies to today's schedule")
    directive_type: Literal[
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    ] = Field(..., description="Category of the directive")
    structured_adjustment: Optional[StructuredAdjustment] = Field(
        default=None,
        description="Structured adjustment parameters (null for no_op)",
    )
    explanation: str = Field(..., description="Human-readable explanation of the interpretation")


class HourlyPlanEntry(BaseModel):
    """Optimal energy dispatch schedule for a single hour."""

    model_config = ConfigDict(extra="ignore")

    hour: int = Field(..., ge=0, le=23, description="Hour of the day (0-23)")
    grid_kwh: float = Field(..., ge=0.0, description="Electricity imported from grid in kWh")
    solar_used_kwh: float = Field(..., ge=0.0, description="Solar energy directly consumed in kWh")
    battery_action: Literal["charge", "discharge", "idle"] = Field(..., description="Battery action for the hour")
    battery_kwh: float = Field(..., ge=0.0, description="Battery energy charged or discharged in kWh")
    battery_energy_after_kwh: float = Field(..., ge=0.0, description="Battery state of charge at end of hour in kWh")


class OptimizationResult(BaseModel):
    """Full 24-hour optimal dispatch solution and summary metrics."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(..., description="Identifier of the scenario")
    hourly_plan: List[HourlyPlanEntry] = Field(..., description="24-hour dispatch schedule")
    total_grid_kwh: float = Field(..., ge=0.0, description="Sum of grid energy imported across 24 hours")
    total_cost_bdt: float = Field(..., ge=0.0, description="Total financial cost in BDT across 24 hours")
    peak_grid_kwh: float = Field(..., ge=0.0, description="Maximum single-hour grid import in kWh")
    solver_status: str = Field(
        ...,
        exclude=True,
        description="Internal status from mathematical optimizer; excluded from final API serialization",
    )


class JudgeOutput(BaseModel):
    """Official 7-key JSON output payload compliant with GridWise judge schema."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(..., description="Scenario identifier")
    directive_interpretation: List[DirectiveInterpretation] = Field(
        ..., description="Array of directive interpretation entries in note_index order"
    )
    hourly_plan: List[HourlyPlanEntry] = Field(..., description="24-hour dispatch schedule")
    total_grid_kwh: float = Field(..., ge=0.0, description="Sum of grid energy imported across 24 hours")
    total_cost_bdt: float = Field(..., ge=0.0, description="Total financial cost in BDT across 24 hours")
    peak_grid_kwh: float = Field(..., ge=0.0, description="Maximum single-hour grid import in kWh")
    plan_summary: str = Field(..., description="Brief summary of the dispatch schedule rationale")


class OptimizeEnergyRequest(BaseModel):
    """Input payload for POST /optimize-energy."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = Field(..., min_length=1, description="Unique scenario identifier")
    operator_notes: List[str] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="Array of 1 to 3 natural language operator notes",
    )
    hours: List[HourInput] = Field(
        ...,
        min_length=24,
        max_length=24,
        description="Hourly parameters for exactly 24 hours (0-23)",
    )
    battery: BatterySpecs = Field(..., description="Battery specifications")

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v: List[HourInput]) -> List[HourInput]:
        if len(v) != 24:
            raise ValueError("hours array must contain exactly 24 entries")
        seen_hours = set()
        for h in v:
            if h.hour in seen_hours:
                raise ValueError(f"Duplicate hour {h.hour} in hours array")
            seen_hours.add(h.hour)
        if seen_hours != set(range(24)):
            missing = sorted(list(set(range(24)) - seen_hours))
            raise ValueError(f"Missing hours in schedule: {missing}")
        return v


