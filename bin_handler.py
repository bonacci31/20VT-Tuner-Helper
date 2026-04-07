"""
Binary ECU file handler - Reads and writes map data from/to ME7 binary files
using XDF definitions for addressing, scaling, and data format.
"""

import struct
import math
import copy


def read_bin(bin_path):
    """Read a binary ECU file into a mutable bytearray."""
    with open(bin_path, 'rb') as f:
        return bytearray(f.read())


def save_bin(data, output_path):
    """Save a bytearray to a binary file."""
    with open(output_path, 'wb') as f:
        f.write(data)


def _read_raw_value(data, address, size_bits, type_flags):
    """
    Read a single raw value from the binary data.

    type_flags:
        bit 0: 0=unsigned, 1=signed
        bit 1: 0=MSB first (big-endian), 1=LSB first (little-endian)
    """
    signed = bool(type_flags & 0x01)
    little_endian = bool(type_flags & 0x02)
    size_bytes = size_bits // 8

    raw_bytes = data[address:address + size_bytes]

    if size_bytes == 1:
        if signed:
            return struct.unpack('b', raw_bytes)[0]
        else:
            return struct.unpack('B', raw_bytes)[0]
    elif size_bytes == 2:
        if little_endian:
            fmt = '<h' if signed else '<H'
        else:
            fmt = '>h' if signed else '>H'
        return struct.unpack(fmt, raw_bytes)[0]
    elif size_bytes == 4:
        if little_endian:
            fmt = '<i' if signed else '<I'
        else:
            fmt = '>i' if signed else '>I'
        return struct.unpack(fmt, raw_bytes)[0]
    else:
        # Fallback for unusual sizes
        val = int.from_bytes(raw_bytes, byteorder='little' if little_endian else 'big', signed=signed)
        return val


def _write_raw_value(data, address, size_bits, type_flags, value):
    """
    Write a single raw value to the binary data.
    """
    signed = bool(type_flags & 0x01)
    little_endian = bool(type_flags & 0x02)
    size_bytes = size_bits // 8

    # Clamp value to valid range
    if signed:
        min_val = -(1 << (size_bits - 1))
        max_val = (1 << (size_bits - 1)) - 1
    else:
        min_val = 0
        max_val = (1 << size_bits) - 1

    value = int(round(value))
    value = max(min_val, min(max_val, value))

    if size_bytes == 1:
        fmt = 'b' if signed else 'B'
        raw_bytes = struct.pack(fmt, value)
    elif size_bytes == 2:
        if little_endian:
            fmt = '<h' if signed else '<H'
        else:
            fmt = '>h' if signed else '>H'
        raw_bytes = struct.pack(fmt, value)
    elif size_bytes == 4:
        if little_endian:
            fmt = '<i' if signed else '<I'
        else:
            fmt = '>i' if signed else '>I'
        raw_bytes = struct.pack(fmt, value)
    else:
        raw_bytes = value.to_bytes(size_bytes, byteorder='little' if little_endian else 'big', signed=signed)

    data[address:address + size_bytes] = raw_bytes


def read_axis_values(data, axis_def, base_offset=0):
    """
    Read axis values (RPM, load, etc.) from the binary file.

    Returns a list of engineering-unit values.
    """
    embedded = axis_def.get('embedded', {})
    address = embedded.get('address')
    labels = axis_def.get('labels', {})
    index_count = axis_def.get('index_count', 1)
    forward_func = axis_def.get('forward_func', lambda x: x)

    # If axis uses labels (not embedded in bin), return label values
    if address is None or address == 0:
        if labels:
            return [labels.get(i, float(i)) for i in range(index_count)]
        else:
            return [float(i) for i in range(index_count)]

    address += base_offset

    size_bits = embedded.get('element_size_bits', 8)
    type_flags = embedded.get('type_flags', 0)
    stride_bits = embedded.get('major_stride_bits', size_bits)

    if stride_bits == 0:
        stride_bits = size_bits

    # Handle negative stride (means MSB-first / big-endian regardless of type_flags)
    if stride_bits < 0:
        stride_bits = abs(stride_bits)
        # Override endianness to big-endian
        type_flags = type_flags & 0x01  # keep signed bit, clear LSB-first bit

    stride_bytes = stride_bits // 8
    if stride_bytes == 0:
        stride_bytes = size_bits // 8

    values = []
    for i in range(index_count):
        addr = address + i * stride_bytes
        raw = _read_raw_value(data, addr, size_bits, type_flags)
        eng = forward_func(raw)
        values.append(round(eng, axis_def.get('decimal_places', 2)))

    return values


