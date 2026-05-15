import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import json
import unicodedata
from itertools import combinations

try:
    import networkx as nx
    NETWORKX_OK = True
except ModuleNotFoundError:
    NETWORKX_OK = False

st.set_page_config(page_title="ERA Fotode Andmebaas", page_icon="📷", layout="wide")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────
# Abifunktsioonid
# ─────────────────────────────────────────────────────────────

NULL_VALS = {"", "nan", "none", "null", "<na>", "nat"}

def is_null_like(x):
    return pd.isna(x) or str(x).strip().lower() in NULL_VALS

def clean_series(series):
    if series is None:
        return pd.Series(dtype="object")
    s = series.dropna().astype(str).str.strip()
    return s[~s.str.lower().isin(NULL_VALS)]

def clean_df(df_in):
    out = df_in.copy()
    for col in out.select_dtypes(include="object").columns:
        out[col] = out[col].apply(lambda x: "" if is_null_like(x) else x)
    return out

def safe_sheet(xl, name):
    return xl.parse(name) if xl and name in xl.sheet_names else pd.DataFrame()

def ensure_col(df, col, default=pd.NA):
    if col not in df.columns:
        df[col] = default
    return df

def safe_contains(series, text):
    return series.fillna("").astype(str).str.contains(text, case=False, na=False)

def normalize_file(name):
    return unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii").lower().replace(" ", "").replace("_", "").replace("-", "")

def find_file(candidates, fallback=None):
    for fname in candidates:
        path = os.path.join(BASE_DIR, fname)
        if os.path.exists(path):
            return path
    if fallback:
        want = normalize_file(fallback)
        for fname in os.listdir(BASE_DIR):
            if want in normalize_file(fname):
                return os.path.join(BASE_DIR, fname)
    return None

def read_sheet(path, sheets, required=None):
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    xl = pd.ExcelFile(path)
    for s in sheets:
        if s in xl.sheet_names:
            df = xl.parse(s)
            if required is None or any(c in df.columns for c in required):
                return df
    if required:
        for s in xl.sheet_names:
            df = xl.parse(s)
            if any(c in df.columns for c in required):
                return df
    return pd.DataFrame()

def normalize_place(x):
    if pd.isna(x):
        return pd.NA
    x = str(x).strip()
    if not x or x.lower() in NULL_VALS:
        return pd.NA
    xl = x.lower()
    if xl in {"tallinn", "tallinna linn", "tallinn linn"}:
        return "Tallinn"
    if xl in {"tartu", "tartu linn", "tartu linn."}:
        return "Tartu"
    if xl in {"petserimaa", "petseri"}:
        return "Petserimaa"
    if xl in {"setumaa", "setomaa", "setu ala"}:
        return "Setumaa"
    return x

def split_cats(series):
    if series is None or len(series) == 0:
        return pd.Series(dtype="object")
    return (series.dropna().astype(str)
            .str.replace(";", ",", regex=False).str.replace("|", ",", regex=False)
            .str.split(",").explode().str.strip().replace("", pd.NA).dropna())

def filter_cats(df, col, selected):
    if not selected or col not in df.columns:
        return df
    sel = {str(x).strip().lower() for x in selected if str(x).strip()}
    def has(val):
        cats = [c.strip().lower() for c in str(val).replace(";", ",").replace("|", ",").split(",") if c.strip()]
        return any(c in sel for c in cats)
    return df[df[col].fillna("").apply(has)]

def poly_rings(geom):
    if not geom or "type" not in geom:
        return []
    rings = []
    try:
        if geom["type"] == "Polygon" and geom["coordinates"] and geom["coordinates"][0]:
            rings.append(geom["coordinates"][0])
        elif geom["type"] == "MultiPolygon":
            for poly in geom["coordinates"]:
                if poly and poly[0]:
                    rings.append(poly[0])
    except Exception:
        pass
    return rings

