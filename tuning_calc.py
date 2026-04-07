"""
ME7 Tuning Calculator - Implements the spreadsheet formulas from ME7 TUNER WIZZARD.xlsm

All formulas are pure math functions that take original map data and tuning
parameters, and return new calculated map values.

Axis detection: XDF files vary in which axis is x vs y. Some have x=RPM/y=load,
others have x=load/y=RPM. bin_handler convention: x_axis=columns, y_axis=rows.
All functions detect RPM axis automatically via units and value ranges.
"""

import math


# ============================================================================
# Axis Detection Utilities
# ============================================================================

RPM_UNIT_KEYWORDS = ['rpm', 'u/min', '1/min', 'upm', 'drehzahl', 'n [']
TEMP_UNIT_KEYWORDS = ['°c', '°f', 'grad c', 'grad f', 'deg']


def _is_rpm_units(units_str):
    """Check if a units string indicates RPM."""
    u = units_str.lower().strip()
    return any(kw in u for kw in RPM_UNIT_KEYWORDS)


def _is_temp_units(units_str):
    """Check if a units string indicates temperature."""
    u = units_str.lower().strip()
    return any(kw in u for kw in TEMP_UNIT_KEYWORDS)


def _get_axes(map_data, row_type='rpm'):
    """
    Identify which axis is RPM and which is load/torque/temp.

    bin_handler convention: x_axis = columns, y_axis = rows.
    data[row_idx][col_idx] → y_axis[row_idx], x_axis[col_idx].

    Args:
        map_data: dict from read_map_data with x_axis, y_axis, x_units, y_units
        row_type: what the row axis should represent for this map.
                  'rpm' for maps like KFMIRL where we iterate RPM as rows.
                  Not used for detection — only documents intent.

    Returns:
        (rpm_axis, other_axis, rpm_is_row) where:
        - rpm_axis: list of RPM values
        - other_axis: list of load/torque/temp values
        - rpm_is_row: True if RPM is y_axis (row dimension)

    Usage:
        rpm_axis, load_axis, rpm_is_row = _get_axes(map_data)
        for row_idx in range(rows):
            for col_idx in range(cols):
                if rpm_is_row:
                    rpm = rpm_axis[row_idx]
                    load = load_axis[col_idx]
                else:
                    rpm = rpm_axis[col_idx]
                    load = load_axis[row_idx]
    """
    x_axis = map_data.get('x_axis', [])
    y_axis = map_data.get('y_axis', [])
    x_units = map_data.get('x_units', '')
    y_units = map_data.get('y_units', '')

    y_is_rpm = _is_rpm_units(y_units)
    x_is_rpm = _is_rpm_units(x_units)

    # If units are ambiguous, fall back to value ranges
    if not x_is_rpm and not y_is_rpm:
        x_max = max(x_axis) if x_axis else 0
        y_max = max(y_axis) if y_axis else 0
        # RPM axis values are typically 300-8000
        if y_max > 200 and y_max > x_max * 1.5:
            y_is_rpm = True
        elif x_max > 200 and x_max > y_max * 1.5:
            x_is_rpm = True
        else:
            x_is_rpm = True  # default: assume x=RPM (original convention)

    if y_is_rpm:
        # y_axis = rows = RPM
        return y_axis, x_axis, True
    else:
        # x_axis = cols = RPM, y_axis = rows = load
        return x_axis, y_axis, False


# ============================================================================
# KFMIRL Calculation (Load Request Map)
# ============================================================================

# Default RPM axis values from the spreadsheet (16 points)
DEFAULT_KFMIRL_RPM = [480, 720, 1000, 1240, 1520, 1760, 2000, 2520, 3000, 3520, 4000, 4520, 5000, 5720, 6000, 6720]

# Default Load axis values from the spreadsheet (16 points)
DEFAULT_KFMIRL_LOAD = [0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 75, 80, 85, 99]

# RPM-specific T-column factors (per RPM row, from spreadsheet column T)
# These shape the RPM-dependent response curve
# Values read directly from the spreadsheet cells T4:T19
RPM_T_FACTORS = {
    480:   0.0,
    720:   0.0,
    1000:  0.0,
    1240:  0.005,
    1520:  0.01,
    1760:  0.015,
    2000:  0.025,
    2520:  0.029,
    3000:  0.027,
    3520:  0.025,
    4000:  0.02,
    4520:  0.015,
    5000: -0.025,
    5720: -0.075,
    6000: -0.09,
    6720: -0.13,
}

