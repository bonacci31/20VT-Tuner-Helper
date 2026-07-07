"""
20VT Tuner Helper - Visual interface for ME7/ME7.5 ECU tuning.

Integrates all calculation modules:
- KFMIRL, KFMIOP, KFZWOP/KFZWOP2, LDRXN/LDRXNZK (formula-based)
- KFLDHBN (boost pressure limit, optional based on intercooler)
- LAMFA, KFLBTS, KFFDLBTS (WOT/high-load fuel enrichment)

Usage:
    python me7_gui.py
"""

import os
import sys
import json
import copy
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xdf_parser import parse_xdf, find_map, list_maps
from bin_handler import read_bin, save_bin, read_map_data, write_map_data
from tuning_calc import (
    calc_kfmirl, calc_kfmiop, calc_kfzwop,
    calc_ldrxn, calc_ldrxnzk,
)
from kfldhbn_calc import calc_kfldhbn
from enrichment_calc import calc_lamfa_enrichment, calc_kflbts_enrichment, calc_kffdlbts_enrichment


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TARGET_MAPS = [['KFMIRL', 'KFMIOP', 'KFZWOP', 'KFZWOP2', 'LDRXN', 'LDRXNZK'],[]]
TARGET_MAPS[1] += ['Kennfeld für Berechnung Sollfüllung']
TARGET_MAPS[1] += ['Kennfeld optimales Motormoment']
TARGET_MAPS[1] += ['optimaler Zündwinkel']
TARGET_MAPS[1] += ['optimaler Zündwinkel Variante 2']
TARGET_MAPS[1] += [ 'Maximalfuellung LDR']
TARGET_MAPS[1] += ['Maximalfuellung LDR bei Dauerklopfen']
extra_maps = [['KFLDHBN', 'LAMFA', 'KFLBTS', 'KFFDLBTS'],[]]
extra_maps[1] += [ 'LDR-Höhenbegrenzung (max. Verdichterdruckverhältnis)']
extra_maps[1] += [ 'Lambda Fahrerwunsch']
extra_maps[1] += [ 'Lambdasoll für Bauteileschutz']
extra_maps[1] += [ 'Faktor Delta Lambdasoll für Bauteileschutz']
mapsname = TARGET_MAPS[0] + extra_maps[0]
mapsDesc = TARGET_MAPS[1] + extra_maps[1]
KFMIRL_Id = mapsname.index('KFMIRL')
KFMIOP_Id = mapsname.index('KFMIOP')
KFZWOP_Id = mapsname.index('KFZWOP')
KFZWOP2_Id = mapsname.index('KFZWOP2')
LDRXN_Id = mapsname.index('LDRXN')
LDRXNZK_Id = mapsname.index('LDRXNZK')

KFLDHBN_Id = mapsname.index('KFLDHBN')
LAMFA_Id = mapsname.index('LAMFA')
KFLBTS_Id = mapsname.index('KFLBTS')
KFFDLBTS_Id = mapsname.index('KFFDLBTS')


RequiredMaps = ['KFMIRL', 'KFMIOP', 'LDRXN']

map_write_order = ['KFMIRL', 'KFMIOP', 'KFZWOP', 'KFZWOP2', 'LDRXN', 'LDRXNZK',
                   'KFLDHBN', 'LAMFA', 'KFLBTS', 'KFFDLBTS']
        
WINDOW_TITLE = "20VT Tuner Helper by Peter Markou"
BG_COLOR = "#1e1e2e"
FG_COLOR = "#cdd6f4"
ACCENT = "#89b4fa"
ACCENT2 = "#a6e3a1"
WARN_COLOR = "#f9e2af"
ERR_COLOR = "#f38ba8"
ENTRY_BG = "#313244"
BUTTON_BG = "#45475a"
BUTTON_FG = "#cdd6f4"
TABLE_BG = "#181825"
TABLE_FG = "#bac2de"
HEADER_BG = "#313244"


# ---------------------------------------------------------------------------
# Helper: find map with flexible name matching (same as me7_tune.py)
# ---------------------------------------------------------------------------
def find_target_map(xdf_data, name):
    table = find_map(xdf_data, name)
    if table:
        return table
    for var in [name + '1', name.rstrip('12'), name + ' ', name.replace('_', '')]:
        table = find_map(xdf_data, var)
        if table:
            return table
    return None


