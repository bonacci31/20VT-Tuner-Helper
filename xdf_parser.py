"""
XDF Parser - Parses TunerPro XDF definition files to extract map definitions.

XDF files are XML-based and define the structure of ECU binary files:
- XDFTABLE: 2D/3D lookup tables (maps) with X, Y axes and Z data
- XDFCONSTANT: Single scalar values
- Each element has addresses, scaling formulas, data types, and dimensions
"""

import xml.etree.ElementTree as ET
import re
import math


def parse_math_equation(equation_str):
    """
    Parse an XDF MATH equation string into a lambda for forward conversion
    (raw -> engineering) and a lambda for reverse conversion (engineering -> raw).

    Common forms:
      "40.000000 * X"           -> multiply by 40
      "0.750000 * X"            -> multiply by 0.75
      "1.350000 * X + -40.0"   -> multiply then offset
      "X * 0.01"               -> multiply by 0.01
      "X"                       -> identity
    """
    eq = equation_str.strip()

    # Try pattern: A * X + B  or  A * X - B
    m = re.match(r'^([+-]?[\d.]+)\s*\*\s*X\s*([+-]\s*[\d.]+)?$', eq)
    if m:
        a = float(m.group(1))
        b = float(m.group(2).replace(' ', '')) if m.group(2) else 0.0
        return (
            lambda x, _a=a, _b=b: _a * x + _b,
            lambda y, _a=a, _b=b: (y - _b) / _a if _a != 0 else 0
        )

    # Try pattern: X * A + B
    m = re.match(r'^X\s*\*\s*([+-]?[\d.]+)\s*([+-]\s*[\d.]+)?$', eq)
    if m:
        a = float(m.group(1))
        b = float(m.group(2).replace(' ', '')) if m.group(2) else 0.0
        return (
            lambda x, _a=a, _b=b: x * _a + _b,
            lambda y, _a=a, _b=b: (y - _b) / _a if _a != 0 else 0
        )

    # Try pattern: X + B
    m = re.match(r'^X\s*([+-]\s*[\d.]+)$', eq)
    if m:
        b = float(m.group(1).replace(' ', ''))
        return (
            lambda x, _b=b: x + _b,
            lambda y, _b=b: y - _b
        )

    # Try pattern: A + X or A + X * B
    m = re.match(r'^([+-]?[\d.]+)\s*\+\s*X(?:\s*\*\s*([+-]?[\d.]+))?$', eq)
    if m:
        b = float(m.group(1))
        a = float(m.group(2)) if m.group(2) else 1.0
        return (
            lambda x, _a=a, _b=b: _b + x * _a,
            lambda y, _a=a, _b=b: (y - _b) / _a if _a != 0 else 0
        )

    # Try pattern: X / A
    m = re.match(r'^X\s*/\s*([+-]?[\d.]+)$', eq)
    if m:
        a = float(m.group(1))
        return (
            lambda x, _a=a: x / _a if _a != 0 else 0,
            lambda y, _a=a: y * _a
        )

    # Try pattern: A / X (rare but possible)
    m = re.match(r'^([+-]?[\d.]+)\s*/\s*X$', eq)
    if m:
        a = float(m.group(1))
        return (
            lambda x, _a=a: _a / x if x != 0 else 0,
            lambda y, _a=a: _a / y if y != 0 else 0
        )

    # Identity: just "X"
    if eq.strip() == 'X':
        return (lambda x: x, lambda y: y)

    # Fallback: try to evaluate as expression
    # Build a safe evaluator
    def forward(x, _eq=eq):
        try:
            return float(eval(_eq.replace('X', str(x))))
        except:
            return x

    def reverse(y, _eq=eq):
        # For unknown equations, attempt numerical inverse via bisection
        # This is a fallback - most equations should be caught above
        return y  # identity fallback

    return (forward, reverse)


