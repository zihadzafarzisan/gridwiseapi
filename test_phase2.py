"""End-to-End Verification and Test Suite for Phase 2.

Integrates LLM Operator Note Interpretation, Deterministic Guardrails,
and the Phase 1 Mathematical Optimizer.

Tests all 10 benchmark scenarios:
1. Passes operator_notes and battery to llm_interpreter.py.
2. Validates and sanitizes directives through guardrails.py.
3. Compares extracted directives against expected_output.directive_interpretation:
   - note_index match
   - applies boolean match
   - directive_type match
   - structured_adjustment.hours match
   - numeric parameters (factor, minimum_energy_kwh, max_grid_kwh) within 0.01 tolerance
4. Feeds interpreted directives into solve_energy_dispatch.
5. Verifies optimal solver status, exact cost matching within 0.5 BDT, and round-trip latency.
"""

import json
from pathlib import Path
import time
from typing import Any, Dict, List

from guardrails import validate_and_sanitize_directives
from llm_interpreter import interpret_operator_notes
from optimizer import solve_energy_dispatch
from schemas import BatterySpecs, DirectiveInterpretation, HourInput


def load_dataset(json_path: Path) -> List[Dict[str, Any]]:
    """Load benchmark cases from JSON file."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


def run_phase2_tests() -> bool:
    json_path = Path("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")
    if not json_path.exists():
        print(f"ERROR: Dataset not found at {json_path.resolve()}")
        return False

    cases = load_dataset(json_path)
    print(f"\nLoaded {len(cases)} benchmark scenarios from {json_path.name}")
    print("=" * 115)
    header = (
        f"{'Scenario ID':<11} | "
        f"{'Extracted Directives':<30} | "
        f"{'Interp':<6} | "
        f"{'Opt':<6} | "
        f"{'Exp Cost':>9} | "
        f"{'Act Cost':>9} | "
        f"{'Time(s)':>7} | "
        f"{'Result':<6}"
    )
    print(header)
    print("-" * 115)

    all_passed = True

    for case in cases:
        start_time = time.perf_counter()
        scenario_id = case["id"]
        inp = case["input"]
        exp = case["expected_output"]

        hours_input = [HourInput(**h) for h in inp["hours"]]
        battery_specs = BatterySpecs(**inp["battery"])
        expected_directives = exp.get("directive_interpretation", [])
        expected_cost = float(exp["total_cost_bdt"])

        # 1. Interpret operator notes via LLM / fallback
        actual_directives, plan_summary = interpret_operator_notes(
            operator_notes=inp["operator_notes"],
            battery=battery_specs,
        )

        # 2. Validate directives against reference ground truth
        interp_passed = True
        interp_errors = []

        if len(actual_directives) != len(expected_directives):
            interp_passed = False
            interp_errors.append(
                f"Count mismatch: got {len(actual_directives)}, expected {len(expected_directives)}"
            )
        else:
            for act, exp_d in zip(actual_directives, expected_directives):
                act_dict = act.model_dump()
                # Check note_index
                if act.note_index != exp_d["note_index"]:
                    interp_passed = False
                    interp_errors.append(f"note_index mismatch: {act.note_index} vs {exp_d['note_index']}")

                # Check applies
                if act.applies != exp_d["applies"]:
                    interp_passed = False
                    interp_errors.append(f"Note {act.note_index} applies mismatch: {act.applies} vs {exp_d['applies']}")

                # Check directive_type
                if act.directive_type != exp_d["directive_type"]:
                    interp_passed = False
                    interp_errors.append(
                        f"Note {act.note_index} directive_type mismatch: {act.directive_type} vs {exp_d['directive_type']}"
                    )

                # Check structured_adjustment
                exp_adj = exp_d.get("structured_adjustment")
                act_adj = act_dict.get("structured_adjustment")

                if exp_adj is None:
                    if act_adj is not None:
                        interp_passed = False
                        interp_errors.append(f"Note {act.note_index} expected null adjustment but got {act_adj}")
                else:
                    if act_adj is None:
                        interp_passed = False
                        interp_errors.append(f"Note {act.note_index} expected adjustment {exp_adj} but got None")
                    else:
                        # Check hours
                        if act_adj.get("hours") != exp_adj.get("hours"):
                            interp_passed = False
                            interp_errors.append(
                                f"Note {act.note_index} hours mismatch: {act_adj.get('hours')} vs {exp_adj.get('hours')}"
                            )
                        # Check factor
                        if exp_adj.get("factor") is not None:
                            diff = abs(float(act_adj.get("factor", 0.0)) - float(exp_adj["factor"]))
                            if diff > 0.01:
                                interp_passed = False
                                interp_errors.append(f"Note {act.note_index} factor diff {diff:.4f} > 0.01")
                        # Check minimum_energy_kwh
                        if exp_adj.get("minimum_energy_kwh") is not None:
                            diff = abs(float(act_adj.get("minimum_energy_kwh", 0.0)) - float(exp_adj["minimum_energy_kwh"]))
                            if diff > 0.01:
                                interp_passed = False
                                interp_errors.append(f"Note {act.note_index} min_energy diff {diff:.4f} > 0.01")
                        # Check max_grid_kwh
                        if exp_adj.get("max_grid_kwh") is not None:
                            diff = abs(float(act_adj.get("max_grid_kwh", 0.0)) - float(exp_adj["max_grid_kwh"]))
                            if diff > 0.01:
                                interp_passed = False
                                interp_errors.append(f"Note {act.note_index} max_grid diff {diff:.4f} > 0.01")

        # 3. Pass interpreted directives into Phase 1 optimizer
        opt_result = solve_energy_dispatch(
            scenario_id=scenario_id,
            hours_data=hours_input,
            battery=battery_specs,
            directives=actual_directives,
        )

        elapsed = time.perf_counter() - start_time
        opt_passed = opt_result.solver_status == "Optimal"
        cost_diff = abs(opt_result.total_cost_bdt - expected_cost)
        if cost_diff > 0.5:
            opt_passed = False

        case_passed = interp_passed and opt_passed
        if not case_passed:
            all_passed = False

        # Build concise extracted summary for table
        dir_strs = []
        for d in actual_directives:
            if d.applies and d.structured_adjustment:
                hrs_str = f"{d.structured_adjustment.hours}"
                dir_strs.append(f"{d.directive_type}{hrs_str}")
            else:
                dir_strs.append("no_op")
        extracted_summary = ", ".join(dir_strs)
        if len(extracted_summary) > 30:
            extracted_summary = extracted_summary[:27] + "..."

        row = (
            f"{scenario_id:<11} | "
            f"{extracted_summary:<30} | "
            f"{'PASS' if interp_passed else 'FAIL':<6} | "
            f"{'PASS' if opt_passed else 'FAIL':<6} | "
            f"{expected_cost:>9.2f} | "
            f"{opt_result.total_cost_bdt:>9.2f} | "
            f"{elapsed:>7.3f} | "
            f"{'PASS' if case_passed else 'FAIL':<6}"
        )
        print(row)

        if interp_errors:
            for err in interp_errors:
                print(f"    --> Interp Error: {err}")
        if not opt_passed:
            print(f"    --> Optimizer Error: Status={opt_result.solver_status}, Cost delta={cost_diff:.2f}")

    print("=" * 115)
    if all_passed:
        print("\nAll 10 Phase 2 scenarios PASSED end-to-end verification (Interpretation + Optimization).")
    else:
        print("\nFAILURES detected during Phase 2 test suite execution.")

    return all_passed


if __name__ == "__main__":
    import sys

    success = run_phase2_tests()
    sys.exit(0 if success else 1)
