import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import json
import unicodedata

st.set_page_config(page_title="ERA Fotode Andmebaas", page_icon="📷", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Abifunktsioonid ──────────────────────────────────────────────────────────

def safe_sheet_parse(xl, sheet_name):
    if sheet_name in xl.sheet_names:
        return xl.parse(sheet_name)
    return pd.DataFrame()


def normalize_filename_for_match(name):
    """Failinimede võrdlus, mis talub täpitähtede eri Unicode-kujusid."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    return text.lower().replace(" ", "").replace("_", "").replace("-", "")


def find_existing_file(candidates, fallback_contains=None):
    for fname in candidates:
        path = os.path.join(BASE_DIR, fname)
        if os.path.exists(path):
            return path

    if fallback_contains:
        wanted = normalize_filename_for_match(fallback_contains)
        for fname in os.listdir(BASE_DIR):
            if wanted in normalize_filename_for_match(fname):
            return os.path.join(BASE_DIR, fname)

    return None


def read_first_existing_sheet(path, preferred_sheets, required_cols=None):
    if not path or not os.path.exists(path):
        return pd.DataFrame()

    xl = pd.ExcelFile(path)

    for sheet in preferred_sheets:
        if sheet in xl.sheet_names:
            df = xl.parse(sheet)
            if required_cols is None or any(c in df.columns for c in required_cols):
                return df

    if required_cols:
        for sheet in xl.sheet_names:
            df = xl.parse(sheet)
            if any(c in df.columns for c in required_cols):
                return df

    return pd.DataFrame()


def ensure_column(df, col, default=pd.NA):
    if col not in df.columns:
        df[col] = default
    return df


def safe_str_contains(series, text):
    return series.fillna("").astype(str).str.contains(text, case=False, na=False)


def clean_region_name(x):
    if pd.isna(x):
        return pd.NA
    x = str(x).strip()
    if not x:
        return pd.NA
    return x


def normalize_place_name(x):
    if pd.isna(x):
        return pd.NA

    x = str(x).strip()
    if not x:
        return pd.NA

    xl = x.lower()

    tallinn_variants = {
        "tallinn", "tallinna linn", "tallinn linn"
    }
    tartu_variants = {
        "tartu", "tartu linn", "tartu linn."
    }
    petseri_variants = {
        "petserimaa", "petseri"
    }
    setu_variants = {
        "setumaa", "setomaa", "setu ala"
    }

    if xl in tallinn_variants:
        return "Tallinn"
    if xl in tartu_variants:
        return "Tartu"
    if xl in petseri_variants:
        return "Petserimaa"
    if xl in setu_variants:
        return "Setumaa"

    return x


def extract_geojson_feature_names(geojson, prop_name="KIHELKOND"):
    names = set()
    if not geojson or "features" not in geojson:
        return names

    for feature in geojson["features"]:
        props = feature.get("properties", {})
        val = props.get(prop_name)
        if val is not None and str(val).strip():
            names.add(str(val).strip())

    return names


def extract_polygon_rings(geom):
    if not geom or "type" not in geom or "coordinates" not in geom:
        return []

    coords = geom["coordinates"]
    rings = []

    try:
        if geom["type"] == "Polygon":
            if coords and len(coords) > 0 and coords[0]:
                rings.append(coords[0])

        elif geom["type"] == "MultiPolygon":
            for poly in coords:
                if poly and len(poly) > 0 and poly[0]:
                    rings.append(poly[0])

    except Exception:
        return []

    return rings


def lisa_piirjooned(fig, geojson, color="black", width=1):
    if not geojson or "features" not in geojson:
        return fig

    for feature in geojson["features"]:
        geom = feature.get("geometry", {})
        coords_list = extract_polygon_rings(geom)

        for coords in coords_list:
            if not coords or len(coords) < 2:
                continue

            try:
                lons = [c[0] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                lats = [c[1] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                if len(lons) < 2 or len(lats) < 2:
                    continue

                fig.add_trace(go.Scattermapbox(
                    lon=lons,
                    lat=lats,
                    mode="lines",
                    line=dict(color=color, width=width),
                    hoverinfo="skip",
                    showlegend=False,
                ))
            except Exception:
                continue

    return fig


def lisa_puuduvad_keskpunktid(fig, df_missing):
    if df_missing is None or df_missing.empty:
        return fig

    sizes = df_missing["Fotode arv"].clip(lower=8, upper=40)

    hover_text = (
        df_missing["kaardi_piirkond"].astype(str)
        + "<br>Fotode arv: " + df_missing["Fotode arv"].astype(str)
    )

    fig.add_trace(go.Scattermapbox(
        lat=df_missing["latitude"],
        lon=df_missing["longitude"],
        mode="markers+text",
        text=df_missing["kaardi_piirkond"],
        textposition="top center",
        marker=dict(size=sizes, opacity=0.85),
        hovertext=hover_text,
        hoverinfo="text",
        name="Puuduvad piirkonnad",
        showlegend=False,
    ))

    return fig


def split_categories(series):
    """Jagab kategooriavälja päris üksikkategooriateks.
    Talub koma, semikoolonit ja püstkriipsu. Nii ei teki filtrisse 100 eri kombinatsiooni.
    """
    if series is None or len(series) == 0:
        return pd.Series(dtype="object")

    return (
        series
        .dropna()
        .astype(str)
        .str.replace(";", ",", regex=False)
        .str.replace("|", ",", regex=False)
        .str.split(",")
        .explode()
        .str.strip()
        .replace("", pd.NA)
        .dropna()
    )


def filter_by_comma_categories(df, col, selected):
    if not selected or col not in df.columns:
        return df

    selected_lower = {str(x).strip().lower() for x in selected if str(x).strip()}

    def has_any_category(value):
        text = str(value).replace(";", ",").replace("|", ",")
        cats = [c.strip().lower() for c in text.split(",") if c.strip()]
        return any(c in selected_lower for c in cats)

    return df[df[col].fillna("").apply(has_any_category)]


def keyword_category_map_from_ml(ml_marksonad):
    """Tagastab tabeli: Märksõna -> Märksõna kategooria.
    Vajalik selleks, et kasutaja saaks valida nt kategooria 'inimene'
    ja selle all näha ainult selle kategooria päris märksõnu.
    """
    if ml_marksonad is None or ml_marksonad.empty:
        return pd.DataFrame(columns=["Märksõna", "Märksõna kategooria"])

    df_map = ml_marksonad.copy()
    df_map.columns = df_map.columns.astype(str).str.strip()

    # Kõige tõenäolisem vorm: pikk mapping märksõna -> klaster/kategooria.
    keyword_col = None
    for c in ["Märksõna", "märksõna", "marksona", "keyword"]:
        if c in df_map.columns:
            keyword_col = c
            break

    category_col = None
    for c in ["Märksõna2", "märksõna2", "klaster", "klastrid", "Märksõna kategooria", "kategooria"]:
        if c in df_map.columns:
            category_col = c
            break

    if keyword_col and category_col:
        out = df_map[[keyword_col, category_col]].copy()
        out.columns = ["Märksõna", "Märksõna kategooria"]
        out = out.dropna()
        out["Märksõna"] = out["Märksõna"].astype(str).str.strip()
        out["Märksõna kategooria"] = out["Märksõna kategooria"].astype(str).str.strip()
        out = out[(out["Märksõna"] != "") & (out["Märksõna kategooria"] != "")]
        return out.drop_duplicates()

    return pd.DataFrame(columns=["Märksõna", "Märksõna kategooria"])


def category_match(row, manual_col="Märksõna kategooria", pred_cols=None):
    if pred_cols is None:
        pred_cols = ["pred_top1", "pred_top2", "pred_top3"]

    manual = [c.strip().lower() for c in str(row.get(manual_col, "")).split(",") if c.strip()]
    preds = [str(row.get(c, "")).strip().lower() for c in pred_cols if str(row.get(c, "")).strip() and str(row.get(c, "")).lower() != "nan"]

    if not manual or not preds:
        return False

    return any(p in manual for p in preds)


def add_ml_strength_columns(df):
    """Lisab ML-i tõlgendamiseks paremad koondväljad kui ainult pred_top1_score."""
    score_cols_top3 = [c for c in ["pred_top1_score", "pred_top2_score", "pred_top3_score"] if c in df.columns]
    score_cols_top5 = [c for c in ["pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score"] if c in df.columns]

    for col in score_cols_top5 + ["confidence_margin_top1_top2"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if score_cols_top3:
        df["ML top3 koondskoor"] = df[score_cols_top3].sum(axis=1, min_count=1)

    if score_cols_top5:
        df["ML top5 koondskoor"] = df[score_cols_top5].sum(axis=1, min_count=1)

    if "confidence_margin_top1_top2" not in df.columns and {"pred_top1_score", "pred_top2_score"}.issubset(df.columns):
        df["confidence_margin_top1_top2"] = df["pred_top1_score"] - df["pred_top2_score"]

    if "pred_top1_score" in df.columns and "confidence_margin_top1_top2" in df.columns:
        def strength(row):
            top1 = row.get("pred_top1_score")
            margin = row.get("confidence_margin_top1_top2")
            if pd.isna(top1) or pd.isna(margin):
                return pd.NA
            if top1 >= 0.565 and margin >= 0.020:
                return "tugev"
            if top1 >= 0.555 and margin >= 0.010:
                return "keskmine"
            return "nõrk / kontrolli üle"

        df["ML otsuse tugevus"] = df.apply(strength, axis=1)
        df["ML kindlus"] = df["ML otsuse tugevus"]

    return df


def get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik,
    valitud_zanr,
    valitud_marksona,
    marksona_loogika,
    valitud_fotograaf,
    valitud_isik,
    valitud_marksona_kategooria=None
):
    df = fotod.copy()

    if "Aasta" in df.columns and df["Aasta"].notna().any():
        df_a = df[df["Aasta"].notna()].copy()
        df_a = df_a[df_a["Aasta"].between(aasta_vahemik[0], aasta_vahemik[1])]
        df = pd.concat([df_a, df[df["Aasta"].isna()]], ignore_index=True)

    if valitud_zanr and "Žanr" in df.columns:
        df = df[df["Žanr"].isin(valitud_zanr)]

    if valitud_fotograaf and "Fotograaf" in df.columns:
        df = df[df["Fotograaf"].isin(valitud_fotograaf)]

    if valitud_marksona and not marksoned.empty and "Märksõna" in marksoned.columns:
        if marksona_loogika == "JA – kõik korraga":
            pids = None
            for ms in valitud_marksona:
                ms_pids = set(marksoned[marksoned["Märksõna"] == ms]["PID"].dropna().unique())
                pids = ms_pids if pids is None else pids & ms_pids
            pids = pids or set()
        else:
            pids = set(marksoned[marksoned["Märksõna"].isin(valitud_marksona)]["PID"].dropna().unique())

        df = df[df["PID"].isin(pids)]

    if valitud_marksona_kategooria:
        df = filter_by_comma_categories(df, "Märksõna kategooria", valitud_marksona_kategooria)

    if valitud_isik and not isikud.empty and "Isik" in isikud.columns:
        isik_pids = set(isikud[isikud["Isik"].isin(valitud_isik)]["PID"].dropna().unique())
        df = df[df["PID"].isin(isik_pids)]

    return df


def get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    valitud_zanr,
    valitud_marksona,
    marksona_loogika,
    valitud_fotograaf,
    valitud_isik,
    valitud_marksona_kategooria=None
):
    # ŽANR: kõik muud filtrid peal, žanr ise maas
    df_for_zanr = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        [],
        valitud_marksona, marksona_loogika,
        valitud_fotograaf, valitud_isik,
        valitud_marksona_kategooria
    )
    zanr_opts = sorted(
        df_for_zanr["Žanr"].dropna().astype(str).unique().tolist()
    ) if "Žanr" in df_for_zanr.columns else []

    # MÄRKSÕNA: kõik muud filtrid peal, märksõna ise maas
    df_for_ms = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        [],
        marksona_loogika,
        valitud_fotograaf, valitud_isik,
        valitud_marksona_kategooria
    )
    pids_ms = set(df_for_ms["PID"].dropna().unique()) if "PID" in df_for_ms.columns else set()

    if not marksoned.empty and "PID" in marksoned.columns and "Märksõna" in marksoned.columns:
        ms_source = marksoned[marksoned["PID"].isin(pids_ms)].copy()

        # Kui kategooria on valitud, näita märksõna filtris ainult selle kategooria märksõnu.
        if valitud_marksona_kategooria and "Märksõna kategooria" in ms_source.columns:
            ms_source = ms_source[ms_source["Märksõna kategooria"].isin(valitud_marksona_kategooria)]

        ms_opts = ms_source["Märksõna"].dropna().astype(str).value_counts().index.tolist()
    else:
        ms_opts = []

    # MÄRKSÕNA KATEGOORIA: kõik muud filtrid peal, kategooria ise maas
    df_for_mk = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        valitud_marksona, marksona_loogika,
        valitud_fotograaf, valitud_isik,
        []
    )
    if not marksoned.empty and "Märksõna kategooria" in marksoned.columns:
        pids_mk = set(df_for_mk["PID"].dropna().unique()) if "PID" in df_for_mk.columns else set()
        mk_source = marksoned[marksoned["PID"].isin(pids_mk)].copy()
        mk_opts = sorted(mk_source["Märksõna kategooria"].dropna().astype(str).str.strip().unique().tolist())
    else:
        mk_opts = sorted(split_categories(df_for_mk["Märksõna kategooria"]).unique().tolist()) if "Märksõna kategooria" in df_for_mk.columns else []

    # FOTOGRAAF: kõik muud filtrid peal, fotograaf ise maas
    df_for_ft = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        valitud_marksona, marksona_loogika,
        [],
        valitud_isik,
        valitud_marksona_kategooria
    )
    ft_opts = sorted(
        df_for_ft["Fotograaf"].dropna().astype(str).unique().tolist()
    ) if "Fotograaf" in df_for_ft.columns else []

    # ISIK: kõik muud filtrid peal, isik ise maas
    df_for_isik = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        valitud_marksona, marksona_loogika,
        valitud_fotograaf, [],
        valitud_marksona_kategooria
    )
    pids_isik = set(df_for_isik["PID"].dropna().unique()) if "PID" in df_for_isik.columns else set()
    isik_opts = (
        isikud[isikud["PID"].isin(pids_isik)]["Isik"]
        .dropna().astype(str).value_counts().index.tolist()
        if not isikud.empty and "PID" in isikud.columns and "Isik" in isikud.columns
        else []
    )

    return zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts


def sanitize_state_list(key, allowed_options, max_n=3):
    current = st.session_state.get(key, [])
    if current is None:
        current = []
    current = [x for x in current if x in allowed_options][:max_n]
    st.session_state[key] = current


def naita_fotopunkte(df_piirkond, pealkiri, load_geojson_func, lisa_asustus_piirid=False):
    required = ["latitude", "longitude"]
    for col in required:
        if col not in df_piirkond.columns:
            st.info("Koordinaadiveerud puuduvad.")
            return

    df_pts = df_piirkond[
        df_piirkond["latitude"].notna() &
        df_piirkond["longitude"].notna()
    ].copy()

    if df_pts.empty:
        st.info("Valitud piirkonnas koordinaatidega fotosid ei ole.")
        return

    hover_data = {
        "Aasta": "Aasta" in df_pts.columns,
        "Kihelkond": "Kihelkond" in df_pts.columns,
        "Fotograaf": "Fotograaf" in df_pts.columns,
        "latitude": False,
        "longitude": False,
    }

    color_col = "lõplik_täpsus" if "lõplik_täpsus" in df_pts.columns else None

    fig = px.scatter_mapbox(
        df_pts,
        lat="latitude",
        lon="longitude",
        hover_name="Sisu kirjeldus" if "Sisu kirjeldus" in df_pts.columns else None,
        hover_data=hover_data,
        color=color_col,
        mapbox_style="open-street-map",
        title=pealkiri,
        zoom=9,
        center={
            "lat": df_pts["latitude"].mean(),
            "lon": df_pts["longitude"].mean(),
        },
    )

    fig.update_traces(marker=dict(size=9, opacity=0.8))

    if lisa_asustus_piirid:
        geojson_ay = load_geojson_func("asustusyksus.geojson")
        if isinstance(geojson_ay, dict) and "features" in geojson_ay:
            lat_min = df_pts["latitude"].min() - 0.1
            lat_max = df_pts["latitude"].max() + 0.1
            lon_min = df_pts["longitude"].min() - 0.1
            lon_max = df_pts["longitude"].max() + 0.1

            for feature in geojson_ay["features"]:
                geom = feature.get("geometry", {})
                coords_list = extract_polygon_rings(geom)

                for coords in coords_list:
                    if not coords or len(coords) < 2:
                        continue

                    try:
                        lons = [c[0] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                        lats = [c[1] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                        if len(lons) < 2 or len(lats) < 2:
                            continue

                        if (min(lons) < lon_max and max(lons) > lon_min and
                                min(lats) < lat_max and max(lats) > lat_min):
                            fig.add_trace(go.Scattermapbox(
                                lon=lons,
                                lat=lats,
                                mode="lines",
                                line=dict(color="rgba(80,80,80,0.5)", width=0.8),
                                hoverinfo="skip",
                                showlegend=False,
                            ))
                    except Exception:
                        continue

    fig.update_layout(height=500, margin={"r": 0, "t": 40, "l": 0, "b": 0})
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Koordinaatidega fotosid: {len(df_pts)}")


# ── Andmete laadimine ────────────────────────────────────────────────────────

@st.cache_data
def load_data():
    xlsx_path = None
    for fname in ["ERA_fotod_250426.xlsx"]:
        path = os.path.join(BASE_DIR, fname)
        if os.path.exists(path):
            xlsx_path = path
            break

    if xlsx_path is None:
        raise FileNotFoundError("Ühtegi Exceli faili ei leitud kaustast.")

    xl = pd.ExcelFile(xlsx_path)

    fotod = safe_sheet_parse(xl, "fotod_koordinaatidega")
    master = safe_sheet_parse(xl, "fotod_master")
    marksoned = safe_sheet_parse(xl, "märksõnad_pikk")
    isikud = safe_sheet_parse(xl, "isikud_fotol_pikk")
    kihelkonnad_kp = safe_sheet_parse(xl, "kihelkond_keskpunktid")

    # ML lisafailid
    ml_marksonad = pd.DataFrame()
    ml_clip = pd.DataFrame()

    ml_marksonad_candidates = [
        "ERA_märksõnad_ML.xlsx",
        "ERA_märksõnad_ML.xlsx",
        "ERA_marksonad_ML.xlsx",
    ]
    ml_clip_candidates = [
        "era_clip_KOIK_pildid_sigmoid.xlsx",
    ]

    ml_marksonad_path = find_existing_file(ml_marksonad_candidates, fallback_contains="marksonadml")
    ml_clip_path = find_existing_file(ml_clip_candidates, fallback_contains="clipkoikpildidsigmoid")

    ml_marksonad = read_first_existing_sheet(
        ml_marksonad_path,
        preferred_sheets=["märksõnad_pikk", "ml_foto_klastrid", "ml_multihot_klastrid"],
        required_cols=["Märksõna2", "klastrid"]
    )

    marksona_kategooriad_map = keyword_category_map_from_ml(ml_marksonad)

    ml_clip = read_first_existing_sheet(
        ml_clip_path,
        preferred_sheets=["predictions_all", "predictions_eval_only", "sample_all"],
        required_cols=["pred_top1", "true_clusters"]
    )

    if fotod.empty:
        raise ValueError("Sheet 'fotod_koordinaatidega' puudub või on tühi.")

    # puhasta veerunimed ja PID-id
    for d in [fotod, master, marksoned, isikud, kihelkonnad_kp, ml_marksonad, ml_clip]:
        if not d.empty:
            d.columns = d.columns.astype(str).str.strip()
            if "PID" in d.columns:
                d["PID"] = d["PID"].fillna("").astype(str).str.strip()
            if "failinimi" in d.columns:
                d["failinimi"] = d["failinimi"].fillna("").astype(str).str.strip()

    # ühtlusta koordinaadiveerud fotode tabelis
    fotod = fotod.rename(columns={
        "Latitude": "latitude",
        "Longitude": "longitude",
        "lat": "latitude",
        "lon": "longitude",
        "long": "longitude",
        "lõplik_latitude": "latitude",
        "lõplik_longitude": "longitude",
    })

    # ühtlasta keskpunktide tabelis
    kihelkonnad_kp = kihelkonnad_kp.rename(columns={
        "Latitude": "latitude",
        "Longitude": "longitude",
        "lat": "latitude",
        "lon": "longitude",
        "long": "longitude",
    })

    # kui Aasta / Žanr jm on masteris, too need PID järgi juurde
    if not master.empty and "PID" in master.columns and "PID" in fotod.columns:
        master = master.rename(columns={
            "Zanr": "Žanr",
            "zanr": "Žanr",
            "žanr": "Žanr",
            "aasta": "Aasta",
        })

        juurde = [
            "PID", "Aasta", "Žanr", "Sisu kirjeldus", "failinimi",
            "Projekt", "ERA märksõnad (koondatud)", "Isikute arv"
        ]
        juurde = [c for c in juurde if c in master.columns]

        # ära tee topeltveerge, kui need juba fotod tabelis olemas ja täidetud
        for c in juurde:
            if c != "PID" and c in fotod.columns:
                fotod = fotod.drop(columns=[c])

        fotod = fotod.merge(
            master[juurde].drop_duplicates(subset=["PID"]),
            on="PID",
            how="left"
        )

    # sinu käsitsi loodud 19 märksõna-kategooriat
    if not ml_marksonad.empty and "PID" in ml_marksonad.columns and "PID" in fotod.columns:
        # Kui kogemata loeti pikem märksõnade leht, koonda see ise PID-tasemele.
        if "klastrid" not in ml_marksonad.columns and "Märksõna2" in ml_marksonad.columns:
            agg_dict = {
                "Märksõna2": lambda s: ", ".join(sorted(set(x for x in s.dropna().astype(str).str.strip() if x))),
            }
            if "Märksõna" in ml_marksonad.columns:
                agg_dict["Märksõna"] = lambda s: ", ".join(sorted(set(x for x in s.dropna().astype(str).str.strip() if x)))

            ml_marksonad = ml_marksonad.groupby("PID", as_index=False).agg(agg_dict)
            ml_marksonad = ml_marksonad.rename(columns={"Märksõna2": "klastrid", "Märksõna": "märksõnad"})
            ml_marksonad["klastrite_arv"] = ml_marksonad["klastrid"].fillna("").apply(lambda x: len([c for c in str(x).split(",") if c.strip()]))
            if "märksõnad" in ml_marksonad.columns:
                ml_marksonad["märksõnade_arv"] = ml_marksonad["märksõnad"].fillna("").apply(lambda x: len([c for c in str(x).split(",") if c.strip()]))

        ml_cols = [c for c in ["PID", "klastrid", "klastrite_arv", "märksõnad", "märksõnade_arv"] if c in ml_marksonad.columns]

        # eemalda varasemad sama info veerud, kui äpp jookseb cache järel või põhitabelis on need juba olemas
        for c in ["Märksõna kategooria", "Märksõna kategooriate arv", "Originaal märksõnad", "Originaal märksõnade arv"]:
            if c in fotod.columns:
                fotod = fotod.drop(columns=[c])

        fotod = fotod.merge(
            ml_marksonad[ml_cols].drop_duplicates(subset=["PID"]),
            on="PID",
            how="left"
        )

        fotod = fotod.rename(columns={
            "klastrid": "Märksõna kategooria",
            "klastrite_arv": "Märksõna kategooriate arv",
            "märksõnad": "Originaal märksõnad",
            "märksõnade_arv": "Originaal märksõnade arv",
        })

    # CLIP masinennustused
    if not ml_clip.empty and "PID" in ml_clip.columns and "PID" in fotod.columns:
        clip_cols = [
            "PID", "pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5",
            "pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score",
            "confidence_margin_top1_top2",
            "true_clusters", "hit_top1", "hit_any_top3", "hit_any_top5"
        ]
        clip_cols = [c for c in clip_cols if c in ml_clip.columns]

        for c in clip_cols:
            if c != "PID" and c in fotod.columns:
                fotod = fotod.drop(columns=[c])

        fotod = fotod.merge(
            ml_clip[clip_cols].drop_duplicates(subset=["PID"]),
            on="PID",
            how="left"
        )

        fotod = add_ml_strength_columns(fotod)

    for col in [
        "PID", "Aasta", "Žanr", "Kihelkond", "Sisu kirjeldus", "failinimi",
        "koordinaadid_leitud",
        "latitude", "longitude",
        "Projekt", "ERA märksõnad (koondatud)", "Isikute arv",
        "kihelkond_kaart", "Kihelkond või linn",
        "Märksõna kategooria", "Märksõna kategooriate arv",
        "Originaal märksõnad", "Originaal märksõnade arv",
        "pred_top1", "pred_top2", "pred_top3",
        "pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5",
        "pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score",
        "confidence_margin_top1_top2", "ML top3 koondskoor", "ML top5 koondskoor",
        "true_clusters", "hit_top1", "hit_any_top3", "hit_any_top5", "ML kindlus", "ML otsuse tugevus"
    ]:
        ensure_column(fotod, col)

    for col in ["PID", "Märksõna"]:
        ensure_column(marksoned, col)

    # Lisa tavalistele märksõnadele juurde nende 19 kategooria mapping.
    # See võimaldab sidebaris: 1) valida kategooria, 2) valida selle kategooria all ainult vastavaid märksõnu.
    if not marksona_kategooriad_map.empty and "Märksõna" in marksoned.columns:
        if "Märksõna kategooria" in marksoned.columns:
            marksoned = marksoned.drop(columns=["Märksõna kategooria"])
        marksoned = marksoned.merge(marksona_kategooriad_map, on="Märksõna", how="left")
    else:
        ensure_column(marksoned, "Märksõna kategooria")

    for col in ["PID", "Isik", "Fotograaf"]:
        ensure_column(isikud, col)

    # fotograaf isikute lehelt
    foto_map = (
        isikud[["PID", "Fotograaf"]]
        .dropna(subset=["Fotograaf"])
        .drop_duplicates(subset=["PID"])
    )

    if "Fotograaf" in fotod.columns:
        fotod = fotod.drop(columns=["Fotograaf"])

    fotod = fotod.merge(foto_map, on="PID", how="left")

    fotod["Aasta"] = pd.to_numeric(fotod["Aasta"], errors="coerce")
    fotod["latitude"] = pd.to_numeric(fotod["latitude"], errors="coerce")
    fotod["longitude"] = pd.to_numeric(fotod["longitude"], errors="coerce")

    # koordinaadid_leitud arvuta päriselt koordinaatide järgi
    fotod["koordinaadid_leitud"] = (
        fotod["latitude"].notna() & fotod["longitude"].notna()
    ).map({True: "jah", False: "ei"})

    # ühtlustatud kaardipiirkond
    fotod["kaardi_piirkond"] = pd.NA

    if "kihelkond_kaart" in fotod.columns:
        fotod["kaardi_piirkond"] = fotod["kihelkond_kaart"].apply(normalize_place_name)

    fallback_mask = fotod["kaardi_piirkond"].isna()
    if "Kihelkond või linn" in fotod.columns:
        fotod.loc[fallback_mask, "kaardi_piirkond"] = (
            fotod.loc[fallback_mask, "Kihelkond või linn"].apply(normalize_place_name)
        )

    fallback_mask = fotod["kaardi_piirkond"].isna()
    fotod.loc[fallback_mask, "kaardi_piirkond"] = (
        fotod.loc[fallback_mask, "Kihelkond"].apply(normalize_place_name)
    )

    if not kihelkonnad_kp.empty:
        esimene_veerg = kihelkonnad_kp.columns[0]
        kihelkonnad_kp = kihelkonnad_kp.rename(columns={esimene_veerg: "kaardi_piirkond"})
        kihelkonnad_kp["kaardi_piirkond"] = kihelkonnad_kp["kaardi_piirkond"].apply(normalize_place_name)

        for col in ["latitude", "longitude"]:
            ensure_column(kihelkonnad_kp, col)
            kihelkonnad_kp[col] = pd.to_numeric(kihelkonnad_kp[col], errors="coerce")

    return fotod, marksoned, isikud, kihelkonnad_kp, os.path.basename(xlsx_path)


@st.cache_data
def load_geojson(nimi):
    path = os.path.join(BASE_DIR, nimi)

    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        st.warning(f"GeoJSON faili '{nimi}' ei saanud laadida: {e}")
        return None


fotod, marksoned, isikud, kihelkonnad_kp, aktiivne_fail = load_data()


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("🗂️ Filtrid")

if st.sidebar.button("🔄 Uuenda andmed"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.info("Praegu on aktiivne ainult ajalooline kihelkonnapõhine kaart.")

if st.sidebar.button("🧹 Tühjenda kõik filtrid"):
    for key in ["valitud_zanr", "valitud_marksona", "valitud_marksona_kategooria", "valitud_fotograaf", "valitud_isik"]:
        st.session_state[key] = []
    st.session_state["marksona_loogika_radio"] = "VÕI – vähemalt üks"
    st.rerun()

if fotod["Aasta"].notna().any():
    aastad = fotod["Aasta"].dropna().astype(int)
    aasta_vahemik = st.sidebar.slider(
        "Aasta vahemik",
        min_value=int(aastad.min()),
        max_value=int(aastad.max()),
        value=(int(aastad.min()), int(aastad.max())),
    )
else:
    aasta_vahemik = (0, 9999)
    st.sidebar.info("Aasta veerus väärtusi ei leitud.")

# session state algväärtused
for key in ["valitud_zanr", "valitud_marksona", "valitud_marksona_kategooria", "valitud_fotograaf", "valitud_isik"]:
    if key not in st.session_state:
        st.session_state[key] = []

if "marksona_loogika_radio" not in st.session_state:
    st.session_state["marksona_loogika_radio"] = "VÕI – vähemalt üks"

marksona_loogika = st.session_state["marksona_loogika_radio"]

# esimene valikute arvutus
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    marksona_loogika,
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_zanr", zanr_opts, max_n=3)
sanitize_state_list("valitud_marksona", ms_opts, max_n=3)
sanitize_state_list("valitud_marksona_kategooria", mk_opts, max_n=3)
sanitize_state_list("valitud_fotograaf", ft_opts, max_n=3)
sanitize_state_list("valitud_isik", isik_opts, max_n=3)

st.sidebar.multiselect(
    "Žanr",
    options=zanr_opts,
    key="valitud_zanr",
    max_selections=3,
    placeholder="Vali kuni 3"
)

# arvuta uuesti pärast žanrit
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_marksona", ms_opts, max_n=3)

st.sidebar.multiselect(
    "Märksõna",
    options=ms_opts,
    key="valitud_marksona",
    max_selections=3,
    placeholder="Vali kuni 3"
)

if st.session_state.get("valitud_marksona_kategooria", []) and len(ms_opts) > 0:
    st.sidebar.caption("Märksõnade valik on kitsendatud valitud kategooria järgi.")

if len(st.session_state["valitud_marksona"]) > 1:
    st.sidebar.radio(
        "Märksõnade loogika",
        ["VÕI – vähemalt üks", "JA – kõik korraga"],
        key="marksona_loogika_radio"
    )

# arvuta uuesti pärast märksõnu
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_marksona_kategooria", mk_opts, max_n=3)

st.sidebar.multiselect(
    "Märksõna kategooria",
    options=mk_opts,
    key="valitud_marksona_kategooria",
    max_selections=3,
    placeholder="Vali kuni 3 kategooriat"
)

# arvuta uuesti pärast märksõna kategooriat
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_fotograaf", ft_opts, max_n=3)

st.sidebar.multiselect(
    "Fotograaf",
    options=ft_opts,
    key="valitud_fotograaf",
    max_selections=3,
    placeholder="Vali kuni 3"
)

# arvuta uuesti pärast fotograafi
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_isik", isik_opts, max_n=3)

st.sidebar.multiselect(
    "Isik pildil",
    options=isik_opts,
    key="valitud_isik",
    max_selections=3,
    placeholder="Vali kuni 3"
)

valitud_zanr = st.session_state["valitud_zanr"]
valitud_marksona = st.session_state["valitud_marksona"]
valitud_marksona_kategooria = st.session_state["valitud_marksona_kategooria"]
valitud_fotograaf = st.session_state["valitud_fotograaf"]
valitud_isik = st.session_state["valitud_isik"]
marksona_loogika = st.session_state["marksona_loogika_radio"]

df = get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik,
    valitud_zanr,
    valitud_marksona,
    marksona_loogika,
    valitud_fotograaf,
    valitud_isik,
    valitud_marksona_kategooria
)


# ── KPI ──────────────────────────────────────────────────────────────────────

st.title("📷 ERA Fotode Andmebaas")
st.caption(f"Kasutusel fail: {aktiivne_fail}")
st.markdown(f"Kuvatud **{len(df):,}** fotot kokku **{len(fotod):,}**-st")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Fotosid kokku", f"{len(df):,}")
c2.metric(
    "Koordinaatidega",
    f"{df['koordinaadid_leitud'].astype(str).eq('jah').sum():,}" if "koordinaadid_leitud" in df.columns else "0"
)
c3.metric("Erinevaid piirkondi", f"{df['kaardi_piirkond'].nunique()}" if "kaardi_piirkond" in df.columns else "0")
c4.metric(
    "Ajavahemik",
    (
        f"{int(df['Aasta'].min()) if df['Aasta'].notna().any() else '?'}–"
        f"{int(df['Aasta'].max()) if df['Aasta'].notna().any() else '?'}"
    ) if "Aasta" in df.columns else "?"
)
st.divider()

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["🗺️ Kaart", "📊 Statistika", "🏷️ Märksõnad", "👤 Isikud", "🤖 ML märksõnad", "📋 Andmetabel"]
)


# ══════════════════ TAB 1 – KAART ════════════════════════════════════════════
with tab1:
    st.subheader("Fotod kihelkondade ja linnade kaupa")

    kihel_veerg = "kaardi_piirkond"

    if kihel_veerg not in df.columns:
        st.warning("Kaardipiirkonna veerg puudub.")
    else:
        df_map_src = df[
            df[kihel_veerg].notna() &
            ~df[kihel_veerg].astype(str).str.lower().isin(["teadmata", "välismaa", "välismaa,"])
        ].copy()

        kihel_counts = (
            df_map_src
            .groupby(kihel_veerg)
            .size()
            .reset_index(name="Fotode arv")
            .rename(columns={kihel_veerg: "kaardi_piirkond"})
        )

        if not kihelkonnad_kp.empty and {"kaardi_piirkond", "latitude", "longitude"}.issubset(kihelkonnad_kp.columns):
            kihel_map = kihel_counts.merge(
                kihelkonnad_kp[["kaardi_piirkond", "latitude", "longitude"]],
                on="kaardi_piirkond",
                how="left"
            )
        else:
            kihel_map = kihel_counts.copy()

        geojson = load_geojson("kih1922_region.json")
        geojson_names = extract_geojson_feature_names(geojson, "KIHELKOND") if geojson else set()

        df_geo = kihel_map[kihel_map["kaardi_piirkond"].isin(geojson_names)].copy()
        df_missing = kihel_map[
            ~kihel_map["kaardi_piirkond"].isin(geojson_names) &
            kihel_map["latitude"].notna() &
            kihel_map["longitude"].notna()
        ].copy()

        if geojson and not df_geo.empty:
            fig = px.choropleth_mapbox(
                df_geo,
                geojson=geojson,
                locations="kaardi_piirkond",
                featureidkey="properties.KIHELKOND",
                color="Fotode arv",
                color_continuous_scale="YlOrRd",
                hover_name="kaardi_piirkond",
                hover_data={"Fotode arv": True},
                mapbox_style="open-street-map",
                zoom=6,
                center={"lat": 58.7, "lon": 25.0},
                opacity=0.65,
                title="Vali piirkond alt detailvaateks",
            )
            fig = lisa_piirjooned(fig, geojson)
            fig = lisa_puuduvad_keskpunktid(fig, df_missing)
            fig.update_layout(height=480, margin={"r": 0, "t": 40, "l": 0, "b": 0})
            st.plotly_chart(fig, use_container_width=True)

        elif not kihel_map.empty and {"latitude", "longitude"}.issubset(kihel_map.columns):
            kihel_pts = kihel_map.dropna(subset=["latitude", "longitude"])
            if not kihel_pts.empty:
                fig = px.scatter_mapbox(
                    kihel_pts,
                    lat="latitude",
                    lon="longitude",
                    size="Fotode arv",
                    color="Fotode arv",
                    hover_name="kaardi_piirkond",
                    hover_data={"Fotode arv": True, "latitude": False, "longitude": False},
                    color_continuous_scale="YlOrRd",
                    size_max=45,
                    zoom=6,
                    center={"lat": 58.7, "lon": 25.0},
                    mapbox_style="open-street-map",
                    title="Vali piirkond alt detailvaateks",
                )
                fig.update_traces(text=kihel_pts["kaardi_piirkond"], textposition="top center")
                fig.update_layout(height=480, margin={"r": 0, "t": 40, "l": 0, "b": 0})
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Kaardi jaoks ei leitud piisavalt piirkonnaandmeid.")
        else:
            st.info("Kihelkonna geojsoni ega keskpunktiandmeid ei leitud piisavalt.")

        st.subheader("Piirkonna detailvaade")
        kihel_valikud = sorted(kihel_map["kaardi_piirkond"].dropna().astype(str).unique().tolist()) if not kihel_map.empty else []
        val_kihel = st.selectbox("Vali piirkond", ["—"] + kihel_valikud)

        if val_kihel != "—":
            df_kihel = df[df[kihel_veerg].astype(str) == val_kihel]
            k1, k2, k3 = st.columns(3)
            k1.metric("Fotosid", len(df_kihel))
            k2.metric(
                "Koordinaatidega",
                df_kihel["koordinaadid_leitud"].astype(str).eq("jah").sum() if "koordinaadid_leitud" in df_kihel.columns else 0
            )
            k3.metric(
                "Ajavahemik",
                (
                    f"{int(df_kihel['Aasta'].min()) if df_kihel['Aasta'].notna().any() else '?'}–"
                    f"{int(df_kihel['Aasta'].max()) if df_kihel['Aasta'].notna().any() else '?'}"
                ) if "Aasta" in df_kihel.columns else "?"
            )

            naita_fotopunkte(df_kihel, f"Fotod – {val_kihel}", load_geojson, lisa_asustus_piirid=True)

            col_kd1, col_kd2 = st.columns(2)
            with col_kd1:
                if "Fotograaf" in df_kihel.columns:
                    ft = df_kihel["Fotograaf"].value_counts().head(8).reset_index()
                    if len(ft.columns) == 2:
                        ft.columns = ["Fotograaf", "Arv"]
                    st.markdown("**Fotograafid**")
                    st.dataframe(ft, hide_index=True, use_container_width=True)

            with col_kd2:
                if not marksoned.empty and "PID" in marksoned.columns and "Märksõna" in marksoned.columns:
                    ms_kihel = (
                        marksoned[marksoned["PID"].isin(df_kihel["PID"])]["Märksõna"]
                        .value_counts().head(8).reset_index()
                    )
                    if len(ms_kihel.columns) == 2:
                        ms_kihel.columns = ["Märksõna", "Arv"]
                    st.markdown("**Top märksõnad**")
                    st.dataframe(ms_kihel, hide_index=True, use_container_width=True)

        puuduvad_nimed = []
        if not kihel_map.empty and {"latitude", "longitude"}.issubset(kihel_map.columns):
            puuduvad_nimed = (
                kihel_map[
                    kihel_map["latitude"].isna() | kihel_map["longitude"].isna()
                ]["kaardi_piirkond"]
                .dropna()
                .astype(str)
                .tolist()
            )

        if puuduvad_nimed:
            st.caption("⚠️ Keskpunkt puudub: " + ", ".join(sorted(puuduvad_nimed[:20])) + (" ..." if len(puuduvad_nimed) > 20 else ""))
        else:
            st.caption(f"ℹ️ Kaardil on {len(kihel_map)} piirkonda. Geojsonist puudu olevad piirkonnad lisatakse keskpunktmarkeritena.")


# ══════════════════ TAB 2 – STATISTIKA ═══════════════════════════════════════
with tab2:
    col_left, col_right = st.columns(2)

    with col_left:
        df_a2 = df[df["Aasta"].notna()].copy()
        if not df_a2.empty:
            df_a2["Aastakümme"] = (df_a2["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
            ak = df_a2["Aastakümme"].value_counts().sort_index()
            fig = px.bar(
                x=ak.index,
                y=ak.values,
                labels={"x": "Aastakümme", "y": "Fotode arv"},
                title="Fotod aastakümne kaupa",
                color=ak.values,
                color_continuous_scale="Blues",
            )
            fig.update_layout(coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

        if "kaardi_piirkond" in df.columns:
            kihel_top = (
                df[df["kaardi_piirkond"].notna() & ~df["kaardi_piirkond"].astype(str).str.lower().isin(["teadmata", "välismaa", "välismaa,"])]["kaardi_piirkond"]
                .value_counts().head(15)
            )
            if len(kihel_top) > 0:
                fig = px.bar(
                    x=kihel_top.values,
                    y=kihel_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Piirkond"},
                    title="Top 15 piirkonda",
                    color=kihel_top.values,
                    color_continuous_scale="Greens",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)

    with col_right:
        if "Žanr" in df.columns:
            zanr_c = df["Žanr"].value_counts().head(15).dropna()
            if len(zanr_c) > 0:
                fig = px.pie(values=zanr_c.values, names=zanr_c.index, title="Žanrite jaotus (top 15)")
                fig.update_traces(textposition="inside", textinfo="percent+label")
                st.plotly_chart(fig, use_container_width=True)

        if "Fotograaf" in df.columns:
            foto_top = df["Fotograaf"].value_counts().head(12).dropna()
            if len(foto_top) > 0:
                fig = px.bar(
                    x=foto_top.values,
                    y=foto_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Fotograaf"},
                    title="Top 12 fotograafi",
                    color=foto_top.values,
                    color_continuous_scale="Oranges",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)

    st.subheader("Projektid")
    if "Projekt" in df.columns:
        proj_c = df["Projekt"].value_counts().head(10).dropna()
        if len(proj_c) > 0:
            fig = px.bar(
                x=proj_c.values,
                y=proj_c.index,
                orientation="h",
                labels={"x": "Fotode arv", "y": "Projekt"},
                title="Top 10 projekti",
                color=proj_c.values,
                color_continuous_scale="Purples",
            )
            fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=350)
            st.plotly_chart(fig, use_container_width=True)


# ══════════════════ TAB 3 – MÄRKSÕNAD ════════════════════════════════════════
with tab3:
    st.subheader("Märksõnade analüüs")
    df_pids = set(df["PID"].dropna().unique()) if "PID" in df.columns else set()
    mf = marksoned[marksoned["PID"].isin(df_pids)] if not marksoned.empty and "PID" in marksoned.columns else pd.DataFrame()

    col_m1, col_m2 = st.columns(2)

    with col_m1:
        top_n = st.slider("Näita top N märksõna", 10, 50, 20)
        if not mf.empty and "Märksõna" in mf.columns:
            ms_c = mf["Märksõna"].value_counts().head(top_n)
            if len(ms_c) > 0:
                fig = px.bar(
                    x=ms_c.values,
                    y=ms_c.index,
                    orientation="h",
                    labels={"x": "Esinemiste arv", "y": "Märksõna"},
                    title=f"Top {top_n} märksõna",
                    color=ms_c.values,
                    color_continuous_scale="Teal",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                st.plotly_chart(fig, use_container_width=True)

    with col_m2:
        st.markdown("#### Märksõna ajaline trend")
        ms_in = st.text_input("Sisesta märksõna", value="portree")
        if ms_in and not mf.empty and "Märksõna" in mf.columns:
            ms_tr = mf[mf["Märksõna"].fillna("").astype(str).str.lower() == ms_in.lower()][["PID"]].copy()
            ms_tr = ms_tr.merge(df[["PID", "Aasta"]].drop_duplicates("PID"), on="PID", how="left")
            ms_tr = ms_tr[ms_tr["Aasta"].notna()]
            if len(ms_tr) > 0:
                ms_tr["Aastakümme"] = (ms_tr["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
                tc = ms_tr["Aastakümme"].value_counts().sort_index()
                fig = px.line(
                    x=tc.index,
                    y=tc.values,
                    markers=True,
                    labels={"x": "Aastakümme", "y": "Esinemiste arv"},
                    title=f"'{ms_in}' ajas",
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Selle märksõnaga aastaga fotosid ei leitud.")


# ══════════════════ TAB 4 – ISIKUD ═══════════════════════════════════════════
with tab4:
    st.subheader("Isikud fotodel")
    df_pids = set(df["PID"].dropna().unique()) if "PID" in df.columns else set()
    isikud_filtered = (
        isikud[isikud["PID"].isin(df_pids)]
        if not isikud.empty and "PID" in isikud.columns else pd.DataFrame()
    )

    col_i1, col_i2 = st.columns(2)

    with col_i1:
        top_isik_n = st.slider("Näita top N isikut", 10, 50, 20, key="isik_slider")
        if not isikud_filtered.empty and "Isik" in isikud_filtered.columns:
            isik_top = isikud_filtered["Isik"].value_counts().head(top_isik_n)
            if len(isik_top) > 0:
                fig = px.bar(
                    x=isik_top.values,
                    y=isik_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Isik"},
                    title=f"Top {top_isik_n} isikut fotodel",
                    color=isik_top.values,
                    color_continuous_scale="Magenta",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                st.plotly_chart(fig, use_container_width=True)

    with col_i2:
        st.markdown("#### Isiku otsing")
        isik_otsing = st.text_input("Otsi isiku nime järgi")
        if isik_otsing and not isikud_filtered.empty and "Isik" in isikud_filtered.columns:
            isik_matches = isikud_filtered[
                isikud_filtered["Isik"].fillna("").astype(str).str.contains(isik_otsing, case=False, na=False)
            ]
            df_isik = df[df["PID"].isin(isik_matches["PID"].unique())]
            st.markdown(f"Leitud **{len(df_isik)}** fotot isikuga '{isik_otsing}'")
            if len(df_isik) > 0:
                cols = [c for c in ["PID", "Aasta", "Kihelkond", "kaardi_piirkond", "Sisu kirjeldus", "failinimi"] if c in df_isik.columns]
                st.dataframe(df_isik[cols].head(50), use_container_width=True, hide_index=True)
        else:
            st.markdown("#### Isikute arv fotol")
            if "Isikute arv" in df.columns:
                isikute_arv = df["Isikute arv"].value_counts().sort_index().head(10)
                if len(isikute_arv) > 0:
                    fig2 = px.bar(
                        x=isikute_arv.index.astype(str),
                        y=isikute_arv.values,
                        labels={"x": "Isikute arv fotol", "y": "Fotode arv"},
                        title="Kui palju isikuid on fotodel?",
                        color=isikute_arv.values,
                        color_continuous_scale="Teal",
                    )
                    fig2.update_layout(coloraxis_showscale=False)
                    st.plotly_chart(fig2, use_container_width=True)


# ══════════════════ TAB 5 – ML MÄRKSÕNAD ═════════════════════════════════════
with tab5:
    st.subheader("🤖 ML märksõnad")

    st.markdown("""
    CLIP ei „näe” sinu märksõnade nimekirja otse. Ta võrdleb pilti tekstikirjeldustega
    ja arvutab, milline tekstiline kategooria sobib pildiga kõige paremini.

    Siin saab võrrelda:
    - sinu olemasolevaid käsitsi loodud märksõna-kategooriaid;
    - CLIP-i pakutud top 1–3 kategooriat;
    - pildi pealkirja / sisukirjeldust.
    """)

    img_path = os.path.join(BASE_DIR, "clip_yhe_pildi_selgitus.png")
    if os.path.exists(img_path):
        st.image(img_path, caption="Näide: kuidas CLIP pildi ja tekstikategooriate sobivust hindab", use_container_width=True)
    else:
        st.info("Näidispilti 'clip_yhe_pildi_selgitus.png' ei leitud rakenduse kaustast.")

    st.divider()

    ml_df = df.copy()
    has_clip = "pred_top1" in ml_df.columns and ml_df["pred_top1"].notna().any()
    has_manual = "Märksõna kategooria" in ml_df.columns and ml_df["Märksõna kategooria"].notna().any()

    if not has_clip and not has_manual:
        st.warning("ML märksõnade infot ei leitud. Kontrolli, et failid 'ERA_märksõnad_ML.xlsx' ja 'era_clip_KOIK_pildid_sigmoid.xlsx' oleksid rakenduse kaustas.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fotosid filtris", f"{len(ml_df):,}")
        c2.metric("CLIP ennustusega", f"{ml_df['pred_top1'].notna().sum():,}" if "pred_top1" in ml_df.columns else "0")
        c3.metric("Käsitsi kategooriaga", f"{ml_df['Märksõna kategooria'].notna().sum():,}" if "Märksõna kategooria" in ml_df.columns else "0")
        st.caption("Need arvud ei peagi olema samad: CLIP ennustused on ainult hindamiseks käsitsi kategooriaga piltidel, ülejäänud fotodel võib olla originaalmärksõnu, aga mitte 19-kategooria käsitsi klastrit.")
        c4.metric(
            "Keskmine top1–top2 vahe",
            f"{ml_df['confidence_margin_top1_top2'].mean():.3f}" if "confidence_margin_top1_top2" in ml_df.columns and ml_df["confidence_margin_top1_top2"].notna().any() else "?"
        )

        st.markdown("### Käsitsi kategooriad vs CLIP pakkumised")

        col_a, col_b = st.columns(2)

        with col_a:
            if has_manual:
                manual_counts = split_categories(ml_df["Märksõna kategooria"]).value_counts().head(20)

                if len(manual_counts) > 0:
                    fig = px.bar(
                        x=manual_counts.values,
                        y=manual_counts.index,
                        orientation="h",
                        labels={"x": "Fotode arv", "y": "Käsitsi kategooria"},
                        title="Sinu märksõna-kategooriad",
                        color=manual_counts.values,
                        color_continuous_scale="Blues",
                    )
                    fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Käsitsi märksõna-kategooriaid ei leitud.")

        with col_b:
            if has_clip:
                clip_counts = ml_df["pred_top1"].dropna().astype(str).value_counts().head(20)

                if len(clip_counts) > 0:
                    fig = px.bar(
                        x=clip_counts.values,
                        y=clip_counts.index,
                        orientation="h",
                        labels={"x": "Fotode arv", "y": "CLIP top 1"},
                        title="CLIP top 1 kategooriad",
                        color=clip_counts.values,
                        color_continuous_scale="Oranges",
                    )
                    fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("CLIP ennustusi ei leitud.")

        if has_clip and has_manual:
            st.markdown("### Kui tihti CLIP kattub käsitsi kategooriaga?")
            st.warning("Kui kattuvus on siin väga madal, siis kõige tõenäolisem põhjus ei ole mudel, vaid kategoorianimede/andmeveeru sobimatus: CLIP-i `pred_top1` peab olema täpselt samas 19 kategooria sõnastikus nagu käsitsi `Märksõna kategooria`.")

            eval_df = ml_df[ml_df["pred_top1"].notna() & ml_df["Märksõna kategooria"].notna()].copy()

            if not eval_df.empty:
                eval_df["top1_kattub"] = eval_df.apply(lambda r: category_match(r, pred_cols=["pred_top1"]), axis=1)
                eval_df["top3_kattub"] = eval_df.apply(lambda r: category_match(r, pred_cols=["pred_top1", "pred_top2", "pred_top3"]), axis=1)
                eval_df["top5_kattub"] = eval_df.apply(lambda r: category_match(r, pred_cols=["pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5"]), axis=1)

                m1, m2, m3 = st.columns(3)
                m1.metric("Top1 kattuvus", f"{eval_df['top1_kattub'].mean() * 100:.1f}%")
                m2.metric("Top3 kattuvus", f"{eval_df['top3_kattub'].mean() * 100:.1f}%")
                m3.metric("Top5 kattuvus", f"{eval_df['top5_kattub'].mean() * 100:.1f}%")

                with st.expander("Kontrolli kategoorianimede kattumist"):
                    manual_set = sorted(split_categories(eval_df["Märksõna kategooria"]).dropna().astype(str).unique().tolist())
                    pred_set = sorted(pd.concat([
                        eval_df[c].dropna().astype(str) for c in ["pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5"] if c in eval_df.columns
                    ]).dropna().astype(str).unique().tolist())
                    col_x, col_y = st.columns(2)
                    with col_x:
                        st.markdown("**Käsitsi kategooriad**")
                        st.write(manual_set)
                    with col_y:
                        st.markdown("**CLIP kategooriad**")
                        st.write(pred_set)

                match_counts = eval_df["top3_kattub"].map({True: "Top3 seas kattub", False: "Top3 seas ei kattu"}).value_counts()
                fig = px.pie(
                    values=match_counts.values,
                    names=match_counts.index,
                    title="CLIP top3 vs käsitsi kategooria"
                )
                st.plotly_chart(fig, use_container_width=True)

        if has_clip:
            st.markdown("### ML skooride tõlgendus")
            score_cols = [c for c in ["pred_top1_score", "confidence_margin_top1_top2", "ML top3 koondskoor", "ML top5 koondskoor"] if c in ml_df.columns]
            if score_cols:
                st.caption("Top1 skoor üksi ei ole väga hea kvaliteedimõõdik. Praktilisem on vaadata koos top1–top2 vahet ning seda, kas õige/sobiv kategooria ilmub top3 või top5 hulka.")
                score_summary = ml_df[score_cols].describe().T.reset_index().rename(columns={"index": "skoor"})
                st.dataframe(score_summary, use_container_width=True, hide_index=True)

        st.markdown("### Vaata üksikuid fotosid")

        otsing_ml = st.text_input("Otsi PID, failinime või pealkirja järgi", key="ml_otsing")

        ml_show = ml_df.copy()

        if otsing_ml:
            mask = pd.Series(False, index=ml_show.index)

            for col in ["PID", "failinimi", "Sisu kirjeldus"]:
                if col in ml_show.columns:
                    mask = mask | ml_show[col].fillna("").astype(str).str.contains(otsing_ml, case=False, na=False)

            ml_show = ml_show[mask]

        if has_clip and has_manual:
            ainult_erinevad = st.checkbox("Näita ainult ridu, kus CLIP top3 ei ole käsitsi kategooriate hulgas")

            if ainult_erinevad:
                ml_show = ml_show[
                    ~ml_show.apply(
                        lambda r: category_match(r, pred_cols=["pred_top1", "pred_top2", "pred_top3"]),
                        axis=1
                    )
                ]

        cols_ml = [
            c for c in [
                "PID",
                "failinimi",
                "Sisu kirjeldus",
                "Märksõna kategooria",
                "Originaal märksõnad",
                "pred_top1",
                "pred_top2",
                "pred_top3",
                "pred_top4",
                "pred_top5",
                "pred_top1_score",
                "confidence_margin_top1_top2",
                "ML top3 koondskoor",
                "ML top5 koondskoor",
                "ML otsuse tugevus",
            ] if c in ml_show.columns
        ]

        st.markdown(f"Näidatakse **{len(ml_show):,}** rida")

        st.dataframe(
            ml_show[cols_ml].head(500),
            use_container_width=True,
            hide_index=True,
            height=420
        )

        csv_ml = ml_show[cols_ml].to_csv(index=False).encode("utf-8")

        st.download_button(
            "⬇️ Lae ML võrdlustabel alla CSV-na",
            data=csv_ml,
            file_name="era_ml_marksonad_vordlus.csv",
            mime="text/csv"
        )


# ══════════════════ TAB 6 – ANDMETABEL ═══════════════════════════════════════
with tab6:
    st.subheader("Andmetabel")
    vaikimisi = [
        c for c in [
            "PID", "Aasta", "Kihelkond", "kaardi_piirkond", "Fotograaf",
            "Žanr", "Märksõna kategooria", "pred_top1",
            "Sisu kirjeldus", "ERA märksõnad (koondatud)", "failinimi"
        ] if c in df.columns
    ]

    show_cols = st.multiselect(
        "Vali kuvatavad veerud",
        options=list(df.columns),
        default=vaikimisi
    )

    otsing = st.text_input("🔍 Otsi (sisu, kihelkond, fotograaf)")
    df_show = df.copy()

    if otsing:
        mask = pd.Series(False, index=df_show.index)

        if "Sisu kirjeldus" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Sisu kirjeldus"], otsing)
        if "Kihelkond" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Kihelkond"], otsing)
        if "kaardi_piirkond" in df_show.columns:
            mask = mask | safe_str_contains(df_show["kaardi_piirkond"], otsing)
        if "Fotograaf" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Fotograaf"], otsing)
        if "Märksõna kategooria" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Märksõna kategooria"], otsing)
        if "pred_top1" in df_show.columns:
            mask = mask | safe_str_contains(df_show["pred_top1"], otsing)

        df_show = df_show[mask]

    st.markdown(f"Näidatakse **{len(df_show):,}** rida")

    if show_cols:
        st.dataframe(df_show[show_cols].head(500), use_container_width=True, height=420)

        if len(df_show) > 500:
            st.caption("ℹ️ Tabelis on esimesed 500 rida. Kitsenda filtritega.")

        csv = df_show[show_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Lae alla CSV",
            data=csv,
            file_name="era_fotod_filteeritud.csv",
            mime="text/csv"
        )
    else:
        st.info("Vali vähemalt üks veerg.")
            if wanted in normalize_filename_for_match(fname):
                return os.path.join(BASE_DIR, fname)

    return None


def read_first_existing_sheet(path, preferred_sheets, required_cols=None):
    if not path or not os.path.exists(path):
        return pd.DataFrame()

    xl = pd.ExcelFile(path)

    for sheet in preferred_sheets:
        if sheet in xl.sheet_names:
            df = xl.parse(sheet)
            if required_cols is None or any(c in df.columns for c in required_cols):
                return df

    if required_cols:
        for sheet in xl.sheet_names:
            df = xl.parse(sheet)
            if any(c in df.columns for c in required_cols):
                return df

    return pd.DataFrame()


def ensure_column(df, col, default=pd.NA):
    if col not in df.columns:
        df[col] = default
    return df


def safe_str_contains(series, text):
    return series.fillna("").astype(str).str.contains(text, case=False, na=False)


def clean_region_name(x):
    if pd.isna(x):
        return pd.NA
    x = str(x).strip()
    if not x:
        return pd.NA
    return x


def normalize_place_name(x):
    if pd.isna(x):
        return pd.NA

    x = str(x).strip()
    if not x:
        return pd.NA

    xl = x.lower()

    tallinn_variants = {
        "tallinn", "tallinna linn", "tallinn linn"
    }
    tartu_variants = {
        "tartu", "tartu linn", "tartu linn."
    }
    petseri_variants = {
        "petserimaa", "petseri"
    }
    setu_variants = {
        "setumaa", "setomaa", "setu ala"
    }

    if xl in tallinn_variants:
        return "Tallinn"
    if xl in tartu_variants:
        return "Tartu"
    if xl in petseri_variants:
        return "Petserimaa"
    if xl in setu_variants:
        return "Setumaa"

    return x


def extract_geojson_feature_names(geojson, prop_name="KIHELKOND"):
    names = set()
    if not geojson or "features" not in geojson:
        return names

    for feature in geojson["features"]:
        props = feature.get("properties", {})
        val = props.get(prop_name)
        if val is not None and str(val).strip():
            names.add(str(val).strip())

    return names


def extract_polygon_rings(geom):
    if not geom or "type" not in geom or "coordinates" not in geom:
        return []

    coords = geom["coordinates"]
    rings = []

    try:
        if geom["type"] == "Polygon":
            if coords and len(coords) > 0 and coords[0]:
                rings.append(coords[0])

        elif geom["type"] == "MultiPolygon":
            for poly in coords:
                if poly and len(poly) > 0 and poly[0]:
                    rings.append(poly[0])

    except Exception:
        return []

    return rings


def lisa_piirjooned(fig, geojson, color="black", width=1):
    if not geojson or "features" not in geojson:
        return fig

    for feature in geojson["features"]:
        geom = feature.get("geometry", {})
        coords_list = extract_polygon_rings(geom)

        for coords in coords_list:
            if not coords or len(coords) < 2:
                continue

            try:
                lons = [c[0] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                lats = [c[1] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                if len(lons) < 2 or len(lats) < 2:
                    continue

                fig.add_trace(go.Scattermapbox(
                    lon=lons,
                    lat=lats,
                    mode="lines",
                    line=dict(color=color, width=width),
                    hoverinfo="skip",
                    showlegend=False,
                ))
            except Exception:
                continue

    return fig


def lisa_puuduvad_keskpunktid(fig, df_missing):
    if df_missing is None or df_missing.empty:
        return fig

    sizes = df_missing["Fotode arv"].clip(lower=8, upper=40)

    hover_text = (
        df_missing["kaardi_piirkond"].astype(str)
        + "<br>Fotode arv: " + df_missing["Fotode arv"].astype(str)
    )

    fig.add_trace(go.Scattermapbox(
        lat=df_missing["latitude"],
        lon=df_missing["longitude"],
        mode="markers+text",
        text=df_missing["kaardi_piirkond"],
        textposition="top center",
        marker=dict(size=sizes, opacity=0.85),
        hovertext=hover_text,
        hoverinfo="text",
        name="Puuduvad piirkonnad",
        showlegend=False,
    ))

    return fig


def split_categories(series):
    """Jagab kategooriavälja päris üksikkategooriateks.
    Talub koma, semikoolonit ja püstkriipsu. Nii ei teki filtrisse 100 eri kombinatsiooni.
    """
    if series is None or len(series) == 0:
        return pd.Series(dtype="object")

    return (
        series
        .dropna()
        .astype(str)
        .str.replace(";", ",", regex=False)
        .str.replace("|", ",", regex=False)
        .str.split(",")
        .explode()
        .str.strip()
        .replace("", pd.NA)
        .dropna()
    )


def filter_by_comma_categories(df, col, selected):
    if not selected or col not in df.columns:
        return df

    selected_lower = {str(x).strip().lower() for x in selected if str(x).strip()}

    def has_any_category(value):
        text = str(value).replace(";", ",").replace("|", ",")
        cats = [c.strip().lower() for c in text.split(",") if c.strip()]
        return any(c in selected_lower for c in cats)

    return df[df[col].fillna("").apply(has_any_category)]


def keyword_category_map_from_ml(ml_marksonad):
    """Tagastab tabeli: Märksõna -> Märksõna kategooria.
    Vajalik selleks, et kasutaja saaks valida nt kategooria 'inimene'
    ja selle all näha ainult selle kategooria päris märksõnu.
    """
    if ml_marksonad is None or ml_marksonad.empty:
        return pd.DataFrame(columns=["Märksõna", "Märksõna kategooria"])

    df_map = ml_marksonad.copy()
    df_map.columns = df_map.columns.astype(str).str.strip()

    # Kõige tõenäolisem vorm: pikk mapping märksõna -> klaster/kategooria.
    keyword_col = None
    for c in ["Märksõna", "märksõna", "marksona", "keyword"]:
        if c in df_map.columns:
            keyword_col = c
            break

    category_col = None
    for c in ["Märksõna2", "märksõna2", "klaster", "klastrid", "Märksõna kategooria", "kategooria"]:
        if c in df_map.columns:
            category_col = c
            break

    if keyword_col and category_col:
        out = df_map[[keyword_col, category_col]].copy()
        out.columns = ["Märksõna", "Märksõna kategooria"]
        out = out.dropna()
        out["Märksõna"] = out["Märksõna"].astype(str).str.strip()
        out["Märksõna kategooria"] = out["Märksõna kategooria"].astype(str).str.strip()
        out = out[(out["Märksõna"] != "") & (out["Märksõna kategooria"] != "")]
        return out.drop_duplicates()

    return pd.DataFrame(columns=["Märksõna", "Märksõna kategooria"])


def category_match(row, manual_col="Märksõna kategooria", pred_cols=None):
    if pred_cols is None:
        pred_cols = ["pred_top1", "pred_top2", "pred_top3"]

    manual = [c.strip().lower() for c in str(row.get(manual_col, "")).split(",") if c.strip()]
    preds = [str(row.get(c, "")).strip().lower() for c in pred_cols if str(row.get(c, "")).strip() and str(row.get(c, "")).lower() != "nan"]

    if not manual or not preds:
        return False

    return any(p in manual for p in preds)


def add_ml_strength_columns(df):
    """Lisab ML-i tõlgendamiseks paremad koondväljad kui ainult pred_top1_score."""
    score_cols_top3 = [c for c in ["pred_top1_score", "pred_top2_score", "pred_top3_score"] if c in df.columns]
    score_cols_top5 = [c for c in ["pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score"] if c in df.columns]

    for col in score_cols_top5 + ["confidence_margin_top1_top2"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if score_cols_top3:
        df["ML top3 koondskoor"] = df[score_cols_top3].sum(axis=1, min_count=1)

    if score_cols_top5:
        df["ML top5 koondskoor"] = df[score_cols_top5].sum(axis=1, min_count=1)

    if "confidence_margin_top1_top2" not in df.columns and {"pred_top1_score", "pred_top2_score"}.issubset(df.columns):
        df["confidence_margin_top1_top2"] = df["pred_top1_score"] - df["pred_top2_score"]

    if "pred_top1_score" in df.columns and "confidence_margin_top1_top2" in df.columns:
        def strength(row):
            top1 = row.get("pred_top1_score")
            margin = row.get("confidence_margin_top1_top2")
            if pd.isna(top1) or pd.isna(margin):
                return pd.NA
            if top1 >= 0.565 and margin >= 0.020:
                return "tugev"
            if top1 >= 0.555 and margin >= 0.010:
                return "keskmine"
            return "nõrk / kontrolli üle"

        df["ML otsuse tugevus"] = df.apply(strength, axis=1)
        df["ML kindlus"] = df["ML otsuse tugevus"]

    return df


def get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik,
    valitud_zanr,
    valitud_marksona,
    marksona_loogika,
    valitud_fotograaf,
    valitud_isik,
    valitud_marksona_kategooria=None
):
    df = fotod.copy()

    if "Aasta" in df.columns and df["Aasta"].notna().any():
        df_a = df[df["Aasta"].notna()].copy()
        df_a = df_a[df_a["Aasta"].between(aasta_vahemik[0], aasta_vahemik[1])]
        df = pd.concat([df_a, df[df["Aasta"].isna()]], ignore_index=True)

    if valitud_zanr and "Žanr" in df.columns:
        df = df[df["Žanr"].isin(valitud_zanr)]

    if valitud_fotograaf and "Fotograaf" in df.columns:
        df = df[df["Fotograaf"].isin(valitud_fotograaf)]

    if valitud_marksona and not marksoned.empty and "Märksõna" in marksoned.columns:
        if marksona_loogika == "JA – kõik korraga":
            pids = None
            for ms in valitud_marksona:
                ms_pids = set(marksoned[marksoned["Märksõna"] == ms]["PID"].dropna().unique())
                pids = ms_pids if pids is None else pids & ms_pids
            pids = pids or set()
        else:
            pids = set(marksoned[marksoned["Märksõna"].isin(valitud_marksona)]["PID"].dropna().unique())

        df = df[df["PID"].isin(pids)]

    if valitud_marksona_kategooria:
        df = filter_by_comma_categories(df, "Märksõna kategooria", valitud_marksona_kategooria)

    if valitud_isik and not isikud.empty and "Isik" in isikud.columns:
        isik_pids = set(isikud[isikud["Isik"].isin(valitud_isik)]["PID"].dropna().unique())
        df = df[df["PID"].isin(isik_pids)]

    return df


def get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    valitud_zanr,
    valitud_marksona,
    marksona_loogika,
    valitud_fotograaf,
    valitud_isik,
    valitud_marksona_kategooria=None
):
    # ŽANR: kõik muud filtrid peal, žanr ise maas
    df_for_zanr = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        [],
        valitud_marksona, marksona_loogika,
        valitud_fotograaf, valitud_isik,
        valitud_marksona_kategooria
    )
    zanr_opts = sorted(
        df_for_zanr["Žanr"].dropna().astype(str).unique().tolist()
    ) if "Žanr" in df_for_zanr.columns else []

    # MÄRKSÕNA: kõik muud filtrid peal, märksõna ise maas
    df_for_ms = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        [],
        marksona_loogika,
        valitud_fotograaf, valitud_isik,
        valitud_marksona_kategooria
    )
    pids_ms = set(df_for_ms["PID"].dropna().unique()) if "PID" in df_for_ms.columns else set()

    if not marksoned.empty and "PID" in marksoned.columns and "Märksõna" in marksoned.columns:
        ms_source = marksoned[marksoned["PID"].isin(pids_ms)].copy()

        # Kui kategooria on valitud, näita märksõna filtris ainult selle kategooria märksõnu.
        if valitud_marksona_kategooria and "Märksõna kategooria" in ms_source.columns:
            ms_source = ms_source[ms_source["Märksõna kategooria"].isin(valitud_marksona_kategooria)]

        ms_opts = ms_source["Märksõna"].dropna().astype(str).value_counts().index.tolist()
    else:
        ms_opts = []

    # MÄRKSÕNA KATEGOORIA: kõik muud filtrid peal, kategooria ise maas
    df_for_mk = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        valitud_marksona, marksona_loogika,
        valitud_fotograaf, valitud_isik,
        []
    )
    if not marksoned.empty and "Märksõna kategooria" in marksoned.columns:
        pids_mk = set(df_for_mk["PID"].dropna().unique()) if "PID" in df_for_mk.columns else set()
        mk_source = marksoned[marksoned["PID"].isin(pids_mk)].copy()
        mk_opts = sorted(mk_source["Märksõna kategooria"].dropna().astype(str).str.strip().unique().tolist())
    else:
        mk_opts = sorted(split_categories(df_for_mk["Märksõna kategooria"]).unique().tolist()) if "Märksõna kategooria" in df_for_mk.columns else []

    # FOTOGRAAF: kõik muud filtrid peal, fotograaf ise maas
    df_for_ft = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        valitud_marksona, marksona_loogika,
        [],
        valitud_isik,
        valitud_marksona_kategooria
    )
    ft_opts = sorted(
        df_for_ft["Fotograaf"].dropna().astype(str).unique().tolist()
    ) if "Fotograaf" in df_for_ft.columns else []

    # ISIK: kõik muud filtrid peal, isik ise maas
    df_for_isik = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        valitud_marksona, marksona_loogika,
        valitud_fotograaf, [],
        valitud_marksona_kategooria
    )
    pids_isik = set(df_for_isik["PID"].dropna().unique()) if "PID" in df_for_isik.columns else set()
    isik_opts = (
        isikud[isikud["PID"].isin(pids_isik)]["Isik"]
        .dropna().astype(str).value_counts().index.tolist()
        if not isikud.empty and "PID" in isikud.columns and "Isik" in isikud.columns
        else []
    )

    return zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts


def sanitize_state_list(key, allowed_options, max_n=3):
    current = st.session_state.get(key, [])
    if current is None:
        current = []
    current = [x for x in current if x in allowed_options][:max_n]
    st.session_state[key] = current


def naita_fotopunkte(df_piirkond, pealkiri, load_geojson_func, lisa_asustus_piirid=False):
    required = ["latitude", "longitude"]
    for col in required:
        if col not in df_piirkond.columns:
            st.info("Koordinaadiveerud puuduvad.")
            return

    df_pts = df_piirkond[
        df_piirkond["latitude"].notna() &
        df_piirkond["longitude"].notna()
    ].copy()

    if df_pts.empty:
        st.info("Valitud piirkonnas koordinaatidega fotosid ei ole.")
        return

    hover_data = {
        "Aasta": "Aasta" in df_pts.columns,
        "Kihelkond": "Kihelkond" in df_pts.columns,
        "Fotograaf": "Fotograaf" in df_pts.columns,
        "latitude": False,
        "longitude": False,
    }

    color_col = "lõplik_täpsus" if "lõplik_täpsus" in df_pts.columns else None

    fig = px.scatter_mapbox(
        df_pts,
        lat="latitude",
        lon="longitude",
        hover_name="Sisu kirjeldus" if "Sisu kirjeldus" in df_pts.columns else None,
        hover_data=hover_data,
        color=color_col,
        mapbox_style="open-street-map",
        title=pealkiri,
        zoom=9,
        center={
            "lat": df_pts["latitude"].mean(),
            "lon": df_pts["longitude"].mean(),
        },
    )

    fig.update_traces(marker=dict(size=9, opacity=0.8))

    if lisa_asustus_piirid:
        geojson_ay = load_geojson_func("asustusyksus.geojson")
        if isinstance(geojson_ay, dict) and "features" in geojson_ay:
            lat_min = df_pts["latitude"].min() - 0.1
            lat_max = df_pts["latitude"].max() + 0.1
            lon_min = df_pts["longitude"].min() - 0.1
            lon_max = df_pts["longitude"].max() + 0.1

            for feature in geojson_ay["features"]:
                geom = feature.get("geometry", {})
                coords_list = extract_polygon_rings(geom)

                for coords in coords_list:
                    if not coords or len(coords) < 2:
                        continue

                    try:
                        lons = [c[0] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                        lats = [c[1] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                        if len(lons) < 2 or len(lats) < 2:
                            continue

                        if (min(lons) < lon_max and max(lons) > lon_min and
                                min(lats) < lat_max and max(lats) > lat_min):
                            fig.add_trace(go.Scattermapbox(
                                lon=lons,
                                lat=lats,
                                mode="lines",
                                line=dict(color="rgba(80,80,80,0.5)", width=0.8),
                                hoverinfo="skip",
                                showlegend=False,
                            ))
                    except Exception:
                        continue

    fig.update_layout(height=500, margin={"r": 0, "t": 40, "l": 0, "b": 0})
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Koordinaatidega fotosid: {len(df_pts)}")


# ── Andmete laadimine ────────────────────────────────────────────────────────

@st.cache_data
def load_data():
    xlsx_path = None
    for fname in ["ERA_fotod_250426.xlsx"]:
        path = os.path.join(BASE_DIR, fname)
        if os.path.exists(path):
            xlsx_path = path
            break

    if xlsx_path is None:
        raise FileNotFoundError("Ühtegi Exceli faili ei leitud kaustast.")

    xl = pd.ExcelFile(xlsx_path)

    fotod = safe_sheet_parse(xl, "fotod_koordinaatidega")
    master = safe_sheet_parse(xl, "fotod_master")
    marksoned = safe_sheet_parse(xl, "märksõnad_pikk")
    isikud = safe_sheet_parse(xl, "isikud_fotol_pikk")
    kihelkonnad_kp = safe_sheet_parse(xl, "kihelkond_keskpunktid")

    # ML lisafailid
    ml_marksonad = pd.DataFrame()
    ml_clip = pd.DataFrame()

    ml_marksonad_candidates = [
        "ERA_märksõnad_ML.xlsx",
        "ERA_märksõnad_ML.xlsx",
        "ERA_marksonad_ML.xlsx",
    ]
    ml_clip_candidates = [
        "era_clip_KOIK_pildid_sigmoid.xlsx",
    ]

    ml_marksonad_path = find_existing_file(ml_marksonad_candidates, fallback_contains="marksonadml")
    ml_clip_path = find_existing_file(ml_clip_candidates, fallback_contains="clipkoikpildidsigmoid")

    ml_marksonad = read_first_existing_sheet(
        ml_marksonad_path,
        preferred_sheets=["märksõnad_pikk", "ml_foto_klastrid", "ml_multihot_klastrid"],
        required_cols=["Märksõna2", "klastrid"]
    )

    marksona_kategooriad_map = keyword_category_map_from_ml(ml_marksonad)

    ml_clip = read_first_existing_sheet(
        ml_clip_path,
        preferred_sheets=["predictions_all", "predictions_eval_only", "sample_all"],
        required_cols=["pred_top1", "true_clusters"]
    )

    if fotod.empty:
        raise ValueError("Sheet 'fotod_koordinaatidega' puudub või on tühi.")

    # puhasta veerunimed ja PID-id
    for d in [fotod, master, marksoned, isikud, kihelkonnad_kp, ml_marksonad, ml_clip]:
        if not d.empty:
            d.columns = d.columns.astype(str).str.strip()
            if "PID" in d.columns:
                d["PID"] = d["PID"].fillna("").astype(str).str.strip()
            if "failinimi" in d.columns:
                d["failinimi"] = d["failinimi"].fillna("").astype(str).str.strip()

    # ühtlusta koordinaadiveerud fotode tabelis
    fotod = fotod.rename(columns={
        "Latitude": "latitude",
        "Longitude": "longitude",
        "lat": "latitude",
        "lon": "longitude",
        "long": "longitude",
        "lõplik_latitude": "latitude",
        "lõplik_longitude": "longitude",
    })

    # ühtlasta keskpunktide tabelis
    kihelkonnad_kp = kihelkonnad_kp.rename(columns={
        "Latitude": "latitude",
        "Longitude": "longitude",
        "lat": "latitude",
        "lon": "longitude",
        "long": "longitude",
    })

    # kui Aasta / Žanr jm on masteris, too need PID järgi juurde
    if not master.empty and "PID" in master.columns and "PID" in fotod.columns:
        master = master.rename(columns={
            "Zanr": "Žanr",
            "zanr": "Žanr",
            "žanr": "Žanr",
            "aasta": "Aasta",
        })

        juurde = [
            "PID", "Aasta", "Žanr", "Sisu kirjeldus", "failinimi",
            "Projekt", "ERA märksõnad (koondatud)", "Isikute arv"
        ]
        juurde = [c for c in juurde if c in master.columns]

        # ära tee topeltveerge, kui need juba fotod tabelis olemas ja täidetud
        for c in juurde:
            if c != "PID" and c in fotod.columns:
                fotod = fotod.drop(columns=[c])

        fotod = fotod.merge(
            master[juurde].drop_duplicates(subset=["PID"]),
            on="PID",
            how="left"
        )

    # sinu käsitsi loodud 19 märksõna-kategooriat
    if not ml_marksonad.empty and "PID" in ml_marksonad.columns and "PID" in fotod.columns:
        # Kui kogemata loeti pikem märksõnade leht, koonda see ise PID-tasemele.
        if "klastrid" not in ml_marksonad.columns and "Märksõna2" in ml_marksonad.columns:
            agg_dict = {
                "Märksõna2": lambda s: ", ".join(sorted(set(x for x in s.dropna().astype(str).str.strip() if x))),
            }
            if "Märksõna" in ml_marksonad.columns:
                agg_dict["Märksõna"] = lambda s: ", ".join(sorted(set(x for x in s.dropna().astype(str).str.strip() if x)))

            ml_marksonad = ml_marksonad.groupby("PID", as_index=False).agg(agg_dict)
            ml_marksonad = ml_marksonad.rename(columns={"Märksõna2": "klastrid", "Märksõna": "märksõnad"})
            ml_marksonad["klastrite_arv"] = ml_marksonad["klastrid"].fillna("").apply(lambda x: len([c for c in str(x).split(",") if c.strip()]))
            if "märksõnad" in ml_marksonad.columns:
                ml_marksonad["märksõnade_arv"] = ml_marksonad["märksõnad"].fillna("").apply(lambda x: len([c for c in str(x).split(",") if c.strip()]))

        ml_cols = [c for c in ["PID", "klastrid", "klastrite_arv", "märksõnad", "märksõnade_arv"] if c in ml_marksonad.columns]

        # eemalda varasemad sama info veerud, kui äpp jookseb cache järel või põhitabelis on need juba olemas
        for c in ["Märksõna kategooria", "Märksõna kategooriate arv", "Originaal märksõnad", "Originaal märksõnade arv"]:
            if c in fotod.columns:
                fotod = fotod.drop(columns=[c])

        fotod = fotod.merge(
            ml_marksonad[ml_cols].drop_duplicates(subset=["PID"]),
            on="PID",
            how="left"
        )

        fotod = fotod.rename(columns={
            "klastrid": "Märksõna kategooria",
            "klastrite_arv": "Märksõna kategooriate arv",
            "märksõnad": "Originaal märksõnad",
            "märksõnade_arv": "Originaal märksõnade arv",
        })

    # CLIP masinennustused
    if not ml_clip.empty and "PID" in ml_clip.columns and "PID" in fotod.columns:
        clip_cols = [
            "PID", "pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5",
            "pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score",
            "confidence_margin_top1_top2",
            "true_clusters", "hit_top1", "hit_any_top3", "hit_any_top5"
        ]
        clip_cols = [c for c in clip_cols if c in ml_clip.columns]

        for c in clip_cols:
            if c != "PID" and c in fotod.columns:
                fotod = fotod.drop(columns=[c])

        fotod = fotod.merge(
            ml_clip[clip_cols].drop_duplicates(subset=["PID"]),
            on="PID",
            how="left"
        )

        fotod = add_ml_strength_columns(fotod)

    for col in [
        "PID", "Aasta", "Žanr", "Kihelkond", "Sisu kirjeldus", "failinimi",
        "koordinaadid_leitud",
        "latitude", "longitude",
        "Projekt", "ERA märksõnad (koondatud)", "Isikute arv",
        "kihelkond_kaart", "Kihelkond või linn",
        "Märksõna kategooria", "Märksõna kategooriate arv",
        "Originaal märksõnad", "Originaal märksõnade arv",
        "pred_top1", "pred_top2", "pred_top3",
        "pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5",
        "pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score",
        "confidence_margin_top1_top2", "ML top3 koondskoor", "ML top5 koondskoor",
        "true_clusters", "hit_top1", "hit_any_top3", "hit_any_top5", "ML kindlus", "ML otsuse tugevus"
    ]:
        ensure_column(fotod, col)

    for col in ["PID", "Märksõna"]:
        ensure_column(marksoned, col)

    # Lisa tavalistele märksõnadele juurde nende 19 kategooria mapping.
    # See võimaldab sidebaris: 1) valida kategooria, 2) valida selle kategooria all ainult vastavaid märksõnu.
    if not marksona_kategooriad_map.empty and "Märksõna" in marksoned.columns:
        if "Märksõna kategooria" in marksoned.columns:
            marksoned = marksoned.drop(columns=["Märksõna kategooria"])
        marksoned = marksoned.merge(marksona_kategooriad_map, on="Märksõna", how="left")
    else:
        ensure_column(marksoned, "Märksõna kategooria")

    for col in ["PID", "Isik", "Fotograaf"]:
        ensure_column(isikud, col)

    # fotograaf isikute lehelt
    foto_map = (
        isikud[["PID", "Fotograaf"]]
        .dropna(subset=["Fotograaf"])
        .drop_duplicates(subset=["PID"])
    )

    if "Fotograaf" in fotod.columns:
        fotod = fotod.drop(columns=["Fotograaf"])

    fotod = fotod.merge(foto_map, on="PID", how="left")

    fotod["Aasta"] = pd.to_numeric(fotod["Aasta"], errors="coerce")
    fotod["latitude"] = pd.to_numeric(fotod["latitude"], errors="coerce")
    fotod["longitude"] = pd.to_numeric(fotod["longitude"], errors="coerce")

    # koordinaadid_leitud arvuta päriselt koordinaatide järgi
    fotod["koordinaadid_leitud"] = (
        fotod["latitude"].notna() & fotod["longitude"].notna()
    ).map({True: "jah", False: "ei"})

    # ühtlustatud kaardipiirkond
    fotod["kaardi_piirkond"] = pd.NA

    if "kihelkond_kaart" in fotod.columns:
        fotod["kaardi_piirkond"] = fotod["kihelkond_kaart"].apply(normalize_place_name)

    fallback_mask = fotod["kaardi_piirkond"].isna()
    if "Kihelkond või linn" in fotod.columns:
        fotod.loc[fallback_mask, "kaardi_piirkond"] = (
            fotod.loc[fallback_mask, "Kihelkond või linn"].apply(normalize_place_name)
        )

    fallback_mask = fotod["kaardi_piirkond"].isna()
    fotod.loc[fallback_mask, "kaardi_piirkond"] = (
        fotod.loc[fallback_mask, "Kihelkond"].apply(normalize_place_name)
    )

    if not kihelkonnad_kp.empty:
        esimene_veerg = kihelkonnad_kp.columns[0]
        kihelkonnad_kp = kihelkonnad_kp.rename(columns={esimene_veerg: "kaardi_piirkond"})
        kihelkonnad_kp["kaardi_piirkond"] = kihelkonnad_kp["kaardi_piirkond"].apply(normalize_place_name)

        for col in ["latitude", "longitude"]:
            ensure_column(kihelkonnad_kp, col)
            kihelkonnad_kp[col] = pd.to_numeric(kihelkonnad_kp[col], errors="coerce")

    return fotod, marksoned, isikud, kihelkonnad_kp, os.path.basename(xlsx_path)


@st.cache_data
def load_geojson(nimi):
    path = os.path.join(BASE_DIR, nimi)

    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        st.warning(f"GeoJSON faili '{nimi}' ei saanud laadida: {e}")
        return None


fotod, marksoned, isikud, kihelkonnad_kp, aktiivne_fail = load_data()


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("🗂️ Filtrid")

if st.sidebar.button("🔄 Uuenda andmed"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.info("Praegu on aktiivne ainult ajalooline kihelkonnapõhine kaart.")

if st.sidebar.button("🧹 Tühjenda kõik filtrid"):
    for key in ["valitud_zanr", "valitud_marksona", "valitud_marksona_kategooria", "valitud_fotograaf", "valitud_isik"]:
        st.session_state[key] = []
    st.session_state["marksona_loogika_radio"] = "VÕI – vähemalt üks"
    st.rerun()

if fotod["Aasta"].notna().any():
    aastad = fotod["Aasta"].dropna().astype(int)
    aasta_vahemik = st.sidebar.slider(
        "Aasta vahemik",
        min_value=int(aastad.min()),
        max_value=int(aastad.max()),
        value=(int(aastad.min()), int(aastad.max())),
    )
else:
    aasta_vahemik = (0, 9999)
    st.sidebar.info("Aasta veerus väärtusi ei leitud.")

# session state algväärtused
for key in ["valitud_zanr", "valitud_marksona", "valitud_marksona_kategooria", "valitud_fotograaf", "valitud_isik"]:
    if key not in st.session_state:
        st.session_state[key] = []

if "marksona_loogika_radio" not in st.session_state:
    st.session_state["marksona_loogika_radio"] = "VÕI – vähemalt üks"

marksona_loogika = st.session_state["marksona_loogika_radio"]

# esimene valikute arvutus
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    marksona_loogika,
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_zanr", zanr_opts, max_n=3)
sanitize_state_list("valitud_marksona", ms_opts, max_n=3)
sanitize_state_list("valitud_marksona_kategooria", mk_opts, max_n=3)
sanitize_state_list("valitud_fotograaf", ft_opts, max_n=3)
sanitize_state_list("valitud_isik", isik_opts, max_n=3)

st.sidebar.multiselect(
    "Žanr",
    options=zanr_opts,
    key="valitud_zanr",
    max_selections=3,
    placeholder="Vali kuni 3"
)

# arvuta uuesti pärast žanrit
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_marksona", ms_opts, max_n=3)

st.sidebar.multiselect(
    "Märksõna",
    options=ms_opts,
    key="valitud_marksona",
    max_selections=3,
    placeholder="Vali kuni 3"
)

if st.session_state.get("valitud_marksona_kategooria", []) and len(ms_opts) > 0:
    st.sidebar.caption("Märksõnade valik on kitsendatud valitud kategooria järgi.")

if len(st.session_state["valitud_marksona"]) > 1:
    st.sidebar.radio(
        "Märksõnade loogika",
        ["VÕI – vähemalt üks", "JA – kõik korraga"],
        key="marksona_loogika_radio"
    )

# arvuta uuesti pärast märksõnu
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_marksona_kategooria", mk_opts, max_n=3)

st.sidebar.multiselect(
    "Märksõna kategooria",
    options=mk_opts,
    key="valitud_marksona_kategooria",
    max_selections=3,
    placeholder="Vali kuni 3 kategooriat"
)

# arvuta uuesti pärast märksõna kategooriat
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_fotograaf", ft_opts, max_n=3)

st.sidebar.multiselect(
    "Fotograaf",
    options=ft_opts,
    key="valitud_fotograaf",
    max_selections=3,
    placeholder="Vali kuni 3"
)

# arvuta uuesti pärast fotograafi
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_isik", isik_opts, max_n=3)

st.sidebar.multiselect(
    "Isik pildil",
    options=isik_opts,
    key="valitud_isik",
    max_selections=3,
    placeholder="Vali kuni 3"
)

valitud_zanr = st.session_state["valitud_zanr"]
valitud_marksona = st.session_state["valitud_marksona"]
valitud_marksona_kategooria = st.session_state["valitud_marksona_kategooria"]
valitud_fotograaf = st.session_state["valitud_fotograaf"]
valitud_isik = st.session_state["valitud_isik"]
marksona_loogika = st.session_state["marksona_loogika_radio"]

df = get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik,
    valitud_zanr,
    valitud_marksona,
    marksona_loogika,
    valitud_fotograaf,
    valitud_isik,
    valitud_marksona_kategooria
)


# ── KPI ──────────────────────────────────────────────────────────────────────

st.title("📷 ERA Fotode Andmebaas")
st.caption(f"Kasutusel fail: {aktiivne_fail}")
st.markdown(f"Kuvatud **{len(df):,}** fotot kokku **{len(fotod):,}**-st")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Fotosid kokku", f"{len(df):,}")
c2.metric(
    "Koordinaatidega",
    f"{df['koordinaadid_leitud'].astype(str).eq('jah').sum():,}" if "koordinaadid_leitud" in df.columns else "0"
)
c3.metric("Erinevaid piirkondi", f"{df['kaardi_piirkond'].nunique()}" if "kaardi_piirkond" in df.columns else "0")
c4.metric(
    "Ajavahemik",
    (
        f"{int(df['Aasta'].min()) if df['Aasta'].notna().any() else '?'}–"
        f"{int(df['Aasta'].max()) if df['Aasta'].notna().any() else '?'}"
    ) if "Aasta" in df.columns else "?"
)
st.divider()

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["🗺️ Kaart", "📊 Statistika", "🏷️ Märksõnad", "👤 Isikud", "🤖 ML märksõnad", "📋 Andmetabel"]
)


# ══════════════════ TAB 1 – KAART ════════════════════════════════════════════
with tab1:
    st.subheader("Fotod kihelkondade ja linnade kaupa")

    kihel_veerg = "kaardi_piirkond"

    if kihel_veerg not in df.columns:
        st.warning("Kaardipiirkonna veerg puudub.")
    else:
        df_map_src = df[
            df[kihel_veerg].notna() &
            ~df[kihel_veerg].astype(str).str.lower().isin(["teadmata", "välismaa", "välismaa,"])
        ].copy()

        kihel_counts = (
            df_map_src
            .groupby(kihel_veerg)
            .size()
            .reset_index(name="Fotode arv")
            .rename(columns={kihel_veerg: "kaardi_piirkond"})
        )

        if not kihelkonnad_kp.empty and {"kaardi_piirkond", "latitude", "longitude"}.issubset(kihelkonnad_kp.columns):
            kihel_map = kihel_counts.merge(
                kihelkonnad_kp[["kaardi_piirkond", "latitude", "longitude"]],
                on="kaardi_piirkond",
                how="left"
            )
        else:
            kihel_map = kihel_counts.copy()

        geojson = load_geojson("kih1922_region.json")
        geojson_names = extract_geojson_feature_names(geojson, "KIHELKOND") if geojson else set()

        df_geo = kihel_map[kihel_map["kaardi_piirkond"].isin(geojson_names)].copy()
        df_missing = kihel_map[
            ~kihel_map["kaardi_piirkond"].isin(geojson_names) &
            kihel_map["latitude"].notna() &
            kihel_map["longitude"].notna()
        ].copy()

        if geojson and not df_geo.empty:
            fig = px.choropleth_mapbox(
                df_geo,
                geojson=geojson,
                locations="kaardi_piirkond",
                featureidkey="properties.KIHELKOND",
                color="Fotode arv",
                color_continuous_scale="YlOrRd",
                hover_name="kaardi_piirkond",
                hover_data={"Fotode arv": True},
                mapbox_style="open-street-map",
                zoom=6,
                center={"lat": 58.7, "lon": 25.0},
                opacity=0.65,
                title="Vali piirkond alt detailvaateks",
            )
            fig = lisa_piirjooned(fig, geojson)
            fig = lisa_puuduvad_keskpunktid(fig, df_missing)
            fig.update_layout(height=480, margin={"r": 0, "t": 40, "l": 0, "b": 0})
            st.plotly_chart(fig, use_container_width=True)

        elif not kihel_map.empty and {"latitude", "longitude"}.issubset(kihel_map.columns):
            kihel_pts = kihel_map.dropna(subset=["latitude", "longitude"])
            if not kihel_pts.empty:
                fig = px.scatter_mapbox(
                    kihel_pts,
                    lat="latitude",
                    lon="longitude",
                    size="Fotode arv",
                    color="Fotode arv",
                    hover_name="kaardi_piirkond",
                    hover_data={"Fotode arv": True, "latitude": False, "longitude": False},
                    color_continuous_scale="YlOrRd",
                    size_max=45,
                    zoom=6,
                    center={"lat": 58.7, "lon": 25.0},
                    mapbox_style="open-street-map",
                    title="Vali piirkond alt detailvaateks",
                )
                fig.update_traces(text=kihel_pts["kaardi_piirkond"], textposition="top center")
                fig.update_layout(height=480, margin={"r": 0, "t": 40, "l": 0, "b": 0})
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Kaardi jaoks ei leitud piisavalt piirkonnaandmeid.")
        else:
            st.info("Kihelkonna geojsoni ega keskpunktiandmeid ei leitud piisavalt.")

        st.subheader("Piirkonna detailvaade")
        kihel_valikud = sorted(kihel_map["kaardi_piirkond"].dropna().astype(str).unique().tolist()) if not kihel_map.empty else []
        val_kihel = st.selectbox("Vali piirkond", ["—"] + kihel_valikud)

        if val_kihel != "—":
            df_kihel = df[df[kihel_veerg].astype(str) == val_kihel]
            k1, k2, k3 = st.columns(3)
            k1.metric("Fotosid", len(df_kihel))
            k2.metric(
                "Koordinaatidega",
                df_kihel["koordinaadid_leitud"].astype(str).eq("jah").sum() if "koordinaadid_leitud" in df_kihel.columns else 0
            )
            k3.metric(
                "Ajavahemik",
                (
                    f"{int(df_kihel['Aasta'].min()) if df_kihel['Aasta'].notna().any() else '?'}–"
                    f"{int(df_kihel['Aasta'].max()) if df_kihel['Aasta'].notna().any() else '?'}"
                ) if "Aasta" in df_kihel.columns else "?"
            )

            naita_fotopunkte(df_kihel, f"Fotod – {val_kihel}", load_geojson, lisa_asustus_piirid=True)

            col_kd1, col_kd2 = st.columns(2)
            with col_kd1:
                if "Fotograaf" in df_kihel.columns:
                    ft = df_kihel["Fotograaf"].value_counts().head(8).reset_index()
                    if len(ft.columns) == 2:
                        ft.columns = ["Fotograaf", "Arv"]
                    st.markdown("**Fotograafid**")
                    st.dataframe(ft, hide_index=True, use_container_width=True)

            with col_kd2:
                if not marksoned.empty and "PID" in marksoned.columns and "Märksõna" in marksoned.columns:
                    ms_kihel = (
                        marksoned[marksoned["PID"].isin(df_kihel["PID"])]["Märksõna"]
                        .value_counts().head(8).reset_index()
                    )
                    if len(ms_kihel.columns) == 2:
                        ms_kihel.columns = ["Märksõna", "Arv"]
                    st.markdown("**Top märksõnad**")
                    st.dataframe(ms_kihel, hide_index=True, use_container_width=True)

        puuduvad_nimed = []
        if not kihel_map.empty and {"latitude", "longitude"}.issubset(kihel_map.columns):
            puuduvad_nimed = (
                kihel_map[
                    kihel_map["latitude"].isna() | kihel_map["longitude"].isna()
                ]["kaardi_piirkond"]
                .dropna()
                .astype(str)
                .tolist()
            )

        if puuduvad_nimed:
            st.caption("⚠️ Keskpunkt puudub: " + ", ".join(sorted(puuduvad_nimed[:20])) + (" ..." if len(puuduvad_nimed) > 20 else ""))
        else:
            st.caption(f"ℹ️ Kaardil on {len(kihel_map)} piirkonda. Geojsonist puudu olevad piirkonnad lisatakse keskpunktmarkeritena.")


# ══════════════════ TAB 2 – STATISTIKA ═══════════════════════════════════════
with tab2:
    col_left, col_right = st.columns(2)

    with col_left:
        df_a2 = df[df["Aasta"].notna()].copy()
        if not df_a2.empty:
            df_a2["Aastakümme"] = (df_a2["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
            ak = df_a2["Aastakümme"].value_counts().sort_index()
            fig = px.bar(
                x=ak.index,
                y=ak.values,
                labels={"x": "Aastakümme", "y": "Fotode arv"},
                title="Fotod aastakümne kaupa",
                color=ak.values,
                color_continuous_scale="Blues",
            )
            fig.update_layout(coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

        if "kaardi_piirkond" in df.columns:
            kihel_top = (
                df[df["kaardi_piirkond"].notna() & ~df["kaardi_piirkond"].astype(str).str.lower().isin(["teadmata", "välismaa", "välismaa,"])]["kaardi_piirkond"]
                .value_counts().head(15)
            )
            if len(kihel_top) > 0:
                fig = px.bar(
                    x=kihel_top.values,
                    y=kihel_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Piirkond"},
                    title="Top 15 piirkonda",
                    color=kihel_top.values,
                    color_continuous_scale="Greens",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)

    with col_right:
        if "Žanr" in df.columns:
            zanr_c = df["Žanr"].value_counts().head(15).dropna()
            if len(zanr_c) > 0:
                fig = px.pie(values=zanr_c.values, names=zanr_c.index, title="Žanrite jaotus (top 15)")
                fig.update_traces(textposition="inside", textinfo="percent+label")
                st.plotly_chart(fig, use_container_width=True)

        if "Fotograaf" in df.columns:
            foto_top = df["Fotograaf"].value_counts().head(12).dropna()
            if len(foto_top) > 0:
                fig = px.bar(
                    x=foto_top.values,
                    y=foto_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Fotograaf"},
                    title="Top 12 fotograafi",
                    color=foto_top.values,
                    color_continuous_scale="Oranges",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)

    st.subheader("Projektid")
    if "Projekt" in df.columns:
        proj_c = df["Projekt"].value_counts().head(10).dropna()
        if len(proj_c) > 0:
            fig = px.bar(
                x=proj_c.values,
                y=proj_c.index,
                orientation="h",
                labels={"x": "Fotode arv", "y": "Projekt"},
                title="Top 10 projekti",
                color=proj_c.values,
                color_continuous_scale="Purples",
            )
            fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=350)
            st.plotly_chart(fig, use_container_width=True)


# ══════════════════ TAB 3 – MÄRKSÕNAD ════════════════════════════════════════
with tab3:
    st.subheader("Märksõnade analüüs")
    df_pids = set(df["PID"].dropna().unique()) if "PID" in df.columns else set()
    mf = marksoned[marksoned["PID"].isin(df_pids)] if not marksoned.empty and "PID" in marksoned.columns else pd.DataFrame()

    col_m1, col_m2 = st.columns(2)

    with col_m1:
        top_n = st.slider("Näita top N märksõna", 10, 50, 20)
        if not mf.empty and "Märksõna" in mf.columns:
            ms_c = mf["Märksõna"].value_counts().head(top_n)
            if len(ms_c) > 0:
                fig = px.bar(
                    x=ms_c.values,
                    y=ms_c.index,
                    orientation="h",
                    labels={"x": "Esinemiste arv", "y": "Märksõna"},
                    title=f"Top {top_n} märksõna",
                    color=ms_c.values,
                    color_continuous_scale="Teal",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                st.plotly_chart(fig, use_container_width=True)

    with col_m2:
        st.markdown("#### Märksõna ajaline trend")
        ms_in = st.text_input("Sisesta märksõna", value="portree")
        if ms_in and not mf.empty and "Märksõna" in mf.columns:
            ms_tr = mf[mf["Märksõna"].fillna("").astype(str).str.lower() == ms_in.lower()][["PID"]].copy()
            ms_tr = ms_tr.merge(df[["PID", "Aasta"]].drop_duplicates("PID"), on="PID", how="left")
            ms_tr = ms_tr[ms_tr["Aasta"].notna()]
            if len(ms_tr) > 0:
                ms_tr["Aastakümme"] = (ms_tr["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
                tc = ms_tr["Aastakümme"].value_counts().sort_index()
                fig = px.line(
                    x=tc.index,
                    y=tc.values,
                    markers=True,
                    labels={"x": "Aastakümme", "y": "Esinemiste arv"},
                    title=f"'{ms_in}' ajas",
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Selle märksõnaga aastaga fotosid ei leitud.")


# ══════════════════ TAB 4 – ISIKUD ═══════════════════════════════════════════
with tab4:
    st.subheader("Isikud fotodel")
    df_pids = set(df["PID"].dropna().unique()) if "PID" in df.columns else set()
    isikud_filtered = (
        isikud[isikud["PID"].isin(df_pids)]
        if not isikud.empty and "PID" in isikud.columns else pd.DataFrame()
    )

    col_i1, col_i2 = st.columns(2)

    with col_i1:
        top_isik_n = st.slider("Näita top N isikut", 10, 50, 20, key="isik_slider")
        if not isikud_filtered.empty and "Isik" in isikud_filtered.columns:
            isik_top = isikud_filtered["Isik"].value_counts().head(top_isik_n)
            if len(isik_top) > 0:
                fig = px.bar(
                    x=isik_top.values,
                    y=isik_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Isik"},
                    title=f"Top {top_isik_n} isikut fotodel",
                    color=isik_top.values,
                    color_continuous_scale="Magenta",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                st.plotly_chart(fig, use_container_width=True)

    with col_i2:
        st.markdown("#### Isiku otsing")
        isik_otsing = st.text_input("Otsi isiku nime järgi")
        if isik_otsing and not isikud_filtered.empty and "Isik" in isikud_filtered.columns:
            isik_matches = isikud_filtered[
                isikud_filtered["Isik"].fillna("").astype(str).str.contains(isik_otsing, case=False, na=False)
            ]
            df_isik = df[df["PID"].isin(isik_matches["PID"].unique())]
            st.markdown(f"Leitud **{len(df_isik)}** fotot isikuga '{isik_otsing}'")
            if len(df_isik) > 0:
                cols = [c for c in ["PID", "Aasta", "Kihelkond", "kaardi_piirkond", "Sisu kirjeldus", "failinimi"] if c in df_isik.columns]
                st.dataframe(df_isik[cols].head(50), use_container_width=True, hide_index=True)
        else:
            st.markdown("#### Isikute arv fotol")
            if "Isikute arv" in df.columns:
                isikute_arv = df["Isikute arv"].value_counts().sort_index().head(10)
                if len(isikute_arv) > 0:
                    fig2 = px.bar(
                        x=isikute_arv.index.astype(str),
                        y=isikute_arv.values,
                        labels={"x": "Isikute arv fotol", "y": "Fotode arv"},
                        title="Kui palju isikuid on fotodel?",
                        color=isikute_arv.values,
                        color_continuous_scale="Teal",
                    )
                    fig2.update_layout(coloraxis_showscale=False)
                    st.plotly_chart(fig2, use_container_width=True)


# ══════════════════ TAB 5 – ML MÄRKSÕNAD ═════════════════════════════════════
with tab5:
    st.subheader("🤖 ML märksõnad")

    st.markdown("""
    CLIP ei „näe” sinu märksõnade nimekirja otse. Ta võrdleb pilti tekstikirjeldustega
    ja arvutab, milline tekstiline kategooria sobib pildiga kõige paremini.

    Siin saab võrrelda:
    - sinu olemasolevaid käsitsi loodud märksõna-kategooriaid;
    - CLIP-i pakutud top 1–3 kategooriat;
    - pildi pealkirja / sisukirjeldust.
    """)

    img_path = os.path.join(BASE_DIR, "clip_yhe_pildi_selgitus.png")
    if os.path.exists(img_path):
        st.image(img_path, caption="Näide: kuidas CLIP pildi ja tekstikategooriate sobivust hindab", use_container_width=True)
    else:
        st.info("Näidispilti 'clip_yhe_pildi_selgitus.png' ei leitud rakenduse kaustast.")

    st.divider()

    ml_df = df.copy()
    has_clip = "pred_top1" in ml_df.columns and ml_df["pred_top1"].notna().any()
    has_manual = "Märksõna kategooria" in ml_df.columns and ml_df["Märksõna kategooria"].notna().any()

    if not has_clip and not has_manual:
        st.warning("ML märksõnade infot ei leitud. Kontrolli, et failid 'ERA_märksõnad_ML.xlsx' ja 'era_clip_KOIK_pildid_sigmoid.xlsx' oleksid rakenduse kaustas.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fotosid filtris", f"{len(ml_df):,}")
        c2.metric("CLIP ennustusega", f"{ml_df['pred_top1'].notna().sum():,}" if "pred_top1" in ml_df.columns else "0")
        c3.metric("Käsitsi kategooriaga", f"{ml_df['Märksõna kategooria'].notna().sum():,}" if "Märksõna kategooria" in ml_df.columns else "0")
        st.caption("Need arvud ei peagi olema samad: CLIP ennustused on ainult hindamiseks käsitsi kategooriaga piltidel, ülejäänud fotodel võib olla originaalmärksõnu, aga mitte 19-kategooria käsitsi klastrit.")
        c4.metric(
            "Keskmine top1–top2 vahe",
            f"{ml_df['confidence_margin_top1_top2'].mean():.3f}" if "confidence_margin_top1_top2" in ml_df.columns and ml_df["confidence_margin_top1_top2"].notna().any() else "?"
        )

        st.markdown("### Käsitsi kategooriad vs CLIP pakkumised")

        col_a, col_b = st.columns(2)

        with col_a:
            if has_manual:
                manual_counts = split_categories(ml_df["Märksõna kategooria"]).value_counts().head(20)

                if len(manual_counts) > 0:
                    fig = px.bar(
                        x=manual_counts.values,
                        y=manual_counts.index,
                        orientation="h",
                        labels={"x": "Fotode arv", "y": "Käsitsi kategooria"},
                        title="Sinu märksõna-kategooriad",
                        color=manual_counts.values,
                        color_continuous_scale="Blues",
                    )
                    fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Käsitsi märksõna-kategooriaid ei leitud.")

        with col_b:
            if has_clip:
                clip_counts = ml_df["pred_top1"].dropna().astype(str).value_counts().head(20)

                if len(clip_counts) > 0:
                    fig = px.bar(
                        x=clip_counts.values,
                        y=clip_counts.index,
                        orientation="h",
                        labels={"x": "Fotode arv", "y": "CLIP top 1"},
                        title="CLIP top 1 kategooriad",
                        color=clip_counts.values,
                        color_continuous_scale="Oranges",
                    )
                    fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("CLIP ennustusi ei leitud.")

        if has_clip and has_manual:
            st.markdown("### Kui tihti CLIP kattub käsitsi kategooriaga?")
            st.warning("Kui kattuvus on siin väga madal, siis kõige tõenäolisem põhjus ei ole mudel, vaid kategoorianimede/andmeveeru sobimatus: CLIP-i `pred_top1` peab olema täpselt samas 19 kategooria sõnastikus nagu käsitsi `Märksõna kategooria`.")

            eval_df = ml_df[ml_df["pred_top1"].notna() & ml_df["Märksõna kategooria"].notna()].copy()

            if not eval_df.empty:
                eval_df["top1_kattub"] = eval_df.apply(lambda r: category_match(r, pred_cols=["pred_top1"]), axis=1)
                eval_df["top3_kattub"] = eval_df.apply(lambda r: category_match(r, pred_cols=["pred_top1", "pred_top2", "pred_top3"]), axis=1)
                eval_df["top5_kattub"] = eval_df.apply(lambda r: category_match(r, pred_cols=["pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5"]), axis=1)

                m1, m2, m3 = st.columns(3)
                m1.metric("Top1 kattuvus", f"{eval_df['top1_kattub'].mean() * 100:.1f}%")
                m2.metric("Top3 kattuvus", f"{eval_df['top3_kattub'].mean() * 100:.1f}%")
                m3.metric("Top5 kattuvus", f"{eval_df['top5_kattub'].mean() * 100:.1f}%")

                with st.expander("Kontrolli kategoorianimede kattumist"):
                    manual_set = sorted(split_categories(eval_df["Märksõna kategooria"]).dropna().astype(str).unique().tolist())
                    pred_set = sorted(pd.concat([
                        eval_df[c].dropna().astype(str) for c in ["pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5"] if c in eval_df.columns
                    ]).dropna().astype(str).unique().tolist())
                    col_x, col_y = st.columns(2)
                    with col_x:
                        st.markdown("**Käsitsi kategooriad**")
                        st.write(manual_set)
                    with col_y:
                        st.markdown("**CLIP kategooriad**")
                        st.write(pred_set)

                match_counts = eval_df["top3_kattub"].map({True: "Top3 seas kattub", False: "Top3 seas ei kattu"}).value_counts()
                fig = px.pie(
                    values=match_counts.values,
                    names=match_counts.index,
                    title="CLIP top3 vs käsitsi kategooria"
                )
                st.plotly_chart(fig, use_container_width=True)

        if has_clip:
            st.markdown("### ML skooride tõlgendus")
            score_cols = [c for c in ["pred_top1_score", "confidence_margin_top1_top2", "ML top3 koondskoor", "ML top5 koondskoor"] if c in ml_df.columns]
            if score_cols:
                st.caption("Top1 skoor üksi ei ole väga hea kvaliteedimõõdik. Praktilisem on vaadata koos top1–top2 vahet ning seda, kas õige/sobiv kategooria ilmub top3 või top5 hulka.")
                score_summary = ml_df[score_cols].describe().T.reset_index().rename(columns={"index": "skoor"})
                st.dataframe(score_summary, use_container_width=True, hide_index=True)

        st.markdown("### Vaata üksikuid fotosid")

        otsing_ml = st.text_input("Otsi PID, failinime või pealkirja järgi", key="ml_otsing")

        ml_show = ml_df.copy()

        if otsing_ml:
            mask = pd.Series(False, index=ml_show.index)

            for col in ["PID", "failinimi", "Sisu kirjeldus"]:
                if col in ml_show.columns:
                    mask = mask | ml_show[col].fillna("").astype(str).str.contains(otsing_ml, case=False, na=False)

            ml_show = ml_show[mask]

        if has_clip and has_manual:
            ainult_erinevad = st.checkbox("Näita ainult ridu, kus CLIP top3 ei ole käsitsi kategooriate hulgas")

            if ainult_erinevad:
                ml_show = ml_show[
                    ~ml_show.apply(
                        lambda r: category_match(r, pred_cols=["pred_top1", "pred_top2", "pred_top3"]),
                        axis=1
                    )
                ]

        cols_ml = [
            c for c in [
                "PID",
                "failinimi",
                "Sisu kirjeldus",
                "Märksõna kategooria",
                "Originaal märksõnad",
                "pred_top1",
                "pred_top2",
                "pred_top3",
                "pred_top4",
                "pred_top5",
                "pred_top1_score",
                "confidence_margin_top1_top2",
                "ML top3 koondskoor",
                "ML top5 koondskoor",
                "ML otsuse tugevus",
            ] if c in ml_show.columns
        ]

        st.markdown(f"Näidatakse **{len(ml_show):,}** rida")

        st.dataframe(
            ml_show[cols_ml].head(500),
            use_container_width=True,
            hide_index=True,
            height=420
        )

        csv_ml = ml_show[cols_ml].to_csv(index=False).encode("utf-8")

        st.download_button(
            "⬇️ Lae ML võrdlustabel alla CSV-na",
            data=csv_ml,
            file_name="era_ml_marksonad_vordlus.csv",
            mime="text/csv"
        )


# ══════════════════ TAB 6 – ANDMETABEL ═══════════════════════════════════════
with tab6:
    st.subheader("Andmetabel")
    vaikimisi = [
        c for c in [
            "PID", "Aasta", "Kihelkond", "kaardi_piirkond", "Fotograaf",
            "Žanr", "Märksõna kategooria", "pred_top1",
            "Sisu kirjeldus", "ERA märksõnad (koondatud)", "failinimi"
        ] if c in df.columns
    ]

    show_cols = st.multiselect(
        "Vali kuvatavad veerud",
        options=list(df.columns),
        default=vaikimisi
    )

    otsing = st.text_input("🔍 Otsi (sisu, kihelkond, fotograaf)")
    df_show = df.copy()

    if otsing:
        mask = pd.Series(False, index=df_show.index)

        if "Sisu kirjeldus" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Sisu kirjeldus"], otsing)
        if "Kihelkond" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Kihelkond"], otsing)
        if "kaardi_piirkond" in df_show.columns:
            mask = mask | safe_str_contains(df_show["kaardi_piirkond"], otsing)
        if "Fotograaf" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Fotograaf"], otsing)
        if "Märksõna kategooria" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Märksõna kategooria"], otsing)
        if "pred_top1" in df_show.columns:
            mask = mask | safe_str_contains(df_show["pred_top1"], otsing)

        df_show = df_show[mask]

    st.markdown(f"Näidatakse **{len(df_show):,}** rida")

    if show_cols:
        st.dataframe(df_show[show_cols].head(500), use_container_width=True, height=420)

        if len(df_show) > 500:
            st.caption("ℹ️ Tabelis on esimesed 500 rida. Kitsenda filtritega.")

        csv = df_show[show_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Lae alla CSV",
            data=csv,
            file_name="era_fotod_filteeritud.csv",
            mime="text/csv"
        )
    else:
        st.info("Vali vähemalt üks veerg.")
            if wanted in normalize_filename_for_match(fname):
                return os.path.join(BASE_DIR, fname)

    return None


def read_first_existing_sheet(path, preferred_sheets, required_cols=None):
    if not path or not os.path.exists(path):
        return pd.DataFrame()

    xl = pd.ExcelFile(path)

    for sheet in preferred_sheets:
        if sheet in xl.sheet_names:
            df = xl.parse(sheet)
            if required_cols is None or any(c in df.columns for c in required_cols):
                return df

    if required_cols:
        for sheet in xl.sheet_names:
            df = xl.parse(sheet)
            if any(c in df.columns for c in required_cols):
                return df

    return pd.DataFrame()


def ensure_column(df, col, default=pd.NA):
    if col not in df.columns:
        df[col] = default
    return df


def safe_str_contains(series, text):
    return series.fillna("").astype(str).str.contains(text, case=False, na=False)


def clean_region_name(x):
    if pd.isna(x):
        return pd.NA
    x = str(x).strip()
    if not x:
        return pd.NA
    return x


def normalize_place_name(x):
    if pd.isna(x):
        return pd.NA

    x = str(x).strip()
    if not x:
        return pd.NA

    xl = x.lower()

    tallinn_variants = {
        "tallinn", "tallinna linn", "tallinn linn"
    }
    tartu_variants = {
        "tartu", "tartu linn", "tartu linn."
    }
    petseri_variants = {
        "petserimaa", "petseri"
    }
    setu_variants = {
        "setumaa", "setomaa", "setu ala"
    }

    if xl in tallinn_variants:
        return "Tallinn"
    if xl in tartu_variants:
        return "Tartu"
    if xl in petseri_variants:
        return "Petserimaa"
    if xl in setu_variants:
        return "Setumaa"

    return x


def extract_geojson_feature_names(geojson, prop_name="KIHELKOND"):
    names = set()
    if not geojson or "features" not in geojson:
        return names

    for feature in geojson["features"]:
        props = feature.get("properties", {})
        val = props.get(prop_name)
        if val is not None and str(val).strip():
            names.add(str(val).strip())

    return names


def extract_polygon_rings(geom):
    if not geom or "type" not in geom or "coordinates" not in geom:
        return []

    coords = geom["coordinates"]
    rings = []

    try:
        if geom["type"] == "Polygon":
            if coords and len(coords) > 0 and coords[0]:
                rings.append(coords[0])

        elif geom["type"] == "MultiPolygon":
            for poly in coords:
                if poly and len(poly) > 0 and poly[0]:
                    rings.append(poly[0])

    except Exception:
        return []

    return rings


def lisa_piirjooned(fig, geojson, color="black", width=1):
    if not geojson or "features" not in geojson:
        return fig

    for feature in geojson["features"]:
        geom = feature.get("geometry", {})
        coords_list = extract_polygon_rings(geom)

        for coords in coords_list:
            if not coords or len(coords) < 2:
                continue

            try:
                lons = [c[0] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                lats = [c[1] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                if len(lons) < 2 or len(lats) < 2:
                    continue

                fig.add_trace(go.Scattermapbox(
                    lon=lons,
                    lat=lats,
                    mode="lines",
                    line=dict(color=color, width=width),
                    hoverinfo="skip",
                    showlegend=False,
                ))
            except Exception:
                continue

    return fig


def lisa_puuduvad_keskpunktid(fig, df_missing):
    if df_missing is None or df_missing.empty:
        return fig

    sizes = df_missing["Fotode arv"].clip(lower=8, upper=40)

    hover_text = (
        df_missing["kaardi_piirkond"].astype(str)
        + "<br>Fotode arv: " + df_missing["Fotode arv"].astype(str)
    )

    fig.add_trace(go.Scattermapbox(
        lat=df_missing["latitude"],
        lon=df_missing["longitude"],
        mode="markers+text",
        text=df_missing["kaardi_piirkond"],
        textposition="top center",
        marker=dict(size=sizes, opacity=0.85),
        hovertext=hover_text,
        hoverinfo="text",
        name="Puuduvad piirkonnad",
        showlegend=False,
    ))

    return fig


def split_categories(series):
    """Jagab kategooriavälja päris üksikkategooriateks.
    Talub koma, semikoolonit ja püstkriipsu. Nii ei teki filtrisse 100 eri kombinatsiooni.
    """
    if series is None or len(series) == 0:
        return pd.Series(dtype="object")

    return (
        series
        .dropna()
        .astype(str)
        .str.replace(";", ",", regex=False)
        .str.replace("|", ",", regex=False)
        .str.split(",")
        .explode()
        .str.strip()
        .replace("", pd.NA)
        .dropna()
    )


def filter_by_comma_categories(df, col, selected):
    if not selected or col not in df.columns:
        return df

    selected_lower = {str(x).strip().lower() for x in selected if str(x).strip()}

    def has_any_category(value):
        text = str(value).replace(";", ",").replace("|", ",")
        cats = [c.strip().lower() for c in text.split(",") if c.strip()]
        return any(c in selected_lower for c in cats)

    return df[df[col].fillna("").apply(has_any_category)]


def keyword_category_map_from_ml(ml_marksonad):
    """Tagastab tabeli: Märksõna -> Märksõna kategooria.
    Vajalik selleks, et kasutaja saaks valida nt kategooria 'inimene'
    ja selle all näha ainult selle kategooria päris märksõnu.
    """
    if ml_marksonad is None or ml_marksonad.empty:
        return pd.DataFrame(columns=["Märksõna", "Märksõna kategooria"])

    df_map = ml_marksonad.copy()
    df_map.columns = df_map.columns.astype(str).str.strip()

    # Kõige tõenäolisem vorm: pikk mapping märksõna -> klaster/kategooria.
    keyword_col = None
    for c in ["Märksõna", "märksõna", "marksona", "keyword"]:
        if c in df_map.columns:
            keyword_col = c
            break

    category_col = None
    for c in ["Märksõna2", "märksõna2", "klaster", "klastrid", "Märksõna kategooria", "kategooria"]:
        if c in df_map.columns:
            category_col = c
            break

    if keyword_col and category_col:
        out = df_map[[keyword_col, category_col]].copy()
        out.columns = ["Märksõna", "Märksõna kategooria"]
        out = out.dropna()
        out["Märksõna"] = out["Märksõna"].astype(str).str.strip()
        out["Märksõna kategooria"] = out["Märksõna kategooria"].astype(str).str.strip()
        out = out[(out["Märksõna"] != "") & (out["Märksõna kategooria"] != "")]
        return out.drop_duplicates()

    return pd.DataFrame(columns=["Märksõna", "Märksõna kategooria"])


def category_match(row, manual_col="Märksõna kategooria", pred_cols=None):
    if pred_cols is None:
        pred_cols = ["pred_top1", "pred_top2", "pred_top3"]

    manual = [c.strip().lower() for c in str(row.get(manual_col, "")).split(",") if c.strip()]
    preds = [str(row.get(c, "")).strip().lower() for c in pred_cols if str(row.get(c, "")).strip() and str(row.get(c, "")).lower() != "nan"]

    if not manual or not preds:
        return False

    return any(p in manual for p in preds)


def add_ml_strength_columns(df):
    """Lisab ML-i tõlgendamiseks paremad koondväljad kui ainult pred_top1_score."""
    score_cols_top3 = [c for c in ["pred_top1_score", "pred_top2_score", "pred_top3_score"] if c in df.columns]
    score_cols_top5 = [c for c in ["pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score"] if c in df.columns]

    for col in score_cols_top5 + ["confidence_margin_top1_top2"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if score_cols_top3:
        df["ML top3 koondskoor"] = df[score_cols_top3].sum(axis=1, min_count=1)

    if score_cols_top5:
        df["ML top5 koondskoor"] = df[score_cols_top5].sum(axis=1, min_count=1)

    if "confidence_margin_top1_top2" not in df.columns and {"pred_top1_score", "pred_top2_score"}.issubset(df.columns):
        df["confidence_margin_top1_top2"] = df["pred_top1_score"] - df["pred_top2_score"]

    if "pred_top1_score" in df.columns and "confidence_margin_top1_top2" in df.columns:
        def strength(row):
            top1 = row.get("pred_top1_score")
            margin = row.get("confidence_margin_top1_top2")
            if pd.isna(top1) or pd.isna(margin):
                return pd.NA
            if top1 >= 0.565 and margin >= 0.020:
                return "tugev"
            if top1 >= 0.555 and margin >= 0.010:
                return "keskmine"
            return "nõrk / kontrolli üle"

        df["ML otsuse tugevus"] = df.apply(strength, axis=1)
        df["ML kindlus"] = df["ML otsuse tugevus"]

    return df


def get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik,
    valitud_zanr,
    valitud_marksona,
    marksona_loogika,
    valitud_fotograaf,
    valitud_isik,
    valitud_marksona_kategooria=None
):
    df = fotod.copy()

    if "Aasta" in df.columns and df["Aasta"].notna().any():
        df_a = df[df["Aasta"].notna()].copy()
        df_a = df_a[df_a["Aasta"].between(aasta_vahemik[0], aasta_vahemik[1])]
        df = pd.concat([df_a, df[df["Aasta"].isna()]], ignore_index=True)

    if valitud_zanr and "Žanr" in df.columns:
        df = df[df["Žanr"].isin(valitud_zanr)]

    if valitud_fotograaf and "Fotograaf" in df.columns:
        df = df[df["Fotograaf"].isin(valitud_fotograaf)]

    if valitud_marksona and not marksoned.empty and "Märksõna" in marksoned.columns:
        if marksona_loogika == "JA – kõik korraga":
            pids = None
            for ms in valitud_marksona:
                ms_pids = set(marksoned[marksoned["Märksõna"] == ms]["PID"].dropna().unique())
                pids = ms_pids if pids is None else pids & ms_pids
            pids = pids or set()
        else:
            pids = set(marksoned[marksoned["Märksõna"].isin(valitud_marksona)]["PID"].dropna().unique())

        df = df[df["PID"].isin(pids)]

    if valitud_marksona_kategooria:
        df = filter_by_comma_categories(df, "Märksõna kategooria", valitud_marksona_kategooria)

    if valitud_isik and not isikud.empty and "Isik" in isikud.columns:
        isik_pids = set(isikud[isikud["Isik"].isin(valitud_isik)]["PID"].dropna().unique())
        df = df[df["PID"].isin(isik_pids)]

    return df


def get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    valitud_zanr,
    valitud_marksona,
    marksona_loogika,
    valitud_fotograaf,
    valitud_isik,
    valitud_marksona_kategooria=None
):
    # ŽANR: kõik muud filtrid peal, žanr ise maas
    df_for_zanr = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        [],
        valitud_marksona, marksona_loogika,
        valitud_fotograaf, valitud_isik,
        valitud_marksona_kategooria
    )
    zanr_opts = sorted(
        df_for_zanr["Žanr"].dropna().astype(str).unique().tolist()
    ) if "Žanr" in df_for_zanr.columns else []

    # MÄRKSÕNA: kõik muud filtrid peal, märksõna ise maas
    df_for_ms = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        [],
        marksona_loogika,
        valitud_fotograaf, valitud_isik,
        valitud_marksona_kategooria
    )
    pids_ms = set(df_for_ms["PID"].dropna().unique()) if "PID" in df_for_ms.columns else set()

    if not marksoned.empty and "PID" in marksoned.columns and "Märksõna" in marksoned.columns:
        ms_source = marksoned[marksoned["PID"].isin(pids_ms)].copy()

        # Kui kategooria on valitud, näita märksõna filtris ainult selle kategooria märksõnu.
        if valitud_marksona_kategooria and "Märksõna kategooria" in ms_source.columns:
            ms_source = ms_source[ms_source["Märksõna kategooria"].isin(valitud_marksona_kategooria)]

        ms_opts = ms_source["Märksõna"].dropna().astype(str).value_counts().index.tolist()
    else:
        ms_opts = []

    # MÄRKSÕNA KATEGOORIA: kõik muud filtrid peal, kategooria ise maas
    df_for_mk = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        valitud_marksona, marksona_loogika,
        valitud_fotograaf, valitud_isik,
        []
    )
    if not marksoned.empty and "Märksõna kategooria" in marksoned.columns:
        pids_mk = set(df_for_mk["PID"].dropna().unique()) if "PID" in df_for_mk.columns else set()
        mk_source = marksoned[marksoned["PID"].isin(pids_mk)].copy()
        mk_opts = sorted(mk_source["Märksõna kategooria"].dropna().astype(str).str.strip().unique().tolist())
    else:
        mk_opts = sorted(split_categories(df_for_mk["Märksõna kategooria"]).unique().tolist()) if "Märksõna kategooria" in df_for_mk.columns else []

    # FOTOGRAAF: kõik muud filtrid peal, fotograaf ise maas
    df_for_ft = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        valitud_marksona, marksona_loogika,
        [],
        valitud_isik,
        valitud_marksona_kategooria
    )
    ft_opts = sorted(
        df_for_ft["Fotograaf"].dropna().astype(str).unique().tolist()
    ) if "Fotograaf" in df_for_ft.columns else []

    # ISIK: kõik muud filtrid peal, isik ise maas
    df_for_isik = get_filtered_df(
        fotod, marksoned, isikud,
        aasta_vahemik,
        valitud_zanr,
        valitud_marksona, marksona_loogika,
        valitud_fotograaf, [],
        valitud_marksona_kategooria
    )
    pids_isik = set(df_for_isik["PID"].dropna().unique()) if "PID" in df_for_isik.columns else set()
    isik_opts = (
        isikud[isikud["PID"].isin(pids_isik)]["Isik"]
        .dropna().astype(str).value_counts().index.tolist()
        if not isikud.empty and "PID" in isikud.columns and "Isik" in isikud.columns
        else []
    )

    return zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts


def sanitize_state_list(key, allowed_options, max_n=3):
    current = st.session_state.get(key, [])
    if current is None:
        current = []
    current = [x for x in current if x in allowed_options][:max_n]
    st.session_state[key] = current


def naita_fotopunkte(df_piirkond, pealkiri, load_geojson_func, lisa_asustus_piirid=False):
    required = ["latitude", "longitude"]
    for col in required:
        if col not in df_piirkond.columns:
            st.info("Koordinaadiveerud puuduvad.")
            return

    df_pts = df_piirkond[
        df_piirkond["latitude"].notna() &
        df_piirkond["longitude"].notna()
    ].copy()

    if df_pts.empty:
        st.info("Valitud piirkonnas koordinaatidega fotosid ei ole.")
        return

    hover_data = {
        "Aasta": "Aasta" in df_pts.columns,
        "Kihelkond": "Kihelkond" in df_pts.columns,
        "Fotograaf": "Fotograaf" in df_pts.columns,
        "latitude": False,
        "longitude": False,
    }

    color_col = "lõplik_täpsus" if "lõplik_täpsus" in df_pts.columns else None

    fig = px.scatter_mapbox(
        df_pts,
        lat="latitude",
        lon="longitude",
        hover_name="Sisu kirjeldus" if "Sisu kirjeldus" in df_pts.columns else None,
        hover_data=hover_data,
        color=color_col,
        mapbox_style="open-street-map",
        title=pealkiri,
        zoom=9,
        center={
            "lat": df_pts["latitude"].mean(),
            "lon": df_pts["longitude"].mean(),
        },
    )

    fig.update_traces(marker=dict(size=9, opacity=0.8))

    if lisa_asustus_piirid:
        geojson_ay = load_geojson_func("asustusyksus.geojson")
        if isinstance(geojson_ay, dict) and "features" in geojson_ay:
            lat_min = df_pts["latitude"].min() - 0.1
            lat_max = df_pts["latitude"].max() + 0.1
            lon_min = df_pts["longitude"].min() - 0.1
            lon_max = df_pts["longitude"].max() + 0.1

            for feature in geojson_ay["features"]:
                geom = feature.get("geometry", {})
                coords_list = extract_polygon_rings(geom)

                for coords in coords_list:
                    if not coords or len(coords) < 2:
                        continue

                    try:
                        lons = [c[0] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                        lats = [c[1] for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
                        if len(lons) < 2 or len(lats) < 2:
                            continue

                        if (min(lons) < lon_max and max(lons) > lon_min and
                                min(lats) < lat_max and max(lats) > lat_min):
                            fig.add_trace(go.Scattermapbox(
                                lon=lons,
                                lat=lats,
                                mode="lines",
                                line=dict(color="rgba(80,80,80,0.5)", width=0.8),
                                hoverinfo="skip",
                                showlegend=False,
                            ))
                    except Exception:
                        continue

    fig.update_layout(height=500, margin={"r": 0, "t": 40, "l": 0, "b": 0})
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Koordinaatidega fotosid: {len(df_pts)}")


# ── Andmete laadimine ────────────────────────────────────────────────────────

@st.cache_data
def load_data():
    xlsx_path = None
    for fname in ["ERA_fotod_250426.xlsx"]:
        path = os.path.join(BASE_DIR, fname)
        if os.path.exists(path):
            xlsx_path = path
            break

    if xlsx_path is None:
        raise FileNotFoundError("Ühtegi Exceli faili ei leitud kaustast.")

    xl = pd.ExcelFile(xlsx_path)

    fotod = safe_sheet_parse(xl, "fotod_koordinaatidega")
    master = safe_sheet_parse(xl, "fotod_master")
    marksoned = safe_sheet_parse(xl, "märksõnad_pikk")
    isikud = safe_sheet_parse(xl, "isikud_fotol_pikk")
    kihelkonnad_kp = safe_sheet_parse(xl, "kihelkond_keskpunktid")

    # ML lisafailid
    ml_marksonad = pd.DataFrame()
    ml_clip = pd.DataFrame()

    ml_marksonad_candidates = [
        "ERA_märksõnad_ML.xlsx",
        "ERA_märksõnad_ML.xlsx",
        "ERA_marksonad_ML.xlsx",
    ]
    ml_clip_candidates = [
        "era_clip_KOIK_pildid_sigmoid.xlsx",
    ]

    ml_marksonad_path = find_existing_file(ml_marksonad_candidates, fallback_contains="marksonadml")
    ml_clip_path = find_existing_file(ml_clip_candidates, fallback_contains="clipkoikpildidsigmoid")

    ml_marksonad = read_first_existing_sheet(
        ml_marksonad_path,
        preferred_sheets=["märksõnad_pikk", "ml_foto_klastrid", "ml_multihot_klastrid"],
        required_cols=["Märksõna2", "klastrid"]
    )

    marksona_kategooriad_map = keyword_category_map_from_ml(ml_marksonad)

    ml_clip = read_first_existing_sheet(
        ml_clip_path,
        preferred_sheets=["predictions_all", "predictions_eval_only", "sample_all"],
        required_cols=["pred_top1", "true_clusters"]
    )

    if fotod.empty:
        raise ValueError("Sheet 'fotod_koordinaatidega' puudub või on tühi.")

    # puhasta veerunimed ja PID-id
    for d in [fotod, master, marksoned, isikud, kihelkonnad_kp, ml_marksonad, ml_clip]:
        if not d.empty:
            d.columns = d.columns.astype(str).str.strip()
            if "PID" in d.columns:
                d["PID"] = d["PID"].fillna("").astype(str).str.strip()
            if "failinimi" in d.columns:
                d["failinimi"] = d["failinimi"].fillna("").astype(str).str.strip()

    # ühtlusta koordinaadiveerud fotode tabelis
    fotod = fotod.rename(columns={
        "Latitude": "latitude",
        "Longitude": "longitude",
        "lat": "latitude",
        "lon": "longitude",
        "long": "longitude",
        "lõplik_latitude": "latitude",
        "lõplik_longitude": "longitude",
    })

    # ühtlasta keskpunktide tabelis
    kihelkonnad_kp = kihelkonnad_kp.rename(columns={
        "Latitude": "latitude",
        "Longitude": "longitude",
        "lat": "latitude",
        "lon": "longitude",
        "long": "longitude",
    })

    # kui Aasta / Žanr jm on masteris, too need PID järgi juurde
    if not master.empty and "PID" in master.columns and "PID" in fotod.columns:
        master = master.rename(columns={
            "Zanr": "Žanr",
            "zanr": "Žanr",
            "žanr": "Žanr",
            "aasta": "Aasta",
        })

        juurde = [
            "PID", "Aasta", "Žanr", "Sisu kirjeldus", "failinimi",
            "Projekt", "ERA märksõnad (koondatud)", "Isikute arv"
        ]
        juurde = [c for c in juurde if c in master.columns]

        # ära tee topeltveerge, kui need juba fotod tabelis olemas ja täidetud
        for c in juurde:
            if c != "PID" and c in fotod.columns:
                fotod = fotod.drop(columns=[c])

        fotod = fotod.merge(
            master[juurde].drop_duplicates(subset=["PID"]),
            on="PID",
            how="left"
        )

    # sinu käsitsi loodud 19 märksõna-kategooriat
    if not ml_marksonad.empty and "PID" in ml_marksonad.columns and "PID" in fotod.columns:
        # Kui kogemata loeti pikem märksõnade leht, koonda see ise PID-tasemele.
        if "klastrid" not in ml_marksonad.columns and "Märksõna2" in ml_marksonad.columns:
            agg_dict = {
                "Märksõna2": lambda s: ", ".join(sorted(set(x for x in s.dropna().astype(str).str.strip() if x))),
            }
            if "Märksõna" in ml_marksonad.columns:
                agg_dict["Märksõna"] = lambda s: ", ".join(sorted(set(x for x in s.dropna().astype(str).str.strip() if x)))

            ml_marksonad = ml_marksonad.groupby("PID", as_index=False).agg(agg_dict)
            ml_marksonad = ml_marksonad.rename(columns={"Märksõna2": "klastrid", "Märksõna": "märksõnad"})
            ml_marksonad["klastrite_arv"] = ml_marksonad["klastrid"].fillna("").apply(lambda x: len([c for c in str(x).split(",") if c.strip()]))
            if "märksõnad" in ml_marksonad.columns:
                ml_marksonad["märksõnade_arv"] = ml_marksonad["märksõnad"].fillna("").apply(lambda x: len([c for c in str(x).split(",") if c.strip()]))

        ml_cols = [c for c in ["PID", "klastrid", "klastrite_arv", "märksõnad", "märksõnade_arv"] if c in ml_marksonad.columns]

        # eemalda varasemad sama info veerud, kui äpp jookseb cache järel või põhitabelis on need juba olemas
        for c in ["Märksõna kategooria", "Märksõna kategooriate arv", "Originaal märksõnad", "Originaal märksõnade arv"]:
            if c in fotod.columns:
                fotod = fotod.drop(columns=[c])

        fotod = fotod.merge(
            ml_marksonad[ml_cols].drop_duplicates(subset=["PID"]),
            on="PID",
            how="left"
        )

        fotod = fotod.rename(columns={
            "klastrid": "Märksõna kategooria",
            "klastrite_arv": "Märksõna kategooriate arv",
            "märksõnad": "Originaal märksõnad",
            "märksõnade_arv": "Originaal märksõnade arv",
        })

    # CLIP masinennustused
    if not ml_clip.empty and "PID" in ml_clip.columns and "PID" in fotod.columns:
        clip_cols = [
            "PID", "pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5",
            "pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score",
            "confidence_margin_top1_top2",
            "true_clusters", "hit_top1", "hit_any_top3", "hit_any_top5"
        ]
        clip_cols = [c for c in clip_cols if c in ml_clip.columns]

        for c in clip_cols:
            if c != "PID" and c in fotod.columns:
                fotod = fotod.drop(columns=[c])

        fotod = fotod.merge(
            ml_clip[clip_cols].drop_duplicates(subset=["PID"]),
            on="PID",
            how="left"
        )

        fotod = add_ml_strength_columns(fotod)

    for col in [
        "PID", "Aasta", "Žanr", "Kihelkond", "Sisu kirjeldus", "failinimi",
        "koordinaadid_leitud",
        "latitude", "longitude",
        "Projekt", "ERA märksõnad (koondatud)", "Isikute arv",
        "kihelkond_kaart", "Kihelkond või linn",
        "Märksõna kategooria", "Märksõna kategooriate arv",
        "Originaal märksõnad", "Originaal märksõnade arv",
        "pred_top1", "pred_top2", "pred_top3",
        "pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5",
        "pred_top1_score", "pred_top2_score", "pred_top3_score", "pred_top4_score", "pred_top5_score",
        "confidence_margin_top1_top2", "ML top3 koondskoor", "ML top5 koondskoor",
        "true_clusters", "hit_top1", "hit_any_top3", "hit_any_top5", "ML kindlus", "ML otsuse tugevus"
    ]:
        ensure_column(fotod, col)

    for col in ["PID", "Märksõna"]:
        ensure_column(marksoned, col)

    # Lisa tavalistele märksõnadele juurde nende 19 kategooria mapping.
    # See võimaldab sidebaris: 1) valida kategooria, 2) valida selle kategooria all ainult vastavaid märksõnu.
    if not marksona_kategooriad_map.empty and "Märksõna" in marksoned.columns:
        if "Märksõna kategooria" in marksoned.columns:
            marksoned = marksoned.drop(columns=["Märksõna kategooria"])
        marksoned = marksoned.merge(marksona_kategooriad_map, on="Märksõna", how="left")
    else:
        ensure_column(marksoned, "Märksõna kategooria")

    for col in ["PID", "Isik", "Fotograaf"]:
        ensure_column(isikud, col)

    # fotograaf isikute lehelt
    foto_map = (
        isikud[["PID", "Fotograaf"]]
        .dropna(subset=["Fotograaf"])
        .drop_duplicates(subset=["PID"])
    )

    if "Fotograaf" in fotod.columns:
        fotod = fotod.drop(columns=["Fotograaf"])

    fotod = fotod.merge(foto_map, on="PID", how="left")

    fotod["Aasta"] = pd.to_numeric(fotod["Aasta"], errors="coerce")
    fotod["latitude"] = pd.to_numeric(fotod["latitude"], errors="coerce")
    fotod["longitude"] = pd.to_numeric(fotod["longitude"], errors="coerce")

    # koordinaadid_leitud arvuta päriselt koordinaatide järgi
    fotod["koordinaadid_leitud"] = (
        fotod["latitude"].notna() & fotod["longitude"].notna()
    ).map({True: "jah", False: "ei"})

    # ühtlustatud kaardipiirkond
    fotod["kaardi_piirkond"] = pd.NA

    if "kihelkond_kaart" in fotod.columns:
        fotod["kaardi_piirkond"] = fotod["kihelkond_kaart"].apply(normalize_place_name)

    fallback_mask = fotod["kaardi_piirkond"].isna()
    if "Kihelkond või linn" in fotod.columns:
        fotod.loc[fallback_mask, "kaardi_piirkond"] = (
            fotod.loc[fallback_mask, "Kihelkond või linn"].apply(normalize_place_name)
        )

    fallback_mask = fotod["kaardi_piirkond"].isna()
    fotod.loc[fallback_mask, "kaardi_piirkond"] = (
        fotod.loc[fallback_mask, "Kihelkond"].apply(normalize_place_name)
    )

    if not kihelkonnad_kp.empty:
        esimene_veerg = kihelkonnad_kp.columns[0]
        kihelkonnad_kp = kihelkonnad_kp.rename(columns={esimene_veerg: "kaardi_piirkond"})
        kihelkonnad_kp["kaardi_piirkond"] = kihelkonnad_kp["kaardi_piirkond"].apply(normalize_place_name)

        for col in ["latitude", "longitude"]:
            ensure_column(kihelkonnad_kp, col)
            kihelkonnad_kp[col] = pd.to_numeric(kihelkonnad_kp[col], errors="coerce")

    return fotod, marksoned, isikud, kihelkonnad_kp, os.path.basename(xlsx_path)


@st.cache_data
def load_geojson(nimi):
    path = os.path.join(BASE_DIR, nimi)

    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        st.warning(f"GeoJSON faili '{nimi}' ei saanud laadida: {e}")
        return None


fotod, marksoned, isikud, kihelkonnad_kp, aktiivne_fail = load_data()


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("🗂️ Filtrid")

if st.sidebar.button("🔄 Uuenda andmed"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.info("Praegu on aktiivne ainult ajalooline kihelkonnapõhine kaart.")

if st.sidebar.button("🧹 Tühjenda kõik filtrid"):
    for key in ["valitud_zanr", "valitud_marksona", "valitud_marksona_kategooria", "valitud_fotograaf", "valitud_isik"]:
        st.session_state[key] = []
    st.session_state["marksona_loogika_radio"] = "VÕI – vähemalt üks"
    st.rerun()

if fotod["Aasta"].notna().any():
    aastad = fotod["Aasta"].dropna().astype(int)
    aasta_vahemik = st.sidebar.slider(
        "Aasta vahemik",
        min_value=int(aastad.min()),
        max_value=int(aastad.max()),
        value=(int(aastad.min()), int(aastad.max())),
    )
else:
    aasta_vahemik = (0, 9999)
    st.sidebar.info("Aasta veerus väärtusi ei leitud.")

# session state algväärtused
for key in ["valitud_zanr", "valitud_marksona", "valitud_marksona_kategooria", "valitud_fotograaf", "valitud_isik"]:
    if key not in st.session_state:
        st.session_state[key] = []

if "marksona_loogika_radio" not in st.session_state:
    st.session_state["marksona_loogika_radio"] = "VÕI – vähemalt üks"

marksona_loogika = st.session_state["marksona_loogika_radio"]

# esimene valikute arvutus
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    marksona_loogika,
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_zanr", zanr_opts, max_n=3)
sanitize_state_list("valitud_marksona", ms_opts, max_n=3)
sanitize_state_list("valitud_marksona_kategooria", mk_opts, max_n=3)
sanitize_state_list("valitud_fotograaf", ft_opts, max_n=3)
sanitize_state_list("valitud_isik", isik_opts, max_n=3)

st.sidebar.multiselect(
    "Žanr",
    options=zanr_opts,
    key="valitud_zanr",
    max_selections=3,
    placeholder="Vali kuni 3"
)

# arvuta uuesti pärast žanrit
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_marksona", ms_opts, max_n=3)

st.sidebar.multiselect(
    "Märksõna",
    options=ms_opts,
    key="valitud_marksona",
    max_selections=3,
    placeholder="Vali kuni 3"
)

if valitud_marksona_kategooria and len(ms_opts) > 0:
    st.sidebar.caption("Märksõnade valik on kitsendatud valitud kategooria järgi.")

if len(st.session_state["valitud_marksona"]) > 1:
    st.sidebar.radio(
        "Märksõnade loogika",
        ["VÕI – vähemalt üks", "JA – kõik korraga"],
        key="marksona_loogika_radio"
    )

# arvuta uuesti pärast märksõnu
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_marksona_kategooria", mk_opts, max_n=3)

st.sidebar.multiselect(
    "Märksõna kategooria",
    options=mk_opts,
    key="valitud_marksona_kategooria",
    max_selections=3,
    placeholder="Vali kuni 3 kategooriat"
)

# arvuta uuesti pärast märksõna kategooriat
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_fotograaf", ft_opts, max_n=3)

st.sidebar.multiselect(
    "Fotograaf",
    options=ft_opts,
    key="valitud_fotograaf",
    max_selections=3,
    placeholder="Vali kuni 3"
)

# arvuta uuesti pärast fotograafi
zanr_opts, ms_opts, mk_opts, ft_opts, isik_opts = get_available_options(
    fotod, marksoned, isikud,
    aasta_vahemik,
    st.session_state["valitud_zanr"],
    st.session_state["valitud_marksona"],
    st.session_state["marksona_loogika_radio"],
    st.session_state["valitud_fotograaf"],
    st.session_state["valitud_isik"],
    st.session_state["valitud_marksona_kategooria"],
)

sanitize_state_list("valitud_isik", isik_opts, max_n=3)

st.sidebar.multiselect(
    "Isik pildil",
    options=isik_opts,
    key="valitud_isik",
    max_selections=3,
    placeholder="Vali kuni 3"
)

valitud_zanr = st.session_state["valitud_zanr"]
valitud_marksona = st.session_state["valitud_marksona"]
valitud_marksona_kategooria = st.session_state["valitud_marksona_kategooria"]
valitud_fotograaf = st.session_state["valitud_fotograaf"]
valitud_isik = st.session_state["valitud_isik"]
marksona_loogika = st.session_state["marksona_loogika_radio"]

df = get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik,
    valitud_zanr,
    valitud_marksona,
    marksona_loogika,
    valitud_fotograaf,
    valitud_isik,
    valitud_marksona_kategooria
)


# ── KPI ──────────────────────────────────────────────────────────────────────

st.title("📷 ERA Fotode Andmebaas")
st.caption(f"Kasutusel fail: {aktiivne_fail}")
st.markdown(f"Kuvatud **{len(df):,}** fotot kokku **{len(fotod):,}**-st")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Fotosid kokku", f"{len(df):,}")
c2.metric(
    "Koordinaatidega",
    f"{df['koordinaadid_leitud'].astype(str).eq('jah').sum():,}" if "koordinaadid_leitud" in df.columns else "0"
)
c3.metric("Erinevaid piirkondi", f"{df['kaardi_piirkond'].nunique()}" if "kaardi_piirkond" in df.columns else "0")
c4.metric(
    "Ajavahemik",
    (
        f"{int(df['Aasta'].min()) if df['Aasta'].notna().any() else '?'}–"
        f"{int(df['Aasta'].max()) if df['Aasta'].notna().any() else '?'}"
    ) if "Aasta" in df.columns else "?"
)
st.divider()

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    ["🗺️ Kaart", "📊 Statistika", "🏷️ Märksõnad", "👤 Isikud", "🤖 ML märksõnad", "📋 Andmetabel"]
)


# ══════════════════ TAB 1 – KAART ════════════════════════════════════════════
with tab1:
    st.subheader("Fotod kihelkondade ja linnade kaupa")

    kihel_veerg = "kaardi_piirkond"

    if kihel_veerg not in df.columns:
        st.warning("Kaardipiirkonna veerg puudub.")
    else:
        df_map_src = df[
            df[kihel_veerg].notna() &
            ~df[kihel_veerg].astype(str).str.lower().isin(["teadmata", "välismaa", "välismaa,"])
        ].copy()

        kihel_counts = (
            df_map_src
            .groupby(kihel_veerg)
            .size()
            .reset_index(name="Fotode arv")
            .rename(columns={kihel_veerg: "kaardi_piirkond"})
        )

        if not kihelkonnad_kp.empty and {"kaardi_piirkond", "latitude", "longitude"}.issubset(kihelkonnad_kp.columns):
            kihel_map = kihel_counts.merge(
                kihelkonnad_kp[["kaardi_piirkond", "latitude", "longitude"]],
                on="kaardi_piirkond",
                how="left"
            )
        else:
            kihel_map = kihel_counts.copy()

        geojson = load_geojson("kih1922_region.json")
        geojson_names = extract_geojson_feature_names(geojson, "KIHELKOND") if geojson else set()

        df_geo = kihel_map[kihel_map["kaardi_piirkond"].isin(geojson_names)].copy()
        df_missing = kihel_map[
            ~kihel_map["kaardi_piirkond"].isin(geojson_names) &
            kihel_map["latitude"].notna() &
            kihel_map["longitude"].notna()
        ].copy()

        if geojson and not df_geo.empty:
            fig = px.choropleth_mapbox(
                df_geo,
                geojson=geojson,
                locations="kaardi_piirkond",
                featureidkey="properties.KIHELKOND",
                color="Fotode arv",
                color_continuous_scale="YlOrRd",
                hover_name="kaardi_piirkond",
                hover_data={"Fotode arv": True},
                mapbox_style="open-street-map",
                zoom=6,
                center={"lat": 58.7, "lon": 25.0},
                opacity=0.65,
                title="Vali piirkond alt detailvaateks",
            )
            fig = lisa_piirjooned(fig, geojson)
            fig = lisa_puuduvad_keskpunktid(fig, df_missing)
            fig.update_layout(height=480, margin={"r": 0, "t": 40, "l": 0, "b": 0})
            st.plotly_chart(fig, use_container_width=True)

        elif not kihel_map.empty and {"latitude", "longitude"}.issubset(kihel_map.columns):
            kihel_pts = kihel_map.dropna(subset=["latitude", "longitude"])
            if not kihel_pts.empty:
                fig = px.scatter_mapbox(
                    kihel_pts,
                    lat="latitude",
                    lon="longitude",
                    size="Fotode arv",
                    color="Fotode arv",
                    hover_name="kaardi_piirkond",
                    hover_data={"Fotode arv": True, "latitude": False, "longitude": False},
                    color_continuous_scale="YlOrRd",
                    size_max=45,
                    zoom=6,
                    center={"lat": 58.7, "lon": 25.0},
                    mapbox_style="open-street-map",
                    title="Vali piirkond alt detailvaateks",
                )
                fig.update_traces(text=kihel_pts["kaardi_piirkond"], textposition="top center")
                fig.update_layout(height=480, margin={"r": 0, "t": 40, "l": 0, "b": 0})
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Kaardi jaoks ei leitud piisavalt piirkonnaandmeid.")
        else:
            st.info("Kihelkonna geojsoni ega keskpunktiandmeid ei leitud piisavalt.")

        st.subheader("Piirkonna detailvaade")
        kihel_valikud = sorted(kihel_map["kaardi_piirkond"].dropna().astype(str).unique().tolist()) if not kihel_map.empty else []
        val_kihel = st.selectbox("Vali piirkond", ["—"] + kihel_valikud)

        if val_kihel != "—":
            df_kihel = df[df[kihel_veerg].astype(str) == val_kihel]
            k1, k2, k3 = st.columns(3)
            k1.metric("Fotosid", len(df_kihel))
            k2.metric(
                "Koordinaatidega",
                df_kihel["koordinaadid_leitud"].astype(str).eq("jah").sum() if "koordinaadid_leitud" in df_kihel.columns else 0
            )
            k3.metric(
                "Ajavahemik",
                (
                    f"{int(df_kihel['Aasta'].min()) if df_kihel['Aasta'].notna().any() else '?'}–"
                    f"{int(df_kihel['Aasta'].max()) if df_kihel['Aasta'].notna().any() else '?'}"
                ) if "Aasta" in df_kihel.columns else "?"
            )

            naita_fotopunkte(df_kihel, f"Fotod – {val_kihel}", load_geojson, lisa_asustus_piirid=True)

            col_kd1, col_kd2 = st.columns(2)
            with col_kd1:
                if "Fotograaf" in df_kihel.columns:
                    ft = df_kihel["Fotograaf"].value_counts().head(8).reset_index()
                    if len(ft.columns) == 2:
                        ft.columns = ["Fotograaf", "Arv"]
                    st.markdown("**Fotograafid**")
                    st.dataframe(ft, hide_index=True, use_container_width=True)

            with col_kd2:
                if not marksoned.empty and "PID" in marksoned.columns and "Märksõna" in marksoned.columns:
                    ms_kihel = (
                        marksoned[marksoned["PID"].isin(df_kihel["PID"])]["Märksõna"]
                        .value_counts().head(8).reset_index()
                    )
                    if len(ms_kihel.columns) == 2:
                        ms_kihel.columns = ["Märksõna", "Arv"]
                    st.markdown("**Top märksõnad**")
                    st.dataframe(ms_kihel, hide_index=True, use_container_width=True)

        puuduvad_nimed = []
        if not kihel_map.empty and {"latitude", "longitude"}.issubset(kihel_map.columns):
            puuduvad_nimed = (
                kihel_map[
                    kihel_map["latitude"].isna() | kihel_map["longitude"].isna()
                ]["kaardi_piirkond"]
                .dropna()
                .astype(str)
                .tolist()
            )

        if puuduvad_nimed:
            st.caption("⚠️ Keskpunkt puudub: " + ", ".join(sorted(puuduvad_nimed[:20])) + (" ..." if len(puuduvad_nimed) > 20 else ""))
        else:
            st.caption(f"ℹ️ Kaardil on {len(kihel_map)} piirkonda. Geojsonist puudu olevad piirkonnad lisatakse keskpunktmarkeritena.")


# ══════════════════ TAB 2 – STATISTIKA ═══════════════════════════════════════
with tab2:
    col_left, col_right = st.columns(2)

    with col_left:
        df_a2 = df[df["Aasta"].notna()].copy()
        if not df_a2.empty:
            df_a2["Aastakümme"] = (df_a2["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
            ak = df_a2["Aastakümme"].value_counts().sort_index()
            fig = px.bar(
                x=ak.index,
                y=ak.values,
                labels={"x": "Aastakümme", "y": "Fotode arv"},
                title="Fotod aastakümne kaupa",
                color=ak.values,
                color_continuous_scale="Blues",
            )
            fig.update_layout(coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

        if "kaardi_piirkond" in df.columns:
            kihel_top = (
                df[df["kaardi_piirkond"].notna() & ~df["kaardi_piirkond"].astype(str).str.lower().isin(["teadmata", "välismaa", "välismaa,"])]["kaardi_piirkond"]
                .value_counts().head(15)
            )
            if len(kihel_top) > 0:
                fig = px.bar(
                    x=kihel_top.values,
                    y=kihel_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Piirkond"},
                    title="Top 15 piirkonda",
                    color=kihel_top.values,
                    color_continuous_scale="Greens",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)

    with col_right:
        if "Žanr" in df.columns:
            zanr_c = df["Žanr"].value_counts().head(15).dropna()
            if len(zanr_c) > 0:
                fig = px.pie(values=zanr_c.values, names=zanr_c.index, title="Žanrite jaotus (top 15)")
                fig.update_traces(textposition="inside", textinfo="percent+label")
                st.plotly_chart(fig, use_container_width=True)

        if "Fotograaf" in df.columns:
            foto_top = df["Fotograaf"].value_counts().head(12).dropna()
            if len(foto_top) > 0:
                fig = px.bar(
                    x=foto_top.values,
                    y=foto_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Fotograaf"},
                    title="Top 12 fotograafi",
                    color=foto_top.values,
                    color_continuous_scale="Oranges",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)

    st.subheader("Projektid")
    if "Projekt" in df.columns:
        proj_c = df["Projekt"].value_counts().head(10).dropna()
        if len(proj_c) > 0:
            fig = px.bar(
                x=proj_c.values,
                y=proj_c.index,
                orientation="h",
                labels={"x": "Fotode arv", "y": "Projekt"},
                title="Top 10 projekti",
                color=proj_c.values,
                color_continuous_scale="Purples",
            )
            fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=350)
            st.plotly_chart(fig, use_container_width=True)


# ══════════════════ TAB 3 – MÄRKSÕNAD ════════════════════════════════════════
with tab3:
    st.subheader("Märksõnade analüüs")
    df_pids = set(df["PID"].dropna().unique()) if "PID" in df.columns else set()
    mf = marksoned[marksoned["PID"].isin(df_pids)] if not marksoned.empty and "PID" in marksoned.columns else pd.DataFrame()

    col_m1, col_m2 = st.columns(2)

    with col_m1:
        top_n = st.slider("Näita top N märksõna", 10, 50, 20)
        if not mf.empty and "Märksõna" in mf.columns:
            ms_c = mf["Märksõna"].value_counts().head(top_n)
            if len(ms_c) > 0:
                fig = px.bar(
                    x=ms_c.values,
                    y=ms_c.index,
                    orientation="h",
                    labels={"x": "Esinemiste arv", "y": "Märksõna"},
                    title=f"Top {top_n} märksõna",
                    color=ms_c.values,
                    color_continuous_scale="Teal",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                st.plotly_chart(fig, use_container_width=True)

    with col_m2:
        st.markdown("#### Märksõna ajaline trend")
        ms_in = st.text_input("Sisesta märksõna", value="portree")
        if ms_in and not mf.empty and "Märksõna" in mf.columns:
            ms_tr = mf[mf["Märksõna"].fillna("").astype(str).str.lower() == ms_in.lower()][["PID"]].copy()
            ms_tr = ms_tr.merge(df[["PID", "Aasta"]].drop_duplicates("PID"), on="PID", how="left")
            ms_tr = ms_tr[ms_tr["Aasta"].notna()]
            if len(ms_tr) > 0:
                ms_tr["Aastakümme"] = (ms_tr["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
                tc = ms_tr["Aastakümme"].value_counts().sort_index()
                fig = px.line(
                    x=tc.index,
                    y=tc.values,
                    markers=True,
                    labels={"x": "Aastakümme", "y": "Esinemiste arv"},
                    title=f"'{ms_in}' ajas",
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Selle märksõnaga aastaga fotosid ei leitud.")


# ══════════════════ TAB 4 – ISIKUD ═══════════════════════════════════════════
with tab4:
    st.subheader("Isikud fotodel")
    df_pids = set(df["PID"].dropna().unique()) if "PID" in df.columns else set()
    isikud_filtered = (
        isikud[isikud["PID"].isin(df_pids)]
        if not isikud.empty and "PID" in isikud.columns else pd.DataFrame()
    )

    col_i1, col_i2 = st.columns(2)

    with col_i1:
        top_isik_n = st.slider("Näita top N isikut", 10, 50, 20, key="isik_slider")
        if not isikud_filtered.empty and "Isik" in isikud_filtered.columns:
            isik_top = isikud_filtered["Isik"].value_counts().head(top_isik_n)
            if len(isik_top) > 0:
                fig = px.bar(
                    x=isik_top.values,
                    y=isik_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Isik"},
                    title=f"Top {top_isik_n} isikut fotodel",
                    color=isik_top.values,
                    color_continuous_scale="Magenta",
                )
                fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                st.plotly_chart(fig, use_container_width=True)

    with col_i2:
        st.markdown("#### Isiku otsing")
        isik_otsing = st.text_input("Otsi isiku nime järgi")
        if isik_otsing and not isikud_filtered.empty and "Isik" in isikud_filtered.columns:
            isik_matches = isikud_filtered[
                isikud_filtered["Isik"].fillna("").astype(str).str.contains(isik_otsing, case=False, na=False)
            ]
            df_isik = df[df["PID"].isin(isik_matches["PID"].unique())]
            st.markdown(f"Leitud **{len(df_isik)}** fotot isikuga '{isik_otsing}'")
            if len(df_isik) > 0:
                cols = [c for c in ["PID", "Aasta", "Kihelkond", "kaardi_piirkond", "Sisu kirjeldus", "failinimi"] if c in df_isik.columns]
                st.dataframe(df_isik[cols].head(50), use_container_width=True, hide_index=True)
        else:
            st.markdown("#### Isikute arv fotol")
            if "Isikute arv" in df.columns:
                isikute_arv = df["Isikute arv"].value_counts().sort_index().head(10)
                if len(isikute_arv) > 0:
                    fig2 = px.bar(
                        x=isikute_arv.index.astype(str),
                        y=isikute_arv.values,
                        labels={"x": "Isikute arv fotol", "y": "Fotode arv"},
                        title="Kui palju isikuid on fotodel?",
                        color=isikute_arv.values,
                        color_continuous_scale="Teal",
                    )
                    fig2.update_layout(coloraxis_showscale=False)
                    st.plotly_chart(fig2, use_container_width=True)


# ══════════════════ TAB 5 – ML MÄRKSÕNAD ═════════════════════════════════════
with tab5:
    st.subheader("🤖 ML märksõnad")

    st.markdown("""
    CLIP ei „näe” sinu märksõnade nimekirja otse. Ta võrdleb pilti tekstikirjeldustega
    ja arvutab, milline tekstiline kategooria sobib pildiga kõige paremini.

    Siin saab võrrelda:
    - sinu olemasolevaid käsitsi loodud märksõna-kategooriaid;
    - CLIP-i pakutud top 1–3 kategooriat;
    - pildi pealkirja / sisukirjeldust.
    """)

    img_path = os.path.join(BASE_DIR, "clip_yhe_pildi_selgitus.png")
    if os.path.exists(img_path):
        st.image(img_path, caption="Näide: kuidas CLIP pildi ja tekstikategooriate sobivust hindab", use_container_width=True)
    else:
        st.info("Näidispilti 'clip_yhe_pildi_selgitus.png' ei leitud rakenduse kaustast.")

    st.divider()

    ml_df = df.copy()
    has_clip = "pred_top1" in ml_df.columns and ml_df["pred_top1"].notna().any()
    has_manual = "Märksõna kategooria" in ml_df.columns and ml_df["Märksõna kategooria"].notna().any()

    if not has_clip and not has_manual:
        st.warning("ML märksõnade infot ei leitud. Kontrolli, et failid 'ERA_märksõnad_ML.xlsx' ja 'era_clip_KOIK_pildid_sigmoid.xlsx' oleksid rakenduse kaustas.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fotosid filtris", f"{len(ml_df):,}")
        c2.metric("CLIP ennustusega", f"{ml_df['pred_top1'].notna().sum():,}" if "pred_top1" in ml_df.columns else "0")
        c3.metric("Käsitsi kategooriaga", f"{ml_df['Märksõna kategooria'].notna().sum():,}" if "Märksõna kategooria" in ml_df.columns else "0")
        st.caption("Need arvud ei peagi olema samad: CLIP ennustused on ainult hindamiseks käsitsi kategooriaga piltidel, ülejäänud fotodel võib olla originaalmärksõnu, aga mitte 19-kategooria käsitsi klastrit.")
        c4.metric(
            "Keskmine top1–top2 vahe",
            f"{ml_df['confidence_margin_top1_top2'].mean():.3f}" if "confidence_margin_top1_top2" in ml_df.columns and ml_df["confidence_margin_top1_top2"].notna().any() else "?"
        )

        st.markdown("### Käsitsi kategooriad vs CLIP pakkumised")

        col_a, col_b = st.columns(2)

        with col_a:
            if has_manual:
                manual_counts = split_categories(ml_df["Märksõna kategooria"]).value_counts().head(20)

                if len(manual_counts) > 0:
                    fig = px.bar(
                        x=manual_counts.values,
                        y=manual_counts.index,
                        orientation="h",
                        labels={"x": "Fotode arv", "y": "Käsitsi kategooria"},
                        title="Sinu märksõna-kategooriad",
                        color=manual_counts.values,
                        color_continuous_scale="Blues",
                    )
                    fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Käsitsi märksõna-kategooriaid ei leitud.")

        with col_b:
            if has_clip:
                clip_counts = ml_df["pred_top1"].dropna().astype(str).value_counts().head(20)

                if len(clip_counts) > 0:
                    fig = px.bar(
                        x=clip_counts.values,
                        y=clip_counts.index,
                        orientation="h",
                        labels={"x": "Fotode arv", "y": "CLIP top 1"},
                        title="CLIP top 1 kategooriad",
                        color=clip_counts.values,
                        color_continuous_scale="Oranges",
                    )
                    fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("CLIP ennustusi ei leitud.")

        if has_clip and has_manual:
            st.markdown("### Kui tihti CLIP kattub käsitsi kategooriaga?")
            st.warning("Kui kattuvus on siin väga madal, siis kõige tõenäolisem põhjus ei ole mudel, vaid kategoorianimede/andmeveeru sobimatus: CLIP-i `pred_top1` peab olema täpselt samas 19 kategooria sõnastikus nagu käsitsi `Märksõna kategooria`.")

            eval_df = ml_df[ml_df["pred_top1"].notna() & ml_df["Märksõna kategooria"].notna()].copy()

            if not eval_df.empty:
                eval_df["top1_kattub"] = eval_df.apply(lambda r: category_match(r, pred_cols=["pred_top1"]), axis=1)
                eval_df["top3_kattub"] = eval_df.apply(lambda r: category_match(r, pred_cols=["pred_top1", "pred_top2", "pred_top3"]), axis=1)
                eval_df["top5_kattub"] = eval_df.apply(lambda r: category_match(r, pred_cols=["pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5"]), axis=1)

                m1, m2, m3 = st.columns(3)
                m1.metric("Top1 kattuvus", f"{eval_df['top1_kattub'].mean() * 100:.1f}%")
                m2.metric("Top3 kattuvus", f"{eval_df['top3_kattub'].mean() * 100:.1f}%")
                m3.metric("Top5 kattuvus", f"{eval_df['top5_kattub'].mean() * 100:.1f}%")

                with st.expander("Kontrolli kategoorianimede kattumist"):
                    manual_set = sorted(split_categories(eval_df["Märksõna kategooria"]).dropna().astype(str).unique().tolist())
                    pred_set = sorted(pd.concat([
                        eval_df[c].dropna().astype(str) for c in ["pred_top1", "pred_top2", "pred_top3", "pred_top4", "pred_top5"] if c in eval_df.columns
                    ]).dropna().astype(str).unique().tolist())
                    col_x, col_y = st.columns(2)
                    with col_x:
                        st.markdown("**Käsitsi kategooriad**")
                        st.write(manual_set)
                    with col_y:
                        st.markdown("**CLIP kategooriad**")
                        st.write(pred_set)

                match_counts = eval_df["top3_kattub"].map({True: "Top3 seas kattub", False: "Top3 seas ei kattu"}).value_counts()
                fig = px.pie(
                    values=match_counts.values,
                    names=match_counts.index,
                    title="CLIP top3 vs käsitsi kategooria"
                )
                st.plotly_chart(fig, use_container_width=True)

        if has_clip:
            st.markdown("### ML skooride tõlgendus")
            score_cols = [c for c in ["pred_top1_score", "confidence_margin_top1_top2", "ML top3 koondskoor", "ML top5 koondskoor"] if c in ml_df.columns]
            if score_cols:
                st.caption("Top1 skoor üksi ei ole väga hea kvaliteedimõõdik. Praktilisem on vaadata koos top1–top2 vahet ning seda, kas õige/sobiv kategooria ilmub top3 või top5 hulka.")
                score_summary = ml_df[score_cols].describe().T.reset_index().rename(columns={"index": "skoor"})
                st.dataframe(score_summary, use_container_width=True, hide_index=True)

        st.markdown("### Vaata üksikuid fotosid")

        otsing_ml = st.text_input("Otsi PID, failinime või pealkirja järgi", key="ml_otsing")

        ml_show = ml_df.copy()

        if otsing_ml:
            mask = pd.Series(False, index=ml_show.index)

            for col in ["PID", "failinimi", "Sisu kirjeldus"]:
                if col in ml_show.columns:
                    mask = mask | ml_show[col].fillna("").astype(str).str.contains(otsing_ml, case=False, na=False)

            ml_show = ml_show[mask]

        if has_clip and has_manual:
            ainult_erinevad = st.checkbox("Näita ainult ridu, kus CLIP top3 ei ole käsitsi kategooriate hulgas")

            if ainult_erinevad:
                ml_show = ml_show[
                    ~ml_show.apply(
                        lambda r: category_match(r, pred_cols=["pred_top1", "pred_top2", "pred_top3"]),
                        axis=1
                    )
                ]

        cols_ml = [
            c for c in [
                "PID",
                "failinimi",
                "Sisu kirjeldus",
                "Märksõna kategooria",
                "Originaal märksõnad",
                "pred_top1",
                "pred_top2",
                "pred_top3",
                "pred_top4",
                "pred_top5",
                "pred_top1_score",
                "confidence_margin_top1_top2",
                "ML top3 koondskoor",
                "ML top5 koondskoor",
                "ML otsuse tugevus",
            ] if c in ml_show.columns
        ]

        st.markdown(f"Näidatakse **{len(ml_show):,}** rida")

        st.dataframe(
            ml_show[cols_ml].head(500),
            use_container_width=True,
            hide_index=True,
            height=420
        )

        csv_ml = ml_show[cols_ml].to_csv(index=False).encode("utf-8")

        st.download_button(
            "⬇️ Lae ML võrdlustabel alla CSV-na",
            data=csv_ml,
            file_name="era_ml_marksonad_vordlus.csv",
            mime="text/csv"
        )


# ══════════════════ TAB 6 – ANDMETABEL ═══════════════════════════════════════
with tab6:
    st.subheader("Andmetabel")
    vaikimisi = [
        c for c in [
            "PID", "Aasta", "Kihelkond", "kaardi_piirkond", "Fotograaf",
            "Žanr", "Märksõna kategooria", "pred_top1",
            "Sisu kirjeldus", "ERA märksõnad (koondatud)", "failinimi"
        ] if c in df.columns
    ]

    show_cols = st.multiselect(
        "Vali kuvatavad veerud",
        options=list(df.columns),
        default=vaikimisi
    )

    otsing = st.text_input("🔍 Otsi (sisu, kihelkond, fotograaf)")
    df_show = df.copy()

    if otsing:
        mask = pd.Series(False, index=df_show.index)

        if "Sisu kirjeldus" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Sisu kirjeldus"], otsing)
        if "Kihelkond" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Kihelkond"], otsing)
        if "kaardi_piirkond" in df_show.columns:
            mask = mask | safe_str_contains(df_show["kaardi_piirkond"], otsing)
        if "Fotograaf" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Fotograaf"], otsing)
        if "Märksõna kategooria" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Märksõna kategooria"], otsing)
        if "pred_top1" in df_show.columns:
            mask = mask | safe_str_contains(df_show["pred_top1"], otsing)

        df_show = df_show[mask]

    st.markdown(f"Näidatakse **{len(df_show):,}** rida")

    if show_cols:
        st.dataframe(df_show[show_cols].head(500), use_container_width=True, height=420)

        if len(df_show) > 500:
            st.caption("ℹ️ Tabelis on esimesed 500 rida. Kitsenda filtritega.")

        csv = df_show[show_cols].to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Lae alla CSV",
            data=csv,
            file_name="era_fotod_filteeritud.csv",
            mime="text/csv"
        )
    else:
        st.info("Vali vähemalt üks veerg.")