# Load factor coefficients (per load column)
# From spreadsheet row 20: col_factor = multiplier * coeff + offset
# where multiplier = clamp(aggressiveness/100, 0, 1.35)
# Columns for loads 0%, 5%, 10% are always 0 (low load uses different formula)
LOAD_FACTOR_COEFFICIENTS = {
    0:  (0.0, 0.0),      # always 0
    5:  (0.0, 0.0),      # low load - uses separate formula
    10: (0.0, 0.0),      # low load - uses separate formula
    15: (0.8, 0.4),      # mult * 0.8 + 0.4
    20: (0.8, 0.1),      # mult * 0.8 + 0.1
    25: (1.1, 0.0),      # mult * 1.1
    30: (1.1, 0.0),      # mult * 1.1
    35: (0.9, 0.0),      # mult * 0.9
    40: (0.76, 0.0),     # mult * 0.76
    50: (0.48, 0.0),     # mult * 0.48
    60: (0.3, 0.0),      # mult * 0.3
    70: (0.18, 0.0),     # mult * 0.18
    75: (0.16, 0.0),     # mult * 0.16
    80: (0.15, 0.0),     # mult * 0.15
    85: (0.12, 0.0),     # mult * 0.12
    99: (0.01, 0.0),     # mult * 0.01
}

# RPM normalization constant ($C$19 in spreadsheet = last RPM in the axis)
# This is dynamic - derived from the actual RPM axis at runtime


def _get_closest_key(value, lookup_dict):
    """Find the closest key in a dict to the given value."""
    keys = sorted(lookup_dict.keys())
    closest = min(keys, key=lambda k: abs(k - value))
    return closest


def _get_load_factor(load, aggr_mult):
    """Calculate the load factor for a given load percentage and aggressiveness multiplier."""
    closest_load = _get_closest_key(load, LOAD_FACTOR_COEFFICIENTS)
    coeff, offset = LOAD_FACTOR_COEFFICIENTS[closest_load]
    return aggr_mult * coeff + offset


def _get_rpm_t_factor(rpm):
    """Get the RPM T-factor from the spreadsheet column T."""
    closest_rpm = _get_closest_key(rpm, RPM_T_FACTORS)
    return RPM_T_FACTORS[closest_rpm]


def calc_kfmirl(original_map, max_boost, aggressiveness, turbo_type='small', gen_low_load=True):
    """
    Calculate new KFMIRL map values using the exact spreadsheet formulas.

    Spreadsheet formula for load >= 15%:
        base = max_charge * (load/100) - 10 + RPM/6720 * 10
        new  = base * (1 + col_factor + rpm_factor)

    Spreadsheet formula for low load (5%, 10%) with gen_low_load:
        new = load*2 + RPM*0.0015 + base * rpm_factor

    Args:
        original_map: dict with 'x_axis', 'y_axis', 'data' (2D array)
                      Axes can be in any order — RPM is auto-detected.
        max_boost: float, 0-3 bar
        aggressiveness: float, 0-135%
        turbo_type: 'small' or 'large'
        gen_low_load: bool, whether to generate low-load region

    Returns:
        dict with same structure as original_map but with new 'data' values
    """
    rpm_axis, load_axis, rpm_is_row = _get_axes(original_map)
    if not rpm_axis:
        rpm_axis = DEFAULT_KFMIRL_RPM
    if not load_axis:
        load_axis = DEFAULT_KFMIRL_LOAD

    # Clamp inputs
    max_boost = max(0.0, min(3.0, max_boost))
    aggressiveness = max(0.0, min(135.0, aggressiveness))

    # Core calculations from spreadsheet
    max_charge = 110.0 + max_boost * 66.7
    aggr_mult = aggressiveness / 100.0
    aggr_mult = max(0.0, min(1.35, aggr_mult))

    # $C$19 in the spreadsheet = last RPM in the axis (normalization constant)
    max_rpm = rpm_axis[-1] if rpm_axis else 6720.0

    rows = len(original_map['data'])
    cols = len(original_map['data'][0]) if rows > 0 else 0

    new_data = []

    for row_idx in range(rows):
        row_values = []

        for col_idx in range(cols):
            # Get RPM and load for this cell based on detected orientation
            if rpm_is_row:
                rpm = rpm_axis[row_idx] if row_idx < len(rpm_axis) else 3000
                load = load_axis[col_idx] if col_idx < len(load_axis) else 50
            else:
                rpm = rpm_axis[col_idx] if col_idx < len(rpm_axis) else 3000
                load = load_axis[row_idx] if row_idx < len(load_axis) else 50

            rpm_factor = _get_rpm_t_factor(rpm)
            original_value = original_map['data'][row_idx][col_idx]

            if load == 0:
                # Load 0% is always 0
                new_val = 0.0

            elif load <= 10:
                # Low load columns (5%, 10%)
                if gen_low_load:
                    # Spreadsheet: =IF(load=0, 0, load*2 + RPM*0.0015 + base * rpm_factor)
                    base = max_charge * (load / 100.0) - 10.0 + rpm / max_rpm * 10.0
                    new_val = load * 2.0 + rpm * 0.0015 + base * rpm_factor
                else:
                    new_val = original_value

            else:
                # Load >= 15%: main formula
                # base = max_charge * (load/100) - ($C$19/$C$19*10) + RPM/$C$19*10
                # $C$19/$C$19 = 1, so: base = max_charge*(load/100) - 10 + RPM/max_rpm*10
                base = max_charge * (load / 100.0) - 10.0 + rpm / max_rpm * 10.0
                col_factor = _get_load_factor(load, aggr_mult)
                # new = base * (1 + col_factor + rpm_factor)
                new_val = base * (1.0 + col_factor + rpm_factor)

                if not gen_low_load and load <= 15:
                    new_val = original_value

            row_values.append(round(new_val, 2))
        new_data.append(row_values)

    result = dict(original_map)
    result['data'] = new_data
    return result


