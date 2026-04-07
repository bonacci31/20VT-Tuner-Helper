"""
ME7 Fuel Enrichment Calculator — WOT & High-Load Enrichment

Adjusts fuel enrichment maps by a user-specified percentage for WOT
and high-load conditions. Richer mixture = cooler combustion = less
detonation risk at high boost.

Maps modified:
- LAMFA: Driver requested lambda (preventive enrichment at WOT)
- KFLBTS: EGT component protection lambda (reactive enrichment at high load)
- KFFDLBTS: Weighting factor for knock-based enrichment (stronger at high load)

ME7 enrichment priority: Final Lambda = MIN(lamfa_w, lamfawkr, lambts)
The richest (lowest lambda) source always wins.

References:
- s4wiki.com/wiki/Tuning — LAMFA, KFLBTS, KFFDLBTS formulas and interaction
- AliantAuto ME7.5 Stage 2 — enrichment strategy and seasonal calibration

Usage:
    python enrichment_calc.py <enrichment_pct> <lamfa.json> <kflbts.json> [kffdlbts.json]
"""

import json
import sys
import copy


RPM_UNIT_KEYWORDS = ['rpm', 'u/min', '1/min', 'upm', 'drehzahl', 'n [']


def _detect_threshold_axis(map_data):
    """
    Detect which axis is the threshold axis (torque%/load%) vs RPM.
    Returns (threshold_axis, rpm_axis, threshold_is_row).

    threshold_is_row=True: data[row][col] → threshold=y_axis[row], rpm=x_axis[col]
    threshold_is_row=False: data[row][col] → rpm=y_axis[row], threshold=x_axis[col]
    """
    x_units = map_data.get('x_units', '').lower().strip()
    y_units = map_data.get('y_units', '').lower().strip()
    x_axis = map_data.get('x_axis', [])
    y_axis = map_data.get('y_axis', [])

    x_is_rpm = any(kw in x_units for kw in RPM_UNIT_KEYWORDS)
    y_is_rpm = any(kw in y_units for kw in RPM_UNIT_KEYWORDS)

    if not x_is_rpm and not y_is_rpm:
        x_max = max(x_axis) if x_axis else 0
        y_max = max(y_axis) if y_axis else 0
        if y_max > 200 and y_max > x_max * 1.5:
            y_is_rpm = True
        elif x_max > 200 and x_max > y_max * 1.5:
            x_is_rpm = True

    if y_is_rpm:
        # y=rows=RPM, x=cols=threshold
        return x_axis, y_axis, False
    else:
        # y=rows=threshold, x=cols=RPM (original assumption)
        return y_axis, x_axis, True


def calc_lamfa_enrichment(original_map, enrichment_pct,
                          torque_threshold_pct=90.0, lambda_floor=0.65):
    """
    Enrich LAMFA (driver requested lambda) at WOT.

    LAMFA axes: torque request % and RPM (auto-detected orientation).
    Lower lambda = richer mixture.

    Args:
        original_map: dict with x_axis, y_axis, data (2D lambda values)
        enrichment_pct: float, percentage to enrich (e.g. 5.0 = 5% richer)
        torque_threshold_pct: float, only enrich cells at or above this torque %
        lambda_floor: float, minimum lambda value (never go below this)

    Returns:
        dict with calculated map data
    """
    x_axis = original_map['x_axis']
    y_axis = original_map['y_axis']
    data = original_map['data']
    rows = len(data)
    cols = len(data[0]) if rows > 0 else 0

    threshold_axis, rpm_axis, threshold_is_row = _detect_threshold_axis(original_map)

    multiplier = 1.0 - (enrichment_pct / 100.0)
    new_data = []
    cells_changed = 0
    cells_total = 0

    for row_idx in range(rows):
        row = []

        for col_idx in range(cols):
            # Get threshold value for this cell
            if threshold_is_row:
                torque_pct = threshold_axis[row_idx] if row_idx < len(threshold_axis) else 0
            else:
                torque_pct = threshold_axis[col_idx] if col_idx < len(threshold_axis) else 0

            apply_enrichment = torque_pct >= torque_threshold_pct
            orig_val = data[row_idx][col_idx]
            cells_total += 1

            if apply_enrichment:
                new_val = orig_val * multiplier
                new_val = max(lambda_floor, new_val)
                new_val = round(new_val, 4)
                if new_val != orig_val:
                    cells_changed += 1
            else:
                new_val = orig_val

            row.append(new_val)
        new_data.append(row)

    return {
        'map_name': 'LAMFA',
        'x_axis': x_axis,
        'y_axis': y_axis,
        'x_units': original_map.get('x_units', 'RPM'),
        'y_units': original_map.get('y_units', '%'),
        'z_units': original_map.get('z_units', ''),
        'data': new_data,
        'rows': rows,
        'cols': cols,
        'params': {
            'enrichment_pct': enrichment_pct,
            'multiplier': round(multiplier, 4),
            'torque_threshold_pct': torque_threshold_pct,
            'lambda_floor': lambda_floor,
            'cells_changed': cells_changed,
            'cells_total': cells_total,
        },
    }