def add_borders(fig, geojson, color="black", width=1):
    if not geojson or "features" not in geojson:
        return fig
    for feat in geojson["features"]:
        for coords in poly_rings(feat.get("geometry", {})):
            if not coords or len(coords) < 2:
                continue
            try:
                lons = [c[0] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                lats = [c[1] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                if len(lons) >= 2:
                    fig.add_trace(go.Scattermapbox(lon=lons, lat=lats, mode="lines",
                                                   line=dict(color=color, width=width),
                                                   hoverinfo="skip", showlegend=False))
            except Exception:
                continue
    return fig

def ml_keyword_map(ml):
    if ml is None or ml.empty:
        return pd.DataFrame(columns=["Märksõna", "Märksõna kategooria"])
    ml = ml.copy()
    ml.columns = ml.columns.astype(str).str.strip()
    kw = next((c for c in ["Märksõna", "märksõna", "marksona", "keyword"] if c in ml.columns), None)
    cat = next((c for c in ["Märksõna2", "märksõna2", "klaster", "klastrid", "Märksõna kategooria", "kategooria"] if c in ml.columns), None)
    if kw and cat:
        out = ml[[kw, cat]].copy()
        out.columns = ["Märksõna", "Märksõna kategooria"]
        out = out.dropna()
        out["Märksõna"] = out["Märksõna"].astype(str).str.strip()
        out["Märksõna kategooria"] = out["Märksõna kategooria"].astype(str).str.strip()
        return out[(out["Märksõna"] != "") & (out["Märksõna kategooria"] != "")].drop_duplicates()
    return pd.DataFrame(columns=["Märksõna", "Märksõna kategooria"])

def cat_match(row, manual_col, pred_cols):
    manual = [c.strip().lower() for c in str(row.get(manual_col, "")).replace(";", ",").replace("|", ",").split(",")
              if c.strip() and c.strip().lower() not in NULL_VALS]
    preds = [str(row.get(c, "")).strip().lower() for c in pred_cols
             if str(row.get(c, "")).strip() and str(row.get(c, "")).strip().lower() not in NULL_VALS]
    return bool(manual and preds and any(p in manual for p in preds))

def add_ml_scores(df):
    top3 = [c for c in ["pred_top1_score", "pred_top2_score", "pred_top3_score"] if c in df.columns]
    top5 = [c for c in ["pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score"] if c in df.columns]
    for col in top5 + ["confidence_margin_top1_top2"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if top3:
        df["ML top3 koondskoor"] = df[top3].sum(axis=1, min_count=1)
    if top5:
        df["ML top5 koondskoor"] = df[top5].sum(axis=1, min_count=1)
    if "confidence_margin_top1_top2" not in df.columns and {"pred_top1_score", "pred_top2_score"}.issubset(df.columns):
        df["confidence_margin_top1_top2"] = df["pred_top1_score"] - df["pred_top2_score"]
    if "pred_top1_score" in df.columns and "confidence_margin_top1_top2" in df.columns:
        def strength(row):
            s, m = row.get("pred_top1_score"), row.get("confidence_margin_top1_top2")
            if pd.isna(s) or pd.isna(m):
                return pd.NA
            if s >= 0.565 and m >= 0.020:
                return "tugev"
            if s >= 0.555 and m >= 0.010:
                return "keskmine"
            return "nõrk / kontrolli üle"
        df["ML otsuse tugevus"] = df.apply(strength, axis=1)
        df["ML kindlus"] = df["ML otsuse tugevus"]
    return df

def prep_clip(clip_df):
    if clip_df is None or clip_df.empty:
        return pd.DataFrame()
    out = clip_df.copy()
    out.columns = out.columns.astype(str).str.strip()
    for col in ["PID", "failinimi", "filename", "image_path"]:
        if col in out.columns:
            out[col] = out[col].fillna("").astype(str).str.strip()
    if "PID" not in out.columns:
        out["PID"] = ""
    if "failinimi" not in out.columns:
        if "filename" in out.columns:
            out["failinimi"] = out["filename"]
        elif "image_path" in out.columns:
            out["failinimi"] = out["image_path"].apply(lambda x: os.path.basename(str(x)))
        else:
            out["failinimi"] = ""
    return add_ml_scores(out)

def build_hover(row, cols):
    """Tooltip ainult olemasolevate väljadega — null-read jäetakse täielikult välja."""
    parts = [f"<b>{c}:</b> {row[c]}" for c in cols if c in row and not is_null_like(row.get(c))]
    return "<br>".join(parts) if parts else "—"

def plot_network(edges_df, src, tgt, w, title, max_e=100):
    if not NETWORKX_OK:
        st.warning("Lisa `networkx` requirements.txt faili.")
        return
    if edges_df is None or edges_df.empty:
        st.info("Piisavalt seoseid ei leitud.")
        return
    edges_df = edges_df.sort_values(w, ascending=False).head(max_e)
    G = nx.Graph()
    for _, row in edges_df.iterrows():
        s, t = str(row[src]).strip(), str(row[tgt]).strip()
        if s and t and s != t:
            G.add_edge(s, t, weight=row[w])
    if G.number_of_edges() == 0:
        st.info("Piisavalt seoseid ei leitud.")
        return
    pos = nx.spring_layout(G, k=0.7, iterations=50, seed=42)
    ex, ey = [], []
    for e in G.edges():
        x0, y0 = pos[e[0]]; x1, y1 = pos[e[1]]
        ex += [x0, x1, None]; ey += [y0, y1, None]
    deg = dict(G.degree(weight="weight"))
    fig = go.Figure(data=[
        go.Scatter(x=ex, y=ey, line=dict(width=0.7), hoverinfo="none", mode="lines"),
        go.Scatter(x=[pos[n][0] for n in G.nodes()], y=[pos[n][1] for n in G.nodes()],
                   mode="markers+text", text=list(G.nodes()), textposition="top center",
                   hovertext=[f"{n}<br>Seoste tugevus: {deg.get(n,0)}" for n in G.nodes()],
                   hoverinfo="text",
                   marker=dict(size=[8 + min(deg.get(n,0),30)*1.5 for n in G.nodes()], opacity=0.85))
    ])
    fig.update_layout(title=title, showlegend=False, height=650,
                      margin=dict(l=0, r=0, t=40, b=0),
                      xaxis=dict(showgrid=False, zeroline=False, visible=False),
                      yaxis=dict(showgrid=False, zeroline=False, visible=False))
    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────
# Andmete laadimine
# ─────────────────────────────────────────────────────────────

@st.cache_data
def load_data():
    xlsx_path = None
    for fname in ["ERA_fotod_piiridega.xlsx", "ERA_fotod_250426.xlsx",
                  "ERA_fotod_10.03.26_koordinaatidega.xlsx", "ERA_fotod_geocoded.xlsx"]:
        p = os.path.join(BASE_DIR, fname)
        if os.path.exists(p):
            xlsx_path = p
            break
    if not xlsx_path:
        raise FileNotFoundError("Ühtegi ERA Exceli faili ei leitud rakenduse kaustast.")

    xl        = pd.ExcelFile(xlsx_path)
    fotod     = safe_sheet(xl, "fotod_koordinaatidega")
    master    = safe_sheet(xl, "fotod_master")
    marksoned = safe_sheet(xl, "märksõnad_pikk")
    isikud    = safe_sheet(xl, "isikud_fotol_pikk")
    kp_sheet  = next((s for s in xl.sheet_names if s.lower() == "kihelkond_keskpunktid"), None)
    kp        = xl.parse(kp_sheet) if kp_sheet else pd.DataFrame()

    ml_path   = find_file(["ERA_märksõnad_ML.xlsx", "ERA_marksonad_ML.xlsx"], "marksonadml")
    clip_path = find_file(["era_clip_KOIK_pildid_sigmoid.xlsx"], "clipkoikpildidsigmoid")
    ml_raw    = read_sheet(ml_path, ["märksõnad_pikk", "ml_foto_klastrid", "ml_multihot_klastrid"], ["Märksõna2", "klastrid"])
    kw_map    = ml_keyword_map(ml_raw)
    ml_clip   = prep_clip(read_sheet(clip_path, ["predictions_all", "predictions_eval_only", "sample_all"], ["pred_top1"]))
    ml_metrics = pd.DataFrame()
    if clip_path and os.path.exists(clip_path):
        try:
            ml_metrics = safe_sheet(pd.ExcelFile(clip_path), "cluster_metrics")
        except Exception:
            pass

    if fotod.empty:
        raise ValueError("Sheet 'fotod_koordinaatidega' puudub või on tühi.")

    for d in [fotod, master, marksoned, isikud, kp, ml_raw, ml_clip, ml_metrics]:
        if not d.empty:
            d.columns = d.columns.astype(str).str.strip()
            if "PID" in d.columns:
                d["PID"] = d["PID"].fillna("").astype(str).str.strip()

    coord_map = {"Latitude": "latitude", "Longitude": "longitude", "lat": "latitude",
                 "lon": "longitude", "long": "longitude",
                 "lõplik_latitude": "latitude", "lõplik_longitude": "longitude"}
    fotod = fotod.rename(columns=coord_map)
    kp    = kp.rename(columns=coord_map)

    if not master.empty and "PID" in master.columns and "PID" in fotod.columns:
        master = master.rename(columns={"Zanr": "Žanr", "zanr": "Žanr", "žanr": "Žanr", "aasta": "Aasta"})
        cols = [c for c in ["PID","Aasta","Žanr","Sisu kirjeldus","failinimi","Projekt",
                             "ERA märksõnad (koondatud)","Isikute arv"] if c in master.columns]
        for c in cols:
            if c != "PID" and c in fotod.columns:
                fotod = fotod.drop(columns=[c])
        fotod = fotod.merge(master[cols].drop_duplicates("PID"), on="PID", how="left")

    if not ml_raw.empty and "PID" in ml_raw.columns and "PID" in fotod.columns:
        if "klastrid" not in ml_raw.columns and "Märksõna2" in ml_raw.columns:
            agg = {"Märksõna2": lambda s: ", ".join(sorted(set(x for x in s.dropna().astype(str).str.strip() if x)))}
            if "Märksõna" in ml_raw.columns:
                agg["Märksõna"] = lambda s: ", ".join(sorted(set(x for x in s.dropna().astype(str).str.strip() if x)))
            ml_raw = ml_raw.groupby("PID", as_index=False).agg(agg)
            ml_raw = ml_raw.rename(columns={"Märksõna2": "klastrid", "Märksõna": "märksõnad"})
            ml_raw["klastrite_arv"] = ml_raw["klastrid"].fillna("").apply(lambda x: len([c for c in str(x).split(",") if c.strip()]))
            if "märksõnad" in ml_raw.columns:
                ml_raw["märksõnade_arv"] = ml_raw["märksõnad"].fillna("").apply(lambda x: len([c for c in str(x).split(",") if c.strip()]))
        ml_cols = [c for c in ["PID","klastrid","klastrite_arv","märksõnad","märksõnade_arv"] if c in ml_raw.columns]
        for c in ["Märksõna kategooria","Märksõna kategooriate arv","Originaal märksõnad","Originaal märksõnade arv"]:
            if c in fotod.columns:
                fotod = fotod.drop(columns=[c])
        fotod = fotod.merge(ml_raw[ml_cols].drop_duplicates("PID"), on="PID", how="left")
        fotod = fotod.rename(columns={"klastrid": "Märksõna kategooria", "klastrite_arv": "Märksõna kategooriate arv",
                                       "märksõnad": "Originaal märksõnad", "märksõnade_arv": "Originaal märksõnade arv"})

    if not ml_clip.empty and "PID" in ml_clip.columns and "PID" in fotod.columns:
        c_pid = ml_clip[ml_clip["PID"] != ""].copy()
        c_cols = [c for c in ["PID","pred_top1","pred_top2","pred_top3","pred_top4","pred_top5",
                               "pred_top1_score","pred_top2_score","pred_top3_score","pred_top4_score","pred_top5_score",
                               "confidence_margin_top1_top2","true_clusters","hit_top1","hit_any_top3","hit_any_top5"]
                  if c in c_pid.columns]
        for c in c_cols:
            if c != "PID" and c in fotod.columns:
                fotod = fotod.drop(columns=[c])
        if c_cols:
            fotod = fotod.merge(c_pid[c_cols].drop_duplicates("PID"), on="PID", how="left")
            fotod = add_ml_scores(fotod)

    req = ["PID","Aasta","Žanr","Kihelkond","Sisu kirjeldus","failinimi","koordinaadid_leitud",
           "latitude","longitude","Projekt","ERA märksõnad (koondatud)","Isikute arv",
           "kihelkond_kaart","Kihelkond või linn","Märksõna kategooria","Märksõna kategooriate arv",
           "Originaal märksõnad","Originaal märksõnade arv",
           "pred_top1","pred_top2","pred_top3","pred_top4","pred_top5",
           "pred_top1_score","pred_top2_score","pred_top3_score","pred_top4_score","pred_top5_score",
           "confidence_margin_top1_top2","ML top3 koondskoor","ML top5 koondskoor",
           "true_clusters","hit_top1","hit_any_top3","hit_any_top5","ML kindlus","ML otsuse tugevus"]
    for col in req:
        ensure_col(fotod, col)
    for col in ["PID", "Märksõna"]:
        ensure_col(marksoned, col)
    if not kw_map.empty and "Märksõna" in marksoned.columns:
        if "Märksõna kategooria" in marksoned.columns:
            marksoned = marksoned.drop(columns=["Märksõna kategooria"])
        marksoned = marksoned.merge(kw_map, on="Märksõna", how="left")
    else:
        ensure_col(marksoned, "Märksõna kategooria")
    for col in ["PID", "Isik", "Fotograaf"]:
        ensure_col(isikud, col)

    if not isikud.empty and {"PID","Fotograaf"}.issubset(isikud.columns):
        fm = isikud[["PID","Fotograaf"]].dropna(subset=["Fotograaf"]).drop_duplicates("PID")
        if "Fotograaf" in fotod.columns:
            fotod = fotod.drop(columns=["Fotograaf"])
        fotod = fotod.merge(fm, on="PID", how="left")

    fotod["Aasta"]     = pd.to_numeric(fotod["Aasta"], errors="coerce")
    fotod["latitude"]  = pd.to_numeric(fotod["latitude"], errors="coerce")
    fotod["longitude"] = pd.to_numeric(fotod["longitude"], errors="coerce")
    fotod["koordinaadid_leitud"] = (fotod["latitude"].notna() & fotod["longitude"].notna()).map({True: "jah", False: "ei"})

    fotod["kaardi_piirkond"] = pd.NA
    for src_col in ["kihelkond_kaart", "Kihelkond või linn", "Kihelkond"]:
        if src_col in fotod.columns:
            mask = fotod["kaardi_piirkond"].isna()
            fotod.loc[mask, "kaardi_piirkond"] = fotod.loc[mask, src_col].apply(normalize_place)

    if not kp.empty:
        kp = kp.rename(columns={kp.columns[0]: "kaardi_piirkond"})
        kp["kaardi_piirkond"] = kp["kaardi_piirkond"].apply(normalize_place)
        for col in ["latitude", "longitude"]:
            ensure_col(kp, col)
            kp[col] = pd.to_numeric(kp[col], errors="coerce")

    return fotod, marksoned, isikud, kp, os.path.basename(xlsx_path), ml_clip, ml_metrics


@st.cache_data
def load_geojson(name):
    path = os.path.join(BASE_DIR, name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        st.warning(f"GeoJSON '{name}' laadimine ebaõnnestus: {e}")
        return None


@st.cache_data
def get_centroids(_gj):
    result = {}
    if not _gj:
        return result
    for feat in _gj.get("features", []):
        name = feat.get("properties", {}).get("KIHELKOND", "")
        geom = feat.get("geometry", {})
        coords = []
        if geom.get("type") == "Polygon":
            coords = geom.get("coordinates", [[]])[0]
        elif geom.get("type") == "MultiPolygon":
            for poly in geom.get("coordinates", []):
                if poly and poly[0]:
                    coords.extend(poly[0])
        if coords and name:
            lons = [c[0] for c in coords if len(c) >= 2]
            lats = [c[1] for c in coords if len(c) >= 2]
            if lons and lats:
                result[name] = (sum(lats)/len(lats), sum(lons)/len(lons))
    return result


def get_filtered(fotod, marksoned, isikud, aasta, zanr, ms, ms_logic, foto, isik, mk=None):
    df = fotod.copy()
    if "Aasta" in df.columns and df["Aasta"].notna().any():
        df = pd.concat([df[df["Aasta"].notna() & df["Aasta"].between(aasta[0], aasta[1])],
                        df[df["Aasta"].isna()]], ignore_index=True)
    if zanr and "Žanr" in df.columns:
        df = df[df["Žanr"].isin(zanr)]
    if foto and "Fotograaf" in df.columns:
        df = df[df["Fotograaf"].isin(foto)]
    if ms and not marksoned.empty and "Märksõna" in marksoned.columns:
        if ms_logic == "JA – kõik korraga":
            pids = None
            for m in ms:
                mp = set(marksoned[marksoned["Märksõna"] == m]["PID"].dropna())
                pids = mp if pids is None else pids & mp
            pids = pids or set()
        else:
            pids = set(marksoned[marksoned["Märksõna"].isin(ms)]["PID"].dropna())
        df = df[df["PID"].isin(pids)]
    if mk:
        df = filter_cats(df, "Märksõna kategooria", mk)
    if isik and not isikud.empty and "Isik" in isikud.columns:
        pids = set(isikud[isikud["Isik"].isin(isik)]["PID"].dropna())
        df = df[df["PID"].isin(pids)]
    return df


def get_opts(fotod, marksoned, isikud, aasta, zanr, ms, ms_logic, foto, isik, mk=None):
    def fdf(**kw):
        return get_filtered(fotod, marksoned, isikud, aasta,
                            kw.get("zanr", zanr), kw.get("ms", ms), ms_logic,
                            kw.get("foto", foto), kw.get("isik", isik), kw.get("mk", mk))

    zanr_o = sorted(clean_series(fdf(zanr=[])["Žanr"]).unique()) if "Žanr" in fotod.columns else []

    base_ms = fdf(ms=[])
    pids_ms = set(base_ms["PID"].dropna()) if "PID" in base_ms.columns else set()
    if not marksoned.empty and "Märksõna" in marksoned.columns:
        src = marksoned[marksoned["PID"].isin(pids_ms)].copy()
        if mk and "Märksõna kategooria" in src.columns:
            src = src[src["Märksõna kategooria"].isin(mk)]
        ms_o = clean_series(src["Märksõna"]).value_counts().index.tolist()
    else:
        ms_o = []

    base_mk = fdf(mk=[])
    pids_mk = set(base_mk["PID"].dropna()) if "PID" in base_mk.columns else set()
    mk_o = (sorted(clean_series(marksoned[marksoned["PID"].isin(pids_mk)]["Märksõna kategooria"]).unique())
            if not marksoned.empty and "Märksõna kategooria" in marksoned.columns else [])

    foto_o = sorted(clean_series(fdf(foto=[])["Fotograaf"]).unique()) if "Fotograaf" in fotod.columns else []

    base_i = fdf(isik=[])
    pids_i = set(base_i["PID"].dropna()) if "PID" in base_i.columns else set()
    isik_o = (clean_series(isikud[isikud["PID"].isin(pids_i)]["Isik"]).value_counts().index.tolist()
              if not isikud.empty and {"PID","Isik"}.issubset(isikud.columns) else [])

    return zanr_o, ms_o, mk_o, foto_o, isik_o


def sanitize(key, opts, max_n=3):
    cur = st.session_state.get(key, []) or []
    st.session_state[key] = [x for x in cur if x in opts][:max_n]


# ─────────────────────────────────────────────────────────────
# Rakendus
# ─────────────────────────────────────────────────────────────

fotod, marksoned, isikud, kp, aktiivne_fail, ml_clip_all, ml_metrics = load_data()

# ── Sidebar ──────────────────────────────────────────────────
st.sidebar.title("🗂️ Filtrid")
if st.sidebar.button("🔄 Uuenda andmed"):
    st.cache_data.clear(); st.rerun()
st.sidebar.info("Praegu on aktiivne ainult ajalooline kihelkonnapõhine kaart.")
if st.sidebar.button("🧹 Tühjenda kõik filtrid"):
    for k in ["v_zanr","v_ms","v_mk","v_foto","v_isik"]:
        st.session_state[k] = []
    st.session_state["ms_logic"] = "VÕI – vähemalt üks"
    st.rerun()

aastad = fotod["Aasta"].dropna().astype(int)
aasta = (st.sidebar.slider("Aasta vahemik", int(aastad.min()), int(aastad.max()),
                            (int(aastad.min()), int(aastad.max())))
         if fotod["Aasta"].notna().any() else (0, 9999))

for k in ["v_zanr","v_ms","v_mk","v_foto","v_isik"]:
    if k not in st.session_state:
        st.session_state[k] = []
if "ms_logic" not in st.session_state:
    st.session_state["ms_logic"] = "VÕI – vähemalt üks"

def refresh():
    return get_opts(fotod, marksoned, isikud, aasta,
                    st.session_state["v_zanr"], st.session_state["v_ms"],
                    st.session_state["ms_logic"], st.session_state["v_foto"],
                    st.session_state["v_isik"], st.session_state["v_mk"])

z_o, ms_o, mk_o, f_o, i_o = refresh()
for k, o in [("v_zanr",z_o),("v_ms",ms_o),("v_mk",mk_o),("v_foto",f_o),("v_isik",i_o)]:
    sanitize(k, o)

st.sidebar.multiselect("Žanr", z_o, key="v_zanr", max_selections=3, placeholder="Vali kuni 3")
z_o, ms_o, mk_o, f_o, i_o = refresh(); sanitize("v_ms", ms_o)
st.sidebar.multiselect("Märksõna", ms_o, key="v_ms", max_selections=3, placeholder="Vali kuni 3")
if st.session_state["v_mk"] and ms_o:
    st.sidebar.caption("Märksõnade valik on kitsendatud valitud kategooria järgi.")
if len(st.session_state["v_ms"]) > 1:
    st.sidebar.radio("Märksõnade loogika", ["VÕI – vähemalt üks", "JA – kõik korraga"], key="ms_logic")
z_o, ms_o, mk_o, f_o, i_o = refresh(); sanitize("v_mk", mk_o)
st.sidebar.multiselect("Märksõna kategooria", mk_o, key="v_mk", max_selections=3, placeholder="Vali kuni 3 kategooriat")
z_o, ms_o, mk_o, f_o, i_o = refresh(); sanitize("v_foto", f_o)
st.sidebar.multiselect("Fotograaf", f_o, key="v_foto", max_selections=3, placeholder="Vali kuni 3")
z_o, ms_o, mk_o, f_o, i_o = refresh(); sanitize("v_isik", i_o)
st.sidebar.multiselect("Isik pildil", i_o, key="v_isik", max_selections=3, placeholder="Vali kuni 3")

df = get_filtered(fotod, marksoned, isikud, aasta,
                  st.session_state["v_zanr"], st.session_state["v_ms"],
                  st.session_state["ms_logic"], st.session_state["v_foto"],
                  st.session_state["v_isik"], st.session_state["v_mk"])

# ── KPI ──────────────────────────────────────────────────────
st.title("📷 ERA Fotode Andmebaas")
st.caption(f"Kasutusel fail: {aktiivne_fail}")
st.markdown(f"Kuvatud **{len(df):,}** fotot kokku **{len(fotod):,}**-st")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Fotosid kokku", f"{len(df):,}")
c2.metric("Koordinaatidega", f"{df['koordinaadid_leitud'].astype(str).eq('jah').sum():,}" if "koordinaadid_leitud" in df.columns else "0")
c3.metric("Erinevaid piirkondi", f"{clean_series(df['kaardi_piirkond']).nunique()}" if "kaardi_piirkond" in df.columns else "0")
c4.metric("Ajavahemik", f"{int(df['Aasta'].min())}–{int(df['Aasta'].max())}" if "Aasta" in df.columns and df["Aasta"].notna().any() else "?")
st.divider()

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["🗺️ Kaart", "📊 Statistika", "🏷️ Märksõnad", "👤 Isikud", "🤖 ML märksõnad", "📋 Andmetabel"])


# ══════════════════ TAB 1 – KAART ════════════════════════════════════════════
with tab1:
    st.markdown("Kaart visualiseerib fotokogu ruumilisi mustreid ajalooliste kihelkondade lõikes.")

    geojson   = load_geojson("kih1922_region.json")
    centroids = get_centroids(geojson) if geojson else {}
    geo_names = set(centroids.keys())

    for sk, sv in [("kaart_vaade","overview"), ("valitud_kihelkond", None)]:
        if sk not in st.session_state:
            st.session_state[sk] = sv

    # ── Ülevaatekaart ────────────────────────────────────────
    if st.session_state["kaart_vaade"] == "overview":
        if not geojson or "kaardi_piirkond" not in df.columns:
            st.warning("GeoJSON fail või kaardi_piirkond veerg puudub.")
        else:
            src = df[df["kaardi_piirkond"].notna() &
                     ~df["kaardi_piirkond"].astype(str).str.lower().isin(
                         ["teadmata","välismaa","välismaa,","nan","none","null","<na>"])].copy()
            src["kaardi_piirkond"] = src["kaardi_piirkond"].astype(str).str.strip()
            counts     = src.groupby("kaardi_piirkond").size().reset_index(name="Fotode arv")
            geo_c      = counts[counts["kaardi_piirkond"].isin(geo_names)].copy()
            missing_c  = counts[~counts["kaardi_piirkond"].isin(geo_names)].copy()

            missing_pts = pd.DataFrame()
            if not missing_c.empty:
                tmp = missing_c.copy()
                if not kp.empty and "kaardi_piirkond" in kp.columns:
                    kp_c = kp.copy()
                    kp_c["kaardi_piirkond"] = kp_c["kaardi_piirkond"].astype(str).str.strip()
                    tmp = tmp.merge(kp_c[["kaardi_piirkond","latitude","longitude"]], on="kaardi_piirkond", how="left")
                else:
                    tmp["latitude"] = tmp["longitude"] = pd.NA
                med = (src[src["latitude"].notna() & src["longitude"].notna()]
                       .groupby("kaardi_piirkond", as_index=False)
                       .agg(ml=("latitude","median"), mlo=("longitude","median")))
                tmp = tmp.merge(med, on="kaardi_piirkond", how="left")
                tmp["latitude"]  = tmp["latitude"].fillna(tmp["ml"])
                tmp["longitude"] = tmp["longitude"].fillna(tmp["mlo"])
                missing_pts = tmp[tmp["latitude"].notna() & tmp["longitude"].notna()].copy()

            if counts.empty:
                st.info("Praeguse filtriga piirkondi ei leitud.")
            else:
                fig = px.choropleth_mapbox(
                    geo_c, geojson=geojson, locations="kaardi_piirkond",
                    featureidkey="properties.KIHELKOND", color="Fotode arv",
                    color_continuous_scale="YlOrRd", hover_name="kaardi_piirkond",
                    hover_data={"Fotode arv": True}, custom_data=["kaardi_piirkond"],
                    mapbox_style="open-street-map", zoom=6.2,
                    center={"lat": 58.7, "lon": 25.0}, opacity=0.65)
                fig = add_borders(fig, geojson, color="rgba(60,60,60,0.5)", width=0.8)
                if not missing_pts.empty:
                    fig.add_trace(go.Scattermapbox(
                        lat=missing_pts["latitude"], lon=missing_pts["longitude"],
                        mode="markers+text", text=missing_pts["kaardi_piirkond"],
                        textposition="top center", customdata=missing_pts[["kaardi_piirkond"]],
                        marker=dict(size=missing_pts["Fotode arv"].clip(13, 36), color="#e63946", opacity=0.9),
                        hovertext=missing_pts["kaardi_piirkond"].astype(str) + "<br>Fotode arv: " + missing_pts["Fotode arv"].astype(str),
                        hoverinfo="text", name="Eraldi kuvatud piirkonnad", showlegend=True))
                fig.update_layout(height=680, margin={"r":0,"t":10,"l":0,"b":0},
                                  clickmode="event+select",
                                  coloraxis_colorbar=dict(title="Fotode arv"),
                                  legend=dict(orientation="h", yanchor="bottom", y=0.01, xanchor="left", x=0.01))

                event = st.plotly_chart(fig, use_container_width=True, key="main_kaart",
                                        on_select="rerun", selection_mode="points")
                try:
                    points = event.get("selection", {}).get("points", [])
                    if points:
                        p = points[0]
                        sel = (p.get("customdata") or [None])[0] or p.get("location")
                        if sel:
                            st.session_state["valitud_kihelkond"] = str(sel)
                            st.session_state["kaart_vaade"] = "detail"
                            st.rerun()
                except Exception:
                    pass

                st.caption(f"Kaardil on {len(geo_c)} kihelkonda ja {len(missing_pts)} eraldi punktina kuvatud piirkonda.")

    # ── Detailvaade ──────────────────────────────────────────
    else:
        val = st.session_state["valitud_kihelkond"]
        if not val:
            st.session_state["kaart_vaade"] = "overview"; st.rerun()

        if st.button("← Tagasi üldkaardile"):
            st.session_state["kaart_vaade"] = "overview"
            st.session_state["valitud_kihelkond"] = None
            st.rerun()

        st.subheader(f"📍 {val}")
        det = df[df["kaardi_piirkond"].astype(str).str.strip() == val].copy()
        ok  = det["latitude"].notna() & det["longitude"].notna()

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Fotosid", len(det))
        k2.metric("Koordinaatidega", int(ok.sum()))
        k3.metric("Ajavahemik",
                  f"{int(det['Aasta'].min())}–{int(det['Aasta'].max())}"
                  if "Aasta" in det.columns and det["Aasta"].notna().any() else "?")
        k4.metric("Fotograafe",
                  clean_series(det["Fotograaf"]).nunique() if "Fotograaf" in det.columns else "?")

        pts = det[ok].copy().rename(columns={"latitude": "_lat", "longitude": "_lon"})
        if not pts.empty:
            center = centroids.get(val, (pts["_lat"].median(), pts["_lon"].median()))

            # Hover: null-väljad jäetakse välja — ainult olemasolevad väärtused kuvatakse
            hover_cols = [c for c in ["Aasta", "Fotograaf", "Žanr", "Koht täpsemalt", "lõplik_täpsus"] if c in pts.columns]
            pts["_hover"] = pts.apply(lambda r: build_hover(r, hover_cols), axis=1)
            title_col = "Sisu kirjeldus" if "Sisu kirjeldus" in pts.columns else None

            fig_d = go.Figure(go.Scattermapbox(
                lat=pts["_lat"], lon=pts["_lon"], mode="markers",
                marker=dict(size=10, opacity=0.85, color="#e63946"),
                customdata=pts[[title_col]].fillna("") if title_col else None,
                text=pts["_hover"],
                hovertemplate=("<b>%{customdata[0]}</b><br>%{text}<extra></extra>"
                               if title_col else "%{text}<extra></extra>")))

            kihel_feat = [f for f in geojson["features"] if f.get("properties",{}).get("KIHELKOND") == val]
            if kihel_feat:
                for ring in poly_rings(kihel_feat[0].get("geometry",{})):
                    lons = [c[0] for c in ring if len(c) >= 2]
                    lats = [c[1] for c in ring if len(c) >= 2]
                    fig_d.add_trace(go.Scattermapbox(lon=lons, lat=lats, mode="lines",
                                                     line=dict(color="rgba(30,30,30,0.9)", width=2),
                                                     hoverinfo="skip", showlegend=False))

            fig_d.update_layout(
                mapbox=dict(style="open-street-map", zoom=10, center={"lat": center[0], "lon": center[1]}),
                height=480, margin={"r":0,"t":10,"l":0,"b":0}, showlegend=False)
            st.plotly_chart(fig_d, use_container_width=True)
            st.caption(f"Koordinaatidega fotosid: {len(pts)} / {len(det)}")
        else:
            st.info("Sellel piirkonnal koordinaatidega fotosid ei ole.")

        col1, col2 = st.columns(2)
        with col1:
            if "Fotograaf" in det.columns:
                ft = clean_series(det["Fotograaf"]).value_counts().head(8).reset_index()
                ft.columns = ["Fotograaf", "Arv"]
                if not ft.empty:
                    st.markdown("**Fotograafid**")
                    st.dataframe(ft, hide_index=True, use_container_width=True)
        with col2:
            if not marksoned.empty and "Märksõna" in marksoned.columns:
                ms_d = clean_series(marksoned[marksoned["PID"].isin(det["PID"])]["Märksõna"]).value_counts().head(8).reset_index()
                ms_d.columns = ["Märksõna", "Arv"]
                if not ms_d.empty:
                    st.markdown("**Top märksõnad**")
                    st.dataframe(ms_d, hide_index=True, use_container_width=True)

        with st.expander("Vaata kõiki fotosid sellest piirkonnast"):
            d_cols = [c for c in ["PID","Aasta","Fotograaf","Žanr","Sisu kirjeldus","Koht täpsemalt","failinimi"] if c in det.columns]
            st.dataframe(clean_df(det[d_cols]).head(500), use_container_width=True, hide_index=True)
            if len(det) > 500:
                st.caption(f"Näidatakse 500 / {len(det)} reast.")


# ══════════════════ TAB 2 – STATISTIKA ═══════════════════════════════════════
with tab2:
    c_l, c_r = st.columns(2)
    with c_l:
        if df["Aasta"].notna().any():
            df2 = df[df["Aasta"].notna()].copy()
            df2["Aastakümme"] = (df2["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
            ak = df2["Aastakümme"].value_counts().sort_index()
            fig = px.bar(x=ak.index, y=ak.values, labels={"x":"Aastakümme","y":"Fotode arv"},
                         title="Fotod aastakümne kaupa", color=ak.values, color_continuous_scale="Blues")
            fig.update_layout(coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
        if "kaardi_piirkond" in df.columns:
            top = (clean_series(df["kaardi_piirkond"])
                   .loc[lambda x: ~x.str.lower().isin(["teadmata","välismaa","välismaa,"])]
                   .value_counts().head(15))
            if len(top):
                fig = px.bar(x=top.values, y=top.index, orientation="h",
                             labels={"x":"Fotode arv","y":"Piirkond"}, title="Top 15 piirkonda",
                             color=top.values, color_continuous_scale="Greens")
                fig.update_layout(yaxis={"autorange":"reversed"}, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)
    with c_r:
        if "Žanr" in df.columns:
            zc = clean_series(df["Žanr"]).value_counts().head(15)
            if len(zc):
                fig = px.pie(values=zc.values, names=zc.index, title="Žanrite jaotus (top 15)")
                fig.update_traces(textposition="inside", textinfo="percent+label")
                st.plotly_chart(fig, use_container_width=True)
        if "Fotograaf" in df.columns:
            ft = clean_series(df["Fotograaf"]).value_counts().head(12)
            if len(ft):
                fig = px.bar(x=ft.values, y=ft.index, orientation="h",
                             labels={"x":"Fotode arv","y":"Fotograaf"}, title="Top 12 fotograafi",
                             color=ft.values, color_continuous_scale="Oranges")
                fig.update_layout(yaxis={"autorange":"reversed"}, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)
    if "Projekt" in df.columns:
        pr = clean_series(df["Projekt"]).value_counts().head(10)
        if len(pr):
            st.subheader("Projektid")
            fig = px.bar(x=pr.values, y=pr.index, orientation="h",
                         labels={"x":"Fotode arv","y":"Projekt"}, title="Top 10 projekti",
                         color=pr.values, color_continuous_scale="Purples")
            fig.update_layout(yaxis={"autorange":"reversed"}, coloraxis_showscale=False, height=350)
            st.plotly_chart(fig, use_container_width=True)


# ══════════════════ TAB 3 – MÄRKSÕNAD ════════════════════════════════════════
with tab3:
    st.subheader("Märksõnade analüüs")
    pids = set(df["PID"].dropna()) if "PID" in df.columns else set()
    mf = marksoned[marksoned["PID"].isin(pids)] if not marksoned.empty and "PID" in marksoned.columns else pd.DataFrame()

    c_l, c_r = st.columns(2)
    with c_l:
        top_n = st.slider("Näita top N märksõna", 10, 50, 20)
        if not mf.empty and "Märksõna" in mf.columns:
            mc = clean_series(mf["Märksõna"]).value_counts().head(top_n)
            if len(mc):
                fig = px.bar(x=mc.values, y=mc.index, orientation="h",
                             labels={"x":"Esinemiste arv","y":"Märksõna"}, title=f"Top {top_n} märksõna",
                             color=mc.values, color_continuous_scale="Teal")
                fig.update_layout(yaxis={"autorange":"reversed"}, coloraxis_showscale=False, height=500)
                st.plotly_chart(fig, use_container_width=True)
    with c_r:
        st.markdown("#### Märksõna ajaline trend")
        ms_q = st.text_input("Sisesta märksõna", value="portree")
        if ms_q and not mf.empty and "Märksõna" in mf.columns:
            ms_tr = mf[mf["Märksõna"].fillna("").astype(str).str.lower() == ms_q.lower()][["PID"]].copy()
            ms_tr = ms_tr.merge(df[["PID","Aasta"]].drop_duplicates("PID"), on="PID", how="left")
            ms_tr = ms_tr[ms_tr["Aasta"].notna()]
            if len(ms_tr):
                ms_tr["Aastakümme"] = (ms_tr["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
                tc = ms_tr["Aastakümme"].value_counts().sort_index()
                st.plotly_chart(px.line(x=tc.index, y=tc.values, markers=True,
                                        labels={"x":"Aastakümme","y":"Esinemiste arv"}, title=f"'{ms_q}' ajas"),
                                use_container_width=True)
            else:
                st.info("Selle märksõnaga aastaga fotosid ei leitud.")


# ══════════════════ TAB 4 – ISIKUD ═══════════════════════════════════════════
with tab4:
    st.subheader("Isikud fotodel")
    pids = set(df["PID"].dropna()) if "PID" in df.columns else set()
    isf  = isikud[isikud["PID"].isin(pids)] if not isikud.empty and "PID" in isikud.columns else pd.DataFrame()

    c_l, c_r = st.columns(2)
    with c_l:
        top_n = st.slider("Näita top N isikut", 10, 50, 20, key="isik_n")
        if not isf.empty and "Isik" in isf.columns:
            it = clean_series(isf["Isik"]).value_counts().head(top_n)
            if len(it):
                fig = px.bar(x=it.values, y=it.index, orientation="h",
                             labels={"x":"Fotode arv","y":"Isik"}, title=f"Top {top_n} isikut",
                             color=it.values, color_continuous_scale="Magenta")
                fig.update_layout(yaxis={"autorange":"reversed"}, coloraxis_showscale=False, height=500)
                st.plotly_chart(fig, use_container_width=True)
    with c_r:
        st.markdown("#### Isiku otsing")
        isik_q = st.text_input("Otsi isiku nime järgi")
        if isik_q and not isf.empty and "Isik" in isf.columns:
            matches = isf[safe_contains(isf["Isik"], isik_q)]
            dfi = df[df["PID"].isin(matches["PID"].unique())]
            st.markdown(f"Leitud **{len(dfi)}** fotot isikuga '{isik_q}'")
            if len(dfi):
                cols = [c for c in ["PID","Aasta","Kihelkond","kaardi_piirkond","Sisu kirjeldus","failinimi"] if c in dfi.columns]
                st.dataframe(clean_df(dfi[cols]).head(50), use_container_width=True, hide_index=True)
        else:
            if "Isikute arv" in df.columns:
                ia = clean_series(df["Isikute arv"]).value_counts().sort_index().head(10)
                if len(ia):
                    st.markdown("#### Isikute arv fotol")
                    fig = px.bar(x=ia.index.astype(str), y=ia.values,
                                 labels={"x":"Isikute arv fotol","y":"Fotode arv"}, title="Kui palju isikuid on fotodel?",
                                 color=ia.values, color_continuous_scale="Teal")
                    fig.update_layout(coloraxis_showscale=False)
                    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Isikute ja fotograafide võrgustikud")
    st.caption("Näitab andmestikus nähtavaid koosesinemisi, mitte ei tõesta välitöödel koos käimist.")
    net_type = st.radio("Vali võrgustiku tüüp", ["Isik–isik: kes on koos pildil", "Fotograaf–isik: kes keda pildistas"])
    min_w = st.slider("Minimaalne seoste arv", 1, 10, 2)
    max_e = st.slider("Maksimaalne kuvatavate seoste arv", 20, 250, 100, step=10)

    if net_type == "Isik–isik: kes on koos pildil":
        rows = []
        if not isf.empty and {"PID","Isik"}.issubset(isf.columns):
            for pid, grp in isf.groupby("PID"):
                persons = clean_series(grp["Isik"]).unique().tolist()
                if len(persons) >= 2:
                    for a, b in combinations(sorted(persons), 2):
                        rows.append({"isik_1": a, "isik_2": b})
        edges = pd.DataFrame(rows)
        if not edges.empty:
            ec = edges.groupby(["isik_1","isik_2"]).size().reset_index(name="koos_fotodel")
            ec = ec[ec["koos_fotodel"] >= min_w]
            plot_network(ec, "isik_1", "isik_2", "koos_fotodel", "Isikute koosesinemise võrgustik", max_e)
            st.markdown("#### Tugevaimad seosed")
            st.dataframe(ec.sort_values("koos_fotodel", ascending=False).head(100), use_container_width=True, hide_index=True)
        else:
            st.info("Ei leitud fotosid, kus oleks vähemalt kaks tuvastatud isikut.")
    else:
        if {"Fotograaf","Isik"}.issubset(isf.columns):
            edges = isf[isf["Fotograaf"].notna() & isf["Isik"].notna()].copy()
            edges["Fotograaf"] = edges["Fotograaf"].astype(str).str.strip()
            edges["Isik"]      = edges["Isik"].astype(str).str.strip()
            edges = edges[(edges["Fotograaf"] != "") & (edges["Isik"] != "") & (edges["Fotograaf"] != edges["Isik"])]
            ec = edges.groupby(["Fotograaf","Isik"]).size().reset_index(name="fotosid")
            ec = ec[ec["fotosid"] >= min_w]
            plot_network(ec, "Fotograaf", "Isik", "fotosid", "Fotograaf–isik võrgustik", max_e)
            st.dataframe(ec.sort_values("fotosid", ascending=False).head(100), use_container_width=True, hide_index=True)
        else:
            st.info("Isikute tabelis puudub 'Fotograaf' või 'Isik' veerg.")


# ══════════════════ TAB 5 – ML MÄRKSÕNAD ═════════════════════════════════════
with tab5:
    st.subheader("🤖 ML märksõnad")
    st.markdown("Kaks vaadet: põhifotodega seotud CLIP tulemused (PID olemas) ja kõik CLIP sh `image_only`.")
    img_path = os.path.join(BASE_DIR, "clip_yhe_pildi_selgitus.png")
    if os.path.exists(img_path):
        st.image(img_path, caption="Näide: CLIP pildi ja tekstikategooriate sobivuse hindamine", use_container_width=True)
    st.divider()

    ml_df  = df.copy()
    clip_a = prep_clip(ml_clip_all)
    has_any = (not clip_a.empty and "pred_top1" in clip_a.columns and clip_a["pred_top1"].notna().any()) or \
              ("pred_top1" in ml_df.columns and ml_df["pred_top1"].notna().any()) or \
              ("Märksõna kategooria" in ml_df.columns and ml_df["Märksõna kategooria"].notna().any())

    if not has_any:
        st.warning("ML infot ei leitud. Kontrolli failide olemasolu.")
    else:
        pid_n    = clip_a["PID"].fillna("").astype(str).str.strip().ne("").sum() if not clip_a.empty and "PID" in clip_a.columns else 0
        img_only = clip_a[clip_a["PID"].fillna("").astype(str).str.strip().eq("")] if not clip_a.empty and "PID" in clip_a.columns else pd.DataFrame()
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Fotosid filtris", f"{len(ml_df):,}")
        c2.metric("CLIP kokku", f"{len(clip_a):,}" if not clip_a.empty else "0")
        c3.metric("CLIP + PID", f"{pid_n:,}")
        c4.metric("Image-only", f"{len(img_only):,}")
        st.caption("`image_only`: pilt leiti kaustast, aga PID-i ei saanud külge panna.")

        view = st.radio("Vali ML-vaade", ["Põhifotodega seotud CLIP", "Kõik CLIP, sh image-only"], horizontal=True)
        if view.startswith("Kõik"):
            active, man_col = clip_a.copy(), ("true_clusters" if "true_clusters" in clip_a.columns else None)
        else:
            active, man_col = ml_df.copy(), "Märksõna kategooria"

        if "pred_top1" in active.columns and active["pred_top1"].notna().any():
            cc = clean_series(active["pred_top1"]).value_counts().head(20)
            st.markdown("### CLIP top1 kategooriad")
            fig = px.bar(x=cc.values, y=cc.index, orientation="h",
                         labels={"x":"Piltide arv","y":"CLIP top1"}, title="CLIP top1 kategooriad",
                         color=cc.values, color_continuous_scale="Oranges")
            fig.update_layout(yaxis={"autorange":"reversed"}, coloraxis_showscale=False, height=500)
            st.plotly_chart(fig, use_container_width=True)

        c_l, c_r = st.columns(2)
        with c_l:
            if man_col and man_col in active.columns and active[man_col].notna().any():
                mc = split_cats(active[man_col]).value_counts().head(20)
                if len(mc):
                    st.markdown("### Olemasolevad kategooriad")
                    fig = px.bar(x=mc.values, y=mc.index, orientation="h",
                                 labels={"x":"Fotode arv","y":"Kategooria"}, color=mc.values, color_continuous_scale="Blues")
                    fig.update_layout(yaxis={"autorange":"reversed"}, coloraxis_showscale=False, height=500)
                    st.plotly_chart(fig, use_container_width=True)
        with c_r:
            if "pred_top1" in active.columns and active["pred_top1"].notna().any():
                cc2 = clean_series(active["pred_top1"]).value_counts().head(20)
                st.markdown("### CLIP top1")
                fig = px.bar(x=cc2.values, y=cc2.index, orientation="h",
                             labels={"x":"Fotode arv","y":"CLIP top1"}, color=cc2.values, color_continuous_scale="Oranges")
                fig.update_layout(yaxis={"autorange":"reversed"}, coloraxis_showscale=False, height=500)
                st.plotly_chart(fig, use_container_width=True)

        if man_col and man_col in active.columns and "pred_top1" in active.columns:
            ev = active[active["pred_top1"].notna() & active[man_col].notna()].copy()
            if not ev.empty:
                st.markdown("### Kattuvus olemasoleva kategooriaga")
                for cn, pc in [("top1",["pred_top1"]),
                                ("top3",["pred_top1","pred_top2","pred_top3"]),
                                ("top5",["pred_top1","pred_top2","pred_top3","pred_top4","pred_top5"])]:
                    ev[f"{cn}_kattub"] = ev.apply(lambda r: cat_match(r, man_col, pc), axis=1)
                m1,m2,m3 = st.columns(3)
                m1.metric("Top1", f"{ev['top1_kattub'].mean()*100:.1f}%")
                m2.metric("Top3", f"{ev['top3_kattub'].mean()*100:.1f}%")
                m3.metric("Top5", f"{ev['top5_kattub'].mean()*100:.1f}%")

                heat = ev.copy()
                heat["ml"] = heat[man_col].astype(str).str.replace(";",",",regex=False).str.replace("|",",",regex=False).str.split(",")
                pairs = heat.explode("ml")
                pairs["ml"] = pairs["ml"].astype(str).str.strip()
                pairs = pairs[pairs["ml"].notna() & ~pairs["ml"].str.lower().isin(NULL_VALS)]
                mat = pairs.groupby(["ml","pred_top1"]).size().reset_index(name="arv")
                if not mat.empty:
                    fig = px.density_heatmap(mat, x="pred_top1", y="ml", z="arv",
                                             color_continuous_scale="Blues",
                                             labels={"pred_top1":"CLIP top1","ml":"Olemasolev","arv":"Arv"},
                                             title="Kategooriate kattuvus heatmap")
                    fig.update_layout(height=600)
                    st.plotly_chart(fig, use_container_width=True)

                pie_c = ev["top3_kattub"].map({True:"Top3 seas kattub", False:"Ei kattu"}).value_counts()
                st.plotly_chart(px.pie(values=pie_c.values, names=pie_c.index, title="CLIP top3 vs olemasolev"), use_container_width=True)

        if not ml_metrics.empty:
            mtr = ml_metrics.copy()
            mtr.columns = mtr.columns.astype(str).str.strip()
            mc2 = next((c for c in ["f1_top3","top3_f1","hit_any_top3","top3_hit_rate"] if c in mtr.columns), None)
            cc3 = next((c for c in ["cluster","kategooria","Märksõna kategooria"] if c in mtr.columns), None)
            if mc2 and cc3:
                mtr[mc2] = pd.to_numeric(mtr[mc2], errors="coerce")
                st.markdown("### CLIP kvaliteet kategooriate kaupa")
                fig = px.bar(mtr.dropna(subset=[mc2]).sort_values(mc2), x=mc2, y=cc3, orientation="h",
                             title="Milliste kategooriate puhul CLIP paremini töötab?")
                fig.update_layout(height=550)
                st.plotly_chart(fig, use_container_width=True)

        st.markdown("### Vaata üksikuid ML ridu")
        q = st.text_input("Otsi PID, failinime järgi", key="ml_q")
        ml_show = active.copy()
        if q:
            mask = pd.Series(False, index=ml_show.index)
            for col in ["PID","failinimi","Sisu kirjeldus"]:
                if col in ml_show.columns:
                    mask |= safe_contains(ml_show[col], q)
            ml_show = ml_show[mask]
        if man_col and man_col in ml_show.columns and "pred_top1" in ml_show.columns:
            if st.checkbox("Näita ainult ridu, kus CLIP top3 ei kata olemasolevat"):
                ml_show = ml_show[~ml_show.apply(lambda r: cat_match(r, man_col, ["pred_top1","pred_top2","pred_top3"]), axis=1)]
        ml_cols = [c for c in ["PID","failinimi","Sisu kirjeldus","Märksõna kategooria","true_clusters",
                                "pred_top1","pred_top2","pred_top3","pred_top1_score",
                                "confidence_margin_top1_top2","ML top3 koondskoor","ML otsuse tugevus",
                                "hit_top1","hit_any_top3"] if c in ml_show.columns]
        st.markdown(f"Näidatakse **{len(ml_show):,}** rida")
        st.dataframe(ml_show[ml_cols].head(1000), use_container_width=True, hide_index=True, height=420)
        csv_ml = ml_show[ml_cols].to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Lae ML tabel CSV-na", data=csv_ml, file_name="era_ml_vordlus.csv", mime="text/csv")


# ══════════════════ TAB 6 – ANDMETABEL ═══════════════════════════════════════
with tab6:
    st.subheader("Andmetabel")
    default_c = [c for c in ["PID","Aasta","Kihelkond","kaardi_piirkond","Fotograaf","Žanr",
                              "Märksõna kategooria","pred_top1","Sisu kirjeldus",
                              "ERA märksõnad (koondatud)","failinimi"] if c in df.columns]
    show_cols = st.multiselect("Vali kuvatavad veerud", list(df.columns), default=default_c)
    q = st.text_input("🔍 Otsi")
    dfs = df.copy()
    if q:
        mask = pd.Series(False, index=dfs.index)
        for col in ["Sisu kirjeldus","Kihelkond","kaardi_piirkond","Fotograaf","Märksõna kategooria","pred_top1"]:
            if col in dfs.columns:
                mask |= safe_contains(dfs[col], q)
        dfs = dfs[mask]
    st.markdown(f"Näidatakse **{len(dfs):,}** rida")
    if show_cols:
        st.dataframe(clean_df(dfs[show_cols]).head(500), use_container_width=True, height=420, hide_index=True)
        if len(dfs) > 500:
            st.caption("ℹ️ Tabelis esimesed 500 rida. Kitsenda filtritega.")
        csv = dfs[show_cols].to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Lae alla CSV", data=csv, file_name="era_fotod_filtreeritud.csv", mime="text/csv")
    else:
        st.info("Vali vähemalt üks veerg.")
