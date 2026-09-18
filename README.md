# GridWise — Smart Campus Energy Optimization Service
**BUP CSE Fest 2026 Hackathon · Online Preliminary Round**

GridWise is an automated, production-grade 24-hour smart campus energy dispatch system. It combines an LLM-powered natural-language operator note interpreter, deterministic safety guardrails, and a High-Performance Linear Programming (LP) solver to schedule grid electricity, solar self-consumption, and battery storage at minimum cost.

---

## Architecture Overview

The system operates as a strict four-stage pipeline:
1. **Natural Language Parser (LLM Interpreter)**: Extracts operational constraints from 1–3 operator notes using `gemini-2.0-flash` with structured JSON schema output and automatic function calling disabled. Includes an offline deterministic fallback parser.
2. **Deterministic Guardrail Firewall**: Sanitizes extracted directives, clamping solar factors to $[0.0, 1.0]$, enforcing chronological uniqueness on hour arrays ($0 \dots 23$), and validating relative battery reserves against physical capacity.
3. **Mathematical Optimizer (HiGHS LP)**: Formulates a 120-variable linear program using `scipy.optimize.linprog(method='highs')`. Guarantees global cost optimality while enforcing hourly energy balance, battery continuity, rate bounds, directive windows, and end-of-day neutrality ($E_{23} = E_{\text{init}}$). Degeneracy during flat tariffs is eliminated via an infinitesimal throughput penalty ($10^{-7}$).
4. **Replay Engine & Schema Serializer**: Re-evaluates exact schedule totals directly from hourly dispatches to eliminate floating-point discrepancies before returning the required 7-key payload.

---

## Technology Stack & Dependencies

- **Framework**: FastAPI, Uvicorn
- **Mathematical Optimization**: SciPy (HiGHS LP Solver), NumPy
- **Data Validation & Schemas**: Pydantic v2
- **Language Models**: Google GenAI SDK (`gemini-2.0-flash`), OpenAI SDK (`gpt-4o-mini` fallback)
- **Environment & Configuration**: python-dotenv
- **Testing**: Pytest, HTTPX, FastAPI TestClient

---

## Environment Variables

The service requires the following configuration (managed via `.env` locally or set in container environments):

| Variable | Required | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Yes (for live LLM) | Google Gemini API key for operator note interpretation. |
| `OPENAI_API_KEY` | Optional | OpenAI API key used as secondary LLM fallback. |
| `PORT` | Optional | Port for the HTTP service (defaults to `8000`). |

*Note: Never commit `.env` or raw API keys to source control.*

---

## Local Quickstart

### Prerequisites
- Python 3.10+
- pip

### Setup & Run
```bash
# 1. Clone the repository
git clone https://github.com/<your-team>/<repo-name>.git
cd <repo-name>

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

## Docker Deployment

Build and run the containerized service:

```bash
# Build Docker image
docker build -t gridwise-energy-service .

# Run container with environment variable
docker run -d -p 8000:8000 -e GEMINI_API_KEY="your_api_key" --name gridwise gridwise-energy-service
```

---

## API Specifications

### 1. Health Check
- **Endpoint**: `GET /health`
- **Response**: `HTTP 200 OK`
```json
{
  "status": "ok"
}
```

### 2. Optimize Energy Dispatch
- **Endpoint**: `POST /optimize-energy`
- **Request Body**:
```json
{
  "scenario_id": "SAMPLE-01",
  "operator_notes": [
    "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
    "The sports office moved next month's registration deadline."
  ],
  "hours": [
    {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
    {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6}
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
- **Response Body**: `HTTP 200 OK` (Exact 7 top-level keys compliant with Judge Output Schema)
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

## Verification Test Suites

Run the automated test suites:

```bash
# Phase 1: Mathematical Optimization Engine Verification (10/10 scenarios)
python test_phase1.py

# Phase 2: End-to-End LLM Interpretation & Optimization (10/10 scenarios)
python test_phase2.py

# Phase 3: REST API Service Verification (Endpoints, Judge Schema, 400 Validation)
python test_api.py
```
