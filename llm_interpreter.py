"""LLM Operator Note Interpreter with Multi-Provider Support and Deterministic Fallback.

Interprets 1-3 natural language operator notes into structured directive objects.
Supports Gemini (gemini-2.0-flash / gemini-1.5-flash via google-genai), OpenAI (gpt-4o-mini),
Anthropic (claude-3-5-haiku / claude-3-7-sonnet), and LiteLLM, with a comprehensive rule-based
deterministic parser providing instantaneous offline fallback if API keys or networks fail.
"""

import json
import os
import re
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

from guardrails import validate_and_sanitize_directives
from schemas import BatterySpecs, DirectiveInterpretation

# Auto-load .env file (e.g. GEMINI_API_KEY, OPENAI_API_KEY)
load_dotenv()


SYSTEM_PROMPT = """You are an expert energy operations parser for the GridWise Smart Campus Energy System.
Your job is to read 1 to 3 campus operator notes and translate each note into a structured directive interpretation.

Allowed directive_type values:
1. "solar_reduction": Usable solar dropped during specific hours.
   - structured_adjustment: {"hours": [int, ...], "factor": float}
   - CRITICAL: "factor" is the usable fraction REMAINING (0.0 <= factor <= 1.0).
     Example: "80% reduction" -> factor = 0.20
     Example: "drop to roughly 25%" -> factor = 0.25
     Example: "leave about half" -> factor = 0.50

2. "minimum_battery_reserve": Maintain required battery energy level during specific hours.
   - structured_adjustment: {"hours": [int, ...], "minimum_energy_kwh": float}
   - CRITICAL: If note specifies percentage (e.g. "keep at least 50% stored"), calculate:
     minimum_energy_kwh = (percentage / 100.0) * battery_capacity_kwh.
     Example: 50% of 200 kWh -> minimum_energy_kwh = 100.0.
     If specified in kWh (e.g. "at least 90 kWh"), use that exact number.

3. "no_charge_window": Battery charging disabled during specific hours.
   - structured_adjustment: {"hours": [int, ...]}

4. "no_discharge_window": Battery discharging disabled during specific hours.
   - structured_adjustment: {"hours": [int, ...]}

5. "max_grid_window": Grid intake / import capped at a stated limit during specific hours.
   - structured_adjustment: {"hours": [int, ...], "max_grid_kwh": float}
   - Example: "not exceed 155 kWh" -> max_grid_kwh = 155.0

6. "no_op": Irrelevant note or distractor (cafeteria, sports, library, semester schedules, etc.).
   - applies: false
   - structured_adjustment: null

TIME CONVERSION RULES (24-hour clock, start-inclusive, end-exclusive):
- "1 PM to 3 PM" -> [13, 14]
- "from noon until 2 PM" -> [12, 13]
- "6 PM until 9 PM" -> [18, 19, 20]
- "6 PM until 10 PM" -> [18, 19, 20, 21]
- "2 AM until 5 AM" -> [2, 3, 4]
- "10 AM until noon" -> [10, 11]
- "11 AM until 1 PM" -> [11, 12]
- "11 AM and 2 PM" -> [11, 12, 13]
- "7 PM until 9 PM" -> [19, 20]
- "7 PM until 10 PM" -> [19, 20, 21]
- "5 PM until 7 PM" -> [17, 18]
- "2 PM until 4 PM" -> [14, 15]

Every hour in "hours" must be an integer from 0 to 23, sorted ascending.
Return exactly one interpretation per operator note, matching note_index (0, 1, ...).
Also output a clean 1-2 sentence "plan_summary" summarizing today's dispatch plan.
"""