# ============================================================================
# KFMIOP Calculation (Operating Point Fuel Correction)
# ============================================================================

def _calc_optimal_kfmiop_axis(new_kfmirl, num_axis_points=11):
    """
    Calculate optimal KFMIOP load axis values using the spreadsheet's VBA algorithm.

    KFMIOP is the inverse of KFMIRL (load → torque). The optimal axis places
    breakpoints at KFMIRL output values where the spacing between consecutive
    load columns is largest, giving best resolution where it matters most.

    Algorithm (from VBA KFMIOP_CALC1_suggest_axes):
    1. Find the RPM row with the largest sum of KFMIRL values (excl. load 0%)
    2. From that row, take the non-zero load column values (as integers)
    3. Compute spacing between consecutive values
    4. Keep the top N values with the largest spacing
    5. Sort by value → these are the optimal axis breakpoints

    Args:
        new_kfmirl: dict with calculated KFMIRL data
        num_axis_points: number of axis breakpoints (default 11 for standard KFMIOP)

    Returns:
        list of optimal load axis values (integers)
    """
    kfmirl_data = new_kfmirl['data']
    rpm_axis, load_axis, rpm_is_row = _get_axes(new_kfmirl)

    if rpm_is_row:
        num_rpm = len(kfmirl_data)
        num_load = len(kfmirl_data[0]) if num_rpm > 0 else 0
    else:
        num_load = len(kfmirl_data)
        num_rpm = len(kfmirl_data[0]) if num_load > 0 else 0

    # Step 1: Find RPM index with the largest sum (excluding load 0%)
    max_sum = -1
    max_sum_rpm_idx = 0
    for ri in range(num_rpm if rpm_is_row else num_rpm):
        s = 0
        for li in range(1, num_load):
            if rpm_is_row:
                s += int(round(kfmirl_data[ri][li]))
            else:
                s += int(round(kfmirl_data[li][ri]))
        if s > max_sum:
            max_sum = s
            max_sum_rpm_idx = ri

    # Step 2: Take non-zero load column values from that RPM (as integers)
    values_with_spacing = []
    non_zero_values = []
    for li in range(1, num_load):
        if rpm_is_row:
            non_zero_values.append(int(round(kfmirl_data[max_sum_rpm_idx][li])))
        else:
            non_zero_values.append(int(round(kfmirl_data[li][max_sum_rpm_idx])))

    for i, val in enumerate(non_zero_values):
        if i == 0:
            spacing = val  # distance from 0
        elif i == len(non_zero_values) - 1:
            spacing = 1000  # last column always gets priority
        else:
            spacing = val - non_zero_values[i - 1]
        values_with_spacing.append((val, spacing))

    # Step 3: Sort by spacing ascending, take the top num_axis_points
    values_with_spacing.sort(key=lambda x: x[1])
    top_values = [v[0] for v in values_with_spacing[-num_axis_points:]]

    # Step 4: Sort by value ascending → optimal axis
    top_values.sort()

    return top_values


