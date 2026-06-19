"""Pure/offline unit tests for the log interval value objects."""
from austrata.domain.stratigraphy import EarthMaterialInterval, StratigraphyInterval

# Field names mirror the live bh:BoreholeStratigraphyLogs probe (UPPERCASE).
STRAT_FEATURE = {
    "ENO": 35147,
    "BOREHOLE_NAME": "AAMT8642",
    "BOREHOLE_PID": "http://pid.geoscience.gov.au/samplingFeature/au/BH35147",
    "INTERVAL_BEGIN_M": 26,
    "INTERVAL_END_M": 27,
    "STRAT_UNIT_NAME": "Loxton Sand",
    "STRAT_UNIT_PID": "http://pid.geoscience.gov.au/geologicFeature/au/SU10825",
    "OLDER_AGE": None,
    "YOUNGER_AGE": None,
    "GEOLOGICAL_PROVINCE": "Murray Basin",
    "DEPTH_REF_POINT_ELEV_M_AHD": 37,
    "STRATIGRAPHY_ID": 340827,
}

EARTH_FEATURE = {
    "ENO": 35151,
    "BOREHOLE_NAME": "AAMT8644",
    "BOREHOLE_PID": "http://pid.geoscience.gov.au/samplingFeature/au/BH35151",
    "INTERVAL_BEGIN_M": 27,
    "INTERVAL_END_M": 28,
    "LITHOLOGY": "unknown",
    "LITHOLOGY_GROUP": "rock",
    "EARTH_MATERIAL_DESC": "Saturated zone",
    "GEOLOGICAL_PROVINCE": "Murray Basin",
    "EARTH_MATERIAL_ID": 154236,
}


def test_stratigraphy_from_feature_normalises_fields():
    iv = StratigraphyInterval.from_feature(STRAT_FEATURE)
    assert iv.eno == 35147
    assert iv.borehole_name == "AAMT8642"
    assert iv.top_depth == 26.0 and iv.bottom_depth == 27.0
    assert iv.unit == "Loxton Sand"
    assert iv.geological_province == "Murray Basin"
    assert iv.ref_elevation_m_ahd == 37.0
    assert iv.valid is True
    assert iv.invalid_reason is None


def test_earth_material_from_feature_normalises_fields():
    iv = EarthMaterialInterval.from_feature(EARTH_FEATURE)
    assert iv.eno == 35151
    assert iv.lithology == "unknown"
    assert iv.lithology_group == "rock"
    assert iv.description == "Saturated zone"
    assert iv.top_depth == 27.0 and iv.bottom_depth == 28.0
    assert iv.valid is True


def test_inverted_interval_flagged_invalid():
    feat = dict(STRAT_FEATURE, INTERVAL_BEGIN_M=30, INTERVAL_END_M=10)
    iv = StratigraphyInterval.from_feature(feat)
    assert iv.valid is False
    assert "inverted" in iv.invalid_reason


def test_missing_depth_flagged_invalid():
    feat = dict(STRAT_FEATURE, INTERVAL_END_M=None)
    iv = StratigraphyInterval.from_feature(feat)
    assert iv.valid is False
    assert "missing" in iv.invalid_reason


def test_zero_thickness_interval_is_valid():
    feat = dict(STRAT_FEATURE, INTERVAL_BEGIN_M=12, INTERVAL_END_M=12)
    iv = StratigraphyInterval.from_feature(feat)
    assert iv.valid is True


def test_blank_strings_become_none():
    feat = dict(STRAT_FEATURE, STRAT_UNIT_NAME="  ", OLDER_AGE="None")
    iv = StratigraphyInterval.from_feature(feat)
    assert iv.unit is None
    assert iv.older_age is None


def test_string_depths_are_parsed():
    feat = dict(STRAT_FEATURE, INTERVAL_BEGIN_M="26.5", INTERVAL_END_M="27.5")
    iv = StratigraphyInterval.from_feature(feat)
    assert iv.top_depth == 26.5 and iv.bottom_depth == 27.5
    assert iv.valid is True


def test_unparseable_depth_treated_as_missing():
    feat = dict(EARTH_FEATURE, INTERVAL_BEGIN_M="deep")
    iv = EarthMaterialInterval.from_feature(feat)
    assert iv.top_depth is None
    assert iv.valid is False


def test_intervals_capture_full_raw_bag():
    s = StratigraphyInterval.from_feature(STRAT_FEATURE)
    assert s.source_attributes == STRAT_FEATURE
    e = EarthMaterialInterval.from_feature(EARTH_FEATURE)
    assert e.source_attributes == EARTH_FEATURE


def test_strat_comment_is_none_for_ga_but_promoted_when_present():
    assert StratigraphyInterval.from_feature(STRAT_FEATURE).comment is None
    feat = dict(STRAT_FEATURE, COMMENT="Bottom of renmark at 186 m")
    assert StratigraphyInterval.from_feature(feat).comment == "Bottom of renmark at 186 m"


def test_source_attributes_compare_false_keeps_intervals_hashable():
    a = StratigraphyInterval.from_feature(STRAT_FEATURE)
    b = StratigraphyInterval.from_feature(dict(STRAT_FEATURE, EXTRA="differs"))
    # Real fields identical, only the raw bag differs (compare=False).
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
