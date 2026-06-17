"""gadata — Geoscience Australia borehole and hydrogeology data access.

The main entry point is :class:`GADataClient`. The domain value objects are
exported too for callers that want to work with the typed model directly.
"""
import logging as _logging

# Structured logging lives under the "gadata" namespace and is silent by
# default (a NullHandler), per library best practice. Applications opt in via
# logging.getLogger("gadata").setLevel(...) and attach their own handler.
# Configured before submodule imports so their module-level getLogger calls
# inherit a silenced parent regardless of import order.
_logging.getLogger("gadata").addHandler(_logging.NullHandler())

from gadata.domain.region import Region  # noqa: E402
from gadata.domain.borehole import Borehole, BoreholeCollection  # noqa: E402
from gadata.domain.stratigraphy import StratigraphyInterval, EarthMaterialInterval  # noqa: E402
from gadata.domain.hydrogeology import HydrogeologyUnit  # noqa: E402
from gadata.client import (  # noqa: E402
    GADataClient,
    hydrogeology_citation,
    hydrogeology_provenance,
)

__all__ = [
    "GADataClient",
    "Region",
    "Borehole",
    "BoreholeCollection",
    "StratigraphyInterval",
    "EarthMaterialInterval",
    "HydrogeologyUnit",
    "hydrogeology_provenance",
    "hydrogeology_citation",
]

__version__ = "0.1.0"
