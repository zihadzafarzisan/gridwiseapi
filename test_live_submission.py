"""Live Submission Verification and Audit Suite for GridWise API on Render.

Audits the deployed service at https://gridwiseapi.onrender.com against the
official BUP CSE Fest 2026 preliminary specifications.
"""

import json
from pathlib import Path
import time
from typing import Any, Dict, List
import httpx
import numpy as np

BASE_URL = "https://gridwiseapi.onrender.com"
DATASET_PATH = Path("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")

REQUIRED_TOP_LEVEL_KEYS = {
    "scenario_id",
    "directive_interpretation",
    "hourly_plan",
    "total_grid_kwh",
    "total_cost_bdt",
    "peak_grid_kwh",
    "plan_summary",
}

REQUIRED_HOURLY_KEYS = {
    "hour",
    "grid_kwh",
    "solar_used_kwh",
    "battery_action",
    "battery_kwh",
    "battery_energy_after_kwh",
}


def load_dataset() -> List[Dict[str, Any]]:
    """Load benchmark cases from JSON file."""
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Dataset not found at {DATASET_PATH.resolve()}")
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])


def run_live_audit() -> bool:
    print("=" * 125)
    print(f"GridWise Live Deployment Audit: {BASE_URL}")
    print("=" * 125)

    client = httpx.Client(base_url=BASE_URL, timeout=60.0)
    all_tests_passed = True

    # ---------------------------------------------------------
    # 1. Health & Root Check
    # ---------------------------------------------------------
    print("\n[Step 1a] Auditing GET / (Root Metadata) ...")
    try:
        t0 = time.perf_counter()
        root_resp = client.get("/")
        latency_root = (time.perf_counter() - t0) * 1000

        if root_resp.status_code != 200:
            print(f"  ✗ FAIL: Expected 200, got {root_resp.status_code}")
            all_tests_passed = False
        else:
            body = root_resp.json()
            if body.get("service") == "GridWise Energy Dispatch API" and body.get("status") == "online":
                print(f"  ✓ PASS: GET / returned 200 OK with service metadata in {latency_root:.1f}ms")
            else:
                print(f"  ✗ FAIL: GET / body mismatch: {body}")
                all_tests_passed = False
    except Exception as e:
        print(f"  ✗ ERROR: GET / failed with exception: {e}")
        all_tests_passed = False

    print("\n[Step 1b] Auditing GET /health ...")
    try:
        t0 = time.perf_counter()
        health_resp = client.get("/health")
        latency_health = (time.perf_counter() - t0) * 1000

        if health_resp.status_code != 200:
            print(f"  ✗ FAIL: Expected 200, got {health_resp.status_code}")
            all_tests_passed = False
        else:
            body = health_resp.json()
            if body == {"status": "ok"}:
                print(f"  ✓ PASS: GET /health returned 200 OK with {body} in {latency_health:.1f}ms")
            else:
                print(f"  ✗ FAIL: GET /health body mismatch: {body}")
                all_tests_passed = False
    except Exception as e:
        print(f"  ✗ ERROR: GET /health failed with exception: {e}")
        all_tests_passed = False

    # ---------------------------------------------------------
    # 2. Benchmark Verification Across All 10 Cases
    # ---------------------------------------------------------
    print("\n[Step 2] Auditing POST /optimize-energy across 10 Benchmark Scenarios ...")
    cases = load_dataset()

    header = (
        f"{'Scenario ID':<12} | "
        f"{'Status':<6} | "
        f"{'Exp Cost':>10} | "
        f"{'Act Cost':>10} | "
        f"{'Delta':>8} | "
        f"{'Latency':>9} | "
        f"{'Keys Valid':<10} | "
        f"{'Result':<6}"
    )
    print("-" * 125)
    print(header)
    print("-" * 125)

    latencies: List[float] = []

    for case in cases:
        scenario_id = case["id"]
        inp = case["input"]
        exp = case["expected_output"]
        exp_cost = float(exp["total_cost_bdt"])
        initial_battery = float(inp["battery"]["initial_energy_kwh"])

        case_passed = True
        status_code = 0
        act_cost = 0.0
        cost_delta = 0.0
        latency_s = 0.0
        keys_valid = True
        err_msgs = []

        try:
            t_start = time.perf_counter()
            resp = client.post("/optimize-energy", json=inp)
            latency_s = time.perf_counter() - t_start
            latencies.append(latency_s)
            status_code = resp.status_code

            if status_code != 200:
                case_passed = False
                err_msgs.append(f"HTTP Status {status_code}: {resp.text[:100]}")
            else:
                data = resp.json()

                # 1. Verify exact 7 top-level keys
                top_keys = set(data.keys())
                if top_keys != REQUIRED_TOP_LEVEL_KEYS:
                    keys_valid = False
                    case_passed = False
                    err_msgs.append(
                        f"Top-level keys mismatch: missing={REQUIRED_TOP_LEVEL_KEYS - top_keys}, extra={top_keys - REQUIRED_TOP_LEVEL_KEYS}"
                    )

                # 2. Verify hourly_plan length & structure
                hourly_plan = data.get("hourly_plan", [])
                if len(hourly_plan) != 24:
                    case_passed = False
                    err_msgs.append(f"hourly_plan has {len(hourly_plan)} entries instead of 24")
                else:
                    for idx, h_entry in enumerate(hourly_plan):
                        h_keys = set(h_entry.keys())
                        if h_keys != REQUIRED_HOURLY_KEYS:
                            keys_valid = False
                            case_passed = False
                            err_msgs.append(f"Hour {idx} key mismatch: {h_keys}")
                            break

                    # 3. Verify battery end-of-day balance (E_23 == initial_energy_kwh)
                    final_battery = float(hourly_plan[23].get("battery_energy_after_kwh", 0.0))
                    bat_delta = abs(final_battery - initial_battery)
                    if bat_delta > 0.01:
                        case_passed = False
                        err_msgs.append(
                            f"Neutrality violation: E_23={final_battery:.4f}, init={initial_battery:.4f} (delta={bat_delta:.4f})"
                        )

                # 4. Verify cost optimality
                act_cost = float(data.get("total_cost_bdt", 0.0))
                cost_delta = abs(act_cost - exp_cost)
                if cost_delta > 0.01:
                    case_passed = False
                    err_msgs.append(f"Cost delta {cost_delta:.4f} exceeds 0.01 BDT tolerance")

        except Exception as e:
            case_passed = False
            err_msgs.append(f"Exception: {e}")

        if not case_passed:
            all_tests_passed = False

        row = (
            f"{scenario_id:<12} | "
            f"{status_code:<6} | "
            f"{exp_cost:>10.2f} | "
            f"{act_cost:>10.2f} | "
            f"{cost_delta:>8.4f} | "
            f"{latency_s:>8.3f}s | "
            f"{'YES' if keys_valid else 'NO':<10} | "
            f"{'PASS' if case_passed else 'FAIL':<6}"
        )
        print(row)

        for err in err_msgs:
            print(f"    --> Error: {err}")

    print("-" * 125)

    # Calculate Latency Statistics
    if latencies:
        avg_latency = float(np.mean(latencies))
        p95_latency = float(np.percentile(latencies, 95))
        max_latency = float(np.max(latencies))
        min_latency = float(np.min(latencies))

        print(f"\nLatency Statistics across {len(latencies)} scenarios:")
        print(f"  • Average Latency : {avg_latency:.3f}s (Budget: <= 5.0s) -> {'PASS' if avg_latency <= 5.0 else 'FAIL'}")
        print(f"  • p95 Latency     : {p95_latency:.3f}s (Budget: <= 5.0s) -> {'PASS' if p95_latency <= 5.0 else 'FAIL'}")
        print(f"  • Min / Max       : {min_latency:.3f}s / {max_latency:.3f}s")

        if p95_latency > 5.0:
            all_tests_passed = False

    # ---------------------------------------------------------
    # 3. Malformed Rejection Check
    # ---------------------------------------------------------
    print("\n[Step 3] Auditing Malformed Payload Rejection (HTTP 400) ...")
    malformed_payloads = [
        # Fewer than 24 hours
        (
            "Payload with only 3 hours",
            {
                "scenario_id": "BAD-01",
                "operator_notes": ["Test note"],
                "hours": [
                    {"hour": 0, "demand_kwh": 50, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
                    {"hour": 1, "demand_kwh": 50, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
                    {"hour": 2, "demand_kwh": 50, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
                ],
                "battery": {
                    "capacity_kwh": 100,
                    "initial_energy_kwh": 50,
                    "minimum_energy_kwh": 10,
                    "max_charge_kwh_per_hour": 20,
                    "max_discharge_kwh_per_hour": 20,
                },
            },
        ),
        # Empty operator notes
        (
            "Payload with empty operator_notes",
            {
                "scenario_id": "BAD-02",
                "operator_notes": [],
                "hours": cases[0]["input"]["hours"],
                "battery": cases[0]["input"]["battery"],
            },
        ),
    ]

    for label, payload in malformed_payloads:
        try:
            resp = client.post("/optimize-energy", json=payload)
            if resp.status_code == 400:
                body = resp.json()
                detail = str(body.get("detail", ""))
                has_traceback = "traceback" in detail.lower()
                has_apikey = "api_key" in detail.lower() or "gemini" in detail.lower()
                if "detail" in body and not has_traceback and not has_apikey:
                    print(f"  ✓ PASS: {label} -> HTTP 400 Bad Request, sanitized detail: \"{detail[:80]}...\"")
                else:
                    print(f"  ✗ FAIL: {label} -> HTTP 400 but detail leaked tracebacks or secrets: {body}")
                    all_tests_passed = False
            else:
                print(f"  ✗ FAIL: {label} -> Expected HTTP 400, got {resp.status_code}: {resp.text}")
                all_tests_passed = False
        except Exception as e:
            print(f"  ✗ ERROR: Malformed test '{label}' failed with exception: {e}")
            all_tests_passed = False

    print("\n" + "=" * 125)
    if all_tests_passed:
        print("OVERALL AUDIT RESULT: ALL SPECIFICATIONS VERIFIED & PASSED (100% COMPLIANT)")
    else:
        print("OVERALL AUDIT RESULT: AUDIT COMPLETED WITH FAILURES")
    print("=" * 125)

    return all_tests_passed


if __name__ == "__main__":
    import sys

    success = run_live_audit()
    sys.exit(0 if success else 1)