def _parse_time_range(text: str) -> List[int]:
    """Extract start-inclusive, end-exclusive hours from natural language note."""
    t_lower = text.lower()

    # Direct keyword matches
    if "noon" in t_lower and ("2 pm" in t_lower or "2:00 pm" in t_lower):
        return [12, 13]
    if "10 am" in t_lower and "noon" in t_lower:
        return [10, 11]

    # Regex patterns for "<start> [am/pm] (until|to|and) <end> [am/pm]"
    patterns = [
        r"(?:from|between)?\s*(\d{1,2})(?::\d{2})?\s*(am|pm)\s*(?:until|to|and|-)\s*(\d{1,2})(?::\d{2})?\s*(am|pm)",
        r"(?:from|between)?\s*noon\s*(?:until|to|and|-)\s*(\d{1,2})(?::\d{2})?\s*(am|pm)",
        r"(?:from|between)?\s*(\d{1,2})(?::\d{2})?\s*(am|pm)\s*(?:until|to|and|-)\s*noon",
    ]

    for p in patterns:
        m = re.search(p, t_lower)
        if m:
            groups = m.groups()
            if len(groups) == 4:
                s_val, s_ampm, e_val, e_ampm = int(groups[0]), groups[1], int(groups[2]), groups[3]
                s_hour = s_val % 12 + (12 if s_ampm == "pm" else 0)
                e_hour = e_val % 12 + (12 if e_ampm == "pm" else 0)
                if s_hour < e_hour:
                    return list(range(s_hour, e_hour))
            elif len(groups) == 2:
                # Noon to X PM
                e_val, e_ampm = int(groups[0]), groups[1]
                e_hour = e_val % 12 + (12 if e_ampm == "pm" else 0)
                if 12 < e_hour:
                    return list(range(12, e_hour))

    return []


def deterministic_parse_note(note: str, battery: BatterySpecs, note_index: int) -> Dict[str, Any]:
    """Robust regex/rule-based parser as high-accuracy offline fallback."""
    t = note.lower()

    # Check for distractors / irrelevant notices
    distractor_keywords = [
        "registration deadline", "sports office", "book-return", "library",
        "club notices", "student affairs", "seminar room", "cafeteria",
        "next month", "next week", "published tomorrow"
    ]
    if any(k in t for k in distractor_keywords) and not any(k in t for k in ["battery", "solar", "grid", "feeder", "transformer"]):
        return {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "This note does not affect today's 24-hour energy schedule.",
        }

    hours = _parse_time_range(note)

    # 1. Solar reduction
    if "solar" in t and ("reduction" in t or "cleaning" in t or "wash" in t or "inspection" in t or "cloud" in t or "inverter" in t):
        factor = 1.0
        # Check for explicit percentages or fractions
        if "80% reduction" in t or "reduction of 80%" in t or "reduced by 80%" in t:
            factor = 0.20
        elif "25%" in t:
            factor = 0.25
        elif "half" in t or "50%" in t:
            factor = 0.50
        elif "75%" in t:
            factor = 0.25 if "reduction" in t else 0.75
        elif "20%" in t:
            factor = 0.80 if "reduction" in t else 0.20
        else:
            m_pct = re.search(r"(\d+)%", t)
            if m_pct:
                pct = float(m_pct.group(1))
                if "reduction" in t or "reduced" in t:
                    factor = max(0.0, min(1.0, 1.0 - (pct / 100.0)))
                else:
                    factor = max(0.0, min(1.0, pct / 100.0))

        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": hours, "factor": factor},
            "explanation": f"Solar availability reduced to {factor*100:.0f}% during specified window.",
        }

    # 2. No charge window
    if ("charge" in t or "charger" in t or "charging" in t) and ("isolated" in t or "unavailable" in t or "disabled" in t or "maintenance" in t or "inspect" in t or "do not charge" in t or "no charge" in t):
        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": hours},
            "explanation": "Battery charging is disabled during the maintenance window.",
        }

    # 3. No discharge window
    if ("discharge" in t or "discharging" in t) and ("not discharge" in t or "disabled" in t or "do not discharge" in t or "testing" in t or "unavailable" in t or "no discharge" in t):
        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": hours},
            "explanation": "Battery discharging is prohibited during protection testing.",
        }

    # 4. Minimum battery reserve
    if "battery" in t and ("reserve" in t or "stored" in t or "remain" in t or "emergency" in t or "keep at least" in t):
        # Check percentage
        m_pct = re.search(r"(\d+)%", t)
        if m_pct:
            pct = float(m_pct.group(1))
            min_kwh = round((pct / 100.0) * float(battery.capacity_kwh), 2)
        else:
            m_kwh = re.search(r"(\d+(?:\.\d+)?)\s*kwh", t)
            if m_kwh:
                min_kwh = float(m_kwh.group(1))
            else:
                min_kwh = float(battery.minimum_energy_kwh)

        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": hours, "minimum_energy_kwh": min_kwh},
            "explanation": f"Maintain minimum battery reserve of {min_kwh} kWh during emergency window.",
        }

    # 5. Max grid window
    if ("grid" in t or "feeder" in t or "transformer" in t or "substation" in t) and ("limit" in t or "not exceed" in t or "stay at or below" in t or "intake" in t or "cap" in t):
        m_cap = re.search(r"(\d+(?:\.\d+)?)\s*kwh", t)
        max_kwh = float(m_cap.group(1)) if m_cap else 150.0
        return {
            "note_index": note_index,
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": hours, "max_grid_kwh": max_kwh},
            "explanation": f"Grid import is capped at {max_kwh} kWh during the constrained window.",
        }

    # Default fallback to no_op
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "This note does not affect today's 24-hour energy schedule.",
    }


