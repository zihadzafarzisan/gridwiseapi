"""Phase 3 API Verification Suite for GridWise Smart Campus Energy Service.

Tests:
1. GET /health returns HTTP 200 with {"status": "ok"}.
2. POST /optimize-energy against SAMPLE-01 validates:
   - HTTP 200 response with exact 7 top-level keys.
   - Each hourly_plan entry contains the 6 required keys.
   - total_cost_bdt matches expected value within 0.01 BDT tolerance.
3. Malformed payload returns HTTP 400 without crashing or leaking stack traces.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from main import app


# Synchronous TestClient for FastAPI application
client = TestClient(app)

# Required keys for the judge output schema
REQUIRED_OUTPUT_KEYS = {
    "scenario_id",
    "directive_interpretation",
    "hourly_plan",
    "total_grid_kwh",
    "total_cost_bdt",
    "peak_grid_kwh",
    "plan_summary",
}

# Required keys for each hourly_plan entry
REQUIRED_HOURLY_KEYS = {
    "hour",
    "grid_kwh",
    "solar_used_kwh",
    "battery_action",
    "battery_kwh",
    "battery_energy_after_kwh",
}


def load_sample_01() -> Dict[str, Any]:
    """Load SAMPLE-01 input and expected output from the benchmark dataset."""
    json_path = Path("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for case in data["cases"]:
        if case["id"] == "SAMPLE-01":
            return case
    raise RuntimeError("SAMPLE-01 not found in benchmark dataset")


# ============================================================
# Test 1: Health Check
# ============================================================
def test_health_endpoint():
    """GET /health returns HTTP 200 with {"status": "ok"}."""
    response = client.get("/health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    body = response.json()
    assert body == {"status": "ok"}, f"Expected {{'status': 'ok'}}, got {body}"


# ============================================================
# Test 2: SAMPLE-01 Full Pipeline Validation
# ============================================================
def test_optimize_energy_sample_01():
    """POST /optimize-energy with SAMPLE-01 returns valid judge-compliant response."""
    case = load_sample_01()
    payload = case["input"]
    expected = case["expected_output"]

    response = client.post("/optimize-energy", json=payload)
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )

    body = response.json()

    # Verify exact 7 top-level keys are present
    actual_keys = set(body.keys())
    assert actual_keys == REQUIRED_OUTPUT_KEYS, (
        f"Key mismatch: missing={REQUIRED_OUTPUT_KEYS - actual_keys}, "
        f"extra={actual_keys - REQUIRED_OUTPUT_KEYS}"
    )

    # Verify scenario_id matches
    assert body["scenario_id"] == payload["scenario_id"], (
        f"scenario_id mismatch: {body['scenario_id']} != {payload['scenario_id']}"
    )

    # Verify hourly_plan has 24 entries with the 6 required keys
    hourly_plan = body["hourly_plan"]
    assert len(hourly_plan) == 24, f"Expected 24 hourly entries, got {len(hourly_plan)}"

    for i, entry in enumerate(hourly_plan):
        entry_keys = set(entry.keys())
        assert REQUIRED_HOURLY_KEYS.issubset(entry_keys), (
            f"Hour {i} missing keys: {REQUIRED_HOURLY_KEYS - entry_keys}"
        )

    # Verify total_cost_bdt matches expected within 0.01 tolerance
    actual_cost = float(body["total_cost_bdt"])
    expected_cost = float(expected["total_cost_bdt"])
    cost_diff = abs(actual_cost - expected_cost)
    assert cost_diff <= 0.01, (
        f"total_cost_bdt deviation {cost_diff:.4f} exceeds 0.01 tolerance "
        f"(actual={actual_cost}, expected={expected_cost})"
    )

    # Verify directive_interpretation is a non-empty list
    directives = body["directive_interpretation"]
    assert isinstance(directives, list), "directive_interpretation must be a list"
    assert len(directives) == len(payload["operator_notes"]), (
        f"Expected {len(payload['operator_notes'])} directives, got {len(directives)}"
    )

    # Verify plan_summary is a non-empty string
    assert isinstance(body["plan_summary"], str), "plan_summary must be a string"
    assert len(body["plan_summary"]) > 0, "plan_summary must not be empty"


# ============================================================
# Test 3: Malformed Payload Returns HTTP 400
# ============================================================
def test_malformed_payload_returns_400():
    """POST /optimize-energy with invalid payload returns HTTP 400 with clean JSON."""
    malformed_payloads = [
        # Empty body
        {},
        # Missing required fields
        {"scenario_id": "BAD-01"},
        # Wrong type for hours (not a list)
        {
            "scenario_id": "BAD-02",
            "operator_notes": ["Test note"],
            "hours": "not-a-list",
            "battery": {
                "capacity_kwh": 100,
                "initial_energy_kwh": 50,
                "minimum_energy_kwh": 10,
                "max_charge_kwh_per_hour": 20,
                "max_discharge_kwh_per_hour": 20,
            },
        },
        # Empty operator_notes (below min_length=1)
        {
            "scenario_id": "BAD-03",
            "operator_notes": [],
            "hours": [],
            "battery": {
                "capacity_kwh": 100,
                "initial_energy_kwh": 50,
                "minimum_energy_kwh": 10,
                "max_charge_kwh_per_hour": 20,
                "max_discharge_kwh_per_hour": 20,
            },
        },
    ]

    for i, payload in enumerate(malformed_payloads):
        response = client.post("/optimize-energy", json=payload)
        assert response.status_code == 400, (
            f"Malformed payload {i}: Expected 400, got {response.status_code}"
        )
        body = response.json()
        assert "detail" in body, (
            f"Malformed payload {i}: Response missing 'detail' key, got {body}"
        )
        # Ensure no stack traces or API keys leaked
        detail_str = str(body["detail"]).lower()
        assert "traceback" not in detail_str, (
            f"Malformed payload {i}: Stack trace leaked in 400 response"
        )
        assert "api_key" not in detail_str, (
            f"Malformed payload {i}: API key leaked in 400 response"
        )


# ============================================================
# Main Runner
# ============================================================
def run_api_tests() -> bool:
    """Execute all Phase 3 API verification tests."""
    tests = [
        ("GET /health", test_health_endpoint),
        ("POST /optimize-energy SAMPLE-01", test_optimize_energy_sample_01),
        ("Malformed payload 400 handling", test_malformed_payload_returns_400),
    ]

    print("\n" + "=" * 80)
    print("Phase 3 - API Service Verification Suite")
    print("=" * 80)

    all_passed = True
    for name, test_fn in tests:
        try:
            test_fn()
            print(f"  ✓ PASS  {name}")
        except AssertionError as e:
            print(f"  ✗ FAIL  {name}")
            print(f"          {e}")
            all_passed = False
        except Exception as e:
            print(f"  ✗ ERROR {name}")
            print(f"          {type(e).__name__}: {e}")
            all_passed = False

    print("=" * 80)
    if all_passed:
        print("All Phase 3 API tests PASSED.\n")
    else:
        print("Phase 3 API tests had FAILURES.\n")

    return all_passed


if __name__ == "__main__":
    import sys

    success = run_api_tests()
    sys.exit(0 if success else 1)
