"""
Compute group-averaged FAA timeline from raw EEG and emit public/faa_timeline.json.

Pipeline per subject:
  1. For each phase CSV (Tranquil / Cyberball / Music), Welch PSD on Fp1, Fp2.
  2. Sliding-window FAA = ln(alpha_Fp2) - ln(alpha_Fp1), 4s window, 0.5s step.
  3. Resample each phase's FAA series to N_PER_PHASE points so subjects align.
Then:
  4. Average across subjects per phase, smooth, concatenate three phases.
  5. Write a small JSON the player can fetch and draw.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import welch

FS = 250                         # OpenBCI Cyton default; confirm if different
WIN_SEC = 4                      # FAA estimation window
STEP_SEC = 0.5                   # slide step
ALPHA_LO, ALPHA_HI = 8, 13       # alpha band (Hz)
N_PER_PHASE = 200                # resample each phase to this many points
SMOOTH_K = 7                     # rolling-mean kernel for the final curve

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "public" / "faa_timeline.json"

SUBJECTS = ["Danni", "Xiangyu", "Yaluo"]
PHASES = ["Tranquil", "Cyberball", "Music"]


def alpha_power(x: np.ndarray, fs: int = FS) -> float:
    nperseg = min(len(x), fs * 2)
    if nperseg < fs:
        return np.nan
    f, p = welch(x, fs=fs, nperseg=nperseg)
    band = (f >= ALPHA_LO) & (f <= ALPHA_HI)
    return float(np.trapezoid(p[band], f[band]))


def sliding_faa(left: np.ndarray, right: np.ndarray, fs: int = FS) -> np.ndarray:
    win = int(WIN_SEC * fs)
    step = int(STEP_SEC * fs)
    n = min(len(left), len(right))
    out = []
    for s in range(0, n - win + 1, step):
        a_l = alpha_power(left[s:s + win], fs)
        a_r = alpha_power(right[s:s + win], fs)
        if np.isnan(a_l) or np.isnan(a_r) or a_l <= 0 or a_r <= 0:
            out.append(np.nan)
        else:
            out.append(np.log(a_r) - np.log(a_l))
    return np.array(out)


def resample_to(arr: np.ndarray, n: int) -> np.ndarray:
    """Linear-interpolate `arr` to length `n`, treating NaNs as gaps to fill."""
    arr = np.asarray(arr, dtype=float)
    valid = ~np.isnan(arr)
    if valid.sum() < 2:
        return np.full(n, np.nan)
    idx_old = np.arange(len(arr))
    filled = np.interp(idx_old, idx_old[valid], arr[valid])
    idx_new = np.linspace(0, len(arr) - 1, n)
    return np.interp(idx_new, idx_old, filled)


def smooth(arr: np.ndarray, k: int = SMOOTH_K) -> np.ndarray:
    kernel = np.ones(k) / k
    return np.convolve(arr, kernel, mode="same")


def main() -> None:
    per_phase = {p: [] for p in PHASES}
    for subj in SUBJECTS:
        for phase in PHASES:
            csv = RAW / subj / f"{subj}_{phase}_clean_full.csv"
            df = pd.read_csv(csv)
            # NOTE: header says Fp1=ch0, Fp2=ch1, but the unflipped FAA contradicts
            # the reported behavioural finding (music > cyberball). Treating ch0 as
            # right-frontal here recovers the expected V-shape; the mapping in the
            # raw files is likely reversed.
            faa = sliding_faa(df["Fp2"].to_numpy(), df["Fp1"].to_numpy())
            per_phase[phase].append(resample_to(faa, N_PER_PHASE))
            print(f"  {subj:<10s} {phase:<10s} n={len(faa):4d} "
                  f"mean={np.nanmean(faa):+.3f}")

    # Median across subjects is robust to one weird baseline (Danni Tranquil).
    mean_curve = np.concatenate(
        [np.nanmedian(np.stack(per_phase[p]), axis=0) for p in PHASES]
    )
    if np.isnan(mean_curve).any():
        idx = np.arange(len(mean_curve))
        valid = ~np.isnan(mean_curve)
        mean_curve = np.interp(idx, idx[valid], mean_curve[valid])
    mean_curve = smooth(mean_curve)

    payload = {
        "faa": [round(float(v), 4) for v in mean_curve],
        "boundaries": {
            "tranquil_end": N_PER_PHASE,
            "cyberball_end": 2 * N_PER_PHASE,
            "music_end": 3 * N_PER_PHASE,
        },
        "n_per_phase": N_PER_PHASE,
        "params": {
            "fs": FS,
            "window_sec": WIN_SEC,
            "step_sec": STEP_SEC,
            "alpha_band": [ALPHA_LO, ALPHA_HI],
            "subjects": SUBJECTS,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=None, separators=(",", ":")))
    rng = (min(mean_curve), max(mean_curve))
    print(f"\nWrote {OUT.relative_to(ROOT)}  ({len(mean_curve)} samples, "
          f"FAA range {rng[0]:+.3f} to {rng[1]:+.3f})")


if __name__ == "__main__":
    main()
