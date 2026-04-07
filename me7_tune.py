"""
ME7 Tuner CLI Orchestrator

Main entry point called by the Claude Code skill via Bash.
Outputs JSON for Claude to parse, format, and present to the user.

Usage:
    python me7_tune.py <xdf_path> <bin_path> <action> [args...]

Actions:
    list_maps                           - List all maps in XDF
    read_map <map_name>                 - Read a map from the bin file
    info_map <map_name>                 - Get map structure info
    calc_kfmirl <boost> <aggr> <turbo> <low_load>  - Calculate new KFMIRL
    calc_kfmiop                         - Calculate new KFMIOP (needs prior KFMIRL calc)
    calc_kfzwop <map_name>              - Calculate new KFZWOP1 or KFZWOP2
    calc_ldrxn                          - Calculate new LDRXN and LDRXNZK
    write_map <map_name> <json_file>    - Write calculated map to bin buffer
    save_bin <output_path>              - Save modified bin to new file
    full_read                           - Read all target maps at once
"""

import sys
import os
import json
import copy

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xdf_parser import parse_xdf, find_map, list_maps, get_map_info
from bin_handler import read_bin, save_bin, read_map_data, write_map_data
from tuning_calc import (
    calc_kfmirl, calc_kfmiop, calc_kfzwop,
    calc_ldrxn, calc_ldrxnzk,
    format_map_table, format_diff_table
)


# Target map names we look for in the XDF
TARGET_MAPS = ['KFMIRL', 'KFMIOP', 'KFZWOP', 'KFZWOP2', 'LDRXN', 'LDRXNZK']

# Session state file for storing intermediate calculations
STATE_FILE = None


def get_state_path(bin_path):
    """Get the state file path based on the bin file."""
    return bin_path + '.me7state.json'


def load_state(bin_path):
    """Load session state (stored calculated maps)."""
    state_path = get_state_path(bin_path)
    if os.path.exists(state_path):
        with open(state_path, 'r') as f:
            return json.load(f)
    return {}


def save_state(bin_path, state):
    """Save session state."""
    state_path = get_state_path(bin_path)
    with open(state_path, 'w') as f:
        json.dump(state, f)


def clean_state(bin_path):
    """Remove state file."""
    state_path = get_state_path(bin_path)
    if os.path.exists(state_path):
        os.remove(state_path)


def find_target_map(xdf_data, name):
    """
    Find a target map with flexible name matching.
    ME7 XDFs may name maps slightly differently (e.g., KFZWOP1 vs KFZWOP).
    """
    # Try exact
    table = find_map(xdf_data, name)
    if table:
        return table

    # Try common variations
    variations = [
        name,
        name + '1',           # KFZWOP -> KFZWOP1
        name.rstrip('12'),    # KFZWOP1 -> KFZWOP
        name + ' ',           # trailing space
        name.replace('_', ''),
    ]
    for var in variations:
        table = find_map(xdf_data, var)
        if table:
            return table

    return None


def cmd_list_maps(xdf_data):
    """List all maps and highlight target maps."""
    all_maps = list_maps(xdf_data)
    target_found = {}

    for target in TARGET_MAPS:
        table = find_target_map(xdf_data, target)
        if table:
            target_found[target] = table['title']

    return {
        'total_maps': len(all_maps),
        'all_maps': all_maps,
        'target_maps': target_found,
        'missing_maps': [t for t in TARGET_MAPS if t not in target_found],
    }


def cmd_read_map(xdf_data, bin_data, base_offset, map_name):
    """Read a single map from the bin file."""
    table = find_target_map(xdf_data, map_name)
    if not table:
        return {'error': f"Map '{map_name}' not found in XDF"}

    map_data = read_map_data(bin_data, table, base_offset)
    return map_data


def cmd_info_map(xdf_data, map_name):
    """Get structural info about a map."""
    info = get_map_info(xdf_data, map_name)
    if not info:
        return {'error': f"Map '{map_name}' not found in XDF"}
    return info


def cmd_full_read(xdf_data, bin_data, base_offset):
    """Read all target maps at once."""
    results = {}
    for name in TARGET_MAPS:
        table = find_target_map(xdf_data, name)
        if table:
            map_data = read_map_data(bin_data, table, base_offset)
            results[name] = {
                'found': True,
                'actual_title': table['title'],
                'data': map_data,
            }
        else:
            results[name] = {'found': False}
    return results