def fix_kfzwop_data(xdf_data, bin_data, base_offset, original, map_name_prefix):
    """Fix KFZWOP transposition and linked axis issues."""
    base_name = map_name_prefix.rstrip('0123456789')
    load_axis_names = [
        f'({map_name_prefix}) - Load Axis',
        f'({base_name}) - Load Axis',
        f'(KZ{map_name_prefix[2:]}) - Load Axis',
        f'(KZ{base_name[2:]}) - Load Axis',
    ]
    load_axis_values = None
    for name in load_axis_names:
        load_table = xdf_data['tables'].get(name)
        if load_table:
            load_data = read_map_data(bin_data, load_table, base_offset)
            load_axis_values = load_data['data'][0] if load_data['data'] else None
            break

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
        return original

    num_load = len(load_axis_values)
    num_rpm = len(rpm_axis_values) if rpm_axis_values else original['cols']

    flat = []
    for row in original['data']:
        flat.extend(row)

    if len(flat) == num_rpm * num_load:
        rpm_by_load = []
        for r in range(num_rpm):
            rpm_by_load.append(flat[r * num_load:(r + 1) * num_load])
        load_by_rpm = []
        for l_idx in range(num_load):
            row = [rpm_by_load[r][l_idx] for r in range(num_rpm)]
            load_by_rpm.append(row)
        original['data'] = load_by_rpm
        original['rows'] = num_load
        original['cols'] = num_rpm

    original['y_axis'] = [round(v) for v in load_axis_values]
    if rpm_axis_values:
        original['x_axis'] = [round(v) for v in rpm_axis_values]
    return original


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------
class ME7TunerApp:
    def __init__(self, root):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1280x900")
        self.root.minsize(1000, 700)
        self.root.configure(bg=BG_COLOR)

        # State
        self.xdf_path = tk.StringVar()
        self.bin_path = tk.StringVar()
        self.boost_var = tk.StringVar(value="1.5")
        self.aggr_var = tk.StringVar(value="100")
        self.turbo_var = tk.StringVar(value="Small")
        self.lowload_var = tk.StringVar(value="Yes")
        self.intercooler_var = tk.StringVar(value="Yes")
        self.enrichment_var = tk.StringVar(value="0")

        self.xdf_data = None
        self.bin_data = None
        self.base_offset = 0
        self.state = {}
        self.mapTable = []

        # Track which maps are calculated and approved
        self.calculated_maps = {}  # name -> {'original': ..., 'calculated': ...}
        self.approved_maps = set()

        self._build_ui()

    # -----------------------------------------------------------------------
    # UI Construction
    # -----------------------------------------------------------------------
    def _build_ui(self):
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background=BG_COLOR)
        style.configure('TLabel', background=BG_COLOR, foreground=FG_COLOR, font=('Segoe UI', 10))
        style.configure('Header.TLabel', background=BG_COLOR, foreground=ACCENT, font=('Segoe UI', 12, 'bold'))
        style.configure('TButton', background=BUTTON_BG, foreground=BUTTON_FG, font=('Segoe UI', 10), padding=6)
        style.map('TButton', background=[('active', ACCENT)])
        style.configure('Accent.TButton', background=ACCENT, foreground='#1e1e2e', font=('Segoe UI', 10, 'bold'))
        style.map('Accent.TButton', background=[('active', ACCENT2)])
        style.configure('TEntry', fieldbackground=ENTRY_BG, foreground=FG_COLOR, font=('Segoe UI', 10))
        style.configure('TCombobox', fieldbackground=ENTRY_BG, foreground=FG_COLOR,
                        selectbackground=ENTRY_BG, selectforeground=FG_COLOR, font=('Segoe UI', 10))
        style.map('TCombobox', fieldbackground=[('readonly', ENTRY_BG)],
                  foreground=[('readonly', FG_COLOR)],
                  selectbackground=[('readonly', ENTRY_BG)],
                  selectforeground=[('readonly', FG_COLOR)])
        # Fix combobox dropdown list colors
        self.root.option_add('*TCombobox*Listbox.background', ENTRY_BG)
        self.root.option_add('*TCombobox*Listbox.foreground', FG_COLOR)
        self.root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        self.root.option_add('*TCombobox*Listbox.selectForeground', '#1e1e2e')
        style.configure('TLabelframe', background=BG_COLOR, foreground=ACCENT, font=('Segoe UI', 10, 'bold'))
        style.configure('TLabelframe.Label', background=BG_COLOR, foreground=ACCENT, font=('Segoe UI', 10, 'bold'))

        # Main container
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Top: title + safety
        title_frame = ttk.Frame(main)
        title_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(title_frame, text="20VT Tuner Helper by Peter Markou", font=('Segoe UI', 16, 'bold'),
                  foreground=ACCENT).pack(side=tk.LEFT)
        ttk.Label(title_frame, text="  ⚠ Always verify with wideband + logging before driving hard",
                  foreground=WARN_COLOR, font=('Segoe UI', 9)).pack(side=tk.LEFT, padx=20)

        # --- File Selection ---
        file_frame = ttk.LabelFrame(main, text="Files", padding=10)
        file_frame.pack(fill=tk.X, pady=(0, 8))

        # XDF row
        ttk.Label(file_frame, text="XDF File:").grid(row=0, column=0, sticky=tk.W, padx=(0, 5))
        xdf_entry = ttk.Entry(file_frame, textvariable=self.xdf_path, width=90)
        xdf_entry.grid(row=0, column=1, sticky=tk.EW, padx=5)
        ttk.Button(file_frame, text="Browse", command=self._browse_xdf).grid(row=0, column=2, padx=5)

        # BIN row
        ttk.Label(file_frame, text="BIN File:").grid(row=1, column=0, sticky=tk.W, padx=(0, 5), pady=(5, 0))
        bin_entry = ttk.Entry(file_frame, textvariable=self.bin_path, width=90)
        bin_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=(5, 0))
        ttk.Button(file_frame, text="Browse", command=self._browse_bin).grid(row=1, column=2, padx=5, pady=(5, 0))

        file_frame.columnconfigure(1, weight=1)

        # --- Parameters ---
        param_frame = ttk.LabelFrame(main, text="Tuning Parameters", padding=10)
        param_frame.pack(fill=tk.X, pady=(0, 8))

        row0_params = [
            ("Max Boost (bar):", self.boost_var, "entry", "0 - 3 bar"),
            ("Aggressiveness (%):", self.aggr_var, "entry", "0 - 135%"),
            ("Turbo Type:", self.turbo_var, "combo", ["Small", "Large"]),
            ("Generate Low Load:", self.lowload_var, "combo", ["Yes", "No"]),
            ("Intercooler Installed:", self.intercooler_var, "combo", ["Yes", "No"]),
        ]

        for i, (label, var, widget_type, hint) in enumerate(row0_params):
            ttk.Label(param_frame, text=label).grid(row=0, column=i * 2, sticky=tk.W, padx=(10 if i > 0 else 0, 5))
            if widget_type == "entry":
                w = ttk.Entry(param_frame, textvariable=var, width=10)
                w.grid(row=0, column=i * 2 + 1, padx=(0, 10))
            else:
                w = ttk.Combobox(param_frame, textvariable=var, values=hint, width=8, state='readonly')
                w.grid(row=0, column=i * 2 + 1, padx=(0, 10))

        # Row 2: Enrichment
        ttk.Label(param_frame, text="WOT Enrichment (%):", foreground=WARN_COLOR).grid(
            row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(param_frame, textvariable=self.enrichment_var, width=10).grid(
            row=1, column=1, padx=(0, 10), pady=(8, 0))
        ttk.Label(param_frame, text="0 = no change, 5 = 5% richer at WOT/high load (LAMFA + KFLBTS + KFFDLBTS)",
                  foreground=TABLE_FG, font=('Segoe UI', 9)).grid(
            row=1, column=2, columnspan=8, sticky=tk.W, padx=5, pady=(8, 0))

        # --- Action Buttons ---
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(btn_frame, text="1. Load & Verify Maps",
                   command=self._load_and_verify).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="2. Calculate All Maps",
                   command=self._calculate_all, style='Accent.TButton').pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="3. Save Tuned BIN",
                   command=self._save_bin, style='Accent.TButton').pack(side=tk.LEFT, padx=5)

        # --- Map Viewer (notebook with tabs) ---
        viewer_frame = ttk.Frame(main)
        viewer_frame.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(viewer_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Log tab
        log_frame = ttk.Frame(self.notebook)
        self.notebook.add(log_frame, text="  Log  ")
        self.log_text = scrolledtext.ScrolledText(log_frame, bg=TABLE_BG, fg=TABLE_FG,
                                                   font=('Consolas', 10), wrap=tk.WORD,
                                                   insertbackground=FG_COLOR)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Status bar
        self.status_var = tk.StringVar(value="Ready — Load XDF and BIN files to begin")
        status_bar = ttk.Label(main, textvariable=self.status_var, foreground=ACCENT,
                               font=('Segoe UI', 9))
        status_bar.pack(fill=tk.X, pady=(5, 0))

        self._log("20VT Tuner Helper initialized. Load your XDF and BIN files to begin.")
        self._log("⚠ Safety: Original BIN will NOT be modified. A new file is created.")

    # -----------------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------------
    def _log(self, msg, tag=None):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def _log_error(self, msg):
        self.log_text.insert(tk.END, "ERROR: " + msg + "\n")
        self.log_text.see(tk.END)

    def _set_status(self, msg):
        self.status_var.set(msg)
        self.root.update_idletasks()

    # -----------------------------------------------------------------------
    # File Browsing
    # -----------------------------------------------------------------------
    def _browse_xdf(self):
        path = filedialog.askopenfilename(
            title="Select XDF Definition File",
            filetypes=[("XDF Files", "*.xdf"), ("All Files", "*.*")],
            initialdir=os.path.expanduser("~/Desktop")
        )
        if path:
            self.xdf_path.set(path)

    def _browse_bin(self):
        path = filedialog.askopenfilename(
            title="Select BIN ECU File",
            filetypes=[("BIN Files", "*.bin"), ("All Files", "*.*")],
            initialdir=os.path.expanduser("~/Desktop")
        )
        if path:
            self.bin_path.set(path)

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------
    def _validate_inputs(self):
        if not self.xdf_path.get() or not os.path.isfile(self.xdf_path.get()):
            messagebox.showerror("Error", "Please select a valid XDF file.")
            return False
        if not self.bin_path.get() or not os.path.isfile(self.bin_path.get()):
            messagebox.showerror("Error", "Please select a valid BIN file.")
            return False
        try:
            boost = float(self.boost_var.get())
            if not 0 <= boost <= 3:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Max Boost must be a number between 0 and 3.")
            return False
        try:
            aggr = float(self.aggr_var.get())
            if not 0 <= aggr <= 135:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Aggressiveness must be a number between 0 and 135.")
            return False
        try:
            enrich = float(self.enrichment_var.get())
            if not 0 <= enrich <= 20:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "WOT Enrichment must be a number between 0 and 20.")
            return False
        return True

    # -----------------------------------------------------------------------
    # Load & Verify
    # -----------------------------------------------------------------------
    def _load_and_verify(self):
        if not self._validate_inputs():
            return

        self._set_status("Loading files...")
        self._log("\n" + "=" * 70)
        self._log("LOADING FILES")
        self._log("=" * 70)

        try:
            self.xdf_data = parse_xdf(self.xdf_path.get())
            self._log(f"XDF loaded: {os.path.basename(self.xdf_path.get())}")
        except Exception as e:
            self._log_error(f"Failed to parse XDF: {e}")
            messagebox.showerror("XDF Error", str(e))
            return

        self.base_offset = (self.xdf_data['header'].get('base_offset', 0)
                            - self.xdf_data['header'].get('base_subtract', 0))

        try:
            self.bin_data = read_bin(self.bin_path.get())
            self._log(f"BIN loaded: {os.path.basename(self.bin_path.get())} ({len(self.bin_data)} bytes)")
        except Exception as e:
            self._log_error(f"Failed to read BIN: {e}")
            messagebox.showerror("BIN Error", str(e))
            return

        # Check target maps
        self._log("\nMap Availability:")
        all_found = True
        
        
        for name in mapsname:
            table = find_target_map(self.xdf_data, name)
            if table:
                self._log(f"  ✓ {name:12s} — {table['title']}")
                self.mapTable += [table]
            else:
                idx = mapsname.index(name)
                descr = mapsDesc[idx]
                table = find_target_map(self.xdf_data, descr)
                if table:
                    self._log(f"  ✓ {name:12s} — {table['title']}")
                    self.mapTable += [table]
                else:
                    marker = "✗ REQUIRED" if name in RequiredMaps else "✗ optional"
                    self._log(f"  {marker}: {name}")
                    self.mapTable += []
                    if name in RequiredMaps:
                        all_found = False

        if not all_found:
            messagebox.showwarning("Missing Maps", "Some required maps are missing. Calculation may be incomplete.")

        self.state = {}
        self.calculated_maps = {}
        self.approved_maps = set()
        self._set_status("Files loaded — Ready to calculate")
        self._log("\nReady. Click 'Calculate All Maps' to proceed.")

    # -----------------------------------------------------------------------
    # Calculate All
    # -----------------------------------------------------------------------
    def _calculate_all(self):
        if self.xdf_data is None or self.bin_data is None:
            messagebox.showwarning("Not Loaded", "Load XDF and BIN files first.")
            return
        if not self._validate_inputs():
            return

        boost = float(self.boost_var.get())
        aggr = float(self.aggr_var.get())
        turbo = self.turbo_var.get().lower()
        low_load = self.lowload_var.get().lower() == 'yes'
        intercooler = self.intercooler_var.get().lower() == 'yes'
        enrichment = float(self.enrichment_var.get())

        self._set_status("Calculating maps...")
        self._log("\n" + "=" * 70)
        self._log(f"CALCULATING — Boost: {boost} bar | Aggr: {aggr}% | Turbo: {turbo} | "
                  f"Low load: {low_load} | Intercooler: {intercooler} | Enrichment: {enrichment}%")
        self._log("=" * 70)

        # Clear previous tabs except Log
        while self.notebook.index("end") > 1:
            self.notebook.forget(1)

        try:
            self._calc_kfmirl(boost, aggr, turbo, low_load)
            self._calc_kfmiop()
            self._calc_kfzwop('KFZWOP')
            self._calc_kfzwop('KFZWOP2')
            self._calc_ldrxn()
            if intercooler:
                self._calc_kfldhbn(boost, turbo)
            else:
                self._log("\nKFLDHBN: Skipped (no intercooler installed)")
            if enrichment > 0:
                self._calc_enrichment(enrichment)
            else:
                self._log("\nEnrichment: Skipped (0%)")
        except Exception as e:
            self._log_error(f"Calculation failed: {e}")
            import traceback
            self._log(traceback.format_exc())
            messagebox.showerror("Calculation Error", str(e))
            return

        self._set_status(f"All maps calculated — Review tabs and click 'Save Tuned BIN'")
        self._log(f"\nAll maps calculated. Review each tab, then save.")

    # -----------------------------------------------------------------------
    # Individual Calculations
    # -----------------------------------------------------------------------
    def _calc_kfmirl(self, boost, aggr, turbo, low_load):
        self._log("\n--- KFMIRL (Engine Load Desired) ---")