def read_map_data(data, table_def, base_offset=0):
    """
    Read a complete map (2D table) from the binary file.

    Returns:
        {
            'title': str,
            'x_axis': [float, ...],  # Column headers (e.g., RPM)
            'y_axis': [float, ...],  # Row headers (e.g., Load %)
            'x_units': str,
            'y_units': str,
            'z_units': str,
            'data': [[float, ...], ...],  # 2D array [row][col]
            'rows': int,
            'cols': int,
        }
    """
    axes = table_def.get('axes', {})
    x_axis_def = axes.get('x', {})
    y_axis_def = axes.get('y', {})
    z_axis_def = axes.get('z', {})

    # Read axis values
    x_values = read_axis_values(data, x_axis_def, base_offset)
    y_values = read_axis_values(data, y_axis_def, base_offset)

    # Read Z data (the actual map values)
    z_embedded = z_axis_def.get('embedded', {})
    z_address = z_embedded.get('address', 0) + base_offset
    z_size_bits = z_embedded.get('element_size_bits', 8)
    z_type_flags = z_embedded.get('type_flags', 0)
    z_rows = z_embedded.get('row_count', 1)
    z_cols = z_embedded.get('col_count', 1)
    z_forward = z_axis_def.get('forward_func', lambda x: x)
    z_decimal = z_axis_def.get('decimal_places', 2)

    z_stride_major = z_embedded.get('major_stride_bits', 0)
    z_stride_minor = z_embedded.get('minor_stride_bits', 0)

    size_bytes = z_size_bits // 8

    # Read all Z values
    z_data = []
    for row in range(z_rows):
        row_data = []
        for col in range(z_cols):
            offset = (row * z_cols + col) * size_bytes
            addr = z_address + offset
            raw = _read_raw_value(data, addr, z_size_bits, z_type_flags)
            eng = z_forward(raw)
            row_data.append(round(eng, z_decimal))
        z_data.append(row_data)

    return {
        'title': table_def.get('title', ''),
        'x_axis': x_values,
        'y_axis': y_values,
        'x_units': x_axis_def.get('units', ''),
        'y_units': y_axis_def.get('units', ''),
        'z_units': z_axis_def.get('units', ''),
        'data': z_data,
        'rows': z_rows,
        'cols': z_cols,
    }


def write_map_data(data, table_def, new_values, base_offset=0):
    """
    Write new map values to the binary data buffer.

    Args:
        data: bytearray of the binary file
        table_def: table definition from XDF parser
        new_values: 2D array of engineering-unit values to write
        base_offset: base offset from XDF header

    Returns:
        Modified bytearray (also modifies in-place)
    """
    z_axis_def = table_def.get('axes', {}).get('z', {})
    z_embedded = z_axis_def.get('embedded', {})
    z_address = z_embedded.get('address', 0) + base_offset
    z_size_bits = z_embedded.get('element_size_bits', 8)
    z_type_flags = z_embedded.get('type_flags', 0)
    z_rows = z_embedded.get('row_count', 1)
    z_cols = z_embedded.get('col_count', 1)
    z_reverse = z_axis_def.get('reverse_func', lambda y: y)

    size_bytes = z_size_bits // 8

    for row in range(min(z_rows, len(new_values))):
        for col in range(min(z_cols, len(new_values[row]))):
            eng_value = new_values[row][col]
            raw_value = z_reverse(eng_value)
            offset = (row * z_cols + col) * size_bytes
            addr = z_address + offset
            _write_raw_value(data, addr, z_size_bits, z_type_flags, raw_value)

    return data


def write_axis_values(data, axis_def, new_values, base_offset=0):
    """
    Write new axis values to the binary data buffer.
    """
    embedded = axis_def.get('embedded', {})
    address = embedded.get('address')

    if address is None or address == 0:
        return data  # Can't write to label-only axes

    address += base_offset
    size_bits = embedded.get('element_size_bits', 8)
    type_flags = embedded.get('type_flags', 0)
    stride_bits = embedded.get('major_stride_bits', size_bits)
    reverse_func = axis_def.get('reverse_func', lambda y: y)

    if stride_bits == 0:
        stride_bits = size_bits
    if stride_bits < 0:
        stride_bits = abs(stride_bits)
        type_flags = type_flags & 0x01

    stride_bytes = stride_bits // 8
    if stride_bytes == 0:
        stride_bytes = size_bits // 8

    for i, val in enumerate(new_values):
        addr = address + i * stride_bytes
        raw = reverse_func(val)
        _write_raw_value(data, addr, size_bits, type_flags, raw)

    return data


def read_constant(data, const_def, base_offset=0):
    """Read a single constant/scalar value from the binary file."""
    embedded = const_def.get('embedded', {})
    address = embedded.get('address', 0) + base_offset
    size_bits = embedded.get('element_size_bits', 8)
    type_flags = embedded.get('type_flags', 0)
    forward_func = const_def.get('forward_func', lambda x: x)

    raw = _read_raw_value(data, address, size_bits, type_flags)
    return forward_func(raw)


def write_constant(data, const_def, value, base_offset=0):
    """Write a single constant/scalar value to the binary file."""
    embedded = const_def.get('embedded', {})
    address = embedded.get('address', 0) + base_offset
    size_bits = embedded.get('element_size_bits', 8)
    type_flags = embedded.get('type_flags', 0)
    reverse_func = const_def.get('reverse_func', lambda y: y)

    raw = reverse_func(value)
    _write_raw_value(data, address, size_bits, type_flags, raw)
    return data


if __name__ == '__main__':
    import sys
    import json
    from xdf_parser import parse_xdf, find_map

    if len(sys.argv) < 3:
        print("Usage: python bin_handler.py <xdf_file> <bin_file> [map_name]")
        sys.exit(1)

    xdf_path = sys.argv[1]
    bin_path = sys.argv[2]

    xdf_data = parse_xdf(xdf_path)
    bin_data = read_bin(bin_path)
    base_offset = xdf_data['header'].get('base_offset', 0) - xdf_data['header'].get('base_subtract', 0)

    if len(sys.argv) > 3:
        map_name = sys.argv[3]
        table = find_map(xdf_data, map_name)
        if table:
            result = read_map_data(bin_data, table, base_offset)
            # Convert to JSON-serializable format
            print(json.dumps(result, indent=2))
        else:
            print(f"Map '{map_name}' not found in XDF")
    else:
        print(f"Binary file: {bin_path} ({len(bin_data)} bytes)")
