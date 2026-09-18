# GridWise — Smart Campus Energy Optimization Service
**BUP CSE Fest 2026 Hackathon · Online Preliminary Round**

GridWise is an automated, production-grade 24-hour smart campus energy dispatch system. It combines an LLM-powered natural-language operator note interpreter, deterministic safety guardrails, and a High-Performance Linear Programming (LP) solver to schedule grid electricity, solar self-consumption, and battery storage at minimum cost.

---

## 🌐 Live Production Deployment

- **Base URL:** `https://gridwiseapi.onrender.com`
- **Swagger Documentation:** `https://gridwiseapi.onrender.com/docs`
- **Status:** Active & Verified across all 10/10 preliminary benchmark cases

### Endpoints Table

| Method | Endpoint | Description | Expected Output / Status |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Live service health check | `{"status": "ok"}` (`200 OK`) |
| `POST` | `/optimize-energy` | 24-hour cost-optimal energy dispatch planning engine | Compliant 7-key JSON response (`200 OK`) |

### Sample Request (`POST /optimize-energy`)

```json
{
  "scenario_id": "SAMPLE-01",
  "operator_notes": [
    "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
    "The sports office moved next month's registration deadline."
  ],
  "hours": [
    {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6}
    // ... exactly 24 entries (hours 0 to 23)
  ],
  "battery": {
    "capacity_kwh": 220,
    "initial_energy_kwh": 110,
    "minimum_energy_kwh": 40,
    "max_charge_kwh_per_hour": 50,
    "max_discharge_kwh_per_hour": 50
  }
}
```

### Sample Response (`200 OK` — Exact 7 Top-Level Keys)

```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {
        "hours": [12, 13],
        "factor": 0.25
      },
      "explanation": "Solar availability reduced to 25% during panel cleaning window."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "This note does not affect today's energy schedule."
    }
  ],
  "hourly_plan": [
    {
      "hour": 0,
      "grid_kwh": 90.0,
      "solar_used_kwh": 0.0,
      "battery_action": "idle",
      "battery_kwh": 0.0,
      "battery_energy_after_kwh": 110.0
    }
    // ... exactly 24 hourly plan entries
  ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365.0,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Uses the reduced midday solar availability, ignores the unrelated note, and shifts battery energy toward higher-tariff hours while restoring initial battery level."
}
```

---

## 🐳 Docker Container & Fallback Instructions (GHCR)

The service is continuously built and published to the GitHub Container Registry (GHCR):

- **Public Container Image:** `ghcr.io/zihadzafarzisan/bup-hackathon:latest`

### Verified Run Command

```bash
docker run -d -p 8000:8000 -e GEMINI_API_KEY="your_gemini_api_key" ghcr.io/zihadzafarzisan/bup-hackathon:latest
```

Once running, access the local container at `http://localhost:8000/health`.

---

## 🏗️ Architecture Overview

GridWise implements a decoupled 4-stage pipeline that guarantees both cognitive interpretation and mathematical optimality:

```
[Operator Notes & Raw Schedule]
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. LLM Parser (Gemini 2.0 Flash)                                            │
│    Extracts operational directives into strict structured JSON.             │
│    Automatic function calling disabled; offline deterministic fallback.     │
└─────────────────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. Deterministic Guardrails Firewall                                        │
│    Clamps solar factors to [0.0, 1.0], validates hour sequences (0..23),    │
│    and bounds relative battery reserves against physical capacity.          │
└─────────────────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. Mathematical Optimizer (HiGHS LP Solver via SciPy)                       │
│    120 decision variables, global minimum cost objective.                   │
│    Enforces hourly energy balance, battery continuity, rate constraints,     │
│    end-of-day neutrality (E23 = E_init), and 1e-7 throughput penalty        │
│    to eliminate degeneracy during flat tariffs.                             │
└─────────────────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. Pydantic Schema Validator & Replay Engine                                │
│    Directly re-evaluates exact schedule totals to prevent floating-point   │
│    discrepancies and validates strict compliance with the 7-key rubric.     │
└─────────────────────────────────────────────────────────────────────────────┘
             │
             ▼
[Optimized 24-Hour Energy Dispatch Plan]
```

---

## 📊 Benchmark Verification Table

The service was audited against all 10 official BUP CSE Fest 2026 preliminary benchmark cases. All test cases achieve a **0.0000 BDT cost delta** relative to ground truth with an average end-to-end response time of **~1.14 seconds** on live cloud infrastructure.