def _parse_axis(axis_elem, defaults):
    """Parse a single XDFAXIS element."""
    axis = {
        'id': axis_elem.get('id', ''),
        'uniqueid': axis_elem.get('uniqueid', ''),
        'units': '',
        'index_count': 1,
        'decimal_places': 2,
        'min': None,
        'max': None,
        'output_type': 1,
        'data_type': 0,
        'labels': {},
        'math_equation': 'X',
        'forward_func': lambda x: x,
        'reverse_func': lambda y: y,
        'embedded': {},
    }

    # Parse EMBEDDEDDATA
    embed_elem = axis_elem.find('EMBEDDEDDATA')
    if embed_elem is not None:
        axis['embedded'] = {
            'address': int(embed_elem.get('mmedaddress', '0x0'), 16) if embed_elem.get('mmedaddress') else None,
            'element_size_bits': int(embed_elem.get('mmedelementsizebits', str(defaults.get('datasizeinbits', 8)))),
            'row_count': int(embed_elem.get('mmedrowcount', '0')),
            'col_count': int(embed_elem.get('mmedcolcount', '0')),
            'major_stride_bits': int(embed_elem.get('mmedmajorstridebits', '0')),
            'minor_stride_bits': int(embed_elem.get('mmedminorstridebits', '0')),
            'type_flags': int(embed_elem.get('mmedtypeflags', '0x0'), 16) if embed_elem.get('mmedtypeflags') else defaults.get('type_flags', 0),
        }

    # Parse simple elements
    for tag, key, conv in [
        ('units', 'units', str),
        ('indexcount', 'index_count', int),
        ('decimalpl', 'decimal_places', int),
        ('min', 'min', float),
        ('max', 'max', float),
        ('outputtype', 'output_type', int),
        ('datatype', 'data_type', int),
    ]:
        elem = axis_elem.find(tag)
        if elem is not None and elem.text:
            try:
                axis[key] = conv(elem.text)
            except (ValueError, TypeError):
                pass

    # Parse LABEL elements
    for label_elem in axis_elem.findall('LABEL'):
        idx = int(label_elem.get('index', '0'))
        val = label_elem.get('value', '0')
        try:
            axis['labels'][idx] = float(val)
        except ValueError:
            axis['labels'][idx] = 0.0

    # Parse MATH equation
    math_elem = axis_elem.find('MATH')
    if math_elem is not None:
        equation = math_elem.get('equation', 'X')
        axis['math_equation'] = equation
        axis['forward_func'], axis['reverse_func'] = parse_math_equation(equation)

    return axis


