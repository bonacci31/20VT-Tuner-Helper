"""
LDORXN Calculator - Overboost Protection Map

Calculates LDORXN values based on LDRXN values.
LDORXN sets the maximum cylinder charge allowed during overboost error (E_ldo).
It MUST stay below LDRXN at all RPM points.

References:
- AliantAuto: "Do not set LDORXN equal to or greater than LDRXN values; this is incorrect"
- s4wiki: KFDLULS (~200mbar) is the deviation threshold triggering P1555 overboost DTC

Formula:
  LDORXN = LDRXN * safety_ratio
  where safety_ratio is typically 0.85-0.95 of LDRXN

The LDORXN RPM axis is typically coarser than LDRXN (8 vs 16 points),
so LDRXN values are interpolated to LDORXN RPM points first.
"""

import json
import sys


def interpolate_to_axis(source_rpm, source_data, target_rpm):
    """
    Interpolate 1D source data from source RPM axis to target RPM axis.

    Args:
        source_rpm: list of RPM breakpoints for source data
        source_data: list of values at each source RPM point
        target_rpm: list of RPM breakpoints to interpolate to

    Returns:
        list of interpolated values at target RPM points
    """
    result = []
    for rpm in target_rpm:
        if rpm <= source_rpm[0]:
            result.append(source_data[0])
        elif rpm >= source_rpm[-1]:
            result.append(source_data[-1])
        else:
            # Find surrounding points
            for i in range(len(source_rpm) - 1):
                if source_rpm[i] <= rpm <= source_rpm[i + 1]:
                    t = (rpm - source_rpm[i]) / (source_rpm[i + 1] - source_rpm[i])
                    val = source_data[i] * (1 - t) + source_data[i + 1] * t
                    result.append(val)
                    break
    return result


RPM_UNIT_KEYWORDS = ['rpm', 'u/min', '1/min', 'upm', 'drehzahl', 'n [']


def _get_rpm_axis_1d(map_data):
    """For 1D maps, find the axis that contains RPM values."""
    x_axis = map_data.get('x_axis', [])
    y_axis = map_data.get('y_axis', [])
    x_units = map_data.get('x_units', '').lower().strip()
    y_units = map_data.get('y_units', '').lower().strip()

    if any(kw in x_units for kw in RPM_UNIT_KEYWORDS):
        return x_axis
    if any(kw in y_units for kw in RPM_UNIT_KEYWORDS):
        return y_axis

    # For 1D, the non-empty longer axis is likely RPM
    if len(x_axis) > len(y_axis):
        return x_axis
    if len(y_axis) > len(x_axis):
        return y_axis
    return x_axis  # default


def calc_ldorxn(ldrxn_map, ldorxn_original=None, safety_ratio=0.90):
    """
    Calculate LDORXN values from LDRXN.

    Args:
        ldrxn_map: dict with LDRXN data. Must have:
                   - 'x_axis': RPM breakpoints
                   - 'data': 2D array (1 row) or 1D list of load values
        ldorxn_original: Optional dict with original LDORXN map for reference.
                         Used to get the target RPM axis.
        safety_ratio: Ratio of LDRXN to use (default 0.90 = 90%)
                      Must be < 1.0. Typical range: 0.85-0.95

    Returns:
        dict with calculated LDORXN map data and metadata
    """
    if safety_ratio >= 1.0:
        raise ValueError("safety_ratio must be < 1.0 — LDORXN must stay below LDRXN")
    if safety_ratio < 0.5:
        raise ValueError("safety_ratio below 0.50 is dangerously low — ECU will trigger overboost too early")

    # Extract LDRXN data — detect RPM axis
    ldrxn_rpm = _get_rpm_axis_1d(ldrxn_map)
    ldrxn_data = ldrxn_map['data']
    # Handle 2D array (1 row) or flat list
    if isinstance(ldrxn_data[0], list):
        ldrxn_values = ldrxn_data[0]
    else:
        ldrxn_values = ldrxn_data

    # Get LDORXN target RPM axis
    if ldorxn_original:
        ldorxn_rpm = _get_rpm_axis_1d(ldorxn_original)
    else:
        # Default 8-point axis common in ME7.5
        ldorxn_rpm = [1000, 1520, 2000, 2520, 3000, 4000, 5000, 6000]

    # Interpolate LDRXN values to LDORXN RPM points
    ldrxn_at_ldorxn_rpm = interpolate_to_axis(ldrxn_rpm, ldrxn_values, ldorxn_rpm)

    # Apply safety ratio
    ldorxn_values = [round(val * safety_ratio) for val in ldrxn_at_ldorxn_rpm]

    # Build result
    result = {
        'map_name': 'LDORXN',
        'params': {
            'safety_ratio': safety_ratio,
            'safety_pct': f"{safety_ratio * 100:.0f}%",
        },
        'x_axis': ldorxn_rpm,
        'x_units': 'RPM',
        'z_units': 'Load',
        'data': [ldorxn_values],
        'rows': 1,
        'cols': len(ldorxn_rpm),
        'detail': [],
    }

    # Add per-RPM detail for review
    for i, rpm in enumerate(ldorxn_rpm):
        ldrxn_interp = round(ldrxn_at_ldorxn_rpm[i], 1)
        ldorxn_val = ldorxn_values[i]
        margin = round(ldrxn_interp - ldorxn_val, 1)
        boost_mbar = round(ldorxn_val * 10 + 300)
        entry = {
            'rpm': rpm,
            'ldrxn_interpolated': ldrxn_interp,
            'ldorxn_calculated': ldorxn_val,
            'margin_load': margin,
            'ldorxn_boost_mbar': boost_mbar,
        }
        if ldorxn_original:
            orig_val = ldorxn_original['data'][0][i] if i < len(ldorxn_original['data'][0]) else None
            entry['ldorxn_original'] = orig_val
        result['detail'].append(entry)

    if ldorxn_original:
        result['original'] = ldorxn_original

    return result


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "usage": "python ldorxn_calc.py <ldrxn_map.json> [ldorxn_original.json] [safety_ratio]",
            "examples": [
                "python ldorxn_calc.py ldrxn_new.json",
                "python ldorxn_calc.py ldrxn_new.json ldorxn_original.json 0.90",
                "python ldorxn_calc.py ldrxn_new.json - 0.85",
            ],
            "params": {
                "ldrxn_map.json": "JSON file with new LDRXN data (x_axis + data)",
                "ldorxn_original.json": "Optional JSON with original LDORXN (for RPM axis). Use '-' to skip.",
                "safety_ratio": "Ratio of LDRXN (0.85-0.95), default: 0.90",
            }
        }, indent=2))
        return

    with open(sys.argv[1], 'r') as f:
        ldrxn_map = json.load(f)

    ldorxn_original = None
    if len(sys.argv) > 2 and sys.argv[2] != '-':
        with open(sys.argv[2], 'r') as f:
            ldorxn_original = json.load(f)

    safety_ratio = float(sys.argv[3]) if len(sys.argv) > 3 else 0.90

    result = calc_ldorxn(ldrxn_map, ldorxn_original, safety_ratio)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
