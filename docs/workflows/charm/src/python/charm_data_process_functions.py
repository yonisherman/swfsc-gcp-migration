# charm_data_process_functions.py

from __future__ import annotations
from numpy.typing import NDArray
import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Sequence
from netCDF4 import Dataset
import numpy as np
import numpy.ma as ma
import concurrent.futures
from scipy.interpolate import griddata, RegularGridInterpolator
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Tuple

from .charm_helper_functions import run_cmd

# logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _meanVar3(
    mean: ma.MaskedArray,
    num: ma.MaskedArray,
    obs: ma.MaskedArray
) -> Tuple[ma.MaskedArray, ma.MaskedArray]:
    """
    Incrementally update a running mean and sample count with new observations.

    This function is designed to update a masked-array mean and count (``num``)
    in place when new observations (``obs``) become available.
    It automatically ignores masked values in ``obs``.

    Args:
        mean (MaskedArray): Current running mean array.
        num (MaskedArray): Current running sample count array.
        obs (MaskedArray): New observation array (same shape as ``mean``).

    Returns:
        tuple[MaskedArray, MaskedArray]: Updated mean and count arrays.

    Notes:
        - This implements an incremental (online) mean update:
          ``mean_new = mean_old + (obs - mean_old) / num_new``
        - Masked values in ``obs`` do not increment the count.

    Examples:
        >>> m = ma.zeros((2,2))
        >>> n = ma.zeros((2,2))
        >>> new_obs = ma.array([[1,2],[3,ma.masked]])
        >>> m, n = _meanVar3(m, n, new_obs)
        >>> m
        masked_array(
          data=[[1.0, 2.0],
                [3.0, --]],
          mask=[[False, False],
                [False,  True]],
          fill_value=1e+20)
        >>> n
        masked_array(
          data=[[1, 1],
                [1, 0]],
          mask=[[False, False],
                [False,  True]],
          fill_value=999999)
    """
    num_shape = num.shape
    diff = np.subtract(obs, mean, dtype=float)

    # add 1 where obs is valid
    num_add = np.ones(num_shape, dtype=int)
    obs_mask = ma.getmask(obs)
    num_add = ma.masked_where(obs_mask, num_add)
    num = ma.add(num, num_add, dtype=int)

    # compute increment safely
    temp_num = ma.masked_where(num == 0, num)
    temp_num = ma.masked_invalid(temp_num).astype('float')

    increment = ma.divide(diff, temp_num, dtype=float)
    increment = ma.masked_invalid(increment)

    mean = ma.add(mean, increment, dtype=float)
    return mean, num


def _update_mean(mean, count, new_obs):
    """
    Update a running mean and count with new observations.

    Args:
        mean (MaskedArray): Current mean array.
        count (MaskedArray): Current sample count array.
        new_obs (MaskedArray): New observation array.

    Returns:
        tuple[MaskedArray, MaskedArray]: Updated mean and count arrays.
    """
    # Only update where new_obs is valid
    mask = ~ma.getmaskarray(new_obs)

    # increment count where valid
    count[mask] = count[mask] + 1
    n = count.astype(float)

    # compute mean increment
    mean[mask] = mean[mask] + (new_obs[mask] - mean[mask]) / n[mask]

    return mean, count


