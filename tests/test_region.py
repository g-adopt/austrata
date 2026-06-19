"""Pure/offline unit tests for the Region value object."""
import pytest
from shapely.geometry import Polygon, box

from austrata.domain.region import Region


def test_wfs_bbox_format_and_crs_suffix():
    """The WFS bbox must be lon/lat order with the short EPSG:4283 suffix."""
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    assert region.wfs_bbox() == "149.0,-36.0,150.0,-35.0,EPSG:4283"


def test_wfs_bbox_always_carries_crs():
    """Regression guard: the CRS suffix is mandatory (omitting it 400s live)."""
    region = Region.from_bbox(148.9, -35.6, 149.3, -35.1)
    assert region.wfs_bbox().endswith(",EPSG:4283")
    assert region.wfs_bbox().count(",") == 4


def test_from_bbox_rejects_degenerate_box():
    with pytest.raises(ValueError):
        Region.from_bbox(150.0, -35.0, 149.0, -36.0)


def test_cache_key_is_stable_across_calls():
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    assert region.cache_key() == region.cache_key()


def test_same_footprint_two_ways_same_key():
    """A bbox and the equivalent explicit polygon must collapse to one key."""
    via_bbox = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    # Same rectangle, different vertex start/order — canonicalisation should
    # normalise both to identical WKT and thus the same key.
    explicit = Region(
        Polygon([(150.0, -35.0), (150.0, -36.0), (149.0, -36.0), (149.0, -35.0), (150.0, -35.0)])
    )
    assert via_bbox.cache_key() == explicit.cache_key()


def test_subpixel_coordinate_noise_same_key():
    """Coordinate noise below the rounding grid must not change the key."""
    a = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    b = Region.from_bbox(149.0000000001, -36.0, 150.0, -35.0)
    assert a.cache_key() == b.cache_key()


def test_different_footprint_different_key():
    a = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    b = Region.from_bbox(149.0, -36.0, 150.0, -34.0)
    assert a.cache_key() != b.cache_key()


def test_subset_region_is_not_reused():
    """A subset region is a distinct entry (documented non-reuse)."""
    whole = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    subset = Region.from_bbox(149.2, -35.8, 149.8, -35.2)
    assert whole.cache_key() != subset.cache_key()


def test_is_rectangular():
    assert Region.from_bbox(149.0, -36.0, 150.0, -35.0).is_rectangular()
    triangle = Region(Polygon([(149.0, -36.0), (150.0, -36.0), (149.5, -35.0)]))
    assert not triangle.is_rectangular()


def test_arcgis_envelope_for_bbox():
    region = Region.from_bbox(149.0, -36.0, 150.0, -35.0)
    assert region.arcgis_geometry_type() == "esriGeometryEnvelope"
    geom = region.arcgis_geometry()
    assert geom["xmin"] == 149.0 and geom["ymax"] == -35.0
    assert geom["spatialReference"]["wkid"] == 4283


def test_arcgis_polygon_for_nonrectangular():
    triangle = Region(Polygon([(149.0, -36.0), (150.0, -36.0), (149.5, -35.0)]))
    assert triangle.arcgis_geometry_type() == "esriGeometryPolygon"
    geom = triangle.arcgis_geometry()
    assert "rings" in geom and geom["spatialReference"]["wkid"] == 4283


def test_empty_geometry_rejected():
    with pytest.raises(ValueError):
        Region(box(0, 0, 0, 0))
