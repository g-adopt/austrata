"""austrata — Geoscience Australia borehole and hydrogeology data access.

The main entry point is :class:`GADataClient`. The domain value objects are
exported too for callers that want to work with the typed model directly.
"""
import logging as _logging

# Structured logging lives under the "austrata" namespace and is silent by
# default (a NullHandler), per library best practice. Applications opt in via
# logging.getLogger("austrata").setLevel(...) and attach their own handler.
# Configured before submodule imports so their module-level getLogger calls
# inherit a silenced parent regardless of import order.
_logging.getLogger("austrata").addHandler(_logging.NullHandler())

from austrata.domain.region import Region  # noqa: E402
from austrata.domain.borehole import Borehole, BoreholeCollection  # noqa: E402
from austrata.domain.stratigraphy import StratigraphyInterval, EarthMaterialInterval  # noqa: E402
from austrata.domain.construction import ConstructionInterval  # noqa: E402
from austrata.domain.hydrogeology import HydrogeologyUnit  # noqa: E402
from austrata.client import (  # noqa: E402
    GADataClient,
    hydrogeology_citation,
    hydrogeology_provenance,
)
from austrata.ngis_client import NGISClient  # noqa: E402
from austrata.groundwater_client import GroundwaterClient  # noqa: E402

__all__ = [
    "GADataClient",
    "NGISClient",
    "GroundwaterClient",
    "Region",
    "Borehole",
    "BoreholeCollection",
    "StratigraphyInterval",
    "EarthMaterialInterval",
    "ConstructionInterval",
    "HydrogeologyUnit",
    "hydrogeology_provenance",
    "hydrogeology_citation",
]

__version__ = "0.1.0"