def calc_kfmiop(original_kfmiop, new_kfmirl, original_kfmirl):
    """
    Calculate new KFMIOP using the spreadsheet's MATCH-based interpolation.

    KFMIOP is the mathematical inverse of KFMIRL (aliantauto.com guide).
    The spreadsheet approach:
    1. Compute optimal KFMIOP load axis from the new KFMIRL values
    2. For each KFMIRL RPM row, create a 100-point interpolation array
       (one per 1% load, spacing matching KFMIRL load axis differences)
    3. For each KFMIOP cell, MATCH the optimal axis value against the
       interpolation array. The 1-based position IS the torque percentage.

    Args:
        original_kfmiop: dict with map data from bin
        new_kfmirl: dict with calculated KFMIRL data
        original_kfmirl: dict with original KFMIRL data from bin

    Returns:
        dict with new KFMIOP data (includes updated y_axis with optimal values)
    """
    kfmiop_data = original_kfmiop['data']
    kfmiop_rows = len(kfmiop_data)
    kfmiop_cols = len(kfmiop_data[0]) if kfmiop_rows > 0 else 0

    # Detect KFMIOP axis orientation
    kfmiop_rpm, kfmiop_load, kfmiop_rpm_is_row = _get_axes(original_kfmiop)

    # KFMIRL data and detected axes
    kfmirl_rpm, kfmirl_load, kfmirl_rpm_is_row = _get_axes(new_kfmirl)

    # Number of load-axis breakpoints for optimal axis
    if kfmiop_rpm_is_row:
        num_load_points = kfmiop_cols
        num_rpm_points = kfmiop_rows
    else:
        num_load_points = kfmiop_rows
        num_rpm_points = kfmiop_cols

    # Calculate optimal KFMIOP load axis from the new KFMIRL
    optimal_axis = _calc_optimal_kfmiop_axis(new_kfmirl, num_axis_points=num_load_points)

    new_data = []

    for row_idx in range(kfmiop_rows):
        row_data = []

        for col_idx in range(kfmiop_cols):
            # Get RPM and load-axis value for this cell
            if kfmiop_rpm_is_row:
                rpm = kfmiop_rpm[row_idx] if row_idx < len(kfmiop_rpm) else 0
                load_axis_idx = col_idx
            else:
                rpm = kfmiop_rpm[col_idx] if col_idx < len(kfmiop_rpm) else 0
                load_axis_idx = row_idx

            kfmiop_load_val = optimal_axis[load_axis_idx] if load_axis_idx < len(optimal_axis) else 0

            # Get the KFMIRL slice at this RPM
            kfmirl_slice = _get_kfmirl_slice_at_rpm(new_kfmirl, rpm)

            # Create fine-grained interpolation matching load axis spacing
            interp = _create_load_interpolation(kfmirl_slice, kfmirl_load)

            # MATCH: find 1-based position where kfmiop_load_val falls
            match_pos = _match_value(kfmiop_load_val, interp)

            row_data.append(match_pos)

        new_data.append(row_data)

    result = dict(original_kfmiop)
    result['data'] = new_data
    # Update the load axis (whichever dimension it's on)
    if kfmiop_rpm_is_row:
        result['x_axis'] = [float(v) for v in optimal_axis]
    else:
        result['y_axis'] = [float(v) for v in optimal_axis]
    result['optimal_axis'] = optimal_axis
    return result


def _get_kfmirl_slice_at_rpm(kfmirl_map, target_rpm):
    """
    Get 1D slice of KFMIRL values across load axis at a given RPM, interpolating if needed.
    Handles both axis orientations.

    Returns:
        list of values across load breakpoints at the interpolated RPM position.
    """
    kfmirl_data = kfmirl_map['data']
    rpm_axis, load_axis, rpm_is_row = _get_axes(kfmirl_map)

    if not rpm_axis or not kfmirl_data:
        return kfmirl_data[0] if kfmirl_data else []

    n = len(rpm_axis)
    num_load = len(load_axis) if load_axis else (len(kfmirl_data[0]) if rpm_is_row else len(kfmirl_data))

    def _get_load_values(rpm_idx):
        """Extract load-axis values at a given RPM index."""
        if rpm_is_row:
            return list(kfmirl_data[rpm_idx])
        else:
            return [kfmirl_data[li][rpm_idx] for li in range(len(kfmirl_data))]

    if target_rpm <= rpm_axis[0]:
        return _get_load_values(0)
    if target_rpm >= rpm_axis[-1]:
        return _get_load_values(n - 1)

    for i in range(n - 1):
        if rpm_axis[i] <= target_rpm <= rpm_axis[i + 1]:
            frac = (target_rpm - rpm_axis[i]) / (rpm_axis[i + 1] - rpm_axis[i])
            vals_lo = _get_load_values(i)
            vals_hi = _get_load_values(i + 1)
            return [
                vals_lo[c] * (1 - frac) + vals_hi[c] * frac
                for c in range(len(vals_lo))
            ]

    return _get_load_values(0)


