from __future__ import annotations
from numpy.typing import NDArray
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence
import numpy as np

# logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("l3_creator")

def forecast(mchla, m488, m555, modelsalt, modeltemp, model_time):
    """
    Run Pseudo-nitzschia, particulate DA, and cellular DA models.

    Args:
        mchla (np.ndarray): Chlorophyll array.
        m488 (np.ndarray): Rrs at 488 nm.
        m555 (np.ndarray): Rrs at 555 nm.
        modelsalt (np.ndarray): Salinity field.
        modeltemp (np.ndarray): Temperature field.
        model_time (array-like): Sequence of datetime objects.

    Returns:
        tuple: (pn, pd, pc, mchla, m488, m555)
    """
    logger.info("Starting forecast at %s", datetime.now().time())
    month = [i.month for i in model_time]
    pn = _pnmodel(m488, m555, month)
    pd = _pdmodel(mchla, modelsalt, m555)
    pc = _cdmodel(modeltemp, m555, modelsalt)
    logger.info("Forecast complete.")
    return pn, pd, pc, mchla, m488, m555


def _expand_to_3d(arr: NDArray[np.float64]) -> NDArray[np.float64]:
    """Ensure array has shape (T, Y, X)."""
    return np.expand_dims(arr, axis=0) if arr.ndim == 2 else arr


