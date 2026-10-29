"""Build the tide data the API computes the tide with (``app/data/tides-eot20.npz``, ``app/data/tides-arguments.npz``; ADR 0015).

    python tools/build_tides.py --eot20 DIR      # DIR holds ocean_tides/*_ocean_eot20.nc
    python tools/build_tides.py --eot20 DIR --check-only

Input: the **ocean tide** files of **EOT20** (DGFI-TUM; Hart-Davis, Piccioni, Dettmering, Schwatke, Passaro and Seitz, 2021, "EOT20 - A
global Empirical Ocean Tide model from multi-mission satellite altimetry", SEANOE, doi:10.17882/79489), **CC BY 4.0**, downloaded with no
registration from https://www.seanoe.org/data/00683/79489/data/85762.zip (2,330,678,793 bytes on 2026-10-28; it holds ``ocean_tides.zip``
and ``load_tides.zip``: only the first is used). 17 constituents on a 1/8 degree grid; SEANOE says the model covers 66 S to 66 N and that
FES2014b fills the higher latitudes: only 66 S to 66 N is kept.

Needs **pyTMD 3.0.9** and **netCDF4**, which the API itself does not (``pip install "pyTMD==3.0.9" netCDF4``, in a separate environment:
pyTMD brings scipy, xarray and pyproj). pyTMD gives the astronomical arguments, the nodal corrections (the FES convention, its choice for
EOT20) and the inference of the minor constituents; the API then only sums cosines (``app/services/tides.py``).

What is kept, and what is changed (the licence asks that changes are said; ``app/data/README.md`` repeats it):

- **The ocean cells near a coast.** A cell is kept when it has a value for all 17 constituents (pyTMD's rule: an amplitude and a phase
  that are not 0) and lies within ``BAND_CELLS`` cells of a cell that has none (land, for the model), between 66 S and 66 N.
- **Not the cells with a constant no tide has.** In the estuary of the St Lawrence, the mouths of the Amazon and the Para, Lake Maracaibo
  and along the 66 S edge, EOT20 gives S1 or T2 amplitudes of metres (up to 45 m for S1); in the equilibrium tide T2 is about 6 % of S2 and
  S1 is a few centimetres. A cell where S1 or T2 is above ``ARTEFACT_CM`` is dropped, with the cells within ``ARTEFACT_BAND_CELLS`` of it.
- **The constants rounded to the millimetre**: real and imaginary parts of ``amplitude * exp(-i * phase)`` (pyTMD's convention), as
  16-bit integers, stored as differences from the previous cell.
- **The arguments every ``TABLE_STEP_DAYS`` days** from ``TABLE_START`` to ``TABLE_END``: the nodal factor ``f`` and the phase
  ``theta - omega * (t - origin)`` (the equilibrium argument plus the nodal angle, less its linear part), which both vary slowly; the API
  interpolates them.

The tool then compares ``app/services/tides.py`` with pyTMD's own prediction (``tide_elevations``, nearest cell, the same model) at
``CHECK_POINTS`` over three periods, and fails above ``CHECK_TOLERANCE_M``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.tides import ARGUMENTS_FILE, CONSTANTS_FILE, TideModel, mjd_from_unix  # noqa: E402

MODEL = "EOT20"
# pyTMD's names and order for the EOT20 files (its model database), lower case.
CONSTITUENTS = ["2n2", "j1", "k1", "k2", "m2", "m4", "mf", "mm", "n2", "o1", "p1", "q1", "s1", "s2", "sa", "ssa", "t2"]
# The minor constituents pyTMD infers for EOT20 with the FES corrections (tide_elevations(...).attrs["inferred"], pyTMD 3.0.9), in the
# order of pyTMD.constituents.minor_arguments, which returns 20 columns; j1, 2n2 and t2 are in the model and are not inferred.
MINOR_ARGUMENT_ORDER = ["2q1", "sigma1", "rho1", "m1b", "m1", "chi1", "pi1", "phi1", "theta1", "j1", "oo1", "2n2", "mu2", "nu2",
                        "lambda2", "l2", "l2b", "t2", "eps2", "eta2"]  # fmt: skip
INFERRED = [name for name in MINOR_ARGUMENT_ORDER if name not in CONSTITUENTS]
STEP_DEG = 0.125
COLUMNS = 2880  # 0 to 359.875 degrees east (the file repeats 360)
MAX_ABS_LAT = 66.0
BAND_CELLS = 3
ARTEFACT_CM = 20.0
ARTEFACT_BAND_CELLS = 2
TABLE_START = dt.date(2024, 1, 1)
TABLE_END = dt.date(2045, 1, 1)
TABLE_STEP_DAYS = 10
# pyTMD measures time in days from 1992-01-01 (MJD 48622).
PYTMD_EPOCH_MJD = 48622.0
CHECK_POINTS = [(48.38, -4.50, "Brest"), (48.64, -2.03, "Saint-Malo"), (49.48, 0.11, "Le Havre"), (40.70, -74.01, "New York"),
                (21.30, -157.86, "Honolulu"), (-33.86, 151.21, "Sydney"), (45.30, -66.06, "Saint John (Fundy)")]  # fmt: skip
CHECK_STARTS = [dt.datetime(2026, 10, 1), dt.datetime(2035, 6, 15), dt.datetime(2044, 12, 20)]
CHECK_TOLERANCE_M = 0.005


def read_eot20(directory: Path) -> tuple[np.ndarray, dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """The latitudes, the complex constants (cm) of each constituent, the cells that have all 17, and those free of artefacts."""
    import netCDF4

    constants: dict[str, np.ndarray] = {}
    amplitudes: dict[str, np.ndarray] = {}
    valid: np.ndarray | None = None
    latitudes = np.zeros(0)
    for name in CONSTITUENTS:
        path = directory / "ocean_tides" / f"{name.upper()}_ocean_eot20.nc"
        with netCDF4.Dataset(path) as data:
            data.set_auto_mask(False)
            latitudes = np.asarray(data["lat"][:])
            amplitude = np.asarray(data["amplitude"][:, :COLUMNS])
            phase = np.asarray(data["phase"][:, :COLUMNS])
        has = (amplitude != 0) & (phase != 0) & np.isfinite(amplitude) & np.isfinite(phase)
        valid = has if valid is None else valid & has
        constants[name] = amplitude * np.exp(-1j * np.radians(phase))
        amplitudes[name] = amplitude
    assert valid is not None
    if len(latitudes) != 1441 or latitudes[0] != -90.0 or latitudes[1] - latitudes[0] != STEP_DEG:
        raise SystemExit("unexpected EOT20 grid")
    return latitudes, constants, valid, _amplitudes_ok(amplitudes)


def _amplitudes_ok(amplitudes: dict[str, np.ndarray]) -> np.ndarray:
    """False on the cells with an S1 or T2 no tide has, and on those near them."""
    artefact = (amplitudes["s1"] > ARTEFACT_CM) | (amplitudes["t2"] > ARTEFACT_CM)
    return ~_dilate(artefact, ARTEFACT_BAND_CELLS)


def _dilate(mask: np.ndarray, cells: int) -> np.ndarray:
    """``mask`` grown by ``cells`` in the eight directions, wrapping around in longitude."""
    grown = mask.copy()
    for _ in range(cells):
        padded = np.pad(grown, ((1, 1), (0, 0)), constant_values=False)
        rows = padded[:-2] | padded[1:-1] | padded[2:]
        grown = rows | np.roll(rows, 1, axis=1) | np.roll(rows, -1, axis=1)
    return grown


def coastal_cells(latitudes: np.ndarray, valid: np.ndarray, sound: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The flat keys (row * COLUMNS + column, sorted) of the cells kept, and their ring: 1 next to a cell with no value (land, for
    the model), up to BAND_CELLS. The band is drawn from the model's own land; the artefacts are taken out after."""
    ring = np.zeros(valid.shape, dtype=np.uint8)
    for distance in range(BAND_CELLS, 0, -1):
        ring[_dilate(~valid, distance)] = distance
    keep = valid & sound & (ring > 0) & (np.abs(latitudes) <= MAX_ABS_LAT)[:, None]
    rows, columns = np.nonzero(keep)
    return rows.astype(np.int64) * COLUMNS + columns, ring[rows, columns]