def cmd_calc_kfmirl(xdf_data, bin_data, base_offset, boost, aggr, turbo, low_load, state):
    """Calculate new KFMIRL and store in state."""
    table = find_target_map(xdf_data, 'KFMIRL')
    if not table:
        return {'error': "KFMIRL not found in XDF"}

    original = read_map_data(bin_data, table, base_offset)
    gen_low = low_load.lower() in ('yes', 'true', '1', 'y')

    new_map = calc_kfmirl(original, boost, aggr, turbo, gen_low)

    # Store in state for subsequent calculations
    state['original_kfmirl'] = original
    state['new_kfmirl'] = new_map

    return {
        'map_name': 'KFMIRL',
        'actual_title': table['title'],
        'original': original,
        'calculated': new_map,
        'params': {
            'max_boost': boost,
            'aggressiveness': aggr,
            'turbo_type': turbo,
            'gen_low_load': gen_low,
            'max_charge': 110 + boost * 66.7,
        },
    }


def cmd_calc_kfmiop(xdf_data, bin_data, base_offset, state):
    """Calculate new KFMIOP using stored KFMIRL state."""
    if 'new_kfmirl' not in state or 'original_kfmirl' not in state:
        return {'error': "KFMIRL must be calculated first (run calc_kfmirl)"}

    table = find_target_map(xdf_data, 'KFMIOP')
    if not table:
        return {'error': "KFMIOP not found in XDF"}

    original = read_map_data(bin_data, table, base_offset)
    new_map = calc_kfmiop(original, state['new_kfmirl'], state['original_kfmirl'])

    state['original_kfmiop'] = original
    state['new_kfmiop'] = new_map

    return {
        'map_name': 'KFMIOP',
        'actual_title': table['title'],
        'original': original,
        'calculated': new_map,
    }


def _fix_kfzwop_data(xdf_data, bin_data, base_offset, original, map_name_prefix):
    """
    Fix KFZWOP/KFZWOP2 data read from the bin.

    The XDF parser doesn't resolve linked axes, so y_axis comes back as indices
    [0,1,2,...]. Also, the XDF says 11 rows x 16 cols but the bin stores the data
    as 16 RPM rows x 11 load cols, causing values to wrap when read as 11x16.

    Fix: read the actual load axis from the linked axis table, reshape the flat
    data as 16x11 (RPM x Load), then transpose to 11x16 (Load x RPM).
    """
    # Try to read the linked load axis table
    # KFZWOP2 shares axes with KFZWOP, so try without trailing digits too
    base_name = map_name_prefix.rstrip('0123456789')
    load_axis_names = [
        f'({map_name_prefix}) - Load Axis',
        f'({base_name}) - Load Axis',
        f'(KZ{map_name_prefix[2:]}) - Load Axis',  # handle typo KZFWOP
        f'(KZ{base_name[2:]}) - Load Axis',
    ]
    load_axis_values = None
    for name in load_axis_names:
        load_table = xdf_data['tables'].get(name)
        if load_table:
            load_data = read_map_data(bin_data, load_table, base_offset)
            load_axis_values = load_data['data'][0] if load_data['data'] else None
            break

    # Try to read the linked RPM axis table
    rpm_axis_names = [
        f'({map_name_prefix}) - RPM Axis',
        f'({base_name}) - RPM Axis',
    ]
    rpm_axis_values = None
    for name in rpm_axis_names:
        rpm_table = xdf_data['tables'].get(name)
        if rpm_table:
            rpm_data = read_map_data(bin_data, rpm_table, base_offset)
            rpm_axis_values = rpm_data['data'][0] if rpm_data['data'] else None
            break

    if load_axis_values is None:
        return original  # Can't fix without the actual load axis

    num_load = len(load_axis_values)  # 11
    num_rpm = len(rpm_axis_values) if rpm_axis_values else original['cols']  # 16

    # Flatten the misread data
    flat = []
    for row in original['data']:
        flat.extend(row)

    # Reshape as num_rpm x num_load (actual bin layout)
    if len(flat) == num_rpm * num_load:
        rpm_by_load = []
        for r in range(num_rpm):
            rpm_by_load.append(flat[r * num_load:(r + 1) * num_load])

        # Transpose to num_load x num_rpm (load rows x RPM cols)
        load_by_rpm = []
        for l in range(num_load):
            row = [rpm_by_load[r][l] for r in range(num_rpm)]
            load_by_rpm.append(row)

        original['data'] = load_by_rpm
        original['rows'] = num_load
        original['cols'] = num_rpm

    # Round load axis to integers to match WinOLS/TunerPro display precision
    original['y_axis'] = [round(v) for v in load_axis_values]
    if rpm_axis_values:
        original['x_axis'] = [round(v) for v in rpm_axis_values]

    return original