##        table = find_target_map(self.xdf_data, 'KFMIRL')
        table = self.mapTable[mapsname.index('KFMIRL')]
        if not table:
            self._log_error("KFMIRL not found in XDF")
            return
        original = read_map_data(self.bin_data, table, self.base_offset)
        calculated = calc_kfmirl(original, boost, aggr, turbo, low_load)

        self.state['original_kfmirl'] = original
        self.state['new_kfmirl'] = calculated
        self.calculated_maps['KFMIRL'] = {'original': original, 'calculated': calculated}

        max_charge = 110 + boost * 66.7
        self._log(f"Max cylinder charge: {max_charge:.1f}%")
        self._log(f"Max original value: {max(max(r) for r in original['data']):.1f}")
        self._log(f"Max calculated value: {max(max(r) for r in calculated['data']):.1f}")
        self._add_map_tab('KFMIRL', original, calculated)

    def _calc_kfmiop(self):
        self._log("\n--- KFMIOP (Optimal Engine Torque) ---")
        if 'new_kfmirl' not in self.state:
            self._log_error("KFMIRL must be calculated first")
            return
##        table = find_target_map(self.xdf_data, 'KFMIOP')
        table = self.mapTable[mapsname.index('KFMIOP')]
        if not table:
            self._log_error("KFMIOP not found in XDF")
            return
        original = read_map_data(self.bin_data, table, self.base_offset)
        calculated = calc_kfmiop(original, self.state['new_kfmirl'], self.state['original_kfmirl'])

        self.state['original_kfmiop'] = original
        self.state['new_kfmiop'] = calculated
        self.calculated_maps['KFMIOP'] = {'original': original, 'calculated': calculated}

        self._log(f"Y-axis rescaled: {original['y_axis'][:3]}...{original['y_axis'][-2:]} -> "
                  f"{calculated['y_axis'][:3]}...{calculated['y_axis'][-2:]}")
        self._add_map_tab('KFMIOP', original, calculated)

    def _calc_kfzwop(self, map_name):
        self._log(f"\n--- {map_name} (Optimal Ignition Angle) ---")
        if 'new_kfmirl' not in self.state:
            self._log_error("KFMIRL must be calculated first")
            return