def _create_load_interpolation(kfmirl_row, load_axis):
    """
    Create a fine-grained interpolation of a KFMIRL row, matching the
    spreadsheet's scheme: spacing between breakpoints equals the load axis
    differences (e.g., 0->5=5 steps, 40->50=10 steps, 85->99=14 steps).
    This produces exactly 100 points (one per 1% load from 0 to 99).
    """
    if not kfmirl_row or not load_axis:
        return []

    n = len(kfmirl_row)
    result = []

    for i in range(n - 1):
        val_start = kfmirl_row[i]
        val_end = kfmirl_row[i + 1]
        steps = int(round(load_axis[i + 1] - load_axis[i]))
        if steps < 1:
            steps = 1

        for s in range(steps):
            frac = s / steps
            result.append(val_start + (val_end - val_start) * frac)

    # Add the final value
    result.append(kfmirl_row[-1])

    return result


def _match_value(lookup_val, sorted_array):
    """
    Excel MATCH equivalent with match_type=1.
    Returns the 1-based position of the largest value <= lookup_val.
    """
    if not sorted_array:
        return 0

    pos = 0
    for i, val in enumerate(sorted_array):
        if val <= lookup_val:
            pos = i + 1  # 1-based
        else:
            break

    # If lookup_val >= all values, return last position
    if lookup_val >= sorted_array[-1]:
        pos = len(sorted_array)

    return pos


def _interpolate_index(idx, src_count, dst_count):
    """Map an index from one grid size to another (returns float)."""
    if src_count <= 1:
        return 0.0
    return idx * (dst_count - 1) / (src_count - 1)


def _interpolate_2d(data, row_f, col_f):
    """Bilinear interpolation in a 2D array."""
    rows = len(data)
    cols = len(data[0]) if rows > 0 else 0

    row_f = max(0.0, min(row_f, rows - 1))
    col_f = max(0.0, min(col_f, cols - 1))

    r0 = int(math.floor(row_f))
    r1 = min(r0 + 1, rows - 1)
    c0 = int(math.floor(col_f))
    c1 = min(c0 + 1, cols - 1)

    fr = row_f - r0
    fc = col_f - c0

    v00 = data[r0][c0]
    v01 = data[r0][c1]
    v10 = data[r1][c0]
    v11 = data[r1][c1]

    return (v00 * (1 - fr) * (1 - fc) +
            v01 * (1 - fr) * fc +
            v10 * fr * (1 - fc) +
            v11 * fr * fc)


# ============================================================================
# KFZWOP1 & KFZWOP2 Calculation (Ignition Timing Reference)
# ============================================================================