def write_constants(keys: np.ndarray, rings: np.ndarray, constants: dict[str, np.ndarray], path: Path) -> None:
    rows, columns = np.divmod(keys, COLUMNS)
    millimetres = np.stack(
        [np.stack([np.real(constants[name][rows, columns]), np.imag(constants[name][rows, columns])]) * 10.0 for name in CONSTITUENTS]
    )
    if np.abs(millimetres).max() > 32767:
        raise SystemExit("a constant does not fit in 16 bits")
    exact = np.round(millimetres).astype(np.int16)  # (17, 2, cells)
    delta = np.diff(exact, axis=-1, prepend=np.zeros((len(CONSTITUENTS), 2, 1), dtype=np.int16)).astype(np.int16)
    if not np.array_equal(np.cumsum(delta, axis=-1, dtype=np.int16), exact):
        raise SystemExit("the differences do not add up to the constants")
    np.savez_compressed(
        path,
        model=np.array([MODEL]),
        step_deg=np.array([STEP_DEG]),
        lat_origin=np.array([-90.0]),
        columns=np.array([COLUMNS]),
        cell_delta=np.diff(keys, prepend=0).astype(np.int32),
        ring=rings,
        constants_delta=delta,
        constituents=np.array(CONSTITUENTS),
    )


def _deltat_days(mjd: np.ndarray) -> np.ndarray:
    """TT - UT1 (days) at the times ``mjd``, as pyTMD's tide_elevations takes it (timescale)."""
    import timescale

    seconds = (mjd - 51544.0) * 86400.0  # since 2000-01-01
    return np.asarray(timescale.from_deltatime(seconds, epoch=(2000, 1, 1, 0, 0, 0), standard="UTC").tt_ut1)