##        table = find_target_map(self.xdf_data, map_name)
        table = self.mapTable[mapsname.index(map_name)]
        if not table:
            self._log_error(f"{map_name} not found in XDF")
            return
        original = read_map_data(self.bin_data, table, self.base_offset)
        original = fix_kfzwop_data(self.xdf_data, self.bin_data, self.base_offset, original, map_name)
        calculated = calc_kfzwop(original, self.state['new_kfmirl'], self.state['original_kfmirl'])

        self.state[f'original_{map_name.lower()}'] = original
        self.state[f'new_{map_name.lower()}'] = calculated
        self.calculated_maps[map_name] = {'original': original, 'calculated': calculated}

        self._log(f"Re-interpolated to new load axis")
        self._add_map_tab(map_name, original, calculated)

    def _calc_ldrxn(self):
        self._log("\n--- LDRXN / LDRXNZK (Boost Request Limits) ---")
        if 'new_kfmirl' not in self.state:
            self._log_error("KFMIRL must be calculated first")
            return

##        ldrxn_table = find_target_map(self.xdf_data, 'LDRXN')
        ldrxn_table = self.mapTable[mapsname.index('LDRXN')]
        original_ldrxn = read_map_data(self.bin_data, ldrxn_table, self.base_offset) if ldrxn_table else None
