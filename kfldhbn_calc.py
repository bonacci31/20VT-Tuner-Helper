"""
KFLDHBN Calculator - Maximum Boost Pressure Ratio Map

Calculates KFLDHBN values based on:
- Target boost pressure (bar gauge)
- Turbo type (small/large)
- Safety margins for temperature
- High-RPM taper for turbo capability

References:
- s4wiki.com/wiki/Tuning - KFLDHBN limits boost via "ldrlts_w" conversion
- AliantAuto ME7.5 Stage 2 Guide - "maximum permissible turbocharger pressure ratio"

Formula:
  Base PSI = (target_boost_bar * 14.504) + headroom_psi
  Temperature derating applied per row
  RPM taper applied for turbo type
"""

import json
import sys


def bar_to_psi(bar):
    """Convert bar (gauge) to PSI (gauge)."""
    return bar * 14.5038


def psi_to_bar(psi):
    """Convert PSI (gauge) to bar (gauge)."""
    return psi / 14.5038


def psi_to_mbar_abs(psi):
    """Convert PSI gauge to mBar absolute (add atmospheric)."""
    return (psi / 14.5038) * 1000 + 1013


RPM_UNIT_KEYWORDS = ['rpm', 'u/min', '1/min', 'upm', 'drehzahl', 'n [']
TEMP_UNIT_KEYWORDS = ['°c', '°f', 'grad c', 'grad f', 'deg']


def _detect_kfldhbn_axes(map_data):
    """
    Detect which axis is RPM and which is temperature for KFLDHBN.
    Returns (rpm_axis, temp_axis, rpm_is_row).
    rpm_is_row=True means data[row][col] = data[rpm_idx][temp_idx].
    """
    x_axis = map_data.get('x_axis', [])
    y_axis = map_data.get('y_axis', [])
    x_units = map_data.get('x_units', '').lower().strip()
    y_units = map_data.get('y_units', '').lower().strip()

    x_is_rpm = any(kw in x_units for kw in RPM_UNIT_KEYWORDS)
    y_is_rpm = any(kw in y_units for kw in RPM_UNIT_KEYWORDS)
    x_is_temp = any(kw in x_units for kw in TEMP_UNIT_KEYWORDS)
    y_is_temp = any(kw in y_units for kw in TEMP_UNIT_KEYWORDS)

    # y_axis = rows, x_axis = columns
    if y_is_rpm:
        return y_axis, x_axis, True  # rows=RPM, cols=temp
    if x_is_rpm:
        return x_axis, y_axis, False  # rows=temp, cols=RPM

    # Fallback: larger max values → RPM
    x_max = max(x_axis) if x_axis else 0
    y_max = max(y_axis) if y_axis else 0
    if y_max > x_max and y_max > 200:
        return y_axis, x_axis, True
    return x_axis, y_axis, False