def arguments_table() -> dict[str, np.ndarray]:
    """theta and f of every constituent, every TABLE_STEP_DAYS days (pyTMD, FES corrections)."""
    import pyTMD.constituents

    start = mjd_from_unix(dt.datetime.combine(TABLE_START, dt.time(), dt.UTC).timestamp())
    end = mjd_from_unix(dt.datetime.combine(TABLE_END, dt.time(), dt.UTC).timestamp())
    mjd = np.arange(start, end + TABLE_STEP_DAYS, TABLE_STEP_DAYS, dtype=np.float64)
    deltat = _deltat_days(mjd)
    major_u, major_f, major_g = pyTMD.constituents.arguments(mjd, CONSTITUENTS, deltat=deltat, corrections="FES")
    minor_u, minor_f, minor_g = pyTMD.constituents.minor_arguments(mjd, deltat=deltat, corrections="FES")
    columns = [MINOR_ARGUMENT_ORDER.index(name) for name in INFERRED]
    theta = np.concatenate([np.radians(major_g) + major_u, (np.radians(minor_g) + minor_u)[:, columns]], axis=1)
    factor = np.concatenate([major_f, minor_f[:, columns]], axis=1)
    names = CONSTITUENTS + INFERRED
    # The speed of each constituent: the mean rate of theta over the table (its integer number of turns read from pyTMD's frequency).
    approximate = np.asarray(pyTMD.constituents.frequency(names, corrections="FES")) * 86400.0  # rad/day
    span = mjd[-1] - mjd[0]
    turns = np.round((approximate * span - (theta[-1] - theta[0])) / (2 * np.pi))
    omega = (theta[-1] - theta[0] + 2 * np.pi * turns) / span
    phase = np.unwrap(theta - omega[None, :] * (mjd - mjd[0])[:, None], axis=0)
    if np.abs(np.diff(phase, axis=0)).max() > 0.5:
        raise SystemExit("the phase is not slow enough to interpolate")
    return {
        "constituents": np.array(names),
        "omega": omega,
        "origin_mjd": np.array([mjd[0]]),
        "mjd": mjd,
        "phase": phase,
        "factor": factor,
        "inference": inference_matrix(mjd[0], deltat[0]),
    }


