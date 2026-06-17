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

from gadata.domain.coercion import to_float as _to_float
from gadata.domain.coercion import to_str as _to_str

if TYPE_CHECKING:  # avoid importing geopandas at domain import time
    from geopandas import GeoDataFrame

    from gadata.domain.region import Region
    from gadata.domain.stratigraphy import EarthMaterialInterval, StratigraphyInterval


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

    # Memoised log stores. ``None`` means "not yet loaded"; a list means loaded.
    _stratigraphy: Optional[List["StratigraphyInterval"]] = field(default=None, repr=False)
    _earth_material: Optional[List["EarthMaterialInterval"]] = field(default=None, repr=False)

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

    def set_stratigraphy(self, intervals: List["StratigraphyInterval"]) -> None:
        """Inject/memoise stratigraphy intervals (used by loaders and tests)."""
        self._stratigraphy = list(intervals)

    def set_earth_material(self, intervals: List["EarthMaterialInterval"]) -> None:
        """Inject/memoise earth-material intervals (used by loaders and tests)."""
        self._earth_material = list(intervals)


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
                }
            )
            geoms.append(b.point)
        return gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4283")

    def load_logs(self, kind: str = "stratigraphy", **kwargs) -> None:
        """Bulk-load downhole logs for every borehole in the collection.

        Delegates to a loader injected by :class:`~gadata.client.GADataClient`
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