def calc_kfzwop(original_kfzwop, new_kfmirl, original_kfmirl, smooth=True):
    """
    Calculate new KFZWOP values using the spreadsheet's KFZWOP|2 CONVERTER algorithm.

    KFZWOP shares its load axis with KFMIOP. When the KFMIOP load axis changes
    (to the optimal axis), KFZWOP must be re-interpolated to the new axis.

    Algorithm (from VBA Sheet9 Worksheet_Change):
    1. For each new load axis value, find the closest original load axis value
    2. Linear interpolation between original breakpoints (or extrapolation at edges)
    3. Optional SMOOTH pass: clamp vertical changes to 20% and horizontal to 10%

    Args:
        original_kfzwop: dict with original map data (load axis from bin)
        new_kfmirl: dict with new KFMIRL (used to compute optimal load axis)
        original_kfmirl: dict with original KFMIRL data from bin
        smooth: bool, whether to apply smoothing (default True)

    Returns:
        dict with new KFZWOP data (with updated load axis)
    """
    orig_data = original_kfzwop['data']
    zwop_rpm, zwop_load, zwop_rpm_is_row = _get_axes(original_kfzwop)
    orig_load_axis = zwop_load

    rows = len(orig_data)
    cols = len(orig_data[0]) if rows else 0

    if zwop_rpm_is_row:
        num_rpm = rows
        num_load = cols
    else:
        num_rpm = cols
        num_load = rows

    num_rpm_cols = num_rpm  # for smoothing loop counts
    num_load_rows = num_load

    # Compute new load axis from KFMIRL (same as KFMIOP optimal axis)
    new_load_axis = _calc_optimal_kfmiop_axis(new_kfmirl, num_axis_points=num_load)

    # Helper to get/set values by (load_idx, rpm_idx) regardless of data orientation
    def _get_orig(load_idx, rpm_idx):
        if zwop_rpm_is_row:
            return orig_data[rpm_idx][load_idx]
        else:
            return orig_data[load_idx][rpm_idx]

    # Build new values indexed as [load_idx][rpm_idx] (normalized)
    new_vals = []  # new_vals[load_idx][rpm_idx]

    for li in range(num_load):
        new_load = new_load_axis[li] if li < len(new_load_axis) else orig_load_axis[li]
        rpm_vals = []

        closest_idx = _closest_match_index(orig_load_axis, new_load)

        for ri in range(num_rpm):
            if closest_idx == 0:
                if orig_load_axis[closest_idx] < new_load:
                    prev_load = orig_load_axis[closest_idx]
                    next_load = orig_load_axis[closest_idx + 1]
                    prev_ign = _get_orig(closest_idx, ri)
                    next_ign = _get_orig(closest_idx + 1, ri)
                    new_val = prev_ign + (next_ign - prev_ign) / (next_load - prev_load) * (new_load - prev_load)
                elif orig_load_axis[closest_idx] > new_load:
                    prev_load = orig_load_axis[closest_idx]
                    next_load = orig_load_axis[closest_idx + 1]
                    prev_ign = _get_orig(closest_idx, ri)
                    next_ign = _get_orig(closest_idx + 1, ri)
                    new_val = prev_ign + (prev_ign - next_ign) / (next_load - prev_load) * (prev_load - new_load)
                else:
                    new_val = _get_orig(closest_idx, ri)

            elif closest_idx == num_load - 1:
                if orig_load_axis[-1] < new_load:
                    prev_load = orig_load_axis[-2]
                    next_load = orig_load_axis[-1]
                    prev_ign = _get_orig(num_load - 2, ri)
                    next_ign = _get_orig(num_load - 1, ri)
                    new_val = next_ign + (next_ign - prev_ign) / (next_load - prev_load) * (new_load - next_load)
                elif orig_load_axis[-1] > new_load:
                    prev_load = orig_load_axis[-2]
                    next_load = orig_load_axis[-1]
                    prev_ign = _get_orig(num_load - 2, ri)
                    next_ign = _get_orig(num_load - 1, ri)
                    new_val = prev_ign + (next_ign - prev_ign) / (next_load - prev_load) * (new_load - prev_load)
                else:
                    new_val = _get_orig(num_load - 1, ri)

            else:
                if orig_load_axis[closest_idx] < new_load:
                    prev_load = orig_load_axis[closest_idx]
                    next_load = orig_load_axis[closest_idx + 1]
                    prev_ign = _get_orig(closest_idx, ri)
                    next_ign = _get_orig(closest_idx + 1, ri)
                    new_val = prev_ign + (next_ign - prev_ign) / (next_load - prev_load) * (new_load - prev_load)
                elif orig_load_axis[closest_idx] > new_load:
                    prev_load = orig_load_axis[closest_idx - 1]
                    next_load = orig_load_axis[closest_idx]
                    prev_ign = _get_orig(closest_idx - 1, ri)
                    next_ign = _get_orig(closest_idx, ri)
                    new_val = prev_ign + (next_ign - prev_ign) / (next_load - prev_load) * (new_load - prev_load)
                else:
                    new_val = _get_orig(closest_idx, ri)

            rpm_vals.append(new_val)
        new_vals.append(rpm_vals)

    # SMOOTH pass (from VBA: DIFF_VERTICAL=20%, DIFF_HORIZONTAL=10%)
    if smooth:
        DIFF_VERTICAL = 20.0
        DIFF_HORIZONTAL = 10.0
        # Smoothing across load axis
        for ri in range(num_rpm):
            for li in range(1, num_load):
                diff_max = abs(new_vals[li - 1][ri]) / 100.0 * DIFF_VERTICAL
                diff_real = new_vals[li][ri] - new_vals[li - 1][ri]
                if abs(diff_real) > diff_max:
                    if diff_real > 0:
                        new_vals[li][ri] = new_vals[li - 1][ri] + diff_max
                    else:
                        new_vals[li][ri] = new_vals[li - 1][ri] - diff_max
        # Smoothing across RPM axis
        for li in range(num_load):
            for ri in range(1, num_rpm):
                diff_max = abs(new_vals[li][ri - 1]) / 100.0 * DIFF_HORIZONTAL
                diff_real = new_vals[li][ri] - new_vals[li][ri - 1]
                if abs(diff_real) > diff_max:
                    if diff_real > 0:
                        new_vals[li][ri] = new_vals[li][ri - 1] + diff_max
                    else:
                        new_vals[li][ri] = new_vals[li][ri - 1] - diff_max

    # Convert back to data[row][col] format matching original orientation
    new_data = [[0.0] * cols for _ in range(rows)]
    for li in range(num_load):
        for ri in range(num_rpm):
            val = round(new_vals[li][ri], 2)
            if zwop_rpm_is_row:
                new_data[ri][li] = val
            else:
                new_data[li][ri] = val

    result = dict(original_kfzwop)
    result['data'] = new_data
    # Update the load axis on whichever dimension it lives
    if zwop_rpm_is_row:
        result['x_axis'] = [float(v) for v in new_load_axis]
    else:
        result['y_axis'] = [float(v) for v in new_load_axis]
    return result