def cmd_calc_kfzwop(xdf_data, bin_data, base_offset, map_name, state):
    """Calculate new KFZWOP1 or KFZWOP2."""
    if 'new_kfmirl' not in state or 'original_kfmirl' not in state:
        return {'error': "KFMIRL must be calculated first"}

    table = find_target_map(xdf_data, map_name)
    if not table:
        return {'error': f"Map '{map_name}' not found in XDF"}

    original = read_map_data(bin_data, table, base_offset)

    # Fix transposition and linked axis issues
    original = _fix_kfzwop_data(xdf_data, bin_data, base_offset, original, map_name)

    new_map = calc_kfzwop(original, state['new_kfmirl'], state['original_kfmirl'])

    state[f'original_{map_name.lower()}'] = original
    state[f'new_{map_name.lower()}'] = new_map

    return {
        'map_name': map_name,
        'actual_title': table['title'],
        'original': original,
        'calculated': new_map,
    }


def cmd_calc_ldrxn(xdf_data, bin_data, base_offset, state):
    """Calculate new LDRXN and LDRXNZK."""
    if 'new_kfmirl' not in state:
        return {'error': "KFMIRL must be calculated first"}

    # Read original LDRXN for axis reference
    ldrxn_table = find_target_map(xdf_data, 'LDRXN')
    original_ldrxn = None
    if ldrxn_table:
        original_ldrxn = read_map_data(bin_data, ldrxn_table, base_offset)

    ldrxnzk_table = find_target_map(xdf_data, 'LDRXNZK')
    original_ldrxnzk = None
    if ldrxnzk_table:
        original_ldrxnzk = read_map_data(bin_data, ldrxnzk_table, base_offset)

    new_ldrxn = calc_ldrxn(state['new_kfmirl'], original_ldrxn)
    new_ldrxnzk = calc_ldrxnzk(new_ldrxn)

    state['original_ldrxn'] = original_ldrxn
    state['new_ldrxn'] = new_ldrxn
    state['original_ldrxnzk'] = original_ldrxnzk
    state['new_ldrxnzk'] = new_ldrxnzk

    return {
        'ldrxn': {
            'map_name': 'LDRXN',
            'actual_title': ldrxn_table['title'] if ldrxn_table else 'LDRXN',
            'original': original_ldrxn,
            'calculated': new_ldrxn,
        },
        'ldrxnzk': {
            'map_name': 'LDRXNZK',
            'actual_title': ldrxnzk_table['title'] if ldrxnzk_table else 'LDRXNZK',
            'original': original_ldrxnzk,
            'calculated': new_ldrxnzk,
        },
    }


def cmd_write_map(xdf_data, bin_data, base_offset, map_name, json_file):
    """Write calculated map values to the bin data buffer."""
    table = find_target_map(xdf_data, map_name)
    if not table:
        return {'error': f"Map '{map_name}' not found in XDF"}

    with open(json_file, 'r') as f:
        new_values = json.load(f)

    if isinstance(new_values, dict) and 'data' in new_values:
        new_values = new_values['data']

    write_map_data(bin_data, table, new_values, base_offset)
    return {'success': True, 'map_name': map_name, 'message': f"Written {map_name} to bin buffer"}


def cmd_write_map_from_state(xdf_data, bin_data, base_offset, map_name, state):
    """Write a map from session state to the bin data buffer."""
    state_key = f'new_{map_name.lower()}'
    if state_key not in state:
        return {'error': f"No calculated data for '{map_name}' in state. Calculate it first."}

    new_map = state[state_key]
    table = find_target_map(xdf_data, map_name)
    if not table:
        return {'error': f"Map '{map_name}' not found in XDF"}

    new_values = new_map['data'] if isinstance(new_map, dict) else new_map
    write_map_data(bin_data, table, new_values, base_offset)
    return {'success': True, 'map_name': map_name, 'message': f"Written {map_name} to bin buffer"}


def cmd_save_bin(bin_data, output_path):
    """Save the modified bin to a new file."""
    save_bin(bin_data, output_path)
    return {'success': True, 'output_path': output_path, 'size': len(bin_data)}


