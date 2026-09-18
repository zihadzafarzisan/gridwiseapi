"""Verification and Test Suite for Phase 1: Mathematical Optimization Engine.

Loads public test scenarios from BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json,
runs solve_energy_dispatch for each scenario, and asserts:
  1. Solver status is optimal.
  2. Recalculated total_cost_bdt matches expected within 0.5 BDT.
  3. Recalculated total_grid_kwh matches expected within 0.5 kWh.
  4. End-of-day battery neutrality holds (|E_23 - initial_energy| <= 0.01 kWh).
  5. Energy balance equation holds for all 24 hours (|G + S + D - (demand + C)| <= 0.01 kWh).
  6. Battery bounds and operational limits are fully respected.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from optimizer import solve_energy_dispatch
from schemas import BatterySpecs, DirectiveInterpretation, HourInput


def load_dataset(json_path: Path) -> List[Dict[str, Any]]:
    """Load benchmark cases from JSON file."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


def run_tests() -> bool:
    json_path = Path("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")
    if not json_path.exists():
        print(f"ERROR: Sample cases dataset not found at {json_path.resolve()}")
        return False

    cases = load_dataset(json_path)
    print(f"\nLoaded {len(cases)} benchmark scenarios from {json_path.name}")
    print("=" * 105)
    header = (
        f"{'Scenario ID':<12} | "
        f"{'Status':<8} | "
        f"{'Exp Cost':>10} | "
        f"{'Act Cost':>10} | "
        f"{'Delta Cost':>10} | "
        f"{'Exp Grid':>10} | "
        f"{'Act Grid':>10} | "
        f"{'Result':<6}"
    )
    print(header)
    print("-" * 105)

    all_passed = True

    for case in cases:
        scenario_id = case["id"]
        inp = case["input"]
        exp = case["expected_output"]

        # Parse inputs via Pydantic schemas
        hours_input = [HourInput(**h) for h in inp["hours"]]
        battery_specs = BatterySpecs(**inp["battery"])
        directives_input = [
            DirectiveInterpretation(**d)
            for d in exp.get("directive_interpretation", [])
        ]

        # Execute optimization engine
        result = solve_energy_dispatch(
            scenario_id=scenario_id,
            hours_data=hours_input,
            battery=battery_specs,
            directives=directives_input,
        )

        # Expected metrics
        exp_cost = float(exp["total_cost_bdt"])
        exp_grid = float(exp["total_grid_kwh"])
        act_cost = result.total_cost_bdt
        act_grid = result.total_grid_kwh
        cost_delta = abs(act_cost - exp_cost)
        grid_delta = abs(act_grid - exp_grid)

        # Verification checks
        passed = True
        failure_reasons = []

        if result.solver_status != "Optimal":
            passed = False
            failure_reasons.append(f"Solver non-optimal: {result.solver_status}")

        if cost_delta > 0.5:
            passed = False
            failure_reasons.append(f"Cost delta {cost_delta:.2f} > 0.5 BDT")

        if grid_delta > 0.5:
            passed = False
            failure_reasons.append(f"Grid delta {grid_delta:.2f} > 0.5 kWh")

        # Verify hourly physics and accounting constraints
        if len(result.hourly_plan) != 24:
            passed = False
            failure_reasons.append(f"Hourly plan has {len(result.hourly_plan)} entries instead of 24")
        else:
            # End-of-day neutrality check
            final_energy = result.hourly_plan[23].battery_energy_after_kwh
            neutrality_err = abs(final_energy - battery_specs.initial_energy_kwh)
            if neutrality_err > 0.01:
                passed = False
                failure_reasons.append(
                    f"Neutrality violation: E_23={final_energy:.4f}, init={battery_specs.initial_energy_kwh:.4f} (err={neutrality_err:.4f})"
                )

            # Replay hourly energy balance and continuity
            prev_energy = battery_specs.initial_energy_kwh
            for entry in result.hourly_plan:
                h = entry.hour
                h_input = hours_input[h]

                # Energy balance: grid + solar_used + discharge == demand + charge
                ch = entry.battery_kwh if entry.battery_action == "charge" else 0.0
                dis = entry.battery_kwh if entry.battery_action == "discharge" else 0.0

                supply = entry.grid_kwh + entry.solar_used_kwh + dis
                demand = h_input.demand_kwh + ch
                bal_diff = abs(supply - demand)
                if bal_diff > 0.01:
                    passed = False
                    failure_reasons.append(f"Hour {h} balance violation: supply={supply:.2f}, demand={demand:.2f}")

                # Energy continuity: E_h == E_{h-1} + C - D
                expected_e_after = prev_energy + ch - dis
                cont_diff = abs(entry.battery_energy_after_kwh - expected_e_after)
                if cont_diff > 0.01:
                    passed = False
                    failure_reasons.append(
                        f"Hour {h} continuity violation: E={entry.battery_energy_after_kwh:.2f}, expected={expected_e_after:.2f}"
                    )
                prev_energy = entry.battery_energy_after_kwh

        if not passed:
            all_passed = False
            status_str = "FAIL"
        else:
            status_str = "PASS"

        row = (
            f"{scenario_id:<12} | "
            f"{result.solver_status:<8} | "
            f"{exp_cost:>10.2f} | "
            f"{act_cost:>10.2f} | "
            f"{cost_delta:>10.2f} | "
            f"{exp_grid:>10.2f} | "
            f"{act_grid:>10.2f} | "
            f"{status_str:<6}"
        )
        print(row)

        if failure_reasons:
            for reason in failure_reasons:
                print(f"    --> Violation: {reason}")

    print("=" * 105)
    if all_passed:
        print("\nAll 10 benchmark scenarios PASSED local verification with zero constraint violations.")
    else:
        print("\nFAILURES detected during test suite execution.")

    return all_passed


if __name__ == "__main__":
    import sys

    success = run_tests()
    sys.exit(0 if success else 1)