def _closest_match_index(arr, target):
    """Find the index of the closest value in arr to target (1-based in VBA, 0-based here)."""
    best_idx = 0
    best_diff = float('inf')
    for i, val in enumerate(arr):
        diff = abs(target - val)
        if diff < best_diff:
            best_diff = diff
            best_idx = i
    return best_idx


def _interpolate_1d_at(arr, pos):
    """Interpolate in a 1D array at a floating-point position."""
    if len(arr) == 0:
        return 0.0
    pos = max(0.0, min(pos, len(arr) - 1))
    i0 = int(math.floor(pos))
    i1 = min(i0 + 1, len(arr) - 1)
    frac = pos - i0
    return arr[i0] * (1 - frac) + arr[i1] * frac


def _get_map_max(data):
    """Get the maximum value in a 2D array."""
    max_val = float('-inf')
    for row in data:
        for val in row:
            if val > max_val:
                max_val = val
    return max_val


# ============================================================================
# LDRXN & LDRXNZK Calculation (Boost Request Limits)
# ============================================================================

# Default LDRXN RPM axis from spreadsheet
DEFAULT_LDRXN_RPM = [1000, 1500, 1750, 1950, 2250, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 5700]


def calc_ldrxn(new_kfmirl, original_ldrxn=None):
    """
    Calculate new LDRXN (max boost request) from the new KFMIRL map.

    For each RPM point in LDRXN:
    1. Interpolate KFMIRL column at that RPM
    2. Take the maximum value across all load rows
    3. LDRXN = ROUNDUP(MAX + 1, 0)

    Args:
        new_kfmirl: dict with calculated KFMIRL data
        original_ldrxn: dict with original LDRXN data (for axis reference)

    Returns:
        dict with LDRXN values (1D map, single row)
    """
    kfmirl_rpm, kfmirl_load, kfmirl_rpm_is_row = _get_axes(new_kfmirl)

    # Determine LDRXN RPM axis (1D map — detect from original)
    if original_ldrxn:
        ldrxn_rpm_axis, _, _ = _get_axes_1d(original_ldrxn)
        ldrxn_rpm = ldrxn_rpm_axis if ldrxn_rpm_axis else DEFAULT_LDRXN_RPM
    else:
        ldrxn_rpm = DEFAULT_LDRXN_RPM

    ldrxn_values = []

    for target_rpm in ldrxn_rpm:
        # Get all load values at this RPM
        load_slice = _get_kfmirl_slice_at_rpm(new_kfmirl, target_rpm)
        max_load_value = max(load_slice) if load_slice else 0
        # LDRXN = roundup(max + 1)
        ldrxn_val = math.ceil(max_load_value + 1)
        ldrxn_values.append(ldrxn_val)

    # Build result as a 1D map — preserve original axis naming
    if original_ldrxn:
        result = dict(original_ldrxn)
        result['data'] = [ldrxn_values]
        result['rows'] = 1
        result['cols'] = len(ldrxn_values)
    else:
        result = {
            'title': 'LDRXN',
            'x_axis': ldrxn_rpm,
            'y_axis': [],
            'x_units': 'RPM',
            'y_units': '',
            'z_units': '%',
            'data': [ldrxn_values],
            'rows': 1,
            'cols': len(ldrxn_values),
        }

    return result