| Scenario ID | Directives Interpreted | Solver Cost (BDT) | Benchmark Cost (BDT) | Delta (BDT) | Peak Grid (kWh) | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `SAMPLE-01` | Solar reduction (hrs 12–13, 25%), No-op | 38,365.0000 | 38,365.0000 | **0.0000** | 175.00 | Passed |
| `SAMPLE-02` | Grid limit (hrs 17–20, 90 kWh), No-op | 43,150.0000 | 43,150.0000 | **0.0000** | 170.00 | Passed |
| `SAMPLE-03` | Reserve battery (hrs 18–21, 120 kWh), No-op | 39,260.0000 | 39,260.0000 | **0.0000** | 175.00 | Passed |
| `SAMPLE-04` | No-op, Demand adjustment (hrs 9–16, +35 kWh) | 43,300.0000 | 43,300.0000 | **0.0000** | 185.00 | Passed |
| `SAMPLE-05` | Solar zero (hrs 11–13), Battery lockout (hrs 14–16) | 42,915.0000 | 42,915.0000 | **0.0000** | 180.00 | Passed |
| `SAMPLE-06` | Pre-charge battery (hrs 0–4), Grid limit (hrs 18–21) | 40,780.0000 | 40,780.0000 | **0.0000** | 170.00 | Passed |
| `SAMPLE-07` | Solar reduction (hrs 13–15, 50%), Grid limit (hrs 17–20) | 41,830.0000 | 41,830.0000 | **0.0000** | 170.00 | Passed |
| `SAMPLE-08` | Reserve battery (hrs 19–22, 100 kWh), Demand +20 kWh | 42,120.0000 | 42,120.0000 | **0.0000** | 180.00 | Passed |
| `SAMPLE-09` | Solar zero (hrs 12–14), Pre-charge (hrs 2–5) | 42,475.0000 | 42,475.0000 | **0.0000** | 175.00 | Passed |
| `SAMPLE-10` | Grid limit (hrs 18–21, 80 kWh), Lockout (hrs 11–13) | 42,650.0000 | 42,650.0000 | **0.0000** | 170.00 | Passed |

- **Average End-to-End Latency:** ~1.14 seconds
- **Mathematical Optimality Gap:** 0.0% across all scenarios
- **Strict Schema Compliance:** 100% (7 top-level keys, 6 hourly keys)

---

## 🛠️ Technology Stack & Dependencies

- **Framework:** FastAPI, Uvicorn
- **Mathematical Optimization:** SciPy (`scipy.optimize.linprog` with HiGHS solver), NumPy
- **Data Validation & Schemas:** Pydantic v2
- **Language Models:** Google GenAI SDK (`gemini-2.0-flash`), OpenAI SDK (`gpt-4o-mini` fallback)
- **Environment & Configuration:** python-dotenv
- **Testing & Verification:** Pytest, HTTPX, FastAPI TestClient

---

## 🔑 Environment Variables

The service requires the following configuration (managed via `.env` locally or set in container environments):

| Variable | Required | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Yes (for live LLM) | Google Gemini API key for operator note interpretation. |
| `OPENAI_API_KEY` | Optional | OpenAI API key used as secondary LLM fallback. |
| `PORT` | Optional | Port for the HTTP service (defaults to `8000`). |

*Note: Never commit `.env` or raw API keys to source control.*

---

## 🚀 Local Quickstart

### Prerequisites
- Python 3.10+
- pip

### Setup & Run
```bash
# 1. Clone the repository
git clone https://github.com/zihadzafarzisan/BUP-HACKATHON.git
cd BUP-HACKATHON

# 2. Create and activate a virtual environment
python -m venv venv
# Linux/macOS:
source venv/bin/activate
# Windows:
.\venv\Scripts\activate

# 3. Install dependencies
pip install --no-cache-dir -r requirements.txt

# 4. Configure environment
cp .env.example .env  # Add your GEMINI_API_KEY in .env

# 5. Start the service
python main.py
```

---

## 🧪 Verification Test Suites

Run the local automated test suites:

```bash
# Mathematical Optimization Engine Verification (10/10 scenarios)
python test_phase1.py

# End-to-End LLM Interpretation & Optimization (10/10 scenarios)
python test_phase2.py

# REST API Service Verification (Endpoints, Judge Schema, 400 Validation)
python test_api.py

# Live Deployed Render Endpoint Audit Suite
python test_live_submission.py
```
