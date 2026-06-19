"""Pure/offline unit tests for the NGIS-only ConstructionInterval object."""
from austrata.domain.construction import ConstructionInterval

# GA-shaped (UPPERCASE) keys, as the NGIS optimiser emits them.
CONSTRUCTION_FEATURE = {
    "ENO": None,
    "BOREHOLE_NAME": "GW029313",
    "INTERVAL_BEGIN_M": 30,
    "INTERVAL_END_M": 42,
    "CONSTRUCTION_TYPE": "Screen",
    "MATERIAL": "PVC",
    "INNER_DIAMETER": 100.0,
    "OUTER_DIAMETER": 110.0,
    "PROPERTY": "Slotted",
    "PROPERTY_SIZE": 0.5,
    "DRILL_METHOD": "Rotary",
    "DEPTH_REF_POINT_ELEV_M_AHD": 120,
    "INTERVAL_BEGIN_ELEV_M_AHD": 90,
    "INTERVAL_END_ELEV_M_AHD": 78,
    "BoreID": 999,
}


def test_construction_from_feature_normalises_fields():
    iv = ConstructionInterval.from_feature(CONSTRUCTION_FEATURE)
    assert iv.borehole_name == "GW029313"
    assert iv.top_depth == 30.0 and iv.bottom_depth == 42.0
    assert iv.construction_type == "Screen"
    assert iv.material == "PVC"
    assert iv.inner_diameter == 100.0 and iv.outer_diameter == 110.0
    assert iv.property == "Slotted" and iv.property_size == 0.5
    assert iv.drill_method == "Rotary"
    assert iv.ref_elevation_m_ahd == 120.0
    assert iv.top_elev_m_ahd == 90.0 and iv.bottom_elev_m_ahd == 78.0
    assert iv.valid is True
    assert iv.invalid_reason is None


def test_construction_captures_raw_bag():
    iv = ConstructionInterval.from_feature(CONSTRUCTION_FEATURE)
    assert iv.source_attributes["BoreID"] == 999
    assert iv.source_attributes == CONSTRUCTION_FEATURE


def test_construction_inverted_interval_flagged_invalid():
    feat = dict(CONSTRUCTION_FEATURE, INTERVAL_BEGIN_M=42, INTERVAL_END_M=30)
    iv = ConstructionInterval.from_feature(feat)
    assert iv.valid is False
    assert "inverted" in iv.invalid_reason


def test_construction_compare_false_keeps_object_hashable():
    a = ConstructionInterval.from_feature(CONSTRUCTION_FEATURE)
    b = ConstructionInterval.from_feature(CONSTRUCTION_FEATURE)
    # source_attributes is compare=False, so equal real fields => equal/hashable.
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
