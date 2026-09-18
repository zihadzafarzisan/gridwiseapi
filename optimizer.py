"""Mathematical Optimization Engine for 24-Hour Smart Campus Energy Dispatch.

Formulates and solves the constrained Linear Program (LP) using scipy.optimize.linprog
with the HiGHS interior point / dual simplex solver. Handles solar reductions, dynamic
battery reserves, charging/discharging outages, grid import caps, and end-of-day neutrality.
"""

from typing import List, Optional
import numpy as np
from scipy.optimize import linprog

from schemas import (
    BatterySpecs,
    DirectiveInterpretation,
    HourInput,
    HourlyPlanEntry,
    OptimizationResult,
)


def solve_energy_dispatch(
    scenario_id: str,
    hours_data: List[HourInput],
    battery: BatterySpecs,
    directives: List[DirectiveInterpretation],
) -> OptimizationResult:
    """Solve the 24-hour cost-minimizing energy dispatch linear program.

    Decision Variables (120 variables total, index h in 0..23):
        - G_h (indices 0..23):   grid_kwh imported in hour h
        - S_h (indices 24..47):  solar_used_kwh consumed in hour h
        - C_h (indices 48..71):  charge_kwh stored into battery in hour h
        - D_h (indices 72..95):  discharge_kwh drawn from battery in hour h
        - E_h (indices 96..119): battery_energy_after_kwh remaining at end of hour h

    Constraints:
        1. Energy Balance (24 equations):
           G_h + S_h + D_h == demand_kwh[h] + C_h
        2. Solar Availability Bounds (24 inequalities):
           0 <= S_h <= effective_solar[h]
        3. Battery Energy Continuity (24 equations):
           h = 0:  E_0 == initial_energy_kwh + C_0 - D_0
           h > 0:  E_h == E_{h-1} + C_h - D_h
        4. Battery Operational Limits:
           min_reserve[h] <= E_h <= capacity_kwh
           0 <= C_h <= max_charge_per_hour (or 0 if charging disabled)
           0 <= D_h <= max_discharge_per_hour (or 0 if discharging disabled)
        5. Grid Import Caps:
           0 <= G_h <= max_grid_kwh[h] (if directive applied)
        6. End-of-Day Neutrality (1 equation):
           E_23 == initial_energy_kwh

    Objective:
        Minimize sum(G_h * tariff_bdt_per_kwh[h] for h in range(24))
    """
    # Sort hours data by hour index to guarantee sequential ordering 0..23
    sorted_hours = sorted(hours_data, key=lambda x: x.hour)
    if len(sorted_hours) != 24 or [h.hour for h in sorted_hours] != list(range(24)):
        raise ValueError("hours_data must contain exactly 24 entries for hours 0 through 23")

    # 1. Compute effective solar generation
    effective_solar = [float(h.solar_kwh) for h in sorted_hours]
    for d in directives:
        if d.applies and d.directive_type == "solar_reduction":
            if d.structured_adjustment and d.structured_adjustment.factor is not None:
                factor = float(d.structured_adjustment.factor)
                for h in d.structured_adjustment.hours or []:
                    effective_solar[h] *= factor

    # 2. Determine hourly dynamic limits
    demand = [float(h.demand_kwh) for h in sorted_hours]
    tariffs = [float(h.tariff_bdt_per_kwh) for h in sorted_hours]

    min_reserve = [float(battery.minimum_energy_kwh)] * 24
    charge_max = [float(battery.max_charge_kwh_per_hour)] * 24
    discharge_max = [float(battery.max_discharge_kwh_per_hour)] * 24
    max_grid: List[Optional[float]] = [None] * 24

    for d in directives:
        if not d.applies or not d.structured_adjustment:
            continue

        adj = d.structured_adjustment
        hrs = adj.hours or []
        dtype = d.directive_type

        if dtype == "minimum_battery_reserve" and adj.minimum_energy_kwh is not None:
            for h in hrs:
                min_reserve[h] = max(min_reserve[h], float(adj.minimum_energy_kwh))
        elif dtype == "no_charge_window":
            for h in hrs:
                charge_max[h] = 0.0
        elif dtype == "no_discharge_window":
            for h in hrs:
                discharge_max[h] = 0.0
        elif dtype == "max_grid_window" and adj.max_grid_kwh is not None:
            for h in hrs:
                current_cap = max_grid[h]
                new_cap = float(adj.max_grid_kwh)
                max_grid[h] = min(current_cap, new_cap) if current_cap is not None else new_cap

    # 3. Formulate the LP cost vector
    # Decision vector x of length 120:
    #   [G_0..G_23, S_0..S_23, C_0..C_23, D_0..D_23, E_0..E_23]
    #
    # Simultaneous Charge/Discharge Guard:
    # Add an infinitesimal throughput penalty (1e-7 * (C_h + D_h)) so degenerate
    # flat-tariff hours never charge and discharge at the same time.
    c = np.zeros(120, dtype=np.float64)
    c[0:24] = tariffs
    c[48:72] = 1e-7   # Infinitesimal penalty on C_h (charge)
    c[72:96] = 1e-7   # Infinitesimal penalty on D_h (discharge)

    # 4. Define variable bounds
    bounds = []
    # Grid variables G_h
    for h in range(24):
        bounds.append((0.0, max_grid[h]))
    # Solar variables S_h
    for h in range(24):
        bounds.append((0.0, effective_solar[h]))
    # Charge variables C_h
    for h in range(24):
        bounds.append((0.0, charge_max[h]))
    # Discharge variables D_h
    for h in range(24):
        bounds.append((0.0, discharge_max[h]))
    # Energy variables E_h
    for h in range(24):
        bounds.append((min_reserve[h], float(battery.capacity_kwh)))

    # 5. Build equality constraints A_eq * x == b_eq
    A_eq = []
    b_eq = []

    # 5a. Energy balance for each hour: G_h + S_h + D_h - C_h == demand_kwh[h]
    for h in range(24):
        row = np.zeros(120, dtype=np.float64)
        row[h] = 1.0       # G_h
        row[24 + h] = 1.0  # S_h
        row[72 + h] = 1.0  # D_h
        row[48 + h] = -1.0  # -C_h
        A_eq.append(row)
        b_eq.append(demand[h])

    # 5b. Battery energy continuity
    # Hour 0: E_0 - C_0 + D_0 == initial_energy_kwh
    row_0 = np.zeros(120, dtype=np.float64)
    row_0[96] = 1.0   # E_0
    row_0[48] = -1.0  # -C_0
    row_0[72] = 1.0   # +D_0
    A_eq.append(row_0)
    b_eq.append(float(battery.initial_energy_kwh))

    # Hours 1..23: E_h - E_{h-1} - C_h + D_h == 0
    for h in range(1, 24):
        row_h = np.zeros(120, dtype=np.float64)
        row_h[96 + h] = 1.0      # E_h
        row_h[96 + h - 1] = -1.0  # -E_{h-1}
        row_h[48 + h] = -1.0     # -C_h
        row_h[72 + h] = 1.0      # +D_h
        A_eq.append(row_h)
        b_eq.append(0.0)

    # 5c. End-of-day neutrality: E_23 == initial_energy_kwh
    row_neutral = np.zeros(120, dtype=np.float64)
    row_neutral[96 + 23] = 1.0
    A_eq.append(row_neutral)
    b_eq.append(float(battery.initial_energy_kwh))

    # 6. Solve LP using HiGHS
    res = linprog(
        c,
        A_eq=np.array(A_eq, dtype=np.float64),
        b_eq=np.array(b_eq, dtype=np.float64),
        bounds=bounds,
        method="highs",
    )

    if res.status != 0:
        solver_status = f"Infeasible or Failed (status {res.status}: {res.message})"
        # Construct fallback empty schedule with status message
        return OptimizationResult(
            scenario_id=scenario_id,
            hourly_plan=[],
            total_grid_kwh=0.0,
            total_cost_bdt=0.0,
            peak_grid_kwh=0.0,
            solver_status=solver_status,
        )

    solver_status = "Optimal"
    x = res.x
    grid_sol = x[0:24]
    solar_sol = x[24:48]
    charge_sol = x[48:72]
    discharge_sol = x[72:96]
    energy_sol = x[96:120]

    # 7. Map action types and construct hourly plan
    hourly_plan: List[HourlyPlanEntry] = []
    for h in range(24):
        c_val = charge_sol[h]
        d_val = discharge_sol[h]

        if c_val > 1e-4:
            action = "charge"
            bat_kwh = round(float(c_val), 4)
        elif d_val > 1e-4:
            action = "discharge"
            bat_kwh = round(float(d_val), 4)
        else:
            action = "idle"
            bat_kwh = 0.0

        hourly_plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=round(float(grid_sol[h]), 4),
                solar_used_kwh=round(float(solar_sol[h]), 4),
                battery_action=action,
                battery_kwh=bat_kwh,
                battery_energy_after_kwh=round(float(energy_sol[h]), 4),
            )
        )

    # 8. Replay and recalculate aggregate metrics from the plan
    total_grid_kwh = round(sum(entry.grid_kwh for entry in hourly_plan), 4)
    total_cost_bdt = round(sum(entry.grid_kwh * tariffs[entry.hour] for entry in hourly_plan), 4)
    peak_grid_kwh = round(max(entry.grid_kwh for entry in hourly_plan), 4)

    return OptimizationResult(
        scenario_id=scenario_id,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid_kwh,
        total_cost_bdt=total_cost_bdt,
        peak_grid_kwh=peak_grid_kwh,
        solver_status=solver_status,
    )