##        ldrxnzk_table = find_target_map(self.xdf_data, 'LDRXNZK')
        ldrxnzk_table = self.mapTable[mapsname.index('LDRXNZK')]
        original_ldrxnzk = read_map_data(self.bin_data, ldrxnzk_table, self.base_offset) if ldrxnzk_table else None

        new_ldrxn = calc_ldrxn(self.state['new_kfmirl'], original_ldrxn)
        new_ldrxnzk = calc_ldrxnzk(new_ldrxn)

        self.state['original_ldrxn'] = original_ldrxn
        self.state['new_ldrxn'] = new_ldrxn
        self.state['original_ldrxnzk'] = original_ldrxnzk
        self.state['new_ldrxnzk'] = new_ldrxnzk
        self.calculated_maps['LDRXN'] = {'original': original_ldrxn, 'calculated': new_ldrxn}
        self.calculated_maps['LDRXNZK'] = {'original': original_ldrxnzk, 'calculated': new_ldrxnzk}

        if new_ldrxn and new_ldrxn.get('data'):
            peak = max(new_ldrxn['data'][0])
            mbar = peak * 10 + 300
            self._log(f"LDRXN peak: {peak} load = {mbar} mBar")

        self._add_map_tab('LDRXN', original_ldrxn, new_ldrxn)
        self._add_map_tab('LDRXNZK', original_ldrxnzk, new_ldrxnzk)

    def _calc_kfldhbn(self, boost, turbo):
        self._log("\n--- KFLDHBN (Max Boost Pressure Ratio) ---")