def inference_matrix(mjd: float, deltat: float) -> np.ndarray:
    """The matrix that gives the minor constants from the major ones (17 x 17), read from pyTMD itself.

    pyTMD infers each minor constant as a fixed linear combination of major ones. Asking it for one minor constituent at a time,
    with a dataset whose points are the unit constants of each major one, gives its row: the series it returns at two times is
    ``f * Re(M * exp(i * theta))``, with f and theta known, so M is read exactly.
    """
    import pyTMD.constituents
    import xarray as xr
    from pyTMD.predict import infer_minor

    eye = np.eye(len(CONSTITUENTS), dtype=np.complex128)
    dataset = xr.Dataset({name: (("point",), eye[:, j]) for j, name in enumerate(CONSTITUENTS)})
    times = np.array([mjd, mjd + 0.37]) - PYTMD_EPOCH_MJD
    minor_u, minor_f, minor_g = pyTMD.constituents.minor_arguments(times + PYTMD_EPOCH_MJD, deltat=deltat, corrections="FES")
    matrix = np.zeros((len(INFERRED), len(CONSTITUENTS)))
    for row, name in enumerate(INFERRED):
        inferred = infer_minor(times, dataset, deltat=deltat, corrections="FES", minor=[name], infer_long_period=False)
        series = np.asarray(inferred.transpose("point", "time"))  # (17, 2)
        column = MINOR_ARGUMENT_ORDER.index(name)
        theta = np.radians(minor_g[:, column]) + minor_u[:, column]
        system = np.stack([minor_f[:, column] * np.cos(theta), -minor_f[:, column] * np.sin(theta)], axis=1)
        solved = np.linalg.solve(system, series.T)  # (2, 17): real and imaginary parts
        if np.abs(solved[1]).max() > 1e-9:
            raise SystemExit(f"the inference of {name} is not real")
        matrix[row] = solved[0]
    return matrix


def check(eot20: Path) -> float:
    """The largest difference between app/services/tides.py and pyTMD over the check points and periods (metres)."""
    import pyTMD.compute

    model = TideModel()
    worst = 0.0
    for lat, lon, name in CHECK_POINTS:
        cell = model.nearest_cell(lat, lon)
        for start in CHECK_STARTS:
            seconds = (start - dt.datetime(2000, 1, 1)).total_seconds() + np.arange(0, 3 * 86400, 600.0)
            reference = pyTMD.compute.tide_elevations(
                np.array([cell.lon]), np.array([cell.lat]), seconds, directory=eot20.parent, model=MODEL,
                type="time series", method="nearest", standard="UTC", epoch=(2000, 1, 1, 0, 0, 0),
            )  # fmt: skip
            ours = model.heights(cell.constants, seconds / 86400.0 + 51544.0)
            difference = float(np.abs(np.asarray(reference).ravel() - ours).max())
            worst = max(worst, difference)
            print(f"  {name:20} {start:%Y-%m-%d}  cell {cell.lat:.3f},{cell.lon:.3f} ({cell.distance_km:.1f} km)  max diff {difference * 1000:.2f} mm")
    return worst


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--eot20", type=Path, required=True, help="the folder named EOT20 that holds ocean_tides/*_ocean_eot20.nc (pyTMD's layout)"
    )
    parser.add_argument("--check-only", action="store_true", help="only compare the bundled files with pyTMD")
    args = parser.parse_args()
    if not args.check_only:
        latitudes, constants, valid, sound = read_eot20(args.eot20)
        keys, rings = coastal_cells(latitudes, valid, sound)
        write_constants(keys, rings, constants, CONSTANTS_FILE)
        print(f"{CONSTANTS_FILE.name}: {len(keys):,} cells, {CONSTANTS_FILE.stat().st_size:,} bytes")
        np.savez_compressed(ARGUMENTS_FILE, **arguments_table())
        print(f"{ARGUMENTS_FILE.name}: {ARGUMENTS_FILE.stat().st_size:,} bytes")
    worst = check(args.eot20)
    print(f"largest difference with pyTMD: {worst * 1000:.2f} mm (tolerance {CHECK_TOLERANCE_M * 1000:.0f} mm)")
    if worst > CHECK_TOLERANCE_M:
        raise SystemExit("the API's tide differs from pyTMD")


if __name__ == "__main__":
    main()
