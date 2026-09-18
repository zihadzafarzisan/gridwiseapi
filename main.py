"""GridWise Smart Campus Energy Optimization Service.

FastAPI application providing:
- GET /health: Health-check endpoint returning {"status": "ok"}
- POST /optimize-energy: End-to-end LLM-assisted energy dispatch optimization
"""

import logging
import os
from typing import Dict

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from llm_interpreter import interpret_operator_notes
from optimizer import solve_energy_dispatch
from schemas import JudgeOutput, OptimizeEnergyRequest

# Configure secure application logging (standard format, never logging secrets)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gridwise.api")

# Initialize FastAPI application
app = FastAPI(
    title="GridWise Smart Campus Energy Service",
    description="24-Hour Energy Dispatch Optimization Engine with LLM Directive Interpretation",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

# CORS middleware: Allow all origins, methods, and headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Custom Sanitized Exception Handlers
# -----------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle request validation errors, sanitizing details into clean 400 responses."""
    errors = exc.errors()
    err_msgs = []
    for err in errors:
        loc_parts = [str(part) for part in err.get("loc", []) if part != "body"]
        loc_str = " -> ".join(loc_parts)
        msg = err.get("msg", "Validation error")
        err_msgs.append(f"{loc_str}: {msg}" if loc_str else msg)

    detail_message = (
        "; ".join(err_msgs)
        if err_msgs
        else "Invalid request payload format or parameter validation failed."
    )
    logger.warning("Request validation failed: %s", detail_message)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": detail_message},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Handle value and domain errors returning clean HTTP 400 Bad Request."""
    clean_message = str(exc)
    logger.warning("Domain validation error: %s", clean_message)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": clean_message},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Pass through standard HTTPExceptions with clean detail strings."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unhandled exceptions.

    CRITICAL SECURITY: Under no circumstances are stack traces, environment variables,
    API keys, or internal details returned in the response body or leaked to stdout.
    """
    logger.error("Unhandled server exception caught in root handler: %s", type(exc).__name__)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


# -----------------------------------------------------------------------------
# API Endpoints
# -----------------------------------------------------------------------------
@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> Dict[str, str]:
    """Health check endpoint.

    Returns HTTP 200 with JSON payload {"status": "ok"} within 60s of startup.
    """
    return {"status": "ok"}


@app.post(
    "/optimize-energy",
    response_model=JudgeOutput,
    status_code=status.HTTP_200_OK,
)
async def optimize_energy(payload: OptimizeEnergyRequest) -> JudgeOutput:
    """Execute end-to-end 24-hour campus energy dispatch optimization.

    Pipeline:
    1. Interprets 1-3 natural language operator notes into structured directives
       and generates plan rationale via LLM with deterministic guardrails.
    2. Solves the constrained 24-hour cost-minimization Linear Program using HiGHS.
    3. Re-computes and verifies total grid import, total financial cost, and peak
       demand directly from the final schedule.
    4. Returns the official 7-key JSON output payload compliant with GridWise schema.
    """
    logger.info("Processing optimization request for scenario_id: %s", payload.scenario_id)

    # 1. Interpret operator notes with LLM and deterministic guardrail validation
    directives, plan_summary = interpret_operator_notes(
        operator_notes=payload.operator_notes,
        battery=payload.battery,
    )

    # 2. Formulate and solve the 24-hour LP dispatch problem
    opt_result = solve_energy_dispatch(
        scenario_id=payload.scenario_id,
        hours_data=payload.hours,
        battery=payload.battery,
        directives=directives,
    )

    # If the mathematical solver encountered an infeasible problem formulation
    if opt_result.solver_status != "Optimal":
        logger.error(
            "Solver failed for scenario %s: %s",
            payload.scenario_id,
            opt_result.solver_status,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Infeasible dispatch problem: {opt_result.solver_status}",
        )

    # 3. Recalculate metrics directly from the hourly schedule to guarantee exact consistency
    tariffs = {h.hour: float(h.tariff_bdt_per_kwh) for h in payload.hours}
    total_grid_kwh = round(sum(entry.grid_kwh for entry in opt_result.hourly_plan), 4)
    total_cost_bdt = round(
        sum(entry.grid_kwh * tariffs[entry.hour] for entry in opt_result.hourly_plan),
        4,
    )
    peak_grid_kwh = round(
        max((entry.grid_kwh for entry in opt_result.hourly_plan), default=0.0),
        4,
    )

    logger.info(
        "Scenario %s solved: Total Cost=%.2f BDT, Total Grid=%.2f kWh, Peak Grid=%.2f kWh",
        payload.scenario_id,
        total_cost_bdt,
        total_grid_kwh,
        peak_grid_kwh,
    )

    # 4. Construct official 7-key output payload
    return JudgeOutput(
        scenario_id=opt_result.scenario_id,
        directive_interpretation=directives,
        hourly_plan=opt_result.hourly_plan,
        total_grid_kwh=total_grid_kwh,
        total_cost_bdt=total_cost_bdt,
        peak_grid_kwh=peak_grid_kwh,
        plan_summary=plan_summary,
    )


# -----------------------------------------------------------------------------
# Server Entry Point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    server_port = int(os.getenv("PORT", 8000))
    logger.info("Starting GridWise API service on 0.0.0.0:%d", server_port)
    uvicorn.run("main:app", host="0.0.0.0", port=server_port)