##        kfldhbn_table = find_target_map(self.xdf_data, 'KFLDHBN')
        kfldhbn_table = self.mapTable[mapsname.index('KFLDHBN')]
        original = read_map_data(self.bin_data, kfldhbn_table, self.base_offset) if kfldhbn_table else None

        result = calc_kfldhbn(boost, turbo, headroom_pct=15, original_map=original)

        new_kfldhbn = {
            'x_axis': result['x_axis'],
            'y_axis': result['y_axis'],
            'x_units': result.get('x_units', 'RPM'),
            'y_units': result.get('y_units', '°F'),
            'z_units': result.get('z_units', 'PSI'),
            'data': result['data'],
            'rows': result['rows'],
            'cols': result['cols'],
        }

        self.state['original_kfldhbn'] = original
        self.state['new_kfldhbn'] = new_kfldhbn
        self.calculated_maps['KFLDHBN'] = {'original': original, 'calculated': new_kfldhbn}

        base_psi = result['params']['base_psi_with_headroom']
        self._log(f"Target: {boost} bar + 15% headroom = {base_psi:.1f} PSI base")
        self._add_map_tab('KFLDHBN', original, new_kfldhbn)

    def _calc_enrichment(self, enrichment_pct):
        self._log(f"\n--- FUEL ENRICHMENT ({enrichment_pct}% richer at WOT/high load) ---")

        # LAMFA
##        lamfa_table = find_target_map(self.xdf_data, 'LAMFA')
        lamfa_table = self.mapTable[mapsname.index('LAMFA')]
        if lamfa_table:
            original_lamfa = read_map_data(self.bin_data, lamfa_table, self.base_offset)
            new_lamfa = calc_lamfa_enrichment(original_lamfa, enrichment_pct)
            self.state['original_lamfa'] = original_lamfa
            self.state['new_lamfa'] = new_lamfa
            self.calculated_maps['LAMFA'] = {'original': original_lamfa, 'calculated': new_lamfa}
            self._log(f"LAMFA: {new_lamfa['params']['cells_changed']} cells enriched "
                      f"(torque >= {new_lamfa['params']['torque_threshold_pct']}%)")
            self._add_map_tab('LAMFA', original_lamfa, new_lamfa)
        else:
            self._log("LAMFA: not found in XDF, skipping")

        # KFLBTS