def _logistic(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Numerically stable logistic (sigmoid) probability transformation."""
    with np.errstate(over="ignore", invalid="ignore"):
        return 1 / (1 + np.exp(-x))


def _pnmodel(
    Rrs_488: NDArray[np.float64],
    Rrs_555: NDArray[np.float64],
    month: Sequence[int],
) -> NDArray[np.float64]:
    """
    Estimate probability of **Pseudo-nitzschia** (PN) occurrence.

    The PN model uses remote-sensing reflectances at 488 nm and 555 nm, along
    with the month of observation, to estimate PN presence probability.

    Inputs may be 2-D (`(Y, X)`) or 3-D (`(T, Y, X)`); 2-D inputs are expanded
    to 3-D automatically with `T=1`.

    Args:
        Rrs_488 (NDArray[np.float64]): Remote-sensing reflectance at 488 nm.
        Rrs_555 (NDArray[np.float64]): Remote-sensing reflectance at 555 nm.
        month (Sequence[int]): Month number(s) (1-12). Must have length `T` if
            3-D inputs; a single value applies to all time steps.

    Returns:
        NDArray[np.float64]:
            Probability values in [0, 1] with `np.nan` for invalid inputs.
            Always returned with shape `(T, Y, X)`.

    Example:
        >>> import numpy as np
        >>> Rrs_488 = np.random.rand(2, 4, 4)
        >>> Rrs_555 = np.random.rand(2, 4, 4)
        >>> month = [6, 7]
        >>> pn = _pnmodel(Rrs_488, Rrs_555, month)
        >>> pn.shape
        (2, 4, 4)
    """
    logger.info("Running _pnmodel")
    month = np.atleast_1d(month)

    Rrs_488 = np.where(Rrs_488 > 1000, np.nan, Rrs_488)
    Rrs_555 = np.where(Rrs_555 > 1000, np.nan, Rrs_555)

    Rrs_488 = _expand_to_3d(Rrs_488)
    Rrs_555 = _expand_to_3d(Rrs_555)

    if len(month) == 1:
        month = np.repeat(month, Rrs_488.shape[0])

    with np.errstate(divide="ignore", invalid="ignore"):
        arr = 5.31 - 2.87 * (Rrs_488 / Rrs_555)
        for i, m in enumerate(month):
            arr[i, :, :] += 0.068 * m

    arr = np.where(np.abs(arr) > 1000, np.nan, arr)
    out = _logistic(arr)

    logger.info("_pnmodel complete")
    return out


def _pdmodel(
    chla: NDArray[np.float64],
    salt: NDArray[np.float64],
    Rrs_555: NDArray[np.float64],
) -> NDArray[np.float64]:
    """
    Estimate probability of **particulate domoic acid** (pDA) presence.

    This model uses chlorophyll-a concentration, salinity, and
    reflectance at 555 nm to predict the likelihood of particulate domoic
    acid occurrence.

    Inputs may be 2-D (`(Y, X)`) or 3-D (`(T, Y, X)`); 2-D inputs are expanded
    to 3-D automatically with `T=1`.

    Args:
        chla (NDArray[np.float64]): Chlorophyll-a concentration (mg m⁻³).
        salt (NDArray[np.float64]): Salinity (PSU).
        Rrs_555 (NDArray[np.float64]): Remote-sensing reflectance at 555 nm.

    Returns:
        NDArray[np.float64]:
            Probability values in [0, 1] with `np.nan` for invalid inputs.
            Always returned with shape `(T, Y, X)`.

    Example:
        >>> import numpy as np
        >>> chla = np.random.rand(3, 5, 5)
        >>> salt = 33 + np.random.rand(3, 5, 5)
        >>> Rrs_555 = np.random.rand(3, 5, 5)
        >>> pda = _pdmodel(chla, salt, Rrs_555)
        >>> pda.shape
        (3, 5, 5)
    """
    logger.info("Running _pdmodel")

    chla = np.where(chla > 1000, np.nan, chla)
    salt = np.where(salt < 0, np.nan, salt)
    Rrs_555 = np.where(Rrs_555 > 1000, np.nan, Rrs_555)

    chla = _expand_to_3d(chla)
    salt = _expand_to_3d(salt)
    Rrs_555 = _expand_to_3d(Rrs_555)

    with np.errstate(divide="ignore", invalid="ignore"):
        arr = -134.3 - 0.253 * chla + 4 * salt + 502 * Rrs_555

    arr = np.where(np.abs(arr) > 1000, np.nan, arr)
    out = _logistic(arr)

    logger.info("_pdmodel complete")
    return out


def _cdmodel(
    sst: NDArray[np.float64],
    Rrs_555: NDArray[np.float64],
    salt: NDArray[np.float64],
) -> NDArray[np.float64]:
    """
    Estimate probability of **cellular domoic acid** (cDA) presence.

    This model uses sea-surface temperature (SST), salinity, and reflectance
    at 555 nm to predict cellular domoic acid probability.

    Inputs may be 2-D (`(Y, X)`) or 3-D (`(T, Y, X)`); 2-D inputs are expanded
    to 3-D automatically with `T=1`.

    Args:
        sst (NDArray[np.float64]): Sea-surface temperature (°C).
        Rrs_555 (NDArray[np.float64]): Remote-sensing reflectance at 555 nm.
        salt (NDArray[np.float64]): Salinity (PSU).

    Returns:
        NDArray[np.float64]:
            Probability values in [0, 1] with `np.nan` for invalid inputs.
            Always returned with shape `(T, Y, X)`.

    Example:
        >>> import numpy as np
        >>> sst = 10 + np.random.rand(2, 4, 4)
        >>> salt = 33 + np.random.rand(2, 4, 4)
        >>> Rrs_555 = np.random.rand(2, 4, 4)
        >>> cda = _cdmodel(sst, Rrs_555, salt)
        >>> cda.shape
        (2, 4, 4)
    """
    logger.info("Running _cdmodel")

    sst = np.where(sst < -2, np.nan, sst)       # physical lower bound for SST
    salt = np.where(salt < 0, np.nan, salt)
    Rrs_555 = np.where(Rrs_555 > 100, np.nan, Rrs_555)

    sst = _expand_to_3d(sst)
    salt = _expand_to_3d(salt)
    Rrs_555 = _expand_to_3d(Rrs_555)

    with np.errstate(divide="ignore", invalid="ignore"):
        arr = -90.0 - 0.35 * sst - 666 * Rrs_555 + 2.87 * salt

    arr = np.where(np.abs(arr) > 1000, np.nan, arr)
    out = _logistic(arr)

    logger.info("_cdmodel complete")
    return out