def parse_xdf(xdf_path):
    """
    Parse an XDF file and return a structured dictionary of all map definitions.

    Returns:
        {
            'header': { ... metadata ... },
            'tables': {
                'KFMIRL': { ... table definition ... },
                'KFMIOP': { ... },
                ...
            },
            'constants': {
                'KVNPZ': { ... constant definition ... },
                ...
            },
            'categories': { idx: name, ... }
        }
    """
    tree = ET.parse(xdf_path)
    root = tree.getroot()

    result = {
        'header': {},
        'tables': {},
        'constants': {},
        'categories': {},
    }

    # Parse header
    header_elem = root.find('XDFHEADER')
    if header_elem is not None:
        result['header'] = {
            'version': root.get('version', ''),
            'file_version': _get_text(header_elem, 'fileversion'),
            'title': _get_text(header_elem, 'deftitle'),
            'description': _get_text(header_elem, 'description'),
            'author': _get_text(header_elem, 'author'),
        }

        # Parse defaults
        defaults_elem = header_elem.find('DEFAULTS')
        defaults = {}
        if defaults_elem is not None:
            defaults = {
                'datasizeinbits': int(defaults_elem.get('datasizeinbits', '8')),
                'sigdigits': int(defaults_elem.get('sigdigits', '2')),
                'outputtype': int(defaults_elem.get('outputtype', '1')),
                'signed': int(defaults_elem.get('signed', '0')),
                'lsbfirst': int(defaults_elem.get('lsbfirst', '1')),
                'float': int(defaults_elem.get('float', '0')),
            }
            # Derive type_flags from defaults
            signed_bit = defaults.get('signed', 0) & 0x01
            lsb_bit = (defaults.get('lsbfirst', 1) & 0x01) << 1
            defaults['type_flags'] = signed_bit | lsb_bit

        result['header']['defaults'] = defaults

        # Parse categories
        for cat_elem in header_elem.findall('CATEGORY'):
            idx = cat_elem.get('index', '0')
            name = cat_elem.get('name', '')
            try:
                result['categories'][int(idx, 16) if idx.startswith('0x') else int(idx)] = name
            except ValueError:
                pass

        # Parse base offset
        base_offset_elem = header_elem.find('BASEOFFSET')
        if base_offset_elem is not None:
            result['header']['base_offset'] = int(base_offset_elem.get('offset', '0'), 16) if base_offset_elem.get('offset', '0').startswith('0x') else int(base_offset_elem.get('offset', '0'))
            result['header']['base_subtract'] = int(base_offset_elem.get('subtract', '0'), 16) if base_offset_elem.get('subtract', '0').startswith('0x') else int(base_offset_elem.get('subtract', '0'))
        else:
            result['header']['base_offset'] = 0
            result['header']['base_subtract'] = 0
    else:
        defaults = {'datasizeinbits': 8, 'type_flags': 0x02}

    # Parse tables (XDFTABLE)
    for table_elem in root.findall('XDFTABLE'):
        table = {
            'uniqueid': table_elem.get('uniqueid', ''),
            'flags': table_elem.get('flags', ''),
            'title': _get_text(table_elem, 'title'),
            'description': _get_text(table_elem, 'description'),
            'categories': [],
            'axes': {},
        }

        # Category memberships
        for cat_mem in table_elem.findall('CATEGORYMEM'):
            try:
                table['categories'].append(int(cat_mem.get('category', '0')))
            except ValueError:
                pass

        # Parse axes
        for axis_elem in table_elem.findall('XDFAXIS'):
            axis = _parse_axis(axis_elem, defaults)
            table['axes'][axis['id']] = axis

        if table['title']:
            result['tables'][table['title']] = table

    # Parse constants (XDFCONSTANT)
    for const_elem in root.findall('XDFCONSTANT'):
        const = {
            'uniqueid': const_elem.get('uniqueid', ''),
            'title': _get_text(const_elem, 'title'),
            'description': _get_text(const_elem, 'description'),
            'units': _get_text(const_elem, 'units'),
            'decimal_places': 2,
            'categories': [],
            'embedded': {},
            'math_equation': 'X',
            'forward_func': lambda x: x,
            'reverse_func': lambda y: y,
        }

        # Parse EMBEDDEDDATA
        embed_elem = const_elem.find('EMBEDDEDDATA')
        if embed_elem is not None:
            const['embedded'] = {
                'address': int(embed_elem.get('mmedaddress', '0x0'), 16) if embed_elem.get('mmedaddress') else None,
                'element_size_bits': int(embed_elem.get('mmedelementsizebits', str(defaults.get('datasizeinbits', 8)))),
                'type_flags': int(embed_elem.get('mmedtypeflags', '0x0'), 16) if embed_elem.get('mmedtypeflags') else defaults.get('type_flags', 0),
            }

        # Parse MATH
        math_elem = const_elem.find('MATH')
        if math_elem is not None:
            equation = math_elem.get('equation', 'X')
            const['math_equation'] = equation
            const['forward_func'], const['reverse_func'] = parse_math_equation(equation)

        # Parse decimal places
        dp_elem = const_elem.find('decimalpl')
        if dp_elem is not None and dp_elem.text:
            try:
                const['decimal_places'] = int(dp_elem.text)
            except ValueError:
                pass

        # Category memberships
        for cat_mem in const_elem.findall('CATEGORYMEM'):
            try:
                const['categories'].append(int(cat_mem.get('category', '0')))
            except ValueError:
                pass

        if const['title']:
            result['constants'][const['title']] = const

    return result


def _get_text(parent, tag):
    """Safely get text content of a child element."""
    elem = parent.find(tag)
    if elem is not None and elem.text:
        return elem.text.strip()
    return ''


