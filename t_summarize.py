#!/usr/bin/env python3
"""Summarize thermodynamic outputs from UppASD temperature sweeps.

Scans temperature folders (T_*) for cumulants.<simid>.out JSON files,
extracts selected fields and writes a space-delimited report
`thermo_report.txt` with columns:

  temperature magnetization binder_cumulant energy susceptibility specific_heat specific_heat_dedt

specific_heat_dedt is computed as d(energy)/d(temperature) using a
numerical derivative (numpy.gradient).
"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np


def find_cumulant_file(folder_path: Path):
    # Prefer exactly 8-char simid: cumulants.????????.json or .out
    for ext in ("json", "out"):
        pattern = f"cumulants.????????.{ext}"
        for p in folder_path.glob(pattern):
            if p.is_file():
                return p

    # Fall back to any cumulants.*.json or cumulants.*.out
    for ext in ("json", "out"):
        for p in folder_path.glob(f"cumulants.*.{ext}"):
            if p.is_file():
                return p

    return None


def parse_cumulant_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    def get_float(key):
        v = data.get(key)
        if v is None:
            return np.nan
        try:
            return float(v)
        except Exception:
            return np.nan

    return {
        "temperature": get_float("temperature"),
        "magnetization": get_float("magnetization"),
        "binder_cumulant": get_float("binder_cumulant"),
        "energy": get_float("energy"),
        "susceptibility": get_float("susceptibility"),
        "specific_heat": get_float("specific_heat"),
    }


def summarize(base_path: Path, out_path: Path):
    entries = []
    for d in sorted(base_path.iterdir()):
        if not d.is_dir():
            continue
        if not d.name.startswith("T_"):
            continue

        cum_path = find_cumulant_file(d)
        if cum_path is None:
            continue
        parsed = parse_cumulant_json(cum_path)
        if parsed is None:
            continue
        entries.append(parsed)

    if not entries:
        print("No cumulant JSON files found.")
        return

    # Sort by temperature
    entries.sort(key=lambda e: e["temperature"] if not np.isnan(e["temperature"]) else np.inf)

    T = np.array([e["temperature"] for e in entries], dtype=float)
    M = np.array([e["magnetization"] for e in entries], dtype=float)
    B = np.array([e["binder_cumulant"] for e in entries], dtype=float)
    E = np.array([e["energy"] for e in entries], dtype=float)
    X = np.array([e["susceptibility"] for e in entries], dtype=float)
    C = np.array([e["specific_heat"] for e in entries], dtype=float)

    # Compute derivative dE/dT using numpy.gradient. For single point result is nan.
    if T.size > 1:
        dEdT = np.gradient(E, T)
    else:
        dEdT = np.array([np.nan])

    # Convert dE/dT from mRy/(atom K) to k_B/atom by multiplying with
    # (mRy -> J) / k_B. Constants: 1 Ry = 13.605693009 eV, 1 eV = 1.602176634e-19 J,
    # k_B = 1.380649e-23 J/K.
    mry_to_j = 1e-3 * 13.605693009 * 1.602176634e-19
    k_b = 1.380649e-23
    MRY_TO_KB = mry_to_j / k_b
    dEdT_kb = dEdT * MRY_TO_KB

    # Compute entropy S(T) in units of k_B/atom by integrating C(T)/T dT
    # where C is specific_heat in units k_B/atom. Use trapezoidal integration.
    if T.size > 1:
        # Replace zero (or non-positive) T for the integrand to avoid divide-by-zero.
        # Per convention: if T == 0, use 1e-3 K as a small positive temperature.
        T_safe = np.where(T <= 0.0, 1e-3, T)
        integrand = C / T_safe
        dT = np.diff(T)
        trap = 0.5 * (integrand[:-1] + integrand[1:]) * dT
        S_num = np.concatenate(([0.0], np.cumsum(trap)))
    else:
        S_num = np.array([0.0])

    # Compute Helmholtz free energy F = E - T*S. E is in mRy/atom, S_num in k_B/atom.
    # Convert T*S (k_B units) to mRy by: T * S_num * k_B (J) -> divide by mry_to_j to get mRy
    # So F_mRy = E_mRy - T * S_num * k_b / mry_to_j = E_mRy - T * S_num / MRY_TO_KB
    F_mRy = E - (T * S_num) / MRY_TO_KB

    out_arr = np.column_stack([T, M, B, E, X, C, dEdT_kb, S_num, F_mRy])

    header = (
        # "temperature magnetization binder_cumulant energy "
        # "susceptibility specific_heat specific_heat_dedt entropy_kBperatom helmholtz_mRyperatom"
        "       T(K)       M_avg(mu_B)        U4            E (mRy)        Xsi     "
        "   C_v_f (k_B)    C_v_e (k_B)      S (k_B)          F (mRy)"
    )
# temperature magnetization binder_cumulant energy susceptibility specific_heat specific_heat_dedt entropy_kBperatom helmholtz_mRyperatom
# "       T(K)       M_avg(mu_B)        U4             Xsi            E (mRy)      C_v_f (k_B)    C_v_e (k_B)      S (k_B)        F (mRy)"
# "       T(K)       M_avg(mu_B)        U4             Xsi            E (mRy)""      C_v_f (k_B)    C_v_e (k_B)      S (k_B)        F (mRy)"
#      0.00100000     2.37389917     0.66666667   -11.47916078     0.00000000     2.62671868     1.01313888     0.00000000   -11.47916078

    np.savetxt(out_path, out_arr, fmt="% 14.8f", delimiter=" ", header=header, comments="# ")
    print(f"Wrote summary to: {out_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Summarize cumulant JSON outputs into a thermo report")
    parser.add_argument("--base", type=str, default=".", help="Base folder containing T_* directories (default: .)")
    parser.add_argument("--out", type=str, default="thermo_report.txt", help="Output file (default: thermo_report.txt)")
    args = parser.parse_args(argv)

    base = Path(args.base)
    if not base.exists() or not base.is_dir():
        print("Base path does not exist or is not a directory:", base)
        sys.exit(1)

    summarize(base, Path(args.out))


if __name__ == "__main__":
    main()
