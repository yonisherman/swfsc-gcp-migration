"""
Unified Canny fronts library (WC + ATL) with shared helpers in one file.

Goal: keep original flow + logic while removing duplication.
- WC region behavior: uses legacy Canny1.canny1()
- ATL region behavior: uses legacy Canny2.canny2() gradients + skimage.feature.canny edges

GCP note:
- The old hard-coded file_base ("/u00/satellite/...") is NOT assumed anymore.
- extract_mur() accepts either:
    * a full path (recommended), OR
    * (file_name + file_base) for legacy compatibility.
"""

from __future__ import annotations

import os
from typing import Tuple

import numpy as np
import numpy.ma as ma
# import cv2
### test ###
import cv2
cv2.setNumThreads(0)
cv2.ocl.setUseOpenCL(False)
###################
from netCDF4 import Dataset


# -----------------------------------------------------------------------------
# Legacy helpers (shared)
# -----------------------------------------------------------------------------
def isleap(year: int) -> bool:
    from datetime import date
    try:
        date(year, 2, 29)
        return True
    except ValueError:
        return False


def extract_mur(
    file_name: str,
    file_base: str | None = None,
    lat_min: float = 22.0,
    lat_max: float = 51.0,
    lon_min: float = -135.0,
    lon_max: float = -105.0,
):
    """
    Extract a subgrid from a MUR41 1-day file.

    IMPORTANT (GCP):
    - Prefer passing a full local path via `file_name`.
    - `file_base` is retained for backward compatibility only.
      If omitted, we will:
        1) use file_name if it contains a path separator, else
        2) use env var MUR41_SSTA_1DAY_BASE, else
        3) use "" (current working dir)
    """
    if file_base is None:
        if ("/" in file_name) or (file_name.startswith("./")):
            nc_file = file_name
        else:
            file_base = os.environ.get("MUR41_SSTA_1DAY_BASE", "")
            nc_file = f"{file_base}{file_name}"
    else:
        nc_file = f"{file_base}{file_name}"

    root = Dataset(nc_file)

    lat = root.variables["lat"][:]
    lon = root.variables["lon"][:]

    # NOTE: legacy behavior expects exact matches for bbox coords
    lat_min_index = np.argwhere(lat == lat_min)[0, 0]
    lat_max_index = np.argwhere(lat == lat_max)[0, 0]
    lon_min_index = np.argwhere(lon == lon_min)[0, 0]
    lon_max_index = np.argwhere(lon == lon_max)[0, 0]

    lon_mur = lon[lon_min_index : lon_max_index + 1]
    lat_mur = lat[lat_min_index : lat_max_index + 1]

    sst_mur = root.variables["analysed_sst"][0, lat_min_index : lat_max_index + 1, lon_min_index : lon_max_index + 1]
    sst_mur = np.squeeze(sst_mur)
    sst_mur = sst_mur - 273.15

    root.close()
    return sst_mur, lon_mur, lat_mur