def calc_kflbts_enrichment(original_map, enrichment_pct,
                           load_threshold_pct=50.0, lambda_floor=0.65):
    """
    Enrich KFLBTS (EGT component protection lambda) at high load.

    KFLBTS axes: cylinder load % and RPM (auto-detected orientation).
    Lower lambda = richer mixture = more protection.
    Only modifies cells above the load threshold.

    Args:
        original_map: dict with x_axis, y_axis, data (2D lambda values)
        enrichment_pct: float, percentage to enrich (e.g. 5.0 = 5% richer)
        load_threshold_pct: float, only enrich cells at or above this load %
        lambda_floor: float, minimum lambda value

    Returns:
        dict with calculated map data
    """
    x_axis = original_map['x_axis']
    y_axis = original_map['y_axis']
    data = original_map['data']
    rows = len(data)
    cols = len(data[0]) if rows > 0 else 0

    threshold_axis, rpm_axis, threshold_is_row = _detect_threshold_axis(original_map)

    multiplier = 1.0 - (enrichment_pct / 100.0)
    new_data = []
    cells_changed = 0
    cells_total = 0

    for row_idx in range(rows):
        row = []

        for col_idx in range(cols):
            if threshold_is_row:
                load_pct = threshold_axis[row_idx] if row_idx < len(threshold_axis) else 0
            else:
                load_pct = threshold_axis[col_idx] if col_idx < len(threshold_axis) else 0

            apply_enrichment = load_pct >= load_threshold_pct
            orig_val = data[row_idx][col_idx]
            cells_total += 1

            if apply_enrichment:
                new_val = orig_val * multiplier
                new_val = max(lambda_floor, new_val)
                new_val = round(new_val, 4)
                if new_val != orig_val:
                    cells_changed += 1
            else:
                new_val = orig_val

            row.append(new_val)
        new_data.append(row)

    return {
        'map_name': 'KFLBTS',
        'x_axis': x_axis,
        'y_axis': y_axis,
        'x_units': original_map.get('x_units', 'RPM'),
        'y_units': original_map.get('y_units', 'Load'),
        'z_units': original_map.get('z_units', '%'),
        'data': new_data,
        'rows': rows,
        'cols': cols,
        'params': {
            'enrichment_pct': enrichment_pct,
            'multiplier': round(multiplier, 4),
            'load_threshold_pct': load_threshold_pct,
            'lambda_floor': lambda_floor,
            'cells_changed': cells_changed,
            'cells_total': cells_total,
        },
    }