def calc_kfldhbn(target_boost_bar, turbo_type='small', headroom_pct=15,
                 original_map=None):
    """
    Calculate KFLDHBN map values.

    Args:
        target_boost_bar: Target boost in bar gauge (e.g., 1.5)
        turbo_type: 'small' (K03/K04) or 'large' (GT28/GT30+)
        headroom_pct: Percentage headroom above target for PID authority (default 15%)
        original_map: Optional dict with original map data for reference.
                      Must have 'x_axis' (RPM), 'y_axis' (temp in F), 'data' (2D)

    Returns:
        dict with calculated map data and metadata
    """
    # Detect axis orientation from original map
    # KFLDHBN has RPM and temperature axes
    if original_map:
        rows = len(original_map['data'])
        cols = len(original_map['data'][0])
        rpm_axis, temp_axis, rpm_is_row = _detect_kfldhbn_axes(original_map)
    else:
        rpm_axis = [1000, 2000, 2520, 3000, 4000, 5000, 6000, 6720]
        temp_axis = [14.45, 49.55, 86.0, 122.45, 157.55, 176.45, 211.55, 248.0]
        rows = len(temp_axis)
        cols = len(rpm_axis)
        rpm_is_row = False  # default: rows=temp, cols=RPM

    # Target PSI with headroom for PID authority
    target_psi = bar_to_psi(target_boost_bar)
    headroom_psi = target_psi * (headroom_pct / 100.0)
    base_psi = target_psi + headroom_psi

    # --- RPM taper factors ---
    # Small turbos (K03/K04) lose efficiency at high RPM
    # Large turbos (GT28+) maintain boost longer but spool slower
    if turbo_type == 'small':
        # K04 taper: starts dropping above 5000 RPM
        rpm_taper = {
            0:    0.90,   # Low RPM - slightly less (spool protection)
            1000: 0.90,
            1500: 0.95,
            1750: 1.00,
            2000: 1.00,
            2500: 1.00,
            3000: 1.00,
            3500: 1.00,
            4000: 1.00,
            4500: 1.00,
            5000: 0.94,   # Starting to fall off
            5500: 0.88,
            6000: 0.82,
            6500: 0.75,
            6720: 0.58,   # Significant falloff
            7000: 0.50,
        }
    else:
        # Large turbo: maintains boost to higher RPM, less at low RPM
        rpm_taper = {
            0:    0.70,
            1000: 0.70,
            1500: 0.80,
            1750: 0.85,
            2000: 0.90,
            2500: 0.95,
            3000: 1.00,
            3500: 1.00,
            4000: 1.00,
            4500: 1.00,
            5000: 1.00,
            5500: 1.00,
            6000: 0.95,
            6500: 0.88,
            6720: 0.80,
            7000: 0.75,
        }

    # --- Temperature derating ---
    # Higher IAT = less boost allowed to prevent knock
    # Based on s4wiki: KFTARX pulls load at high IAT
    # AliantAuto: "above 50°C apply appropriate reduction factors"
    # Temp axis in Fahrenheit
    temp_derating = {
        -20:  1.00,   # Cold: full boost
        0:    1.00,
        14:   1.00,
        32:   1.00,
        50:   1.00,   # ~10°C
        68:   1.00,   # ~20°C
        86:   1.00,   # ~30°C - still OK with intercooler
        104:  0.96,   # ~40°C - slight derating
        122:  0.92,   # ~50°C - AliantAuto threshold
        140:  0.85,   # ~60°C - significant derating
        158:  0.78,   # ~70°C
        176:  0.70,   # ~80°C - hot soak
        194:  0.60,   # ~90°C
        212:  0.52,   # ~100°C - extreme
        230:  0.45,
        248:  0.40,   # ~120°C - survival mode
        280:  0.30,
    }

    def interpolate_factor(value, factor_map):
        """Linear interpolation from a factor map."""
        keys = sorted(factor_map.keys())
        if value <= keys[0]:
            return factor_map[keys[0]]
        if value >= keys[-1]:
            return factor_map[keys[-1]]
        for i in range(len(keys) - 1):
            if keys[i] <= value <= keys[i + 1]:
                t = (value - keys[i]) / (keys[i + 1] - keys[i])
                return factor_map[keys[i]] * (1 - t) + factor_map[keys[i + 1]] * t
        return factor_map[keys[-1]]

    # --- Calculate the map ---
    new_data = []
    for row_idx in range(rows):
        row_values = []
        for col_idx in range(cols):
            if rpm_is_row:
                rpm = rpm_axis[row_idx] if row_idx < len(rpm_axis) else 3000
                temp_f = temp_axis[col_idx] if col_idx < len(temp_axis) else 86.0
            else:
                temp_f = temp_axis[row_idx] if row_idx < len(temp_axis) else 86.0
                rpm = rpm_axis[col_idx] if col_idx < len(rpm_axis) else 3000

            temp_factor = interpolate_factor(temp_f, temp_derating)
            rpm_factor = interpolate_factor(rpm, rpm_taper)

            # Final PSI = base * rpm_taper * temp_derating
            psi_val = base_psi * rpm_factor * temp_factor

            # Clamp: minimum 4 PSI, maximum 35 PSI (ME7 hard limit ~2560 mBar)
            psi_val = max(4.0, min(35.0, psi_val))

            # Round to 2 decimal places (match XDF precision)
            psi_val = round(psi_val, 2)
            row_values.append(psi_val)

        new_data.append(row_values)

    # Build result — preserve original axis orientation
    if original_map:
        result = {
            'map_name': 'KFLDHBN',
            'params': {
                'target_boost_bar': target_boost_bar,
                'target_boost_psi': round(target_psi, 2),
                'base_psi_with_headroom': round(base_psi, 2),
                'headroom_pct': headroom_pct,
                'turbo_type': turbo_type,
                'max_mbar_abs': round(psi_to_mbar_abs(base_psi), 0),
            },
            'x_axis': original_map['x_axis'],
            'y_axis': original_map['y_axis'],
            'x_units': original_map.get('x_units', ''),
            'y_units': original_map.get('y_units', ''),
            'z_units': original_map.get('z_units', 'PSI'),
            'data': new_data,
            'rows': rows,
            'cols': cols,
            'original': original_map,
        }
    else:
        result = {
            'map_name': 'KFLDHBN',
            'params': {
                'target_boost_bar': target_boost_bar,
                'target_boost_psi': round(target_psi, 2),
                'base_psi_with_headroom': round(base_psi, 2),
                'headroom_pct': headroom_pct,
                'turbo_type': turbo_type,
                'max_mbar_abs': round(psi_to_mbar_abs(base_psi), 0),
            },
            'x_axis': rpm_axis,
            'y_axis': temp_axis,
            'x_units': 'RPM',
            'y_units': '°F',
            'z_units': 'PSI',
            'data': new_data,
            'rows': rows,
            'cols': cols,
        }

    return result


def print_table(data, x_axis, y_axis, title=""):
    """Print a formatted table."""
    if title:
        print(f"\n### {title}")

    # Header
    header = "| Temp\\RPM |"
    for rpm in x_axis:
        header += f" {int(rpm)} |"
    print(header)

    sep = "|" + "------|" * (len(x_axis) + 1)
    print(sep)

    for row_idx, row in enumerate(data):
        temp = y_axis[row_idx] if row_idx < len(y_axis) else "?"
        line = f"| **{temp}°F** |"
        for val in row:
            line += f" {val:.1f} |"
        print(line)


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "usage": "python kfldhbn_calc.py <target_boost_bar> [turbo_type] [headroom_pct] [original_map.json]",
            "examples": [
                "python kfldhbn_calc.py 1.5",
                "python kfldhbn_calc.py 1.5 small 15",
                "python kfldhbn_calc.py 2.0 large 20 original_kfldhbn.json",
            ],
            "params": {
                "target_boost_bar": "Target boost in bar gauge (e.g., 1.5)",
                "turbo_type": "small (K03/K04) or large (GT28+), default: small",
                "headroom_pct": "PID headroom percentage above target, default: 15",
                "original_map.json": "Optional JSON file with original map for comparison",
            }
        }, indent=2))
        return

    target_boost = float(sys.argv[1])
    turbo_type = sys.argv[2] if len(sys.argv) > 2 else 'small'
    headroom_pct = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0

    original_map = None
    if len(sys.argv) > 4:
        with open(sys.argv[4], 'r') as f:
            original_map = json.load(f)

    result = calc_kfldhbn(target_boost, turbo_type, headroom_pct, original_map)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
