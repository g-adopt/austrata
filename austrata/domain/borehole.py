"""The :class:`Borehole` entity and :class:`BoreholeCollection` aggregate.

A ``Borehole`` is a header record (identity, location, drill metadata) plus
lazy accessors for its downhole logs. The header fields map from the
``gsmlp:BoreholeView`` layer, whose join key ``eno`` is lowercase (the log
layers expose it UPPERCASE as ``ENO`` — the mappers reconcile this).

Logs are *not* loaded here. Stratigraphy and earth-material loading depend on
the infrastructure layer (ENO-chunked POST GetFeature against the log tables),
built in a later task. The accessors below are the seam: they either return a
list that was injected into the entity, or raise ``NotImplementedError`` to
mark the unbuilt path. The same applies to ``BoreholeCollection.load_logs``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, List, Optional

from shapely.geometry import Point

from austrata.domain.coercion import to_float as _to_float
from austrata.domain.coercion import to_str as _to_str

if TYPE_CHECKING:  # avoid importing geopandas at domain import time
    from geopandas import GeoDataFrame

    from austrata.domain.construction import ConstructionInterval
    from austrata.domain.region import Region
    from austrata.domain.stratigraphy import EarthMaterialInterval, StratigraphyInterval


@dataclass
class Borehole:
    """A single borehole header with lazy downhole-log accessors.

    Mutable (it is an entity with identity ``eno``, and logs are memoised onto
    it once loaded), unlike the frozen interval/unit value objects.
    """

    eno: Optional[int]
    name: Optional[str]
    longitude: Optional[float]
    latitude: Optional[float]
    identifier: Optional[str] = None
    elevation_m: Optional[float] = None
    state: Optional[str] = None
    province: Optional[str] = None
    purpose: Optional[str] = None
    status: Optional[str] = None
    drilling_method: Optional[str] = None
    observation_method: Optional[str] = None
    data_custodian: Optional[str] = None
    depth_reference: Optional[str] = None
    # Source tag: 'GA' for the WFS, 'NGIS:NSW' etc. for the NGIS state cores.
    source: Optional[str] = None
    # Promoted NGIS header fields (broadly useful; null on GA).
    bore_depth_m: Optional[float] = None
    drilled_depth_m: Optional[float] = None
    drilled_date: Optional[str] = None
    # The entire original record verbatim, for whatever isn't promoted above.
    source_attributes: dict = field(default_factory=dict)

    # Memoised log stores. ``None`` means "not yet loaded"; a list means loaded.
    _stratigraphy: Optional[List["StratigraphyInterval"]] = field(default=None, repr=False)
    _earth_material: Optional[List["EarthMaterialInterval"]] = field(default=None, repr=False)
    _construction: Optional[List["ConstructionInterval"]] = field(default=None, repr=False)

    @classmethod
    def from_feature(cls, properties: dict, geometry: Optional[dict] = None) -> "Borehole":
        """Build a header from a ``gsmlp:BoreholeView`` GeoJSON feature.

        ``geometry`` is the GeoJSON geometry dict; its lon/lat point is the
        authoritative location when present, falling back to the GDA94 property
        fields otherwise.
        """
        p = properties or {}
        lon = lat = None
        if geometry and geometry.get("type") == "Point":
            coords = geometry.get("coordinates") or [None, None]
            lon, lat = _to_float(coords[0]), _to_float(coords[1])
        if lon is None:
            lon = _to_float(p.get("GDA94_dlong"))
        if lat is None:
            lat = _to_float(p.get("GDA94_dlat"))
        eno = _to_float(p.get("eno"))
        return cls(
            eno=int(eno) if eno is not None else None,
            name=_to_str(p.get("name")),
            longitude=lon,
            latitude=lat,
            identifier=_to_str(p.get("identifier")),
            elevation_m=_to_float(p.get("elevation_m")),
            state=_to_str(p.get("state")),
            province=_to_str(p.get("geologicalProvinces")),
            purpose=_to_str(p.get("purpose")),
            status=_to_str(p.get("status")),
            drilling_method=_to_str(p.get("drillingMethod")),
            observation_method=_to_str(p.get("observationMethod")),
            data_custodian=_to_str(p.get("boreholeDataCustodian")),
            depth_reference=_to_str(p.get("depthReferencePoints")),
            source="GA",
            source_attributes=dict(p),
        )

    @property
    def point(self) -> Optional[Point]:
        """Location as a shapely ``Point`` (lon, lat) in EPSG:4283, or ``None``."""
        if self.longitude is None or self.latitude is None:
            return None
        return Point(self.longitude, self.latitude)

    # -- log accessors (seam; infrastructure-backed loading is a later task) --

    @property
    def stratigraphy(self) -> List["StratigraphyInterval"]:
        """Stratigraphy intervals. Returns injected/memoised logs if present.

        TODO(task: logs): when not yet loaded, fetch via the injected source
        (ENO-chunked POST GetFeature) and memoise. Until that infrastructure
        exists, accessing an unloaded log raises ``NotImplementedError``.
        """
        if self._stratigraphy is None:
            raise NotImplementedError(
                "Stratigraphy log loading is not implemented yet (depends on the "
                "infrastructure layer); inject intervals via set_stratigraphy() for now."
            )
        return self._stratigraphy

    @property
    def earth_material(self) -> List["EarthMaterialInterval"]:
        """Earth-material intervals. Returns injected/memoised logs if present.

        TODO(task: logs): same loading seam as :attr:`stratigraphy`.
        """
        if self._earth_material is None:
            raise NotImplementedError(
                "Earth-material log loading is not implemented yet (depends on the "
                "infrastructure layer); inject intervals via set_earth_material() for now."
            )
        return self._earth_material

    @property
    def construction(self) -> List["ConstructionInterval"]:
        """Construction (screen/casing) intervals. NGIS-only; null on GA.

        Returns injected/memoised logs if present; raises until loaded.
        """
        if self._construction is None:
            raise NotImplementedError(
                "Construction log loading is not implemented yet (depends on the "
                "infrastructure layer); inject intervals via set_construction() for now."
            )
        return self._construction

    def set_stratigraphy(self, intervals: List["StratigraphyInterval"]) -> None:
        """Inject/memoise stratigraphy intervals (used by loaders and tests)."""
        self._stratigraphy = list(intervals)

    def set_earth_material(self, intervals: List["EarthMaterialInterval"]) -> None:
        """Inject/memoise earth-material intervals (used by loaders and tests)."""
        self._earth_material = list(intervals)

    def set_construction(self, intervals: List["ConstructionInterval"]) -> None:
        """Inject/memoise construction intervals (used by loaders and tests)."""
        self._construction = list(intervals)


class BoreholeCollection:
    """The set of boreholes for a :class:`Region` — iterable aggregate."""

    def __init__(self, boreholes: List[Borehole], region: "Region") -> None:
        self._boreholes = list(boreholes)
        self.region = region
        # Injected by GADataClient to back load_logs (keeps the domain free of
        # any infrastructure dependency). None until wired.
        self._loader = None
        # Provenance of the underlying cache entry, stamped by the client.
        self._provenance: Optional[dict] = None

    def provenance(self) -> dict:
        """Provenance of the data backing this collection (or ``{}``).

        Keys mirror the cache manifest: ``source_url``, ``license``,
        ``citation``, ``service_version``, ``feature_count``, ``fetched_at``.
        Empty when the collection was built outside :class:`GADataClient`.
        """
        return dict(self._provenance or {})

    def citation(self) -> str:
        """A human-readable citation string including the data access date."""
        prov = self._provenance or {}
        base = prov.get("citation") or "Geoscience Australia borehole data."
        parts = [base]
        fetched_at = prov.get("fetched_at")
        if fetched_at:
            import datetime as _dt

            date = _dt.datetime.fromtimestamp(float(fetched_at), _dt.timezone.utc).date()
            parts.append(f"Accessed {date.isoformat()}.")
        if prov.get("license"):
            parts.append(f"Licensed {prov['license']}.")
        if prov.get("source_url"):
            parts.append(f"Source: {prov['source_url']}")
        return " ".join(parts)

    def __iter__(self) -> Iterator[Borehole]:
        return iter(self._boreholes)

    def __len__(self) -> int:
        return len(self._boreholes)

    def __getitem__(self, index: int) -> Borehole:
        return self._boreholes[index]

    @property
    def enos(self) -> List[int]:
        """The ENO join keys present in this collection (skipping any None)."""
        return [b.eno for b in self._boreholes if b.eno is not None]

    def to_geodataframe(self) -> "GeoDataFrame":
        """Header attributes as a GeoDataFrame of points in EPSG:4283.

        Imported lazily so the domain module stays free of geopandas at import.
        """
        import geopandas as gpd

        records = []
        geoms = []
        for b in self._boreholes:
            records.append(
                {
                    "eno": b.eno,
                    "name": b.name,
                    "identifier": b.identifier,
                    "longitude": b.longitude,
                    "latitude": b.latitude,
                    "elevation_m": b.elevation_m,
                    "state": b.state,
                    "province": b.province,
                    "purpose": b.purpose,
                    "status": b.status,
                    "drilling_method": b.drilling_method,
                    "source": b.source,
                    "bore_depth_m": b.bore_depth_m,
                    "drilled_depth_m": b.drilled_depth_m,
                    "drilled_date": b.drilled_date,
                }
            )
            geoms.append(b.point)
        return gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4283")

    # -- downhole-log export --------------------------------------------

    #: Column order for the stratigraphy export (also used for an empty frame).
    _STRAT_COLUMNS = [
        "source", "eno", "borehole_name", "longitude", "latitude",
        "top_depth_m", "bottom_depth_m", "ref_elev_m_ahd",
        "top_elev_m_ahd", "bottom_elev_m_ahd",
        "unit", "unit_pid", "older_age", "younger_age",
        "older_age_ma", "younger_age_ma", "top_contact", "base_contact",
        "geological_province", "stratigraphy_id", "comment",
        "valid", "invalid_reason",
    ]
    #: Column order for the earth-material export.
    _EARTH_COLUMNS = [
        "source", "eno", "borehole_name", "longitude", "latitude",
        "top_depth_m", "bottom_depth_m", "ref_elev_m_ahd",
        "top_elev_m_ahd", "bottom_elev_m_ahd",
        "lithology", "lithology_group", "lithology_qualifier", "description",
        "geological_province", "earth_material_id", "valid", "invalid_reason",
    ]
    #: Column order for the construction export.
    _CONSTRUCTION_COLUMNS = [
        "source", "eno", "borehole_name", "longitude", "latitude",
        "top_depth_m", "bottom_depth_m", "ref_elev_m_ahd",
        "top_elev_m_ahd", "bottom_elev_m_ahd",
        "construction_type", "material", "inner_diameter", "outer_diameter",
        "property", "property_size", "drill_method", "valid", "invalid_reason",
    ]

    def stratigraphy_geodataframe(self) -> "GeoDataFrame":
        """Loaded stratigraphy intervals as a tidy GeoDataFrame (EPSG:4283).

        One row per :class:`StratigraphyInterval` across the collection, with the
        borehole's Point as geometry. Requires logs to have been loaded first
        (``load_logs('stratigraphy')``); raises ``RuntimeError`` otherwise. When
        loaded but empty, returns an empty frame with the documented columns.
        """
        if not any(b._stratigraphy is not None for b in self._boreholes):
            raise RuntimeError(
                "stratigraphy not loaded; call load_logs('stratigraphy') first"
            )
        records, geoms = [], []
        for b in self._boreholes:
            for iv in (b._stratigraphy or []):
                records.append({
                    "source": b.source,
                    "eno": iv.eno,
                    "borehole_name": iv.borehole_name or b.name,
                    "longitude": b.longitude,
                    "latitude": b.latitude,
                    "top_depth_m": iv.top_depth,
                    "bottom_depth_m": iv.bottom_depth,
                    "ref_elev_m_ahd": iv.ref_elevation_m_ahd,
                    "top_elev_m_ahd": iv.top_elev_m_ahd,
                    "bottom_elev_m_ahd": iv.bottom_elev_m_ahd,
                    "unit": iv.unit,
                    "unit_pid": iv.unit_pid,
                    "older_age": iv.older_age,
                    "younger_age": iv.younger_age,
                    "older_age_ma": iv.older_age_ma,
                    "younger_age_ma": iv.younger_age_ma,
                    "top_contact": iv.top_contact,
                    "base_contact": iv.base_contact,
                    "geological_province": iv.geological_province,
                    "stratigraphy_id": iv.stratigraphy_id,
                    "comment": iv.comment,
                    "valid": iv.valid,
                    "invalid_reason": iv.invalid_reason,
                })
                geoms.append(b.point)
        return self._log_frame(records, geoms, self._STRAT_COLUMNS)

    def earth_material_geodataframe(self) -> "GeoDataFrame":
        """Loaded earth-material intervals as a tidy GeoDataFrame (EPSG:4283).

        One row per :class:`EarthMaterialInterval`; same contract as
        :meth:`stratigraphy_geodataframe` (load first, empty-frame when empty).
        """
        if not any(b._earth_material is not None for b in self._boreholes):
            raise RuntimeError(
                "earth_material not loaded; call load_logs('earth_material') first"
            )
        records, geoms = [], []
        for b in self._boreholes:
            for iv in (b._earth_material or []):
                records.append({
                    "source": b.source,
                    "eno": iv.eno,
                    "borehole_name": iv.borehole_name or b.name,
                    "longitude": b.longitude,
                    "latitude": b.latitude,
                    "top_depth_m": iv.top_depth,
                    "bottom_depth_m": iv.bottom_depth,
                    "ref_elev_m_ahd": iv.ref_elevation_m_ahd,
                    "top_elev_m_ahd": iv.top_elev_m_ahd,
                    "bottom_elev_m_ahd": iv.bottom_elev_m_ahd,
                    "lithology": iv.lithology,
                    "lithology_group": iv.lithology_group,
                    "lithology_qualifier": iv.lithology_qualifier,
                    "description": iv.description,
                    "geological_province": iv.geological_province,
                    "earth_material_id": iv.earth_material_id,
                    "valid": iv.valid,
                    "invalid_reason": iv.invalid_reason,
                })
                geoms.append(b.point)
        return self._log_frame(records, geoms, self._EARTH_COLUMNS)

    def construction_geodataframe(self) -> "GeoDataFrame":
        """Loaded construction intervals as a tidy GeoDataFrame (EPSG:4283).

        One row per :class:`ConstructionInterval` (NGIS-only); same contract as
        :meth:`stratigraphy_geodataframe` (load first via
        ``load_logs('construction')``, empty-frame when loaded but empty).
        """
        if not any(b._construction is not None for b in self._boreholes):
            raise RuntimeError(
                "construction not loaded; call load_logs('construction') first"
            )
        records, geoms = [], []
        for b in self._boreholes:
            for iv in (b._construction or []):
                records.append({
                    "source": b.source,
                    "eno": iv.eno,
                    "borehole_name": iv.borehole_name or b.name,
                    "longitude": b.longitude,
                    "latitude": b.latitude,
                    "top_depth_m": iv.top_depth,
                    "bottom_depth_m": iv.bottom_depth,
                    "ref_elev_m_ahd": iv.ref_elevation_m_ahd,
                    "top_elev_m_ahd": iv.top_elev_m_ahd,
                    "bottom_elev_m_ahd": iv.bottom_elev_m_ahd,
                    "construction_type": iv.construction_type,
                    "material": iv.material,
                    "inner_diameter": iv.inner_diameter,
                    "outer_diameter": iv.outer_diameter,
                    "property": iv.property,
                    "property_size": iv.property_size,
                    "drill_method": iv.drill_method,
                    "valid": iv.valid,
                    "invalid_reason": iv.invalid_reason,
                })
                geoms.append(b.point)
        return self._log_frame(records, geoms, self._CONSTRUCTION_COLUMNS)

    @staticmethod
    def _log_frame(records, geoms, columns) -> "GeoDataFrame":
        """Build a log GeoDataFrame, falling back to an empty typed frame."""
        import geopandas as gpd

        if not records:
            return gpd.GeoDataFrame({c: [] for c in columns}, geometry=[], crs="EPSG:4283")
        return gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4283")[columns + ["geometry"]]

    def load_logs(self, kind: str = "stratigraphy", **kwargs) -> None:
        """Bulk-load downhole logs for every borehole in the collection.

        Delegates to a loader injected by :class:`~austrata.client.GADataClient`
        (which collects ``self.enos``, fetches the ENO-set log pull through the
        cache, and distributes intervals onto each :class:`Borehole` by ENO). A
        collection built outside the client has no loader and raises.
        """
        if self._loader is not None:
            self._loader(kind, **kwargs)
            return
        raise NotImplementedError(
            "This BoreholeCollection has no log loader; obtain it from "
            "GADataClient.boreholes() to enable load_logs()."
        )