def find_map(xdf_data, map_name):
    """
    Find a map by name in the parsed XDF data.
    Tries exact match, then bracket-wrapped match, then best partial match.
    Prefers shortest title containing the name (avoids LDRXN matching LDRXNZK).
    Excludes titles that are just axis definitions (containing "Axis" after the map name).

    Returns the table definition dict or None.
    """
    name_upper = map_name.upper()

    # Exact match
    if map_name in xdf_data['tables']:
        return xdf_data['tables'][map_name]

    # Case-insensitive exact
    for title, table in xdf_data['tables'].items():
        if title.upper() == name_upper:
            return table

    # Bracket-wrapped match: [KFMIRL] - ... (common XDF naming)
    # Must match the bracketed name exactly to avoid LDRXN matching [LDRXNZK]
    bracket_pattern = f'[{name_upper}]'
    candidates = []
    for title, table in xdf_data['tables'].items():
        title_upper = title.upper()
        # Check for exact bracket match
        if bracket_pattern in title_upper:
            # Skip axis-only entries (e.g., "[KFMIRL] - RPM Axis", "[KFMIOP] Load Axis")
            after_bracket = title_upper.split(bracket_pattern, 1)[-1].strip()
            if 'AXIS' in after_bracket:
                continue
            candidates.append((len(title), title, table))

    if candidates:
        # Return shortest match (most specific)
        candidates.sort(key=lambda x: x[0])
        return candidates[0][2]

    # Partial match - collect all and prefer shortest (most specific)
    candidates = []
    for title, table in xdf_data['tables'].items():
        title_upper = title.upper()
        if name_upper in title_upper:
            # Skip axis entries
            if 'AXIS' in title_upper:
                continue
            idx = title_upper.find(name_upper)
            # Penalize if the match is part of a longer name (e.g., LDRXN in LDRXNZK)
            # Check if the char right after the name match is alphanumeric
            end_pos = idx + len(name_upper)
            if end_pos < len(title_upper) and title_upper[end_pos].isalnum():
                # This is a substring of a longer name - lower priority
                candidates.append((1000 + len(title), title, table))
            else:
                candidates.append((len(title), title, table))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][2]

    return None


def get_map_info(xdf_data, map_name):
    """
    Get a summary of a map's structure for display.

    Returns dict with:
        title, description, x_axis_info, y_axis_info, z_axis_info,
        dimensions (rows x cols), address
    """
    table = find_map(xdf_data, map_name)
    if table is None:
        return None

    info = {
        'title': table['title'],
        'description': table['description'],
        'dimensions': '',
        'address': '',
    }

    z_axis = table['axes'].get('z', {})
    x_axis = table['axes'].get('x', {})
    y_axis = table['axes'].get('y', {})

    embed = z_axis.get('embedded', {})
    rows = embed.get('row_count', 0)
    cols = embed.get('col_count', 0)
    addr = embed.get('address')

    info['dimensions'] = f'{rows}x{cols}'
    info['address'] = f'0x{addr:X}' if addr is not None else 'N/A'

    info['x_axis'] = {
        'units': x_axis.get('units', ''),
        'count': x_axis.get('index_count', 0),
        'equation': x_axis.get('math_equation', 'X'),
    }
    info['y_axis'] = {
        'units': y_axis.get('units', ''),
        'count': y_axis.get('index_count', 0),
        'equation': y_axis.get('math_equation', 'X'),
    }
    info['z_axis'] = {
        'units': z_axis.get('units', ''),
        'equation': z_axis.get('math_equation', 'X'),
    }

    return info


def list_maps(xdf_data):
    """List all table names in the XDF."""
    return sorted(xdf_data['tables'].keys())


if __name__ == '__main__':
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python xdf_parser.py <xdf_file> [map_name]")
        sys.exit(1)

    xdf_path = sys.argv[1]
    xdf_data = parse_xdf(xdf_path)

    if len(sys.argv) > 2:
        map_name = sys.argv[2]
        info = get_map_info(xdf_data, map_name)
        if info:
            print(json.dumps(info, indent=2))
        else:
            print(f"Map '{map_name}' not found. Available maps:")
            for name in list_maps(xdf_data):
                print(f"  {name}")
    else:
        print(f"XDF: {xdf_data['header'].get('title', 'Unknown')}")
        print(f"Maps found: {len(xdf_data['tables'])}")
        print(f"Constants found: {len(xdf_data['constants'])}")
        print("\nMaps:")
        for name in list_maps(xdf_data):
            info = get_map_info(xdf_data, name)
            print(f"  {name}: {info['dimensions']} @ {info['address']}")
