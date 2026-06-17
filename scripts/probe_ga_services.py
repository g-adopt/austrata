"""Empirically probe the GA services to validate gadata's design assumptions.

Run with the project python. Hits live endpoints; read-only GETs only.
"""
import requests

BH_WFS = "https://services.ga.gov.au/gis/boreholes/wfs"
HYDRO = "https://services.ga.gov.au/gis/rest/services/Hydrogeology_of_Australia/MapServer"

# Small test bbox around the ACT/Canberra region (lon/lat, GDA94)
BBOX = "148.9,-35.6,149.3,-35.1"

session = requests.Session()
session.headers.update({"User-Agent": "gadata-probe/0.1"})


def show_headers(resp, label):
    print(f"\n### {label} — HTTP {resp.status_code}")
    for h in ("ETag", "Last-Modified", "Cache-Control", "Content-Type"):
        if h in resp.headers:
            print(f"  {h}: {resp.headers[h]}")


def wfs_hits(typename):
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": typename, "resultType": "hits",
    }
    r = session.get(BH_WFS, params=params, timeout=60)
    show_headers(r, f"WFS hits {typename}")
    # numberMatched lives in the root element attributes
    text = r.text
    for token in text.split():
        if token.startswith("numberMatched="):
            print("  ", token)


def wfs_sample(typename, count=1, bbox=None):
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": typename, "count": count, "outputFormat": "application/json",
    }
    if bbox:
        params["bbox"] = bbox + ",urn:ogc:def:crs:EPSG::4283"
    r = session.get(BH_WFS, params=params, timeout=120)
    show_headers(r, f"WFS sample {typename}")
    try:
        gj = r.json()
    except Exception:
        print("  (not JSON) first 400 chars:\n", r.text[:400])
        return
    feats = gj.get("features", [])
    print(f"  returned features: {len(feats)}  numberMatched={gj.get('numberMatched')}  CRS={gj.get('crs')}")
    if feats:
        props = feats[0].get("properties", {})
        print("  property keys:")
        for k in sorted(props):
            v = props[k]
            sval = str(v)
            if len(sval) > 60:
                sval = sval[:60] + "..."
            print(f"    {k}: {sval}")
        print("  geometry type:", (feats[0].get("geometry") or {}).get("type"))


def arcgis_layer_info(layer=0):
    r = session.get(f"{HYDRO}/{layer}", params={"f": "json"}, timeout=60)
    show_headers(r, f"ArcGIS layer {layer} info")
    info = r.json()
    print("  name:", info.get("name"), "| geom:", info.get("geometryType"))
    print("  maxRecordCount:", info.get("maxRecordCount"))
    print("  editingInfo:", info.get("editingInfo"))
    print("  supportsPagination:", info.get("advancedQueryCapabilities", {}).get("supportsPagination"))


def arcgis_service_info():
    r = session.get(HYDRO, params={"f": "json"}, timeout=60)
    show_headers(r, "ArcGIS service info")
    info = r.json()
    print("  service modified:", info.get("modified"))
    print("  layers:", [(lyr.get("id"), lyr.get("name")) for lyr in info.get("layers", [])])


def arcgis_query_sample(layer=0):
    params = {
        "where": "1=1", "outFields": "*", "resultRecordCount": 1,
        "f": "geojson", "returnGeometry": "true",
    }
    r = session.get(f"{HYDRO}/{layer}/query", params=params, timeout=60)
    show_headers(r, f"ArcGIS query sample layer {layer}")
    try:
        gj = r.json()
        feats = gj.get("features", [])
        print("  returned:", len(feats), "exceededTransferLimit:", gj.get("exceededTransferLimit"))
        if feats:
            print("  property keys:", sorted(feats[0].get("properties", {}).keys()))
            print("  geometry type:", (feats[0].get("geometry") or {}).get("type"))
    except Exception:
        print("  first 300:", r.text[:300])


if __name__ == "__main__":
    print("=" * 70)
    print("BOREHOLES (GeoServer WFS)")
    print("=" * 70)
    wfs_hits("gsmlp:BoreholeView")
    wfs_sample("gsmlp:BoreholeView", count=1, bbox=BBOX)
    wfs_sample("bh:BoreholeStratigraphyLogs", count=1)
    wfs_sample("bh:BoreholeEarthMaterialLogs", count=1)

    print("\n" + "=" * 70)
    print("HYDROGEOLOGY (ArcGIS REST)")
    print("=" * 70)
    arcgis_service_info()
    arcgis_layer_info(0)
    arcgis_query_sample(0)