def main():
    if len(sys.argv) < 4:
        print(json.dumps({
            'error': 'Usage: python me7_tune.py <xdf_path> <bin_path> <action> [args...]',
            'actions': [
                'list_maps', 'read_map', 'info_map', 'full_read',
                'calc_kfmirl', 'calc_kfmiop', 'calc_kfzwop', 'calc_ldrxn',
                'write_map', 'apply_map', 'save_bin', 'clean_state'
            ]
        }))
        sys.exit(1)

    xdf_path = sys.argv[1]
    bin_path = sys.argv[2]
    action = sys.argv[3]

    # Parse XDF
    try:
        xdf_data = parse_xdf(xdf_path)
    except Exception as e:
        print(json.dumps({'error': f'Failed to parse XDF: {str(e)}'}))
        sys.exit(1)

    base_offset = xdf_data['header'].get('base_offset', 0) - xdf_data['header'].get('base_subtract', 0)

    # Read bin
    try:
        bin_data = read_bin(bin_path)
    except Exception as e:
        print(json.dumps({'error': f'Failed to read bin: {str(e)}'}))
        sys.exit(1)

    # Load state
    state = load_state(bin_path)

    result = None

    try:
        if action == 'list_maps':
            result = cmd_list_maps(xdf_data)

        elif action == 'read_map':
            if len(sys.argv) < 5:
                result = {'error': 'Usage: read_map <map_name>'}
            else:
                result = cmd_read_map(xdf_data, bin_data, base_offset, sys.argv[4])

        elif action == 'info_map':
            if len(sys.argv) < 5:
                result = {'error': 'Usage: info_map <map_name>'}
            else:
                result = cmd_info_map(xdf_data, sys.argv[4])

        elif action == 'full_read':
            result = cmd_full_read(xdf_data, bin_data, base_offset)

        elif action == 'calc_kfmirl':
            if len(sys.argv) < 8:
                result = {'error': 'Usage: calc_kfmirl <boost> <aggressiveness> <turbo_type> <low_load>'}
            else:
                boost = float(sys.argv[4])
                aggr = float(sys.argv[5])
                turbo = sys.argv[6]
                low_load = sys.argv[7]
                result = cmd_calc_kfmirl(xdf_data, bin_data, base_offset, boost, aggr, turbo, low_load, state)
                save_state(bin_path, state)

        elif action == 'calc_kfmiop':
            result = cmd_calc_kfmiop(xdf_data, bin_data, base_offset, state)
            save_state(bin_path, state)

        elif action == 'calc_kfzwop':
            if len(sys.argv) < 5:
                map_name = 'KFZWOP'
            else:
                map_name = sys.argv[4]
            result = cmd_calc_kfzwop(xdf_data, bin_data, base_offset, map_name, state)
            save_state(bin_path, state)

        elif action == 'calc_ldrxn':
            result = cmd_calc_ldrxn(xdf_data, bin_data, base_offset, state)
            save_state(bin_path, state)

        elif action == 'write_map':
            if len(sys.argv) < 6:
                result = {'error': 'Usage: write_map <map_name> <json_file>'}
            else:
                result = cmd_write_map(xdf_data, bin_data, base_offset, sys.argv[4], sys.argv[5])
                if result.get('success'):
                    # Save modified bin back to a temp location
                    temp_path = bin_path + '.modified'
                    save_bin(bin_data, temp_path)
                    result['temp_bin'] = temp_path

        elif action == 'apply_map':
            if len(sys.argv) < 5:
                result = {'error': 'Usage: apply_map <map_name>'}
            else:
                result = cmd_write_map_from_state(xdf_data, bin_data, base_offset, sys.argv[4], state)
                if result.get('success'):
                    temp_path = bin_path + '.modified'
                    save_bin(bin_data, temp_path)
                    result['temp_bin'] = temp_path

        elif action == 'save_bin':
            if len(sys.argv) < 5:
                # Default output name
                base_name = os.path.splitext(bin_path)[0]
                output_path = base_name + '_tuned.bin'
            else:
                output_path = sys.argv[4]

            # If there's a modified bin from apply_map, use that
            temp_path = bin_path + '.modified'
            if os.path.exists(temp_path):
                with open(temp_path, 'rb') as f:
                    final_data = bytearray(f.read())
                result = cmd_save_bin(final_data, output_path)
                os.remove(temp_path)
            else:
                result = cmd_save_bin(bin_data, output_path)

        elif action == 'clean_state':
            clean_state(bin_path)
            temp_path = bin_path + '.modified'
            if os.path.exists(temp_path):
                os.remove(temp_path)
            result = {'success': True, 'message': 'State cleaned'}

        else:
            result = {'error': f"Unknown action: {action}"}

    except Exception as e:
        result = {'error': f"Action '{action}' failed: {str(e)}"}
        import traceback
        result['traceback'] = traceback.format_exc()

    print(json.dumps(result, indent=2, default=str))


if __name__ == '__main__':
    main()