def _get_axes_1d(map_data):
    """
    For 1D maps (single row), detect which axis has the RPM values.
    Returns (rpm_axis, other_axis, rpm_is_x).
    """
    x_axis = map_data.get('x_axis', [])
    y_axis = map_data.get('y_axis', [])
    x_units = map_data.get('x_units', '')
    y_units = map_data.get('y_units', '')

    if _is_rpm_units(x_units):
        return x_axis, y_axis, True
    if _is_rpm_units(y_units):
        return y_axis, x_axis, False

    # For 1D maps, the non-empty axis with values > 200 is likely RPM
    if x_axis and (not y_axis or len(y_axis) <= 1):
        return x_axis, y_axis, True
    if y_axis and (not x_axis or len(x_axis) <= 1):
        return y_axis, x_axis, False

    return x_axis, y_axis, True  # default


def calc_ldrxnzk(ldrxn_map):
    """
    Calculate LDRXNZK from LDRXN.
    LDRXNZK = ROUNDUP(LDRXN * 0.85, 0)  (15% reduction)

    Args:
        ldrxn_map: dict with LDRXN values

    Returns:
        dict with LDRXNZK values
    """
    ldrxn_data = ldrxn_map['data']

    new_data = []
    for row in ldrxn_data:
        new_row = [math.ceil(val * 0.85) for val in row]
        new_data.append(new_row)

    result = dict(ldrxn_map)
    result['title'] = 'LDRXNZK'
    result['data'] = new_data
    return result




# ============================================================================
# Utility Functions
# ============================================================================

def format_map_table(map_data, precision=1):
    """
    Format a map as a readable table string for display.

    Returns a string with aligned columns showing axis values and data.
    """
    data = map_data['data']
    x_axis = map_data.get('x_axis', [])
    y_axis = map_data.get('y_axis', [])
    x_units = map_data.get('x_units', '')
    y_units = map_data.get('y_units', '')

    if not data:
        return "(empty map)"

    rows = len(data)
    cols = len(data[0]) if rows > 0 else 0

    # Format values
    def fmt(v):
        return f"{v:.{precision}f}"

    lines = []

    # Header line with x-axis values
    if x_axis:
        header = f"{'':>8} |"
        for i in range(min(cols, len(x_axis))):
            header += f" {fmt(x_axis[i]):>8}"
        lines.append(header)
        lines.append("-" * len(header))

    # Data rows with y-axis values
    for r in range(rows):
        if y_axis and r < len(y_axis):
            line = f"{fmt(y_axis[r]):>8} |"
        else:
            line = f"{r:>8} |"
        for c in range(cols):
            line += f" {fmt(data[r][c]):>8}"
        lines.append(line)

    return "\n".join(lines)


def format_diff_table(original_map, new_map, precision=1):
    """
    Format a side-by-side diff showing changes between original and new map.

    Shows: original value -> new value (delta)
    """
    orig_data = original_map['data']
    new_data = new_map['data']
    x_axis = original_map.get('x_axis', [])
    y_axis = original_map.get('y_axis', [])

    if not orig_data or not new_data:
        return "(empty map)"

    rows = min(len(orig_data), len(new_data))
    cols = min(len(orig_data[0]), len(new_data[0])) if rows > 0 else 0

    def fmt(v):
        return f"{v:.{precision}f}"

    lines = []

    # Header
    if x_axis:
        header = f"{'':>8} |"
        for i in range(min(cols, len(x_axis))):
            header += f" {fmt(x_axis[i]):>14}"
        lines.append(header)
        lines.append("-" * len(header))

    for r in range(rows):
        if y_axis and r < len(y_axis):
            line = f"{fmt(y_axis[r]):>8} |"
        else:
            line = f"{r:>8} |"

        for c in range(cols):
            orig_v = orig_data[r][c]
            new_v = new_data[r][c]
            delta = new_v - orig_v

            if abs(delta) < 0.05:
                cell = f"{fmt(orig_v):>6}      "
            else:
                sign = "+" if delta >= 0 else ""
                cell = f"{fmt(orig_v)}→{fmt(new_v)}"

            line += f" {cell:>14}"
        lines.append(line)

    return "\n".join(lines)
