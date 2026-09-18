"""Deterministic Guardrails for Operator Directive Interpretations.

Validates and sanitizes raw LLM interpretations before passing them into the
mathematical optimization engine. Ensures type safety, bounds compliance,
chronological sorting, strict applies semantics, and end-exclusive hour index bounds.
"""

from typing import Any, Dict, List
from schemas import BatterySpecs, DirectiveInterpretation, StructuredAdjustment

VALID_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def sanitize_hours(raw_hours: Any) -> List[int]:
    """Validate, deduplicate, clamp to [0, 23], and sort hours ascending."""
    if not isinstance(raw_hours, list):
        return []
    valid_hrs = set()
    for h in raw_hours:
        try:
            h_int = int(h)
            if 0 <= h_int <= 23:
                valid_hrs.add(h_int)
        except (ValueError, TypeError):
            continue
    return sorted(list(valid_hrs))


def validate_and_sanitize_directives(
    raw_interpretations: List[Dict[str, Any]],
    battery: BatterySpecs,
    operator_notes: List[str],
) -> List[DirectiveInterpretation]:
    """Validate and sanitize a list of raw directive interpretations.

    Guarantees:
    1. Output array length exactly equals len(operator_notes).
    2. note_index strictly sequences from 0 to N-1.
    3. directive_type is an allowed enum value.
    4. applies is False if and only if directive_type == 'no_op'.
    5. structured_adjustment is None for 'no_op' and structured dict for actionable directives.
    6. hours are unique, strictly in [0, 23], and sorted ascending.
    7. Numeric bounds:
       - factor in [0.0, 1.0] for solar_reduction.
       - minimum_energy_kwh in [0.0, battery.capacity_kwh] for minimum_battery_reserve.
       - max_grid_kwh >= 0.0 for max_grid_window.
    """
    sanitized: List[DirectiveInterpretation] = []

    # Map raw interpretations by note_index if available
    raw_by_index: Dict[int, Dict[str, Any]] = {}
    for idx, item in enumerate(raw_interpretations):
        if isinstance(item, dict):
            n_idx = item.get("note_index", idx)
            try:
                raw_by_index[int(n_idx)] = item
            except (ValueError, TypeError):
                raw_by_index[idx] = item

    for idx, _ in enumerate(operator_notes):
        raw = raw_by_index.get(idx) or {}
        raw_type = str(raw.get("directive_type", "no_op")).strip()

        if raw_type not in VALID_DIRECTIVE_TYPES:
            raw_type = "no_op"

        explanation = str(raw.get("explanation") or f"Interpreted directive for note {idx}")
        raw_adj = raw.get("structured_adjustment")

        if raw_type == "no_op":
            sanitized.append(
                DirectiveInterpretation(
                    note_index=idx,
                    applies=False,
                    directive_type="no_op",
                    structured_adjustment=None,
                    explanation=explanation,
                )
            )
            continue

        # Non-no_op directives: applies must be True
        applies = True
        if not isinstance(raw_adj, dict):
            raw_adj = {}

        hours = sanitize_hours(raw_adj.get("hours"))

        factor: float | None = None
        min_kwh: float | None = None
        max_grid: float | None = None

        if raw_type == "solar_reduction":
            raw_factor = raw_adj.get("factor")
            if raw_factor is not None:
                try:
                    factor = float(raw_factor)
                    factor = max(0.0, min(1.0, factor))
                except (ValueError, TypeError):
                    factor = 1.0
            else:
                factor = 1.0

        elif raw_type == "minimum_battery_reserve":
            raw_min = raw_adj.get("minimum_energy_kwh")
            if raw_min is not None:
                try:
                    min_kwh = float(raw_min)
                    min_kwh = max(0.0, min(float(battery.capacity_kwh), min_kwh))
                except (ValueError, TypeError):
                    min_kwh = float(battery.minimum_energy_kwh)
            else:
                min_kwh = float(battery.minimum_energy_kwh)

        elif raw_type == "max_grid_window":
            raw_max = raw_adj.get("max_grid_kwh")
            if raw_max is not None:
                try:
                    max_grid = max(0.0, float(raw_max))
                except (ValueError, TypeError):
                    max_grid = 1e6
            else:
                max_grid = 1e6

        adj_model = StructuredAdjustment(
            hours=hours,
            factor=factor,
            minimum_energy_kwh=min_kwh,
            max_grid_kwh=max_grid,
        )

        sanitized.append(
            DirectiveInterpretation(
                note_index=idx,
                applies=applies,
                directive_type=raw_type,  # type: ignore[arg-type]
                structured_adjustment=adj_model,
                explanation=explanation,
            )
        )

    return sanitized
