"""Pure domain layer: value objects and entities with no I/O or HTTP."""
from austrata.domain.region import Region
from austrata.domain.borehole import Borehole, BoreholeCollection
from austrata.domain.stratigraphy import EarthMaterialInterval, StratigraphyInterval
from austrata.domain.construction import ConstructionInterval
from austrata.domain.hydrogeology import HydrogeologyUnit

__all__ = [
    "Region",
    "Borehole",
    "BoreholeCollection",
    "StratigraphyInterval",
    "EarthMaterialInterval",
    "ConstructionInterval",
    "HydrogeologyUnit",
]
