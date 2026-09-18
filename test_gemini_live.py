"""Quick Gemini API live test — loads GEMINI_API_KEY from .env via dotenv."""

import json
import time
from dotenv import load_dotenv

load_dotenv()

from schemas import BatterySpecs, HourInput
from llm_interpreter import interpret_operator_notes
from optimizer import solve_energy_dispatch


def main():
    data = json.load(open("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))
    print("Running END-TO-END test with LIVE Gemini API...\n")

    all_passed = True
    for case in data["cases"]:
        cid = case["id"]
        start = time.perf_counter()

        notes = case["input"]["operator_notes"]
        battery = BatterySpecs(**case["input"]["battery"])
        directives, summary = interpret_operator_notes(notes, battery)

        hours_input = [HourInput(**h) for h in case["input"]["hours"]]
        result = solve_energy_dispatch(cid, hours_input, battery, directives)

        elapsed = (time.perf_counter() - start) * 1000

        exp = case["expected_output"]
        cost_ok = abs(result.total_cost_bdt - exp["total_cost_bdt"]) < 0.5
        grid_ok = abs(result.total_grid_kwh - exp["total_grid_kwh"]) < 0.5

        status = "PASS" if (cost_ok and grid_ok) else "FAIL"
        if not (cost_ok and grid_ok):
            all_passed = False

        print(
            cid
            + " | Cost: "
            + str(result.total_cost_bdt)
            + " (Exp: "
            + str(exp["total_cost_bdt"])
            + ") | Time: "
            + str(round(elapsed))
            + "ms | "
            + status
        )

    print("\nFinal: " + ("ALL PASSED" if all_passed else "FAILURES"))


if __name__ == "__main__":
    main()
