"""Validate elevation encoding before reading raw GDAL pixel values."""

_METRE_UNITS = {"m", "metre", "meter", "metres", "meters"}


def validate_elevation_band(band, label, kind):
    """Require physical metre elevations, without silently ignoring encoding.

    GDAL ReadAsArray and PDAL's DEM filter read stored samples without applying
    band scale/offset. Unspecified units remain allowed: users must confirm that
    those elevations are metres, as described in the tool help.
    """
    if band.GetScale() not in (None, 1.0) or band.GetOffset() not in (None, 0.0):
        raise ValueError(
            f"{label} uses a scale or offset. Export a raster with the scale/offset "
            "applied to its pixel values, so it stores physical elevations in metres, "
            "then use that raster."
        )
    unit = (band.GetUnitType() or "").strip()
    if unit and unit.lower() not in _METRE_UNITS:
        raise ValueError(
            f"{label} declares elevation units '{unit}'. "
            f"Convert {kind} elevations to metres before processing."
        )