def my_contours(edges):
    edge_image = edges.astype(np.uint8)
    contours, hierarchy = cv2.findContours(edge_image, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    return contours


def contours_to_edges(contours, edge_shape, min_len: int = 20):
    num_contours = len(contours)
    contour_lens = []
    contour_edges = np.zeros(edge_shape)

    for i in list(range(0, num_contours)):
        contour = contours[i]
        contour_len = contour.shape[0]
        contour_lens.append(contour_len)

        if contour_len > min_len:
            for ilen in list(range(0, contour_len)):
                xloc = contour[ilen, 0, 1]
                yloc = contour[ilen, 0, 0]
                contour_edges[xloc, yloc] = 1

    return contour_edges, contour_lens


def filt5(lon, lat, ingrid):
    l1 = lat.shape[0]
    l2 = lon.shape[0]
    outgrid = np.zeros((l1, l2), np.int32)

    for i in list(range(2, l1 - 2)):
        for j in list(range(2, l2 - 2)):
            subg = ingrid[(i - 2) : (i + 3), (j - 2) : (j + 3)]
            if np.sum(subg.mask == True) == 25:  # noqa: E712 (legacy style)
                outgrid[i, j] = 0
            else:
                my_max = np.argmax(subg)
                my_min = np.argmin(subg)
                if (my_max == 12) or (my_min == 12):
                    outgrid[i, j] = 1
                else:
                    outgrid[i, j] = 0

    outgrid = ma.array(outgrid, mask=ingrid.mask)
    return outgrid


def filt35(lon, lat, ingrid, grid5):
    l1 = lat.shape[0]
    l2 = lon.shape[0]
    outgrid = np.zeros((l1, l2))

    for i in list(range(2, l1 - 2)):
        for j in list(range(2, l2 - 2)):
            if grid5[i, j] == 0:
                subg = ingrid[(i - 1) : (i + 2), (j - 1) : (j + 2)]
                if np.sum(subg.mask == True) == 9:  # noqa: E712 (legacy style)
                    outgrid[i, j] = ingrid[i, j]
                else:
                    my_max = np.argmax(subg)
                    my_min = np.argmin(subg)
                    if (my_max == 4) or (my_min == 4):
                        outgrid[i, j] = ma.median(subg)
                    else:
                        outgrid[i, j] = ingrid[i, j]
            else:
                outgrid[i, j] = ingrid[i, j]

    outgrid = ma.array(outgrid, mask=ingrid.mask)
    outgrid[outgrid == 0] = ma.masked
    return outgrid


def create_canny_nc(
    file_year,
    file_month,
    file_day,
    base_dir: str = "/u00/satellite/front/",
    lat_min: float = 22.0,
    lat_max: float = 51.0,
    lon_min: float = -135.0,
    lon_max: float = -105.0,
):
    from netCDF4 import Dataset  # local import preserved (legacy style)
    import numpy as np  # local import preserved
    import numpy.ma as ma  # local import preserved

    c_file_year = str(file_year)
    c_file_month = str(file_month).rjust(2, "0")
    c_file_day = str(file_day).rjust(2, "0")

    file_name = base_dir + "Canny_Front_" + c_file_year + c_file_month + c_file_day + ".nc"
    ncfile = Dataset(file_name, "w", format="NETCDF4")

    lat_diff = lat_max - lat_min
    latsdim = (lat_diff * 100) + 1
    lats = lat_min + (np.arange(0, latsdim) * 0.01)

    lon_diff = lon_max - lon_min
    lonsdim = (lon_diff * 100) + 1
    lons = lon_min + (np.arange(0, lonsdim) * 0.01)

    timedim = ncfile.createDimension("time", None)
    latdim = ncfile.createDimension("lat", latsdim)
    londim = ncfile.createDimension("lon", lonsdim)
    altdim = ncfile.createDimension("altitude", 1)

    LatLon_Projection = ncfile.createVariable("LatLon_Projection", "i4")
    time = ncfile.createVariable("time", "f8", ("time"), zlib=True, complevel=2)
    altitude = ncfile.createVariable("altitude", "f4", ("altitude"))
    latitude = ncfile.createVariable("lat", "f4", ("lat"), zlib=True, complevel=2)
    longitude = ncfile.createVariable("lon", "f4", ("lon"), zlib=True, complevel=2)
    edges = ncfile.createVariable("edges", "f4", ("time", "altitude", "lat", "lon"), fill_value=-9999.0, zlib=True, complevel=2)
    x_gradient = ncfile.createVariable("x_gradient", "f4", ("time", "altitude", "lat", "lon"), fill_value=-9999.0, zlib=True, complevel=2)
    y_gradient = ncfile.createVariable("y_gradient", "f4", ("time", "altitude", "lat", "lon"), fill_value=-9999.0, zlib=True, complevel=2)
    magnitude_gradient = ncfile.createVariable("magnitude_gradient", "f4", ("time", "altitude", "lat", "lon"), fill_value=-9999.0, zlib=True, complevel=2)

    LatLon_Projection.grid_mapping_name = "latitude_longitude"
    LatLon_Projection.earth_radius = 6367470.0

    latitude._CoordinateAxisType = "Lat"
    latitude.actual_range = (lat_min, lat_max)
    latitude.axis = "Y"
    latitude.grid_mapping = "Equidistant Cylindrical"
    latitude.ioos_category = "Location"
    latitude.long_name = "Latitude"
    latitude.reference_datum = "geographical coordinates, WGS84 projection"
    latitude.standard_name = "latitude"
    latitude.units = "degrees_north"
    latitude.valid_max = lat_max
    latitude.valid_min = lat_min

    longitude._CoordinateAxisType = "Lon"
    longitude.actual_range = (lon_min, lon_max)
    longitude.axis = "X"
    longitude.grid_mapping = "Equidistant Cylindrical"
    longitude.ioos_category = "Location"
    longitude.long_name = "Longitude"
    longitude.reference_datum = "geographical coordinates, WGS84 projection"
    longitude.standard_name = "longitude"
    longitude.units = "degrees_east"
    longitude.valid_max = lon_max
    longitude.valid_min = lon_min

    altitude.units = "m"
    altitude.long_name = "Specified height level above ground"
    altitude.standard_name = "altitude"
    altitude.positive = "up"
    altitude.axis = "Z"

    time._CoordinateAxisType = "Time"
    time.axis = "T"
    time.calendar = "Gregorian"
    time.ioos_category = "Time"
    time.long_name = "Time"
    time.units = "Hour since 1970-01-01T00:00:00Z"
    time.standard_name = "time"

    edges.long_name = "Frontal Edge"
    edges.missing_value = -9999.0
    edges.grid_mapping = "LatLon_Projection"
    edges.coordinates = "time altitude lat lon "

    x_gradient.long_name = "East-West Gradient of SST"
    x_gradient.missing_value = -9999.0
    x_gradient.grid_mapping = "LatLon_Projection"
    x_gradient.coordinates = "time altitude lat lon "

    y_gradient.long_name = "North-South Gradient of SST"
    y_gradient.missing_value = -9999.0
    y_gradient.grid_mapping = "LatLon_Projection"
    y_gradient.coordinates = "time altitude lat lon "

    magnitude_gradient.long_name = "Magnitude of SST Gradient"
    magnitude_gradient.missing_value = -9999.0
    magnitude_gradient.grid_mapping = "LatLon_Projection"
    magnitude_gradient.coordinates = "time altitude lat lon "

    ncfile.title = "Daily estimated MUR SST Frontal edges, x_gradient, y_gradient and gradient magnitude"
    ncfile.cdm_data_type = "Grid"
    ncfile.Conventions = "COARDS, CF-1.6, ACDD-1.3"
    ncfile.standard_name_vocabulary = "CF Standard Name Table v55"
    ncfile.creator_email = "erd.data@noaa.gov"
    ncfile.creator_name = "NOAA NMFS SWFSC ERD"
    ncfile.creator_type = "institution"
    ncfile.creator_url = "https://www.pfeg.noaa.gov"
    ncfile.Easternmost_Easting = lon_max
    ncfile.Northernmost_Northing = lat_max
    ncfile.Westernmost_Easting = lon_min
    ncfile.Southernmost_Northing = lat_max
    ncfile.geospatial_lat_max = lat_max
    ncfile.geospatial_lat_min = lat_min
    ncfile.geospatial_lat_resolution = 0.01
    ncfile.geospatial_lat_units = "degrees_north"
    ncfile.geospatial_lon_max = lon_max
    ncfile.geospatial_lon_min = lon_min
    ncfile.geospatial_lon_resolution = 0.01
    ncfile.geospatial_lon_units = "degrees_east"
    ncfile.infoUrl = ""
    ncfile.institution = "NOAA ERD"
    ncfile.keywords = ""
    ncfile.keywords_vocabulary = "GCMD Science Keywords"
    ncfile.summary = """Front Edges estimated from daily MUR SST files
    using the Python scikit-image canny algorithm  with sigma = 10., and
    threshold values of .8 and .9,  as well as the OpenCV algorithm findContours with a minimum length of 20.
    The SST x-gradient, y-gradient and gradient magnitude are also included
    """
    ncfile.license = """The data may be used and redistributed for free but is not intended
    for legal use, since it may contain inaccuracies. Neither the data
    Contributor, ERD, NOAA, nor the United States Government, nor any
    of their employees or contractors, makes any warranty, express or
    implied, including warranties of merchantability and fitness for a
    particular purpose, or assumes any legal liability for the accuracy,
    completeness, or usefulness, of this information.
    """

    file_name1 = c_file_year + c_file_month + c_file_day + "090000-JPL-L4_GHRSST-SSTfnd-MUR-GLOB-v02.0-fv04.1.nc"
    ncfile.history = (
        "created from MUR SST file " + file_name1 +
        "using python scikit-image canny algorithm, sigma = 10, thresholds of 0.8, 0.9 and OpenCV findContours function with minimum length 20"
    )

    altitude[0] = 0.0
    latitude[:] = lats
    longitude[:] = lons

    ncfile.close()
    return file_name


# -----------------------------------------------------------------------------
# Region-specific myCanny (preserves original deps + call graph)
# -----------------------------------------------------------------------------
def myCanny_WC(myData, myMask, sigma=10.0, lower=0.8, upper=0.9, use_quantiles=True):
    # WC: edges + gradients from Canny1.canny1
    from Canny1 import canny1  # local import to preserve region package differences

    edges, x_gradient, y_gradient, magnitude = canny1(
        myData,
        sigma=sigma,
        mask=myMask,
        low_threshold=lower,
        high_threshold=upper,
        use_quantiles=use_quantiles,
    )
    x_gradient = ma.array(x_gradient, mask=myData.mask)
    y_gradient = ma.array(y_gradient, mask=myData.mask)
    magnitude = ma.array(magnitude, mask=myData.mask)
    return edges, x_gradient, y_gradient, magnitude


def myCanny_ATL(myData, myMask, sigma=10.0, lower=0.8, upper=0.9, use_quantiles=True):
    # ATL: gradients from Canny2.canny2, edges from skimage.feature.canny
    from Canny2 import canny2  # local import to preserve region package differences
    from skimage.feature import canny

    y_gradient, x_gradient, magnitude = canny2(
        myData,
        sigma=sigma,
        mask=myMask,
        low_threshold=lower,
        high_threshold=upper,
        use_quantiles=use_quantiles,
    )
    edges = canny(
        myData,
        sigma=sigma,
        mask=myMask,
        low_threshold=lower,
        high_threshold=upper,
        use_quantiles=use_quantiles,
    )
    x_gradient = ma.array(x_gradient, mask=myData.mask)
    y_gradient = ma.array(y_gradient, mask=myData.mask)
    magnitude = ma.array(magnitude, mask=myData.mask)
    return edges, x_gradient, y_gradient, magnitude