def m_fdist(
    lon1: np.ndarray | list | float,
    lat1: np.ndarray | list | float,
    azimuth: float,
    distance: float,
    spheroid: str = "wgs84",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute the destination point(s) and reverse azimuth along a geodesic on a
    specified ellipsoidal spheroid using Vincenty's forward formula.

    Given starting positions (longitude and latitude), a forward azimuth (bearing),
    and a surface distance, this function computes:
    
    * the destination longitude(s)
    * the destination latitude(s)
    * the reverse azimuth(s) back toward the starting point

    This implementation is fully vectorized and supports scalar values,
    Python lists, or NumPy arrays as inputs.

    Args:
        lon1 (float | list | np.ndarray):
            Starting longitude(s) in degrees east. Scalars, lists, and NumPy arrays
            are all accepted. Shape must match ``lat1``.
        lat1 (float | list | np.ndarray):
            Starting latitude(s) in degrees north. Must match the shape of ``lon1``.
        azimuth (float):
            Forward bearing in degrees clockwise from north
            (0° = north, 90° = east, 180° = south).
        distance (float):
            Geodesic distance to travel along the azimuth in **metres**.
        spheroid (str, optional):
            Name of the reference ellipsoid. Supported values include:
            ``"normal"``, ``"sphere"``, ``"grs80"``, ``"grs67"``,
            ``"wgs84"``, ``"wgs72"``, ``"wgs66"``, ``"wgs60"``,
            ``"clrk66"``, ``"clrk80"``, ``"intl24"``, ``"intl67"``.
            Defaults to ``"wgs84"``.

    Returns:
        tuple[np.ndarray, np.ndarray, np.ndarray]:
            A tuple ``(lon2, lat2, back_azimuth)`` where:

            - ``lon2``: Destination longitude(s) in degrees east.
            - ``lat2``: Destination latitude(s) in degrees north.
            - ``back_azimuth``: Reverse bearing at the destination point(s),
              in degrees clockwise from north.

    Raises:
        KeyError:
            If ``spheroid`` is not in the supported spheroid table.
        ValueError:
            If ``lon1`` and ``lat1`` have incompatible shapes.

    Notes:
        * Algorithm: Vincenty's forward formula (ellipsoidal geodesics).
        * Output longitude is wrapped into the ``0–360°`` range.
        * This function converges for the vast majority of Earth geodesics but,
          like all Vincenty-based formulas, may fail for nearly antipodal points.

    Examples:
        >>> import numpy as np
        >>> lon1 = np.array([-123.0])
        >>> lat1 = np.array([37.0])
        >>> lon2, lat2, baz = m_fdist(lon1, lat1, azimuth=90, distance=1000)
        >>> lon2.shape, lat2.shape
        ((1,), (1,))
    """
    spheroids = {
        "normal": (1.0, 0.0),
        "sphere": (6370997.0, 0.0),
        "grs80": (6378137.0, 1.0 / 298.257),
        "grs67": (6378160.0, 1.0 / 247.247),
        "wgs84": (6378137.0, 1.0 / 298.257223563),
        "wgs72": (6378135.0, 1.0 / 298.260),
        "wgs66": (6378145.0, 1.0 / 298.250),
        "wgs60": (6378165.0, 1.0 / 298.300),
        "clrk66": (6378206.4, 1.0 / 294.980),
        "clrk80": (6378249.1, 1.0 / 293.466),
        "intl24": (6378388.0, 1.0 / 297.000),
        "intl67": (6378157.5, 1.0 / 298.250),
    }

    if spheroid not in spheroids:
        raise KeyError(f"Unsupported spheroid: {spheroid}")

    a, f = spheroids[spheroid]
    b = a * (1.0 - f)

    lon1 = np.asanyarray(lon1, dtype=np.float64)
    lat1 = np.asanyarray(lat1, dtype=np.float64)

    if lon1.shape != lat1.shape:
        raise ValueError("lon1 and lat1 must have the same shape")

    pi180 = np.pi / 180.0

    # Reduced latitude U1
    U1 = np.arctan((1.0 - f) * np.tan(lat1 * pi180))
    sinU1 = np.sin(U1)
    cosU1 = np.cos(U1)

    alpha1 = azimuth * pi180
    sinAlpha1 = np.sin(alpha1)
    cosAlpha1 = np.cos(alpha1)

    sigma1 = np.arctan2(np.tan(U1), cosAlpha1)
    sinAlpha = cosU1 * sinAlpha1
    cosSqAlpha = 1.0 - sinAlpha**2

    uSq = cosSqAlpha * (a**2 - b**2) / (b**2)
    A = 1 + (uSq / 16384.0) * (4096.0 + uSq * (-768.0 + uSq * (320.0 - 175.0 * uSq)))
    B = (uSq / 1024.0) * (256.0 + uSq * (-128.0 + uSq * (74.0 - 47.0 * uSq)))

    sigma = distance / (b * A)

    # Iterative solution
    for _ in range(100):
        twoSigmaM = 2 * sigma1 + sigma
        cos2SigmaM = np.cos(twoSigmaM)
        sinSigma = np.sin(sigma)
        cosSigma = np.cos(sigma)

        deltaSigma = (
            B * sinSigma *
            (
                cos2SigmaM
                + (B / 4.0)
                * (cosSigma * (-1 + 2 * cos2SigmaM**2)
                   - (B / 6.0) * cos2SigmaM
                   * (-3 + 4 * sinSigma**2)
                   * (-3 + 4 * cos2SigmaM**2))
            )
        )

        sigma_prev = sigma
        sigma = distance / (b * A) + deltaSigma

        if np.all(np.abs(sigma - sigma_prev) < 1e-12):
            break
    else:
        logger.warning("Vincenty forward algorithm did not converge after 100 iterations")

    sinSigma = np.sin(sigma)
    cosSigma = np.cos(sigma)

    tmp = sinU1 * sinSigma - cosU1 * cosSigma * cosAlpha1

    lat2 = np.arctan2(
        sinU1 * cosSigma + cosU1 * sinSigma * cosAlpha1,
        (1 - f) * np.sqrt(sinAlpha**2 + tmp**2),
    )

    lamb = np.arctan2(
        sinSigma * sinAlpha1,
        cosU1 * cosSigma - sinU1 * sinSigma * cosAlpha1,
    )

    C = (f / 16.0) * cosSqAlpha * (4.0 + f * (4.0 - 3.0 * cosSqAlpha))
    L = lamb - (
        (1 - C) * f * sinAlpha *
        (sigma + C * sinSigma * (cos2SigmaM + C * cosSigma * (-1 + 2 * cos2SigmaM**2)))
    )

    lon2 = (lon1 + L / pi180) % 360.0
    lat2 = lat2 / pi180

    back_azimuth = (np.arctan2(-sinAlpha, tmp) / pi180) % 360.0

    return lon2, lat2, back_azimuth


def LonLatPerCM(Lon: np.ndarray, Lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Calculate the longitude and latitude displacement equivalent to 1 cm at a given
    location on Earth.

    This function uses a spheroid model (WGS84) to compute how much a 1 cm movement
    corresponds to a change in longitude and latitude at each grid point.

    Args:
        Lon (np.ndarray): Array of longitudes (degrees east). Can be 1-D or 2-D.
        Lat (np.ndarray): Array of latitudes (degrees north). Same shape as ``Lon``.

    Returns:
        tuple:
            np.ndarray: ``LonPerCM`` — change in longitude (degrees) for 1 cm
            east-west displacement at each grid point.
            np.ndarray: ``LatPerCM`` — change in latitude (degrees) for 1 cm
            north-south displacement at each grid point.

    Raises:
        ValueError: If ``Lon`` and ``Lat`` have different shapes.

    Examples:
        >>> import numpy as np
        >>> from yourmodule import LonLatPerCM
        >>> lon = np.array([[ -123.0, -122.5],
        ...                 [ -123.0, -122.5]])
        >>> lat = np.array([[  37.0,  37.0],
        ...                 [  36.5,  36.5]])
        >>> dlon, dlat = LonLatPerCM(lon, lat)
        >>> dlon.shape, dlat.shape
        ((2, 2), (2, 2))
        >>> float(dlon[0,0]), float(dlat[0,0])  # doctest: +SKIP
        (some small degree change, some small degree change)
    """
    if Lon.shape != Lat.shape:
        raise ValueError("Lon and Lat must have the same shape")

    # Displacement for 1 cm east-west
    y_east = m_fdist(Lon, Lat, 90, 0.01, "wgs84")
    LonPerCM = np.mod(y_east[0] - Lon, 360)

    # Displacement for 1 cm north-south
    y_north = m_fdist(Lon, Lat, 0, 0.01, "wgs84")
    LatPerCM = np.mod(y_north[1] - Lat, 360)

    return LonPerCM, LatPerCM


def mymove1(
    time: Sequence[float],
    x_pos: NDArray[np.float_],
    y_pos: NDArray[np.float_],
    xarr: NDArray[np.float_],
    yarr: NDArray[np.float_],
    modelu: NDArray[np.float_],
    modelv: NDArray[np.float_],
    t0: float,
    themask: NDArray[np.float_],
) -> Tuple[NDArray[np.float_], NDArray[np.float_], NDArray[np.float_]]:
    """Compute particle trajectories on a lon/lat grid using model u/v.

    Advance particle positions (initialized at the model grid nodes) forward
    in time using the provided model velocity fields and a simple forward-
    Euler integration.

    Args:
        time: 1D sequence of times (same units as returned - typically days as
            ordinal+fraction, e.g. `date.toordinal() + hour/24`). Length `nt`.
        x_pos: 1D array of longitudes (size `nx`) OR 2D grid of longitudes
            (shape `ny,nx`) — used to derive the lon axis.
        y_pos: 1D array of latitudes (size `ny`) OR 2D grid of latitudes
            (shape `ny,nx`) — used to derive the lat axis.
        xarr: 2D longitude grid (`ny, nx`) (usually `np.meshgrid(mylonm, mylatm)[1]`).
        yarr: 2D latitude grid (`ny, nx`)  (usually `np.meshgrid(mylonm, mylatm)[0]`).
        modelu: 3D u-velocity array with shape `(nt, ny, nx)` (degrees/day).
        modelv: 3D v-velocity array with shape `(nt, ny, nx)` (degrees/day).
        t0: initial reference time (kept for signature compatibility; not used
            internally here — `time` controls the integration).
        themask: 2D mask array (`ny, nx`) where `1` means ocean/valid and `0`
            means land/invalid.

    Returns:
        Tuple containing:
        - advected_x: 3D array shape `(nt, ny, nx)` of particle longitudes
        - advected_y: 3D array shape `(nt, ny, nx)` of particle latitudes
        - rtime: 1D numpy array of times (same as the input `time` converted)

    Notes:
        - This function expects `modelu`/`modelv` to be in the same coordinate
          order as `xarr`/`yarr` (lat dimension first, lon second).
        - The interpolation uses `RegularGridInterpolator` with linear method
          and `fill_value=0.0` for velocities outside the grid.
    """
    # convert inputs to numpy arrays
    time = np.asarray(time, dtype=float)
    rtime = time.copy()
    modelu = np.asarray(modelu, dtype=float)
    modelv = np.asarray(modelv, dtype=float)
    themask = np.asarray(themask, dtype=bool)

    # check shapes
    if modelu.ndim != 3 or modelv.ndim != 3:
        raise ValueError("modelu and modelv must be 3D arrays with shape (nt, ny, nx)")
    nt, ny, nx = modelu.shape
    if modelv.shape != modelu.shape:
        raise ValueError("modelu and modelv must have the same shape")

    # derive lon/lat axes (1D) from inputs
    # allow x_pos/y_pos to be either 1D axis arrays or 2D meshgrids
    if x_pos.ndim == 1 and y_pos.ndim == 1:
        lon_axis = np.asarray(x_pos, dtype=float)
        lat_axis = np.asarray(y_pos, dtype=float)
    else:
        # use xarr/yarr to derive axes
        lon_axis = np.asarray(xarr)[0, :].astype(float)
        lat_axis = np.asarray(yarr)[:, 0].astype(float)

    # Ensure axes are strictly increasing; if not, flip arrays to make them so
    # (RegularGridInterpolator requires ascending coordinates)
    lon_asc = lon_axis[0] < lon_axis[-1]
    lat_asc = lat_axis[0] < lat_axis[-1]

    if not lon_asc:
        lon_axis = lon_axis[::-1].copy()
        modelu = modelu[:, :, ::-1]
        modelv = modelv[:, :, ::-1]
        themask = themask[:, ::-1]
        xarr = xarr[:, ::-1]
        logger.debug("Reversed lon axis to ascending order")

    if not lat_asc:
        lat_axis = lat_axis[::-1].copy()
        modelu = modelu[:, ::-1, :]
        modelv = modelv[:, ::-1, :]
        themask = themask[::-1, :]
        yarr = yarr[::-1, :]
        logger.debug("Reversed lat axis to ascending order")

    # initial positions: start particles at model grid nodes (lon/lat)
    # If xarr,yarr are 2D grids, use them; otherwise build meshgrid from axes
    if np.asarray(xarr).ndim == 2 and np.asarray(yarr).ndim == 2:
        pos_lon = np.zeros((nt, ny, nx), dtype=float)
        pos_lat = np.zeros((nt, ny, nx), dtype=float)
        pos_lon[0, :, :] = np.asarray(xarr, dtype=float)
        pos_lat[0, :, :] = np.asarray(yarr, dtype=float)
    else:
        lon_grid, lat_grid = np.meshgrid(lon_axis, lat_axis)
        pos_lon = np.zeros((nt, ny, nx), dtype=float)
        pos_lat = np.zeros((nt, ny, nx), dtype=float)
        pos_lon[0, :, :] = lon_grid
        pos_lat[0, :, :] = lat_grid

    # integration loop: forward Euler using model velocities sampled at particle positions
    # Prepare points layout dims for interpolation
    for k in range(1, nt):
        dt = float(rtime[k] - rtime[k - 1])  # time step in same units as rtime (days)
        # build interpolators for the velocity fields at previous timestep
        # note argument order for RegularGridInterpolator is (lat_axis, lon_axis)
        u_interp = RegularGridInterpolator(
            (lat_axis, lon_axis),
            modelu[k - 1, :, :],
            bounds_error=False,
            fill_value=0.0,
        )
        v_interp = RegularGridInterpolator(
            (lat_axis, lon_axis),
            modelv[k - 1, :, :],
            bounds_error=False,
            fill_value=0.0,
        )

        # sample velocities at current particle positions
        pts = np.column_stack(
            (pos_lat[k - 1].ravel(), pos_lon[k - 1].ravel())
        )  # shape (ny*nx, 2) in (lat, lon) order
        u_vals = u_interp(pts).reshape(ny, nx)
        v_vals = v_interp(pts).reshape(ny, nx)

        # update positions: forward Euler (vel in degrees/day * dt in days)
        new_lon = pos_lon[k - 1] + u_vals * dt
        new_lat = pos_lat[k - 1] + v_vals * dt

        # clip to grid bounds to avoid runaway positions
        lon_min, lon_max = lon_axis[0], lon_axis[-1]
        lat_min, lat_max = lat_axis[0], lat_axis[-1]
        new_lon = np.clip(new_lon, lon_min, lon_max)
        new_lat = np.clip(new_lat, lat_min, lat_max)

        # mask out land/invalid cells (propagate mask)
        new_lon = np.where(themask, new_lon, np.nan)
        new_lat = np.where(themask, new_lat, np.nan)

        pos_lon[k, :, :] = new_lon
        pos_lat[k, :, :] = new_lat

    logger.info(
        "Particle movement complete: produced %d time steps (%d x %d grid)",
        nt, ny, nx
    )

    logger.info("Subset for 24, 48 and 72 hour forecast")
    subset_idx = [23, 47, -1]
    pos_lon_subset = pos_lon[subset_idx]
    pos_lat_subset = pos_lat[subset_idx]
    rtime_subset = rtime[subset_idx]

    return pos_lon_subset, pos_lat_subset, rtime_subset


def advect(
    x: np.ndarray, y: np.ndarray,
    lon_grid: np.ndarray, lat_grid: np.ndarray,
    scalar_field: np.ndarray, oirange: np.ndarray,
    mask: np.ndarray, rtime: np.ndarray, t0: float,
) -> tuple[np.ma.MaskedArray, np.ndarray]:
    """
    Advect a 2D scalar field (e.g., chlorophyll, SST) forward in time
    using precomputed displacement fields derived from ocean surface currents.

    This function applies spatial advection to propagate a scalar field
    through time following the computed particle displacements (`x`, `y`)
    obtained from the velocity integration in ``mymove1()``. It handles
    masked arrays, performs interpolation over a regular grid, and
    outputs the advected scalar field for each forecast time step.

    Args:
        x (np.ndarray): 3D array of particle longitudes over time
            with shape ``(nt, ny, nx)`` (as from ``mymove1()``).
        y (np.ndarray): 3D array of particle latitudes over time
            with shape ``(nt, ny, nx)`` (same as ``x``).
        lon_grid (np.ndarray): 2D array of longitudes for the model grid.
        lat_grid (np.ndarray): 2D array of latitudes for the model grid.
        scalar_field (np.ndarray): 2D or 3D array of the scalar variable to advect.
            Typically a masked array of shape ``(ny, nx)``.
        oirange (np.ndarray): 1D array of time indices or steps to integrate over.
            Example: ``np.arange(0, 3)`` for three forecast steps.
        mask (np.ndarray): 2D land/sea mask, where 0 indicates land and
            1 indicates valid ocean pixels.
        rtime (np.ndarray): 1D array of time values (e.g., ordinal days)
            corresponding to ``x`` and ``y`` displacements.
        t0 (float): Initial time value (ordinal days) corresponding to
            the first valid scalar field frame.

    Returns:
        tuple[np.ma.MaskedArray, np.ndarray]:
            - **advected_field**: 3D masked array of advected scalar field
              with shape ``(nt, ny, nx)``, containing forecasted scalar maps.
            - **forecast_times**: 1D array of times (ordinal days) matching
              each advected step in the output.

    Raises:
        ValueError: If input arrays have incompatible shapes or missing dimensions.

    Example:
        >>> from datetime import datetime, timedelta
        >>> import numpy as np
        >>> ny, nx = 100, 120
        >>> lon = np.linspace(-125, -115, nx)
        >>> lat = np.linspace(30, 40, ny)
        >>> lon_grid, lat_grid = np.meshgrid(lon, lat)
        >>> scalar = np.exp(-((lon_grid + 120)**2 + (lat_grid - 35)**2) / 2)
        >>> x = np.stack([lon_grid + 0.01 * t for t in range(3)], axis=0)
        >>> y = np.stack([lat_grid + 0.005 * t for t in range(3)], axis=0)
        >>> mask = np.ones_like(lon_grid)
        >>> oirange = np.arange(0, 3)
        >>> rtime = np.linspace(0, 2, 3)
        >>> adv_field, times = advect(x, y, lon_grid, lat_grid, scalar, oirange, mask, rtime, 0)
        >>> adv_field.shape
        (3, 100, 120)
    """
    if scalar_field.ndim == 3:
        scalar_field = scalar_field[0, :, :]  # use the initial slice
    if lon_grid.shape != lat_grid.shape:
        raise ValueError("lon_grid and lat_grid must have the same shape.")
    if x.shape[1:] != lon_grid.shape or y.shape[1:] != lat_grid.shape:
        raise ValueError("x and y grid dimensions must match lon/lat grid shape.")

    nt, ny, nx = x.shape
    logger.info("Advection start: %d time steps, %dx%d grid", nt, ny, nx)

    advected_field = ma.masked_all((nt, ny, nx))
    forecast_times = np.zeros(nt)

    # Interpolator for the initial scalar field
    interp = RegularGridInterpolator(
        (lat_grid[:, 0], lon_grid[0, :]),
        scalar_field,
        bounds_error=False,
        fill_value=np.nan,
    )

    for t in oirange:
        logger.debug("Advecting step %d / %d", t + 1, len(oirange))
        points = np.stack([y[t].ravel(), x[t].ravel()], axis=-1)
        interp_values = interp(points).reshape(ny, nx)
        adv_field_t = ma.masked_invalid(interp_values)
        adv_field_t = ma.masked_where(mask == 0, adv_field_t)
        advected_field[t, :, :] = adv_field_t
        forecast_times[t] = t0 + (rtime[t] - rtime[0])

    logger.info("Advection complete: %d forecast steps produced", nt)
    return advected_field, forecast_times


def regrid_irr_2_reg(
    wcofs_lon: np.ndarray,
    wcofs_lat: np.ndarray,
    wcofs_var: np.ndarray,
    viirs_2d_lon: np.ndarray,
    viirs_2d_lat: np.ndarray
) -> ma.MaskedArray:
    """
    Regrid an irregular variable (with WCOFS lat/lon) to a regular 2-D grid (e.g., VIIRS).

    This function uses bilinear interpolation (``scipy.interpolate.griddata``)
    to map a field defined on irregular coordinates to a target 2-D grid.
    Invalid or extreme values are masked out.

    Args:
        wcofs_lon (ndarray): 1-D or 2-D array of source longitudes.
        wcofs_lat (ndarray): 1-D or 2-D array of source latitudes.
        wcofs_var (ndarray): Array of variable values (same shape as ``wcofs_lon``/``wcofs_lat``).
        viirs_2d_lon (ndarray): Target 2-D grid of longitudes.
        viirs_2d_lat (ndarray): Target 2-D grid of latitudes.

    Returns:
        numpy.ma.MaskedArray: The variable interpolated onto the target grid.
        Invalid values and those above 100 are masked.

    Examples:
        >>> new_field = regrid_irr_2_reg(wlon, wlat, temp, grid_lon, grid_lat)
        >>> print(new_field.shape)
        (grid_lat.shape[0], grid_lat.shape[1])
    """
    var_regrid = griddata(
        (wcofs_lon.ravel(), wcofs_lat.ravel()),
        wcofs_var.ravel(),
        (viirs_2d_lon, viirs_2d_lat),
        method='linear'
    )
    var_regrid = ma.masked_invalid(var_regrid)
    var_regrid = ma.masked_where(var_regrid > 100, var_regrid)
    return var_regrid


def regrid_st_aws(
    st_files: List,
    work_dir: Path,
    v_2d_lon: np.ndarray,
    v_2d_lat: np.ndarray
):
    """Compute progressive mean of surface salinity and temperature from WCOFS files
    and regrid the results to a regular 2-D CHARM grid.

    This function reads a list of WCOFS NetCDF files containing surface salinity
    (`salt_sur`) and surface temperature (`temp_sur`) fields stored on a
    curvilinear ROMS grid. It computes progressive (running) means of the
    temperature and salinity fields using :func:`_meanVar3`, then interpolates
    the resulting mean fields onto the target CHARM grid using
    :func:`regrid_irr_2_reg`.

    The function returns regridded temperature/salinity fields, time stamps,
    and minimum pre-regridding values.

    Args:
        st_files (list[str]):
            List of WCOFS NetCDF filenames **without paths**.
            Each file must exist inside ``work_dir``.
        work_dir (Path):
            Directory containing the WCOFS input files.
        v_2d_lon (np.ndarray):
            2-D array of longitudes for the target CHARM grid.
        v_2d_lat (np.ndarray):
            2-D array of latitudes for the target CHARM grid.

    Returns:
        dict:
            Dictionary containing:
            
            - **temp_day** (ma.MaskedArray):  
              Regridded mean surface temperature on target grid.
            - **salt_day** (ma.MaskedArray):  
              Regridded mean surface salinity on target grid.
            - **salt_time** (list[datetime]):  
              List of timestamps extracted from each file.
            - **salt_min** (float):  
              Minimum salinity encountered before regridding.
            - **temp_min** (float):  
              Minimum temperature encountered before regridding.

    Raises:
        FileNotFoundError:
            If any file in ``st_files`` does not exist in ``work_dir``.
        KeyError:
            If required NetCDF variables (e.g., ``salt_sur``) are missing.

    Notes:
        * ``ocean_time`` is assumed to be seconds since ``2016-01-01``.
        * ROMS longitudes are converted to ``0–360`` format.
        * The interpolation function ``regrid_irr_2_reg`` must be available.

    Example:
        >>> mean_out = regrid_st_aws(["st001.nc", "st002.nc"],
        ...                          Path("/data/wcofs"),
        ...                          lon_grid, lat_grid)
        >>> mean_out["temp_day"].shape
        (720, 1440)
    """
    logger.info("Starting surface T/S regridding for %d files", len(st_files))

    cnt = 0
    o_time = []

    for fl in st_files:
        file_path = work_dir / fl
        logger.info("Reading file: %s", file_path)

        if not file_path.exists():
            logger.error("Missing file: %s", file_path)
            raise FileNotFoundError(f"Missing file: {file_path}")

        # ----------------------------
        # Read T/S data
        # ----------------------------
        with Dataset(file_path) as nc:
            try:
                s = nc["salt_sur"][0, :, :]
                t = nc["temp_sur"][0, :, :]

                lat_wc = ma.filled(nc["lat_rho"][:, :], fill_value=np.nan)
                lon_wc = ma.filled(nc["lon_rho"][:, :] + 360.0, fill_value=np.nan)

                ocean_time = nc["ocean_time"][0]

                logger.debug("Loaded shapes salt=%s temp=%s", s.shape, t.shape)

            except KeyError as e:
                logger.error("Missing required variable in %s: %s", fl, e)
                raise

        # ----------------------------
        # Initialize accumulators
        # ----------------------------
        if cnt == 0:
            num_s = np.zeros_like(s, dtype=int)
            mean_s = np.zeros_like(s, dtype=float)

            num_t = np.zeros_like(t, dtype=int)
            mean_t = np.zeros_like(t, dtype=float)

            first_time = datetime(2016, 1, 1) + timedelta(seconds=int(ocean_time))
            o_time.append(first_time)

            cnt = 1

        # ----------------------------
        # Update running means
        # ----------------------------
        mean_t, num_t = _meanVar3(mean_t, num_t, t)
        mean_s, num_s = _meanVar3(mean_s, num_s, s)

        this_time = datetime(2016, 1, 1) + timedelta(seconds=int(ocean_time))
        o_time.append(this_time)

    # ----------------------------
    # Capture minima before masking
    # ----------------------------
    the_Tmin = np.nanmin(mean_t)
    the_Smin = np.nanmin(mean_s)

    logger.info("Minimum temperature before regrid: %.3f", the_Tmin)
    logger.info("Minimum salinity before regrid: %.3f", the_Smin)

    # ----------------------------
    # Regrid temperature
    # ----------------------------
    mean_t = regrid_irr_2_reg(
        lon_wc, lat_wc, ma.filled(mean_t, np.nan), v_2d_lon, v_2d_lat
    )
    mean_t = ma.masked_where(mean_t <= the_Tmin, mean_t)

    # ----------------------------
    # Regrid salinity
    # ----------------------------
    mean_s = regrid_irr_2_reg(
        lon_wc, lat_wc, ma.filled(mean_s, np.nan), v_2d_lon, v_2d_lat
    )
    mean_s = ma.masked_where(mean_s <= the_Smin, mean_s)

    logger.info("Completed T/S regridding for %d timestamps", len(o_time))

    return {
        "temp_day": mean_t,
        "salt_day": mean_s,
        "salt_time": o_time,
        "salt_min": the_Smin,
        "temp_min": the_Tmin,
    }


def regrid_uv_aws(
    uv_files: List,
    work_dir: str | Path,
    v_2d_lon: np.ndarray,
    v_2d_lat: np.ndarray
):
    """
    Regrid WCOFS surface velocity components (U and V) to the CHARM grid.

    This function reads a sequence of WCOFS NetCDF files containing
    surface U/V currents (`u_sur`, `v_sur`) and their corresponding
    curvilinear grid coordinates (`lon_u`, `lat_u`, `lon_v`, `lat_v`).
    It regrids the velocity fields to the target CHARM grid specified by
    `v_2d_lon` and `v_2d_lat` using the AWS regridding utility
    `regrid_irr_2_reg()`. It returns stacked masked arrays for U and V
    along with their corresponding timestamps.

    Args:
        uv_files (list[str]):
            List of filenames for WCOFS UV NetCDF files to process.
            Each file must exist in `work_dir`.
        work_dir (str | Path):
            Directory containing the WCOFS NetCDF input files.
        v_2d_lon (np.ndarray):
            2D array of target longitudes (CHARM grid).
        v_2d_lat (np.ndarray):
            2D array of target latitudes (CHARM grid).

    Returns:
        tuple[ma.MaskedArray, ma.MaskedArray, list[datetime]]:
            * **us** – Masked array of regridded U-component velocities
              with shape `(ntime, ny, nx)`.
            * **vs** – Masked array of regridded V-component velocities
              with shape `(ntime, ny, nx)`.
            * **o_time** – List of corresponding Python `datetime` objects.

    Example:
        >>> us, vs, times = regrid_uv_aws(
        ...     uv_files=['wcofs_uv_20251006T12.nc', 'wcofs_uv_20251006T18.nc'],
        ...     work_dir='/data/work',
        ...     v_2d_lon=sat_lon,
        ...     v_2d_lat=sat_lat
        ... )
        >>> us.shape
        (2, 720, 1440)
        >>> times[0]
        datetime.datetime(2025, 10, 6, 12, 0)

    Notes:
        * The `ocean_time` variable is assumed to be seconds since 2016-01-01.
        * Longitudes are converted to [0, 360] convention before interpolation.
        * The interpolation function `regrid_irr_2_reg()` must be available
          from `charm2022Lib_aws_nasa_n20`.
        * Files missing required variables are skipped gracefully.
    """
    logger.info("Starting UV regridding for %d files", len(uv_files))

    us_list, vs_list, o_time = [], [], []

    for fl in uv_files:
        in_path = work_dir / fl
        logger.info("Processing file: %s", in_path)

        try:
            with Dataset(in_path, "r") as nc:
                u = nc["u_sur"][0, :, :]
                v = nc["v_sur"][0, :, :]
                lat_u = nc["lat_u"][:, :]
                lon_u = nc["lon_u"][:, :] + 360.0
                lat_v = nc["lat_v"][:, :]
                lon_v = nc["lon_v"][:, :] + 360.0
                ocean_time = nc["ocean_time"][0]
        except Exception as e:
            logger.error("Failed to read %s: %s", in_path, e)
            continue

        try:
            us_reg = regrid_irr_2_reg(lon_u, lat_u, u, v_2d_lon, v_2d_lat)
            vs_reg = regrid_irr_2_reg(lon_v, lat_v, v, v_2d_lon, v_2d_lat)

            us_list.append(np.expand_dims(us_reg, axis=0))
            vs_list.append(np.expand_dims(vs_reg, axis=0))

            o_time_obj = datetime(2016, 1, 1) + timedelta(seconds=int(ocean_time))
            o_time.append(o_time_obj)

            logger.info("Regridded velocities for time %s", o_time_obj.isoformat())

        except Exception as e:
            logger.warning("Interpolation failed for %s: %s", fl, e)
            continue

    if not us_list or not vs_list:
        logger.warning("No valid velocity fields were successfully regridded.")
        return None, None, []

    us = ma.vstack(us_list)
    vs = ma.vstack(vs_list)

    logger.info("Completed UV regridding: %d time steps processed", len(o_time))
    return us, vs, o_time


def get_wcofs_salt_temp_aws(
    work_dir: str | Path,
    st: int,
    ed: int,
    forecast: bool = True,
    charm_date: datetime | None = None,
) -> List[str]:
    """Download WCOFS salinity and temperature (2D) NetCDF files from NOAA AWS.

    This function downloads WCOFS (West Coast Operational Forecast System)
    salinity and temperature 2D NetCDF files from NOAA’s public AWS S3 archive.
    It supports both forecast and nowcast modes and seamlessly handles the
    transition between NOAA’s older and newer filename formats. Each timestep
    between ``st`` (inclusive) and ``ed`` (exclusive) is attempted under both
    naming conventions, and any failed downloads are skipped gracefully.

    The function always creates ``work_dir`` if it does not exist and returns a
    list of successfully downloaded filenames (basenames only).

    Args:
        work_dir (str | Path):
            Directory where downloaded NetCDF files will be stored.
        st (int):
            Starting timestep index (inclusive). Must be >= 0.
        ed (int):
            Ending timestep index (exclusive). Must be > ``st``.
        forecast (bool, optional):
            Whether to download **forecast** files (``True``) or **nowcast**
            files (``False``). Defaults to ``True``.  
            When ``False``, NOAA naming requires shifting the model date
            backward by one day.
        charm_date (datetime | None):
            Datetime representing the model run initialization date. Required.

    Returns:
        list[str]:
            List of basenames of successfully downloaded NetCDF files.

    Raises:
        ValueError:
            If ``charm_date`` is missing or not a ``datetime`` instance.
        RuntimeError:
            (Not used here, provided for consistency with related functions.)
            Could be raised if future logic requires failed-download enforcement.

    Example:
        >>> from datetime import datetime
        >>> files = get_wcofs_salt_temp_aws(
        ...     work_dir="/tmp",
        ...     st=1,
        ...     ed=4,
        ...     forecast=True,
        ...     charm_date=datetime(2024, 8, 15),
        ... )
        >>> files
        ['st01wcofs.t03z.20240815.2ds.f001.nc',
         'st02wcofs.t03z.20240815.2ds.f002.nc',
         'st03wcofs.t03z.20240815.2ds.f003.nc']
    """
    logging.info("Enter get_wcofs_salt_temp_aws")

    if not isinstance(charm_date, datetime):
        raise ValueError("charm_date must be a datetime object")

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    if not forecast:
        logging.info("Nowcast mode selected — adjusting date to previous day.")
        for_now = "n"
        charm_date -= timedelta(days=1)
    else:
        logging.info("Forecast mode selected.")
        for_now = "f"

    base_url = "https://noaa-nos-ofs-pds.s3.amazonaws.com/wcofs/netcdf"
    date_path = charm_date.strftime("%Y/%m/%d")
    date_str = charm_date.strftime("%Y%m%d")

    downloaded_files: list[str] = []

    for n in range(st, ed):
        logging.info("Attempting time step %s...", n)
        suffix = f"{for_now}{n:03}"
        local_prefix = f"st{n:02}"

        # Define old and new filename formats
        file_old = f"nos.wcofs.2ds.{suffix}.{date_str}.t03z.nc"
        file_new = f"wcofs.t03z.{date_str}.2ds.{suffix}.nc"

        url_old = f"{base_url}/{date_path}/{file_old}"
        url_new = f"{base_url}/{date_path}/{file_new}"

        local_old = work_dir / f"{local_prefix}{file_old}"
        local_new = work_dir / f"{local_prefix}{file_new}"

        # Try old version first
        logging.info("Downloading (old format): %s", url_old)
        result = subprocess.run(
            ["wget", "-q", "-O", str(local_old), url_old],
            check=False,
        )

        if result.returncode == 0 and local_old.exists():
            downloaded_files.append(local_old.name)
            continue

        if local_old.exists():
            local_old.unlink()

        # Try new naming convention
        logging.info("Retrying (new format): %s", url_new)
        result = subprocess.run(
            ["wget", "-q", "-O", str(local_new), url_new],
            check=False,
        )

        if result.returncode == 0 and local_new.exists():
            downloaded_files.append(local_new.name)
        else:
            if local_new.exists():
                local_new.unlink()
            logging.warning(
                "Failed to download both %s and %s", file_old, file_new
            )

    logging.info("Exit get_wcofs_salt_temp_aws")
    return downloaded_files


def get_wcofs_uv_aws(
    work_dir: str | Path,
    overflight_offset: int = 7,
    charm_date: datetime | None = None,
) -> List[str]:
    """Download WCOFS u/v current-velocity NetCDF files from NOAA AWS.

    This function retrieves 2D u/v (eastward/northward velocity) fields
    from the WCOFS AWS S3 archive. It automatically attempts both the
    legacy and current file-naming conventions and infers which hours
    correspond to nowcasts vs. forecasts using ``overflight_offset``.

    File download order:
        * Nowcast hours: last ``overflight_offset`` hours of ``1–24``
        * Forecast hours: all forecast hours except the overlapping offset

    A legacy (``nos.wcofs``) filename is attempted first; if unavailable,
    the AWS “new-style” (``wcofs.t``) filename is tried.

    Args:
        work_dir (str | Path):
            Local directory where downloaded NetCDF files will be saved.
        overflight_offset (int, optional):
            Number of initial time steps considered “nowcasts” before
            switching to “forecasts”. Default is ``7``.
        charm_date (datetime | None):
            The model initialization/run date. Must be a ``datetime``.
            Example: ``datetime(2024, 8, 15)``.

    Returns:
        list[str]:  
            A list of paths to successfully downloaded NetCDF files.

    Raises:
        ValueError:
            If ``charm_date`` is not a ``datetime`` instance.
        RuntimeError:
            If **no** files can be downloaded (both naming schemes fail).

    Example:
        >>> from datetime import datetime
        >>> files = get_wcofs_uv_aws(
        ...     work_dir="/tmp",
        ...     overflight_offset=6,
        ...     charm_date=datetime(2024, 8, 15),
        ... )
        >>> len(files)
        72
    """
    if not isinstance(charm_date, datetime):
        raise ValueError("charm_date must be a datetime object")

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Define sequences for nowcast and forecast hours
    forecast_hours = list(range(1, 73))   # 1–72
    nowcast_hours = list(range(1, 25))    # 1–24

    # Combine series based on offset rule
    hrange = nowcast_hours[-overflight_offset:] + forecast_hours[:-overflight_offset]

    base_url = "https://noaa-nos-ofs-pds.s3.amazonaws.com/wcofs/netcdf"
    date_path = charm_date.strftime("%Y/%m/%d")
    date_str = charm_date.strftime("%Y%m%d")

    downloaded_files: list[str] = []

    for idx, hour in enumerate(hrange):
        now_or_fore = "n" if idx < overflight_offset else "f"
        prefix = f"o{hour:02}"

        # File names for legacy vs. new AWS convention
        file_old = f"nos.wcofs.2ds.{now_or_fore}{hour:03}.{date_str}.t03z.nc"
        file_new = f"wcofs.t03z.{date_str}.2ds.{now_or_fore}{hour:03}.nc"

        url_old = f"{base_url}/{date_path}/{file_old}"
        url_new = f"{base_url}/{date_path}/{file_new}"

        local_old = work_dir / f"{prefix}{file_old}"
        local_new = work_dir / f"{prefix}{file_new}"

        # Try legacy filename
        logging.info("Downloading (old): %s", url_old)
        result = subprocess.run(
            ["wget", "-q", "-O", str(local_old), url_old], check=False
        )

        if result.returncode == 0 and local_old.exists():
            downloaded_files.append(str(local_old))
            continue

        # Remove failed old-version download
        if local_old.exists():
            local_old.unlink()

        # Try new filename
        logging.info("Retrying (new): %s", url_new)
        result = subprocess.run(
            ["wget", "-q", "-O", str(local_new), url_new], check=False
        )

        if result.returncode == 0 and local_new.exists():
            downloaded_files.append(str(local_new))
        else:
            if local_new.exists():
                local_new.unlink()
            logging.warning(
                "Failed to download both legacy and new-style files: %s , %s",
                file_old,
                file_new,
            )

    if not downloaded_files:
        raise RuntimeError("No WCOFS u/v files were successfully downloaded.")

    return downloaded_files



def concat_l3_files(
    l3_files_to_use: list[str],
    out_var_list: list[str],
    dineof1_nc_templ: list[str],
    l3_dir: Path,
    eof_work_dir: Path
) -> None:
    """Concatenate 180 days of Level-3 (L3) satellite files for each variable.

    This function constructs full file paths for a list of input L3 filenames,
    groups them by the variables provided, and concatenates the corresponding
    NetCDF files into 180-day stacks using ``ncrcat``. Output files are written
    to pre-defined directories following the template naming convention.

    The function relies on an external command-line tool (``ncrcat`` from NCO)
    and uses ``run_cmd`` to execute the concatenation.

    Args:
        l3_files_to_use (list[str]):
            Filenames of the L3 daily input files to concatenate.
            Each filename is assumed to follow the convention where the
            year directory is derived as ``fn.split("_")[1][:4]``.

        out_var_list (list[str]):
            List of variable names to extract and concatenate from the L3 files.
            Each variable results in a single output NetCDF file.

        dineof1_nc_templ (list[str]):
            Template strings used to name the output NetCDF files.
            Each template should contain a ``{}`` or ``{var}`` slot where
            ``out_var`` will be inserted.

        l3_dir (Path):
            Base directory containing Level-3 files arranged in subdirectories
            by year.

        eof_work_dir (Path):
            Directory where concatenated output files will be written.
            The function expects ``eof_work_dir/out_var/`` to exist or be
            creatable prior to writing.

    Returns:
        None: The function writes concatenated NetCDF files to disk but
        does not return any object.

    Raises:
        RuntimeError:
            May be raised if ``run_cmd`` fails internally (depending
            on its implementation).

    Notes:
        - Requires NCO utilities, specifically ``ncrcat``.
        - All file paths passed to NCO are string-cast.
        - The function assumes exactly 180 input files are provided for each
          variable, though it does not explicitly validate this.

    Example:
        >>> concat_l3_files(
        ...     l3_files_to_use=["A2024001_example.nc", ...],
        ...     out_var_list=["chlor_a", "sst"],
        ...     dineof1_nc_templ="L3_{}.nc",
        ...     l3_dir=Path("/data/L3"),
        ...     eof_work_dir=Path("/data/work/eof")
        ... )
        # Produces:
        # /data/work/eof/chlor_a/L3_chlor_a.nc
        # /data/work/eof/sst/L3_sst.nc
    """
    paths_to_use = [l3_dir / fn.split("_")[1][:4] / fn for fn in l3_files_to_use]
    for out_var in out_var_list:
        out_file = eof_work_dir / out_var / dineof1_nc_templ.format(out_var)
        cmd = [
            "ncrcat", "-v", out_var, "-O", "-h"
        ] + [str(p) for p in paths_to_use] + [str(out_file)]
        run_cmd(cmd, msg=f"Concatenate 180-day stack for {out_var}")


def mask_and_log_transform(
    out_var_list: list[str],
    dineof1_nc_templ: list[str],
    eof_work_dir: Path,
    res_dir: Path
) -> None:
    """Apply land mask and log-transform chlorophyll to DINEOF input NetCDF files.

    This function loads a land/sea mask from ``themask.nc`` and applies it to
    each DINEOF input file corresponding to variables in ``out_var_list``.
    The mask is written (or updated) into a ``mask`` variable within each
    NetCDF file. In addition, if the variable ``chlor_a`` exists in the file,
    it is log-transformed in-place.

    The land mask is flipped vertically (axis=0) to match the orientation
    expected by the DINEOF grid.

    Args:
        out_var_list (list[str]):
            A list of variable names whose corresponding NetCDF files should be
            updated. Each item corresponds to a subdirectory
            ``eof_work_dir/out_var`` containing the target file.

        dineof1_nc_templ (list[str]):
            A string template for constructing the filename of each DINEOF
            NetCDF file. Must contain a placeholder for the variable name,
            e.g. ``"dineof_{}.nc"``.

        eof_work_dir (Path):
            Base directory containing DINEOF input subdirectories. Each variable
            in ``out_var_list`` is expected to have its own subdirectory under
            this directory.

        res_dir (Path):
            Directory containing the precomputed land mask file ``themask.nc``.
            This file must include the variables ``latitude``, ``longitude``,
            and ``mask``.

    Returns:
        tuple:
            - ``mylatm`` (numpy.ndarray): Latitude array from ``themask.nc``.
            - ``mylonm`` (numpy.ndarray): Longitude array from ``themask.nc``.
            - ``themask`` (numpy.ndarray): 2-D land/sea mask array.

    Raises:
        FileNotFoundError:
            If ``themask.nc`` does not exist.
        KeyError:
            If required mask variables are missing from ``themask.nc``.
        OSError:
            If a NetCDF file cannot be opened or modified.

    Notes:
        - The log transform is applied as ``log(x)`` without offset; if any
          chlorophyll values are zero or negative, this may produce ``-inf``.
        - Any errors during log transformation are caught and logged as warnings,
          and processing continues.
        - Mask is written with dtype ``float32`` as required by many DINEOF
          workflows.

    Example:
        >>> mask_and_log_transform(
        ...     out_var_list=["chlor_a", "sst"],
        ...     dineof1_nc_templ="L3_{}.nc",
        ...     eof_work_dir=Path("/work/eof"),
        ...     res_dir=Path("/work/resources")
        ... )
        (lat_array, lon_array, mask_array)
    """
    maskfile = Dataset(res_dir / "themask.nc", "r")
    mylatm = maskfile["latitude"][:]
    mylonm = maskfile["longitude"][:]
    themask = maskfile["mask"][:, :]
    maskfile.close()

    for out_var in out_var_list:
        nc_path = eof_work_dir / out_var / dineof1_nc_templ.format(out_var)
        with Dataset(nc_path, "a") as nc_add:
            if "mask" not in nc_add.variables:
                dine_mask = nc_add.createVariable("mask", "f4", ("latitude", "longitude"))
            else:
                dine_mask = nc_add.variables["mask"]
            dine_mask[:, :] = np.flip(themask, axis=0)

            if "chlor_a" in nc_add.variables:
                try:
                    log_chl = np.log(nc_add.variables["chlor_a"][:, :, :])
                    nc_add.variables["chlor_a"][:, :, :] = log_chl
                except Exception as e:
                    logger.warning("Could not log-transform chlor_a: %s", e)

    return mylatm, mylonm, themask


def archive_charm_outputs(
    charms_out_files: Iterable[str | Path],
    work_dir: str | Path,
    results_dir: str | Path,
    now_satellite: datetime
) -> None:
    """
    Archive CHARM output files into a year-organized results directory.

    This function moves CHARM-generated output files from a working directory
    into a structured results directory organized by year. Files are placed
    under a subdirectory named after the year extracted from the provided
    ``now_satellite`` datetime. Missing files are skipped gracefully, and
    all file operations are logged for traceability.

    The function ensures:
    * the destination year directory exists (auto-created if needed),
    * missing files are logged with a warning,
    * existing destination files are safely replaced,
    * failures to move individual files are logged without interrupting
      the remaining operations.

    Args:
        charms_out_files (Iterable[str | Path]):
            Iterable of CHARM output filenames or Paths to archive.
        work_dir (str | Path):
            Directory containing the generated CHARM output files.
        results_dir (str | Path):
            Base directory where archived results should be stored.
        now_satellite (datetime):
            Datetime whose year determines the output subdirectory
            (e.g., if `now_satellite.year == 2025`, files go to
            ``results_dir/2025``).

    Returns:
        None

    Example:
        >>> archive_charm_outputs(
        ...     ["now_bf_chlor_a_v4.nc", "now_bf_Rrs_489_v4.nc"],
        ...     "/home/cwatch/work",
        ...     "/home/cwatch/results",
        ...     datetime(2025, 10, 7)
        ... )
        # Logs:
        # INFO    Created results directory: /home/cwatch/results/2025
        # INFO    archive (1/2): /home/cwatch/work/now_bf_chlor_a_v4.nc -> /home/cwatch/results/2025/now_bf_chlor_a_v4.nc
        # INFO    archive (2/2): /home/cwatch/work/now_bf_Rrs_489_v4.nc -> /home/cwatch/results/2025/now_bf_Rrs_489_v4.nc
        # INFO    Archiving complete.
    """
    work_dir = Path(work_dir)
    results_dir = Path(results_dir)

    year_dir = results_dir / str(now_satellite.year)
    year_dir.mkdir(parents=True, exist_ok=True)

    logging.info("Archiving CHARM outputs to %s", year_dir)

    charms_out_files = list(charms_out_files)  # for length & enumeration
    total = len(charms_out_files)

    for n, fl in enumerate(charms_out_files, start=1):
        src = work_dir / fl
        dst = year_dir / Path(fl).name

        if not src.exists():
            logging.warning(
                "Skipping missing file (%d/%d): %s",
                n, total, src
            )
            continue

        logging.info(
            "archive (%d/%d): %s -> %s",
            n, total, src, dst
        )

        try:
            # Remove destination if it already exists
            if dst.exists():
                dst.unlink()

            shutil.move(str(src), str(dst))

        except Exception as e:
            logging.error(
                "Failed to move %s -> %s: %s",
                src, dst, e
            )
            continue

    logging.info("Archiving complete.")


def run_commands_parallel(
    cmd_list: List[list[str] | str],
    max_workers: int | None = None,
    print_output: bool = True,
    msg: str | None = None
) -> Dict[str, Dict[str, Any]]:
    """
    Run multiple shell commands in parallel and wait until all complete.

    Each command is executed using the `run_cmd()` helper, which handles
    subprocess execution, logging, and return parsing. Parallel execution is
    bounded by `max_workers`, and all results are collected before returning.

    Args:
        cmd_list (list[list[str] | str]):
            List of commands to execute. Each element can be:
            * a list of tokens (e.g., `["/usr/bin/echo", "hello"]`), or
            * a single command string (e.g., `"/usr/bin/echo hello"`).
        max_workers (int, optional):
            Maximum number of parallel processes. Defaults to
            `min(cpu_count, len(cmd_list))`.
        print_output (bool, optional):
            Whether to log stdout/stderr for each command as they complete.
            Defaults to True.
        msg (str, optional):
            Base message for logging context, passed to `run_cmd()`.

    Returns:
        dict[str, dict[str, Any]]:
            A mapping from command string to result dictionary:
            {
                "returncode": int,
                "stdout": str,
                "stderr": str
            }

    Raises:
        SystemExit: If any command returns a non-zero exit code.
    """
    if max_workers is None:
        max_workers = min(len(cmd_list), os.cpu_count() or 4)

    logging.info(
        "Running %d commands in parallel (max_workers=%s)",
        len(cmd_list), max_workers
    )

    results = {}

    def _run_one(cmd):
        """Execute one command for the surrounding parallel command runner."""
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        logging.info("START: %s", cmd_str)

        result = run_cmd(cmd, msg=f"{msg or 'parallel'}: {cmd_str}")

        # Normalize return fields
        if hasattr(result, "returncode"):
            rc = result.returncode
            out = getattr(result, "stdout", "") or ""
            err = getattr(result, "stderr", "") or ""
        elif isinstance(result, tuple) and len(result) == 3:
            rc, out, err = result
        else:
            raise TypeError(f"Unexpected return type from run_cmd(): {type(result)}")

        if print_output:
            if out.strip():
                logging.debug("[OUT] %s:\n%s", cmd_str, out.strip())
            if err.strip():
                logging.warning("[ERR] %s:\n%s", cmd_str, err.strip())

        logging.info("END: %s rc=%s", cmd_str, rc)

        return cmd_str, {"returncode": rc, "stdout": out, "stderr": err}

    # Parallel execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_run_one, cmd) for cmd in cmd_list]
        for fut in concurrent.futures.as_completed(futures):
            cmd_str, result = fut.result()
            results[cmd_str] = result

    # Check failures
    failed = {k: v for k, v in results.items() if v["returncode"] != 0}
    if failed:
        logging.error("Some commands failed:")
        for cmd, res in failed.items():
            logging.error("  - %s: rc=%s", cmd, res["returncode"])
        raise SystemExit(1)

    logging.info("All commands completed successfully.")
    return results


def regrid_stack_wcofs_st(
    now_time: datetime,
    wk_dir: Path,
    res_dir: Path,
    model_lon: np.ndarray,
    model_lat: np.ndarray,
    wcofs_inputs: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Regrid and stack WCOFS salinity/temperature outputs for multiple time windows.

    This function loops over a dictionary of WCOFS configuration blocks
    (``wcofs_inputs``), each specifying start/end timesteps and whether the
    block represents forecast or nowcast data. For each block, it calls
    ``merge_wcofs_and_regrid()`` to download WCOFS salinity/temperature
    NetCDF files and regrid them to the CHARM model grid. The resulting
    regridded arrays are collected and stacked into unified multi-day arrays
    for salinity and temperature.

    The function returns:
      * A 3-D salinity array with shape ``(4, ny, nx)``
      * A 3-D temperature array with shape ``(4, ny, nx)``
      * Lists of minimum salinity and temperature values for each day

    All directory inputs must be ``Path`` objects (not strings).

    Args:
        now_time (datetime):
            Timestamp associated with the current WCOFS model run.
        wk_dir (Path):
            Working directory where intermediate files are written.
        res_dir (Path):
            Results directory where regridded outputs are stored.
        model_lon (np.ndarray):
            2-D longitude array of the CHARM target grid.
        model_lat (np.ndarray):
            2-D latitude array of the CHARM target grid.
        wcofs_inputs (dict[str, dict]):
            Configuration dictionary of the form:

                {
                    "st_0": {"st_day": 1, "ed_day": 7, "forecast": True},
                    "st_1": {"st_day": 2, "ed_day": 8, "forecast": True},
                    ...
                }

            Each block is passed to ``merge_wcofs_and_regrid()``.

    Returns:
        dict[str, Any]:
            Dictionary containing:

            {
                "thesalt": np.ndarray (4, ny, nx),
                "thetemp": np.ndarray (4, ny, nx),
                "salt_mins": list[float],
                "temp_mins": list[float],
            }

            where index 0–3 correspond to the four configured ``st_i`` blocks.

    Raises:
        KeyError:
            If expected keys (e.g., ``st_0`` … ``st_3`` or regridding outputs)
            are missing from the input dictionaries.

    Example:
        >>> outputs = regrid_stack_wcofs_st(
        ...     now_time=datetime(2025, 1, 3, 3),
        ...     wk_dir=Path("/tmp/work"),
        ...     res_dir=Path("/tmp/results"),
        ...     model_lon=lon_grid,
        ...     model_lat=lat_grid,
        ...     wcofs_inputs={
        ...         "st_0": {"st_day": 1, "ed_day": 7, "forecast": True},
        ...         "st_1": {"st_day": 2, "ed_day": 8, "forecast": True},
        ...         "st_2": {"st_day": 3, "ed_day": 9, "forecast": True},
        ...         "st_3": {"st_day": 4, "ed_day": 10, "forecast": True},
        ...     },
        ... )
        >>> outputs["thesalt"].shape
        (4, ny, nx)
    """
    logging.info("Beginning WCOFS salinity/temperature merge + regrid stack.")

    # Run merge/regrid for each time block
    wcofs_st_out = {}
    for key, val in wcofs_inputs.items():
        logging.info(
            "Processing %s (st_day=%s, ed_day=%s, forecast=%s)",
            key, val["st_day"], val["ed_day"], val["forecast"]
        )
        wcofs_st_out[key] = merge_wcofs_and_regrid(
            now_time,
            int(val["st_day"]),
            int(val["ed_day"]),
            val["forecast"],
            wk_dir,
            res_dir,
            model_lon,
            model_lat,
        )

    # Stack outputs — expects keys st_0, st_1, st_2, st_3
    logging.info("Stacking regridded salinity and temperature arrays.")

    thesalt = np.stack(
        [wcofs_st_out[f"st_{i}"]["salt_day"] for i in range(4)], axis=0
    )
    thetemp = np.stack(
        [wcofs_st_out[f"st_{i}"]["temp_day"] for i in range(4)], axis=0
    )

    salt_mins = [wcofs_st_out[f"st_{i}"]["salt_min"] for i in range(4)]
    temp_mins = [wcofs_st_out[f"st_{i}"]["temp_min"] for i in range(4)]

    logging.info("Completed WCOFS salinity/temperature stack.")

    return {
        "thesalt": thesalt,
        "thetemp": thetemp,
        "salt_mins": salt_mins,
        "temp_mins": temp_mins,
    }


def merge_wcofs_and_regrid(
    now_wcofs_date,
    start_hr: int,
    end_hr: int,
    cast4: bool,
    work_dir: str,
    res_dir: str,
    v_2d_lon,
    v_2d_lat,
) -> None:
    """Retrieve WCOFS salinity/temperature fields and regrid them to the CHARM grid.

    This function orchestrates a two-step workflow:  
      (1) Retrieve WCOFS (West Coast Operational Forecast System) salinity and  
          temperature fields from AWS for a specified forecast window.  
      (2) Regrid the resulting fields onto the CHARM model grid.

    It acts as a wrapper around:
      - ``get_wcofs_salt_temp_aws()`` for data retrieval.
      - ``regrid_st_aws()`` for regridding to the target CHARM grid.

    Args:
        now_wcofs_date:
            The WCOFS analysis/forecast date for which to retrieve data.
            Typically a ``datetime`` or date string accepted by the retrieval function.
        start_hr (int):
            Starting forecast hour (inclusive) for the retrieval window.
        end_hr (int):
            Ending forecast hour (inclusive) for the retrieval window.
        cast4 (bool):
            Whether to retrieve the 4-cast forecast data or analysis product,
            depending on how ``get_wcofs_salt_temp_aws()`` interprets this flag.
        work_dir (str):
            Directory where downloaded/intermediate WCOFS files will be stored.
        res_dir (str):
            Directory where final regridded output should be stored.
        v_2d_lon:
            2D longitude array defining the CHARM output grid.
        v_2d_lat:
            2D latitude array defining the CHARM output grid.

    Returns:
        None:
            Output files are written to ``res_dir`` by ``regrid_st_aws()``.
            This function returns nothing.

    Raises:
        FileNotFoundError:
            If ``work_dir`` or ``res_dir`` does not exist.
        RuntimeError:
            If no salinity/temperature files are returned by the retrieval function.
    """
    if not os.path.isdir(work_dir):
        raise FileNotFoundError(f"Working directory does not exist: {work_dir}")
    if not os.path.isdir(res_dir):
        raise FileNotFoundError(f"Result directory does not exist: {res_dir}")

    logging.info(
        "Retrieving WCOFS salinity/temperature data for %s (hrs %s to %s, cast4=%s)",
        now_wcofs_date,
        start_hr,
        end_hr,
        cast4,
    )

    st_files = get_wcofs_salt_temp_aws(
        work_dir,
        start_hr,
        end_hr,
        forecast=cast4,
        charm_date=now_wcofs_date,
    )

    if not st_files:
        raise RuntimeError(
            "No WCOFS salinity/temperature files returned by get_wcofs_salt_temp_aws()."
        )

    logging.info(
        "Regridding %d salinity/temperature files to the CHARM grid.",
        len(st_files),
    )

    return regrid_st_aws(
        st_files,
        work_dir,
        v_2d_lon,
        v_2d_lat,
    )


def create_advection_step(
        now_wcofs, timezone_offset, work_dir, mylonm, mylatm,
        model_lon_grid, model_lat_grid, themask
):
    """Build trajectory fields used to advect DINEOF-filled variables forward."""
    foreu1, forev1, foretime = merge_uv_wcofs_and_regrid(
        now_wcofs, timezone_offset, work_dir, model_lon_grid, model_lat_grid
    )
    t_init = foretime[0] - timedelta(hours=1)
    foretime_or = [ln.toordinal() + ln.hour / 24 for ln in foretime]
    t_init_or = t_init.toordinal() + t_init.hour / 24

    foreu = ma.masked_where(foreu1 > 100, foreu1) * 100.0
    forev = ma.masked_where(forev1 > 100, forev1) * 100.0

    lonpercm, latpercm = LonLatPerCM(model_lon_grid, model_lat_grid)
    foreu *= 60 * 60 * 24 * lonpercm
    forev *= 60 * 60 * 24 * latpercm

    x, y, rtime = mymove1(
        foretime_or, mylonm, mylatm, model_lon_grid, model_lat_grid,
        foreu, forev, t_init_or, themask
    )

    return x, y, rtime


def merge_uv_wcofs_and_regrid(
    now_wcofs_date,
    offset: int,
    work_dir: str,
    v_2d_lon,
    v_2d_lat,
) -> None:
    """Retrieve WCOFS surface current data and regrid to the CHARM grid.

    This function retrieves WCOFS (West Coast Operational Forecast System)
    U/V current fields for a specified date and overflight offset, and then
    regrids the resulting files onto the CHARM grid using a parallelized
    regridding workflow.

    It is a workflow wrapper around:
      - ``get_wcofs_uv_aws()``: Downloads or retrieves U/V current files.
      - ``regrid_uv_aws_parallel()``: Regrids the current fields to the target grid.

    Args:
        now_wcofs_date:
            The target WCOFS model date for which data should be retrieved.
            Typically a ``datetime`` or a date-formatted string, depending on
            the expected input of ``get_wcofs_uv_aws()``.
        offset (int):
            Overflight time offset (hours) used to determine which WCOFS cycle
            to retrieve.
        work_dir (str):
            Path to a working directory where temporary or output files
            will be stored.
        v_2d_lon:
            2D longitude array defining the target CHARM grid.
        v_2d_lat:
            2D latitude array defining the target CHARM grid.

    Returns:
        None:
            The function returns None, but writes output files to ``work_dir``
            as produced by ``regrid_uv_aws_parallel()``.

    Raises:
        FileNotFoundError:
            If ``work_dir`` does not exist.
        RuntimeError:
            If no U/V files are returned by ``get_wcofs_uv_aws()``.
    """
    if not os.path.isdir(work_dir):
        raise FileNotFoundError(f"Working directory does not exist: {work_dir}")

    logging.info("Retrieving WCOFS U/V current data for date %s with offset %s",
                 now_wcofs_date, offset)

    uv_files_to_regrid = get_wcofs_uv_aws(
        work_dir,
        overflight_offset=offset,
        charm_date=now_wcofs_date,
    )

    if not uv_files_to_regrid:
        raise RuntimeError("No WCOFS U/V files returned by get_wcofs_uv_aws().")

    logging.info("Regridding %d U/V current files onto the CHARM grid.",
                 len(uv_files_to_regrid))

    return regrid_uv_aws_parallel(
        uv_files_to_regrid,
        work_dir,
        v_2d_lon,
        v_2d_lat,
        7,    # hard-coded workers, assuming original intent
    )


def update_eof_files(
    eof_work_dir: str | Path, first_eof_nc: str, second_eof_nc: str,
    filled_vars: list[str], gap_vars: list[str], x: np.ndarray, y: np.ndarray,
    model_lon_grid: np.ndarray, model_lat_grid: np.ndarray, themask: np.ndarray,
    rtime: np.ndarray, advect_func,
) -> None:
    """Update EOF NetCDF files with advected fields.

    Args:
        eof_work_dir: Directory containing input/output NetCDF files.
        first_eof_nc: Filename of the first EOF NetCDF (read-only).
        second_eof_nc: Filename of the second EOF NetCDF (append mode).
        idx: Array of time indices; the first element selects the time slice.
        filled_vars: Variable names to read from the first EOF file.
        eof2_vars: Variable names to update in the second EOF file.
        x, y: Coordinate arrays for advection.
        model_lon_grid, model_lat_grid: Model longitude/latitude grids.
        themask: Mask to apply during advection.
        rtime: Reference time array for advection.
        time_last: Array of last time values (use time_last[0]).
        advect_func: Function to run advection, must return (band_var, longtime).

    Returns:
        None. Updates the second EOF NetCDF file in place.
    """
    oirange = np.arange(0, 3)

    cntr = 0
    for cntr, gvar in enumerate(gap_vars):
        with Dataset(eof_work_dir / gvar / first_eof_nc.format(gvar), "r") as nc_eof1, \
             Dataset(eof_work_dir / gvar / second_eof_nc.format(gvar), "a") as nc_eof2:

            time_eof1 = nc_eof1['time'][:]
            idx, = np.where(time_eof1 == time_eof1.max())
            time_last = time_eof1[idx[0]:idx[0]+1]

            var_eof1 = nc_eof1[filled_vars[cntr]][idx[0]:idx[0]+1, :, :]

            # Run advection
            band_var2, longtime = advect_func(
                x, y,
                model_lon_grid, model_lat_grid,
                np.flip(var_eof1, axis=1),
                oirange, themask,
                rtime, time_last[0]
            )

            # Update time variable (only on first variable loop)
            if cntr == 0:
                eof2_time = nc_eof2.variables["time"][:]
                new_time = np.append(eof2_time, longtime[1:])
                nc_eof2.variables["time"][:] = new_time
                cntr = 1
            else:
                nc_eof2.variables["time"][:] = new_time

            # Insert new data into last 3 time slots
            eof2_var = nc_eof2.variables[gvar][:, :, :]
            eof2_var[-3:, :, :] = np.flip(band_var2, axis=1)

            # Vectorized masking in one step
            data = np.array(eof2_var, copy=False)  # avoid extra copy
            mask = np.isnan(data) | (data > 9000)
            if gvar == 'Rrs_489' or gvar == 'Rrs_556':
                mask |= (data <= 0.0)

            eof2_var_masked = ma.array(data, mask=mask)

            # Write back to file
            nc_eof2.variables[gvar][:, :, :] = eof2_var_masked

    return time_last[0]


def _regrid_single_uv(
    fl: Path, v_2d_lon: np.ndarray, v_2d_lat: np.ndarray
) -> Tuple[np.ndarray, np.ndarray, datetime] | None:
    """Worker function to regrid a single UV NetCDF file."""
    try:
        with Dataset(fl, "r") as nc:
            u = nc["u_sur"][0, :, :]
            v = nc["v_sur"][0, :, :]
            lat_u = nc["lat_u"][:, :]
            lon_u = nc["lon_u"][:, :] + 360.0
            lat_v = nc["lat_v"][:, :]
            lon_v = nc["lon_v"][:, :] + 360.0
            ocean_time = nc["ocean_time"][0]
    except Exception as e:
        logger.error("Failed to read %s: %s", fl, e)
        return None

    try:
        us_reg = regrid_irr_2_reg(lon_u, lat_u, u, v_2d_lon, v_2d_lat)
        vs_reg = regrid_irr_2_reg(lon_v, lat_v, v, v_2d_lon, v_2d_lat)
        o_time_obj = datetime(2016, 1, 1) + timedelta(seconds=int(ocean_time))

        return us_reg, vs_reg, o_time_obj
    except Exception as e:
        logger.warning("Interpolation failed for %s: %s", fl, e)
        return None


def regrid_uv_aws_parallel(
    uv_files: List[str],
    work_dir: Path,
    v_2d_lon: np.ndarray,
    v_2d_lat: np.ndarray,
    max_workers: int | None = None,
) -> Tuple[ma.MaskedArray, ma.MaskedArray, List[datetime]]:
    """
    Regrid WCOFS surface velocity components (U and V) to the CHARM grid in parallel.

    This function processes multiple WCOFS NetCDF files concurrently. Each file is
    regridded to the CHARM grid using `regrid_irr_2_reg()` and the results are
    collected in the same order as input.

    Args:
        uv_files (list[str]):
            List of filenames for WCOFS UV NetCDF files to process.
        work_dir (Path | str):
            Directory containing the WCOFS NetCDF input files.
        v_2d_lon (np.ndarray):
            2D array of target longitudes (CHARM grid).
        v_2d_lat (np.ndarray):
            2D array of target latitudes (CHARM grid).
        max_workers (int | None):
            Maximum number of worker processes. Defaults to number of CPU cores.

    Returns:
        tuple[ma.MaskedArray, ma.MaskedArray, list[datetime]]:
            * **us** – Masked array of regridded U-component velocities
              with shape `(ntime, ny, nx)`.
            * **vs** – Masked array of regridded V-component velocities
              with shape `(ntime, ny, nx)`.
            * **o_time** – List of corresponding Python `datetime` objects.

    Example:
        >>> us, vs, times = regrid_uv_aws_parallel(
        ...     uv_files=['wcofs_uv_20251006T12.nc', 'wcofs_uv_20251006T18.nc'],
        ...     work_dir=Path('/data/work'),
        ...     v_2d_lon=sat_lon,
        ...     v_2d_lat=sat_lat,
        ...     max_workers=4,
        ... )
        >>> us.shape
        (2, 720, 1440)
        >>> times[0]
        datetime.datetime(2025, 10, 6, 12, 0)

    Notes:
        * Uses `concurrent.futures.ProcessPoolExecutor` for true parallel CPU processing.
        * Preserves order of input files in returned arrays.
        * Skips files that fail to read or interpolate, logging warnings.
    """
    logger.info("Starting parallel UV regridding for %d files", len(uv_files))
    if max_workers is None:
        max_workers = min(len(uv_files), os.cpu_count() or 4)

    work_dir = Path(work_dir)

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_regrid_single_uv, work_dir / fl, v_2d_lon, v_2d_lat): fl
            for fl in uv_files
        }

        results = []
        for fut in as_completed(futures):
            fl = futures[fut]
            try:
                res = fut.result()
                if res:
                    results.append((uv_files.index(fl), *res))  # (index, us, vs, time)
            except Exception as e:
                logger.error("Worker failed for %s: %s", fl, e)

    # Sort results by original order
    results.sort(key=lambda x: x[0])

    if not results:
        logger.warning("No valid velocity fields were successfully regridded.")
        return None, None, []

    us_list = [np.expand_dims(r[1], axis=0) for r in results]
    vs_list = [np.expand_dims(r[2], axis=0) for r in results]
    o_time = [r[3] for r in results]

    us = ma.masked_invalid(ma.vstack(us_list))
    vs = ma.masked_invalid(ma.vstack(vs_list))

    logger.info("Completed UV regridding: %d time steps processed", len(o_time))
    return us, vs, o_time