##        kflbts_table = find_target_map(self.xdf_data, 'KFLBTS')
        kflbts_table = self.mapTable[mapsname.index('KFLBTS')]
        if kflbts_table:
            original_kflbts = read_map_data(self.bin_data, kflbts_table, self.base_offset)
            new_kflbts = calc_kflbts_enrichment(original_kflbts, enrichment_pct)
            self.state['original_kflbts'] = original_kflbts
            self.state['new_kflbts'] = new_kflbts
            self.calculated_maps['KFLBTS'] = {'original': original_kflbts, 'calculated': new_kflbts}
            self._log(f"KFLBTS: {new_kflbts['params']['cells_changed']} cells enriched "
                      f"(load >= {new_kflbts['params']['load_threshold_pct']}%)")
            self._add_map_tab('KFLBTS', original_kflbts, new_kflbts)
        else:
            self._log("KFLBTS: not found in XDF, skipping")

        # KFFDLBTS
##        kffdlbts_table = find_target_map(self.xdf_data, 'KFFDLBTS')
        kffdlbts_table = self.mapTable[mapsname.index('KFFDLBTS')]
        if kffdlbts_table:
            original_kffdlbts = read_map_data(self.bin_data, kffdlbts_table, self.base_offset)
            new_kffdlbts = calc_kffdlbts_enrichment(original_kffdlbts, enrichment_pct)
            self.state['original_kffdlbts'] = original_kffdlbts
            self.state['new_kffdlbts'] = new_kffdlbts
            self.calculated_maps['KFFDLBTS'] = {'original': original_kffdlbts, 'calculated': new_kffdlbts}
            self._log(f"KFFDLBTS: {new_kffdlbts['params']['cells_changed']} cells increased "
                      f"(load >= {new_kffdlbts['params']['load_threshold_pct']}%)")
            self._add_map_tab('KFFDLBTS', original_kffdlbts, new_kffdlbts)
        else:
            self._log("KFFDLBTS: not found in XDF, skipping")

    # -----------------------------------------------------------------------
    # Map Tab Display
    # -----------------------------------------------------------------------
    def _add_map_tab(self, name, original, calculated):
        """Add a notebook tab showing original vs calculated map as tables."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text=f"  {name}  ")

        # Top info bar
        info_frame = ttk.Frame(tab)
        info_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(info_frame, text=name, style='Header.TLabel').pack(side=tk.LEFT)

        # Paned window: original on top, calculated on bottom
        paned = ttk.PanedWindow(tab, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Original
        orig_frame = ttk.LabelFrame(tab, text="Original", padding=5)
        paned.add(orig_frame, weight=1)
        if original:
            self._create_map_table(orig_frame, original)
        else:
            ttk.Label(orig_frame, text="Map not found in XDF").pack()

        # Calculated
        calc_frame = ttk.LabelFrame(tab, text="Calculated", padding=5)
        paned.add(calc_frame, weight=1)
        if calculated:
            self._create_map_table(calc_frame, calculated, highlight=True)
        else:
            ttk.Label(calc_frame, text="Not calculated").pack()

    def _create_map_table(self, parent, map_data, highlight=False):
        """Create a scrollable table widget for a map."""
        container = ttk.Frame(parent)
        container.pack(fill=tk.BOTH, expand=True)

        # Scrollbars
        x_scroll = ttk.Scrollbar(container, orient=tk.HORIZONTAL)
        y_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL)

        canvas = tk.Canvas(container, bg=TABLE_BG, highlightthickness=0,
                           xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)

        x_scroll.config(command=canvas.xview)
        y_scroll.config(command=canvas.yview)

        x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        table_frame = tk.Frame(canvas, bg=TABLE_BG)
        canvas.create_window((0, 0), window=table_frame, anchor=tk.NW)

        data = map_data.get('data', [])
        x_axis = map_data.get('x_axis', [])
        y_axis = map_data.get('y_axis', [])

        if not data:
            tk.Label(table_frame, text="No data", bg=TABLE_BG, fg=TABLE_FG).grid(row=0, column=0)
            return

        cell_font = ('Consolas', 9)
        header_font = ('Consolas', 9, 'bold')
        pad = 2

        # Corner cell
        tk.Label(table_frame, text="", bg=HEADER_BG, fg=ACCENT, font=header_font,
                 width=8, relief=tk.FLAT, padx=pad, pady=pad).grid(row=0, column=0, sticky=tk.NSEW)

        # Column headers (x_axis = RPM or similar)
        for col_idx, x_val in enumerate(x_axis):
            txt = f"{x_val:g}" if isinstance(x_val, float) else str(x_val)
            tk.Label(table_frame, text=txt, bg=HEADER_BG, fg=ACCENT, font=header_font,
                     width=8, relief=tk.FLAT, padx=pad, pady=pad).grid(row=0, column=col_idx + 1, sticky=tk.NSEW)

        # Data rows
        for row_idx, row in enumerate(data):
            # Row header (y_axis)
            y_val = y_axis[row_idx] if row_idx < len(y_axis) else row_idx
            y_txt = f"{y_val:g}" if isinstance(y_val, float) else str(y_val)
            tk.Label(table_frame, text=y_txt, bg=HEADER_BG, fg=ACCENT, font=header_font,
                     width=8, relief=tk.FLAT, padx=pad, pady=pad).grid(row=row_idx + 1, column=0, sticky=tk.NSEW)

            for col_idx, val in enumerate(row):
                if isinstance(val, float):
                    txt = f"{val:.2f}" if abs(val) < 100 else f"{val:.1f}"
                else:
                    txt = str(val)

                bg = TABLE_BG
                fg = TABLE_FG
                if highlight:
                    fg = ACCENT2

                tk.Label(table_frame, text=txt, bg=bg, fg=fg, font=cell_font,
                         width=8, relief=tk.FLAT, padx=pad, pady=pad).grid(
                    row=row_idx + 1, column=col_idx + 1, sticky=tk.NSEW)

        table_frame.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _on_shift_mousewheel(event):
            canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)
        canvas.bind("<Shift-MouseWheel>", _on_shift_mousewheel)
        table_frame.bind("<MouseWheel>", _on_mousewheel)
        table_frame.bind("<Shift-MouseWheel>", _on_shift_mousewheel)

    # -----------------------------------------------------------------------
    # Show Individual Map
    # -----------------------------------------------------------------------
    def _show_map(self, map_name):
        if map_name not in self.calculated_maps:
            messagebox.showinfo("Not Calculated", f"{map_name} has not been calculated yet.")
            return
        # Find and select the tab
        for i in range(self.notebook.index("end")):
            tab_text = self.notebook.tab(i, "text").strip()
            if tab_text == map_name:
                self.notebook.select(i)
                return

    # -----------------------------------------------------------------------
    # Save BIN
    # -----------------------------------------------------------------------
    def _save_bin(self):
        if not self.calculated_maps:
            messagebox.showwarning("Nothing to Save", "Calculate maps first.")
            return

        # Ask for output path
        base = os.path.splitext(self.bin_path.get())[0]
        default_name = os.path.basename(base) + "_tuned.bin"
        output_path = filedialog.asksaveasfilename(
            title="Save Tuned BIN",
            initialfile=default_name,
            initialdir=os.path.dirname(self.bin_path.get()),
            filetypes=[("BIN Files", "*.bin"), ("All Files", "*.*")],
            defaultextension=".bin"
        )
        if not output_path:
            return

        self._set_status("Saving tuned BIN...")
        self._log("\n" + "=" * 70)
        self._log("SAVING TUNED BIN")
        self._log("=" * 70)

        # Work on a copy of the bin data
        modified_bin = bytearray(self.bin_data)

        # Write all calculated maps


        written = []
        for map_name in map_write_order:
            state_key = f'new_{map_name.lower()}'
            if state_key not in self.state:
                continue

##            table = find_target_map(self.xdf_data, map_name)
            table = self.mapTable[mapsname.index(map_name)]
            if not table:
                self._log(f"  ⚠ {map_name}: not found in XDF, skipping")
                continue

            new_map = self.state[state_key]
            new_values = new_map['data'] if isinstance(new_map, dict) else new_map

            try:
                write_map_data(modified_bin, table, new_values, self.base_offset)
                self._log(f"  ✓ {map_name}: written")
                written.append(map_name)
            except Exception as e:
                self._log_error(f"  ✗ {map_name}: {e}")

        # Save
        try:
            save_bin(modified_bin, output_path)
            self._log(f"\nSaved to: {output_path}")
            self._log(f"Size: {len(modified_bin)} bytes")
            self._log(f"Maps written: {', '.join(written)}")
            self._set_status(f"Saved: {os.path.basename(output_path)} ({len(written)} maps written)")
            messagebox.showinfo("Saved",
                                f"Tuned BIN saved successfully!\n\n"
                                f"File: {os.path.basename(output_path)}\n"
                                f"Maps: {', '.join(written)}\n\n"
                                f"⚠ Verify with wideband + logging before driving hard.")
        except Exception as e:
            self._log_error(f"Failed to save: {e}")
            messagebox.showerror("Save Error", str(e))


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
def main():
    root = tk.Tk()

    # Set icon if available
    try:
        root.iconbitmap(default='')
    except Exception:
        pass

    app = ME7TunerApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