def calc_kffdlbts_enrichment(original_map, enrichment_pct,
                              load_threshold_pct=50.0, factor_cap=2.0):
    """
    Increase KFFDLBTS (knock-based enrichment weighting) at high load.

    KFFDLBTS axes: load % and RPM (auto-detected orientation).
    Higher factor = stronger enrichment when timing is retarded (knock).
    Formula: lambts = (KFLBTS + KFDLBTS × KFFDLBTS) × FBSTABGM

    Args:
        original_map: dict with x_axis, y_axis, data (2D factor values)
        enrichment_pct: float, percentage to increase factor (e.g. 5.0 = 5% stronger)
        load_threshold_pct: float, only increase cells at or above this load %
        factor_cap: float, maximum factor value (no benefit beyond ~2.0)

    Returns:
        dict with calculated map data
    """
    x_axis = original_map['x_axis']
    y_axis = original_map['y_axis']
    data = original_map['data']
    rows = len(data)
    cols = len(data[0]) if rows > 0 else 0

    threshold_axis, rpm_axis, threshold_is_row = _detect_threshold_axis(original_map)

    multiplier = 1.0 + (enrichment_pct / 100.0)
    new_data = []
    cells_changed = 0
    cells_total = 0

    for row_idx in range(rows):
        row = []

        for col_idx in range(cols):
            if threshold_is_row:
                load_pct = threshold_axis[row_idx] if row_idx < len(threshold_axis) else 0
            else:
                load_pct = threshold_axis[col_idx] if col_idx < len(threshold_axis) else 0

            apply_enrichment = load_pct >= load_threshold_pct
            orig_val = data[row_idx][col_idx]
            cells_total += 1

            if apply_enrichment and orig_val > 0:
                new_val = orig_val * multiplier
                new_val = min(factor_cap, new_val)
                new_val = round(new_val, 4)
                if new_val != orig_val:
                    cells_changed += 1
            else:
                new_val = orig_val

            row.append(new_val)
        new_data.append(row)

    return {
        'map_name': 'KFFDLBTS',
        'x_axis': x_axis,
        'y_axis': y_axis,
        'x_units': original_map.get('x_units', 'RPM'),
        'y_units': original_map.get('y_units', '%'),
        'z_units': original_map.get('z_units', '%'),
        'data': new_data,
        'rows': rows,
        'cols': cols,
        'params': {
            'enrichment_pct': enrichment_pct,
            'multiplier': round(multiplier, 4),
            'load_threshold_pct': load_threshold_pct,
            'factor_cap': factor_cap,
            'cells_changed': cells_changed,
            'cells_total': cells_total,
        },
    }


def calc_enrichment(enrichment_pct, lamfa_map, kflbts_map,
                    kffdlbts_map=None,
                    lamfa_threshold_pct=90.0,
                    kflbts_threshold_load=50.0,
                    lambda_floor=0.65):
    """
    Calculate all enrichment maps at once.

    Args:
        enrichment_pct: float, enrichment percentage (e.g. 5.0)
        lamfa_map: dict, original LAMFA map
        kflbts_map: dict, original KFLBTS map
        kffdlbts_map: dict or None, original KFFDLBTS map
        lamfa_threshold_pct: float, torque threshold for LAMFA
        kflbts_threshold_load: float, load threshold for KFLBTS/KFFDLBTS
        lambda_floor: float, minimum lambda

    Returns:
        dict with 'lamfa', 'kflbts', 'kffdlbts' sub-results
    """
    result = {
        'enrichment_pct': enrichment_pct,
        'lamfa': calc_lamfa_enrichment(
            lamfa_map, enrichment_pct, lamfa_threshold_pct, lambda_floor
        ),
        'kflbts': calc_kflbts_enrichment(
            kflbts_map, enrichment_pct, kflbts_threshold_load, lambda_floor
        ),
    }

    if kffdlbts_map:
        result['kffdlbts'] = calc_kffdlbts_enrichment(
            kffdlbts_map, enrichment_pct, kflbts_threshold_load
        )

    return result


def main():
    if len(sys.argv) < 4:
        print(json.dumps({
            "usage": "python enrichment_calc.py <enrichment_pct> <lamfa.json> <kflbts.json> [kffdlbts.json]",
            "examples": [
                "python enrichment_calc.py 5 lamfa.json kflbts.json",
                "python enrichment_calc.py 10 lamfa.json kflbts.json kffdlbts.json",
            ],
            "params": {
                "enrichment_pct": "Enrichment percentage (e.g. 5 = 5% richer at WOT/high load)",
                "lamfa.json": "JSON file with original LAMFA map data",
                "kflbts.json": "JSON file with original KFLBTS map data",
                "kffdlbts.json": "Optional JSON with original KFFDLBTS map data",
            },
            "notes": [
                "LAMFA: enriched at torque request >= 90%",
                "KFLBTS: enriched at cylinder load >= 50%",
                "KFFDLBTS: factor increased at load >= 50%",
                "Lambda floor: 0.65 (AFR ~9.5, safety limit)",
            ]
        }, indent=2))
        return

    enrichment_pct = float(sys.argv[1])

    with open(sys.argv[2], 'r') as f:
        lamfa_map = json.load(f)
    with open(sys.argv[3], 'r') as f:
        kflbts_map = json.load(f)

    kffdlbts_map = None
    if len(sys.argv) > 4:
        with open(sys.argv[4], 'r') as f:
            kffdlbts_map = json.load(f)

    result = calc_enrichment(enrichment_pct, lamfa_map, kflbts_map, kffdlbts_map)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