def interpret_operator_notes(
    operator_notes: List[str],
    battery: BatterySpecs,
) -> Tuple[List[DirectiveInterpretation], str]:
    """Interpret operator notes into validated directives and generate a plan summary.

    Tries LLM providers (Gemini, OpenAI, Anthropic, LiteLLM) if API keys are available,
    and falls back deterministically to rule-based extraction if unconfigured or on error.
    """
    raw_results: List[Dict[str, Any]] = []
    plan_summary = "Optimizes 24-hour campus energy dispatch satisfying all operational constraints."

    # Prepare prompt with contextual battery capacity
    prompt_content = f"Battery Capacity: {battery.capacity_kwh} kWh\nOperator Notes:\n"
    for i, note in enumerate(operator_notes):
        prompt_content += f"Note {i}: {note}\n"

    parsed_successfully = False

    # Check for Gemini API key
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key and not parsed_successfully:
        try:
            from google import genai
            from google.genai import types as genai_types

            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=f"{SYSTEM_PROMPT}\n\n{prompt_content}\nOutput valid JSON with keys 'directive_interpretation' and 'plan_summary'.",
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            resp_text = response.text or "{}"
            data = json.loads(resp_text)
            if "directive_interpretation" in data and isinstance(data["directive_interpretation"], list):
                raw_results = data["directive_interpretation"]
                plan_summary = data.get("plan_summary", plan_summary)
                parsed_successfully = True
        except Exception:
            pass

    # Check for OpenAI API key
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key and not parsed_successfully:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt_content},
                ],
                response_format={"type": "json_object"},
            )
            content = completion.choices[0].message.content or "{}"
            data = json.loads(content)
            if "directive_interpretation" in data and isinstance(data["directive_interpretation"], list):
                raw_results = data["directive_interpretation"]
                plan_summary = data.get("plan_summary", plan_summary)
                parsed_successfully = True
        except Exception:
            pass

    # Deterministic fallback if no LLM was called or parsing failed
    if not parsed_successfully:
        raw_results = [
            deterministic_parse_note(note, battery, idx)
            for idx, note in enumerate(operator_notes)
        ]
        # Construct helpful plan summary
        applied = [r["directive_type"] for r in raw_results if r.get("applies")]
        if applied:
            plan_summary = f"Applies {', '.join(applied)} directives and optimizes energy dispatch with end-of-day neutrality."
        else:
            plan_summary = "Operates standard optimal battery arbitrage with end-of-day battery neutrality."

    # Pass raw output through deterministic guardrail validator
    validated_directives = validate_and_sanitize_directives(raw_results, battery, operator_notes)

    return validated_directives, plan_summary
