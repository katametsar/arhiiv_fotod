import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import json

st.set_page_config(page_title="ERA Fotode Andmebaas", page_icon="📷", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Abifunktsioonid ──────────────────────────────────────────────────────────

def safe_sheet_parse(xl, sheet_name):
    if sheet_name in xl.sheet_names:
        return xl.parse(sheet_name)
    return pd.DataFrame()


def ensure_column(df, col, default=pd.NA):
    if col not in df.columns:
        df[col] = default
    return df


def safe_str_contains(series, text):
    return series.fillna("").astype(str).str.contains(text, case=False, na=False)


def normalize_text_series(series):
    return series.fillna("").astype(str).str.strip()


@st.cache_data
def load_data():
    xlsx_path = None
    for fname in ["ERA_fotod_1804.xlsx", ERA_fotod_piiridega.xlsx", "ERA_fotod_geocoded.xlsx"]:
        path = os.path.join(BASE_DIR, fname)
        if os.path.exists(path):
            xlsx_path = path
            break

    if xlsx_path is None:
        raise FileNotFoundError("Ühtegi Exceli faili ei leitud kaustast.")

    xl = pd.ExcelFile(xlsx_path)

    fotod = safe_sheet_parse(xl, "fotod_koordinaatidega")
    marksoned = safe_sheet_parse(xl, "märksõnad_pikk")
    isikud = safe_sheet_parse(xl, "isikud_fotol_pikk")
    kihelkonnad_kp = safe_sheet_parse(xl, "kihelkond_keskpunktid")

    if fotod.empty:
        raise ValueError("Sheet 'fotod_koordinaatidega' puudub või on tühi.")

    # kindlusta vajalikud veerud
    for col in [
        "PID", "Aasta", "Žanr", "Kihelkond", "Sisu kirjeldus", "failinimi",
        "Fotograaf", "Fotograaf (puhastatud)", "koordinaadid_leitud",
        "lõplik_latitude", "lõplik_longitude", "lõplik_täpsus",
        "maakond", "vald", "asula", "Projekt", "ERA märksõnad (koondatud)",
        "Isikute arv"
    ]:
        ensure_column(fotod, col)

    for col in ["PID", "Märksõna"]:
        ensure_column(marksoned, col)

    for col in ["PID", "Isik"]:
        ensure_column(isikud, col)

    if "Fotograaf (puhastatud)" in fotod.columns and fotod["Fotograaf (puhastatud)"].notna().any():
        fotod["Fotograaf (normaliseeritud)"] = fotod["Fotograaf (puhastatud)"]
    elif "Fotograaf" in fotod.columns:
        fotod["Fotograaf (normaliseeritud)"] = fotod["Fotograaf"]
    else:
        fotod["Fotograaf (normaliseeritud)"] = pd.NA

    # arvulised väljad
    fotod["Aasta"] = pd.to_numeric(fotod["Aasta"], errors="coerce")
    fotod["lõplik_latitude"] = pd.to_numeric(fotod["lõplik_latitude"], errors="coerce")
    fotod["lõplik_longitude"] = pd.to_numeric(fotod["lõplik_longitude"], errors="coerce")

    on_geocoded = "maakond" in fotod.columns and fotod["maakond"].notna().any()

    return fotod, marksoned, isikud, kihelkonnad_kp, on_geocoded

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


def get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik, valitud_zanr,
    valitud_marksona, marksona_loogika,
    valitud_fotograaf, valitud_isik
):
    df = fotod.copy()

    if "Aasta" in df.columns and df["Aasta"].notna().any():
        df_a = df[df["Aasta"].notna()].copy()
        df_a = df_a[df_a["Aasta"].between(aasta_vahemik[0], aasta_vahemik[1])]
        df = pd.concat([df_a, df[df["Aasta"].isna()]], ignore_index=True)

    if valitud_zanr and "Žanr" in df.columns:
        df = df[df["Žanr"].isin(valitud_zanr)]

    if valitud_fotograaf and "Fotograaf (normaliseeritud)" in df.columns:
        df = df[df["Fotograaf (normaliseeritud)"].isin(valitud_fotograaf)]

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

    if valitud_isik and not isikud.empty and "Isik" in isikud.columns:
        isik_pids = set(isikud[isikud["Isik"].isin(valitud_isik)]["PID"].dropna().unique())
        df = df[df["PID"].isin(isik_pids)]

    return df


def naita_fotopunkte(df_piirkond, pealkiri, load_geojson_func, lisa_asustus_piirid=False):
    required = ["lõplik_latitude", "lõplik_longitude"]
    for col in required:
        if col not in df_piirkond.columns:
            st.info("Koordinaadiveerud puuduvad.")
            return

    df_pts = df_piirkond[
        df_piirkond["lõplik_latitude"].notna() &
        df_piirkond["lõplik_longitude"].notna()
    ].copy()

    if df_pts.empty:
        st.info("Valitud piirkonnas koordinaatidega fotosid ei ole.")
        return

    hover_data = {
        "Aasta": "Aasta" in df_pts.columns,
        "Kihelkond": "Kihelkond" in df_pts.columns,
        "Fotograaf (normaliseeritud)": "Fotograaf (normaliseeritud)" in df_pts.columns,
        "lõplik_latitude": False,
        "lõplik_longitude": False,
    }

    color_col = "lõplik_täpsus" if "lõplik_täpsus" in df_pts.columns else None

    fig = px.scatter_mapbox(
        df_pts,
        lat="lõplik_latitude",
        lon="lõplik_longitude",
        hover_name="Sisu kirjeldus" if "Sisu kirjeldus" in df_pts.columns else None,
        hover_data=hover_data,
        color=color_col,
        mapbox_style="open-street-map",
        title=pealkiri,
        zoom=9,
        center={
            "lat": df_pts["lõplik_latitude"].mean(),
            "lon": df_pts["lõplik_longitude"].mean(),
        },
    )

    fig.update_traces(marker=dict(size=9, opacity=0.8))


    if lisa_asustus_piirid:
        geojson_ay = load_geojson_func("asustusyksus.geojson")
        if isinstance(geojson_ay, dict) and "features" in geojson_ay:
            lat_min = df_pts["lõplik_latitude"].min() - 0.1
            lat_max = df_pts["lõplik_latitude"].max() + 0.1
            lon_min = df_pts["lõplik_longitude"].min() - 0.1
            lon_max = df_pts["lõplik_longitude"].max() + 0.1

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

fotod, marksoned, isikud, kihelkonnad_kp, on_geocoded = load_data()


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("🗂️ Filtrid")

if st.sidebar.button("🔄 Uuenda andmed"):
    st.cache_data.clear()
    st.rerun()

asukoha_valik = st.sidebar.radio(
    "Asukoha kuvamise viis",
    ["🏛️ Kihelkonnapõhine (ajalooline)", "📍 Tänapäevane (koordinaadid)"],
)

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

zanrid = sorted(fotod["Žanr"].dropna().astype(str).unique().tolist()) if "Žanr" in fotod.columns else []
valitud_zanr = st.sidebar.multiselect("Žanr", zanrid, placeholder="Kõik žanrid")

top_marksoned = (
    marksoned["Märksõna"].dropna().astype(str).value_counts().head(40).index.tolist()
    if "Märksõna" in marksoned.columns else []
)
valitud_marksona = st.sidebar.multiselect("Märksõna", top_marksoned, placeholder="Kõik märksõnad")

marksona_loogika = "VÕI – vähemalt üks"
if len(valitud_marksona) > 1:
    marksona_loogika = st.sidebar.radio(
        "Märksõnade loogika",
        ["VÕI – vähemalt üks", "JA – kõik korraga"],
    )

df_ilma_ft_isik = get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik, valitud_zanr,
    valitud_marksona, marksona_loogika, [], []
)

fotograafid_saadaval = (
    sorted(df_ilma_ft_isik["Fotograaf (normaliseeritud)"].dropna().astype(str).unique().tolist())
    if "Fotograaf (normaliseeritud)" in df_ilma_ft_isik.columns else []
)
valitud_fotograaf = st.sidebar.multiselect(
    "Fotograaf", fotograafid_saadaval, placeholder="Kõik fotograafid"
)

pids_ilma_isik = set(df_ilma_ft_isik["PID"].dropna().unique()) if "PID" in df_ilma_ft_isik.columns else set()
isikud_saadaval = (
    isikud[isikud["PID"].isin(pids_ilma_isik)]["Isik"].dropna().astype(str).value_counts().head(80).index.tolist()
    if not isikud.empty and "PID" in isikud.columns and "Isik" in isikud.columns else []
)
valitud_isik = st.sidebar.multiselect("Isik pildil", isikud_saadaval, placeholder="Kõik isikud")

df = get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik, valitud_zanr,
    valitud_marksona, marksona_loogika,
    valitud_fotograaf, valitud_isik
)


# ── KPI ──────────────────────────────────────────────────────────────────────

st.title("📷 ERA Fotode Andmebaas")
st.markdown(f"Kuvatud **{len(df):,}** fotot kokku **{len(fotod):,}**-st")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Fotosid kokku", f"{len(df):,}")
c2.metric(
    "Koordinaatidega",
    f"{df['koordinaadid_leitud'].astype(str).eq('jah').sum():,}" if "koordinaadid_leitud" in df.columns else "0"
)
c3.metric("Erinevaid kihelkondi", f"{df['Kihelkond'].nunique()}" if "Kihelkond" in df.columns else "0")
c4.metric(
    "Ajavahemik",
    (
        f"{int(df['Aasta'].min()) if df['Aasta'].notna().any() else '?'}–"
        f"{int(df['Aasta'].max()) if df['Aasta'].notna().any() else '?'}"
    ) if "Aasta" in df.columns else "?"
)
st.divider()

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["🗺️ Kaart", "📊 Statistika", "🏷️ Märksõnad", "👤 Isikud", "📋 Andmetabel"]
)


# ══════════════════ TAB 1 – KAART ════════════════════════════════════════════
with tab1:
    if "Kihelkonnapõhine" in asukoha_valik:
        st.subheader("Fotod kihelkondade kaupa")

        kihel_veerg = "kihelkond_kaart" if "kihelkond_kaart" in df.columns else "Kihelkond"

        if kihel_veerg not in df.columns:
            st.warning("Kihelkonna veerg puudub.")
        else:
            kihel_counts = (
                df[df[kihel_veerg].notna() & ~df[kihel_veerg].astype(str).isin(["teadmata", "välismaa"])]
                .groupby(kihel_veerg).size().reset_index(name="Fotode arv")
                .rename(columns={kihel_veerg: "Kihelkond"})
            )

            kihel_orig = (
                df[df[kihel_veerg].notna() & df["Kihelkond"].notna()]
                [[kihel_veerg, "Kihelkond"]].drop_duplicates()
                .rename(columns={kihel_veerg: "Kihelkond_kaart", "Kihelkond": "Kihelkond_orig"})
            ) if "Kihelkond" in df.columns else pd.DataFrame(columns=["Kihelkond_kaart", "Kihelkond_orig"])

            if not kihelkonnad_kp.empty and "Kihelkond või linn" in kihelkonnad_kp.columns:
                kihel_map = (
                    kihel_counts
                    .merge(
                        kihel_orig.rename(columns={"Kihelkond_kaart": "Kihelkond"}),
                        on="Kihelkond", how="left"
                    )
                    .merge(
                        kihelkonnad_kp.rename(columns={"Kihelkond või linn": "Kihelkond_orig"}),
                        on="Kihelkond_orig", how="left"
                    )
                )
            else:
                kihel_map = kihel_counts.copy()

            geojson = load_geojson("kih1922_region.json")

            if geojson and not kihel_map.empty:
                fig = px.choropleth_mapbox(
                    kihel_map,
                    geojson=geojson,
                    locations="Kihelkond",
                    featureidkey="properties.KIHELKOND",
                    color="Fotode arv",
                    color_continuous_scale="YlOrRd",
                    hover_name="Kihelkond",
                    hover_data={"Fotode arv": True},
                    mapbox_style="open-street-map",
                    zoom=6,
                    center={"lat": 58.7, "lon": 25.0},
                    opacity=0.65,
                    title="Vali kihelkond alt detailvaateks",
                )
                fig = lisa_piirjooned(fig, geojson)
                fig.update_layout(height=480, margin={"r": 0, "t": 40, "l": 0, "b": 0})
                st.plotly_chart(fig, use_container_width=True)

            else:
                st.info("Kihelkonna geojsoni ei leitud või kihelkonnaandmeid pole piisavalt.")

                if "latitude" in kihel_map.columns and "longitude" in kihel_map.columns:
                    kihel_pts = kihel_map.dropna(subset=["latitude", "longitude"])
                    if not kihel_pts.empty:
                        fig = px.scatter_mapbox(
                            kihel_pts,
                            lat="latitude",
                            lon="longitude",
                            size="Fotode arv",
                            color="Fotode arv",
                            hover_name="Kihelkond",
                            hover_data={"Fotode arv": True, "latitude": False, "longitude": False},
                            color_continuous_scale="YlOrRd",
                            size_max=45,
                            zoom=6,
                            center={"lat": 58.7, "lon": 25.0},
                            mapbox_style="open-street-map",
                        )
                        fig.update_layout(height=480, margin={"r": 0, "t": 40, "l": 0, "b": 0})
                        st.plotly_chart(fig, use_container_width=True)

            st.subheader("Kihelkonna detailvaade")
            kihel_valikud = sorted(kihel_map["Kihelkond"].dropna().astype(str).unique().tolist()) if not kihel_map.empty else []
            val_kihel = st.selectbox("Vali kihelkond", ["—"] + kihel_valikud)

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
                    if "Fotograaf (normaliseeritud)" in df_kihel.columns:
                        ft = df_kihel["Fotograaf (normaliseeritud)"].value_counts().head(8).reset_index()
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

            st.caption(f"ℹ️ {len(kihel_map)} kihelkonda. 'Teadmata' ja välismaa on välja jäetud.")

    else:
        st.subheader("Fotod tänapäevase haldusjaotuse järgi")

        if not on_geocoded:
            st.warning(
                "⚠️ Geokodeeritud andmestik puudub. "
                "Lae `ERA_fotod_piiridega.xlsx` reposse ja vajuta '🔄 Uuenda andmed'."
            )
        else:
            geojson_maakond = load_geojson("maakond.geojson")
            geojson_vald = load_geojson("omavalitsus.geojson")

            df_geo = df[df["maakond"].notna() & (df["maakond"].astype(str) != "")].copy()

            maakonnad = sorted([m for m in df_geo["maakond"].dropna().astype(str).unique() if m])
            col_f1, col_f2, col_f3 = st.columns(3)

            with col_f1:
                val_maakond = st.selectbox("Maakond", ["Kõik"] + maakonnad)

            with col_f2:
                subset = df_geo if val_maakond == "Kõik" else df_geo[df_geo["maakond"].astype(str) == val_maakond]
                vallad = sorted([v for v in subset["vald"].dropna().astype(str).unique() if v])
                val_vald = st.selectbox("Vald", ["Kõik"] + vallad)

            with col_f3:
                if val_vald != "Kõik":
                    asulad = sorted([
                        a for a in df_geo[df_geo["vald"].astype(str) == val_vald]["asula"].dropna().astype(str).unique() if a
                    ])
                    val_asula = st.selectbox("Asula / küla", ["Kõik"] + asulad)
                else:
                    val_asula = st.selectbox("Asula / küla", ["Kõik"])

            df_map = df_geo.copy()
            if val_maakond != "Kõik":
                df_map = df_map[df_map["maakond"].astype(str) == val_maakond]
            if val_vald != "Kõik":
                df_map = df_map[df_map["vald"].astype(str) == val_vald]
            if val_asula != "Kõik":
                df_map = df_map[df_map["asula"].astype(str) == val_asula]

            if val_maakond == "Kõik":
                gjson = geojson_maakond
                agg_veerg = "maakond"
                nimiveerg = "MNIMI"
            else:
                gjson = geojson_vald
                agg_veerg = "vald"
                nimiveerg = "ONIMI"

            if gjson and not df_map.empty:
                agg = df_map.groupby(agg_veerg).size().reset_index(name="Fotode arv")
                fig = px.choropleth_mapbox(
                    agg,
                    geojson=gjson,
                    locations=agg_veerg,
                    featureidkey=f"properties.{nimiveerg}",
                    color="Fotode arv",
                    color_continuous_scale="Blues",
                    hover_name=agg_veerg,
                    mapbox_style="open-street-map",
                    zoom=6,
                    center={"lat": 58.7, "lon": 25.0},
                    opacity=0.65,
                    title=f"Fotod {'maakondade' if agg_veerg == 'maakond' else 'valdade'} kaupa",
                )
                fig = lisa_piirjooned(fig, gjson)
                fig.update_layout(height=450, margin={"r": 0, "t": 40, "l": 0, "b": 0})
                st.plotly_chart(fig, use_container_width=True)

            if val_maakond != "Kõik" or val_vald != "Kõik":
                piirkond_nimi = (
                    val_asula if val_asula != "Kõik"
                    else val_vald if val_vald != "Kõik"
                    else val_maakond
                )
                naita_fotopunkte(df_map, f"Fotod – {piirkond_nimi}", load_geojson)
            elif not gjson:
                naita_fotopunkte(df_map, "Kõik koordinaatidega fotod", load_geojson)

            st.caption(f"ℹ️ Filtreeritud: {len(df_map):,} fotot")


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

        if "Kihelkond" in df.columns:
            kihel_top = (
                df[df["Kihelkond"].notna() & ~df["Kihelkond"].astype(str).isin(["teadmata", "välismaa"])]["Kihelkond"]
                .value_counts().head(15)
            )
            if len(kihel_top) > 0:
                fig = px.bar(
                    x=kihel_top.values,
                    y=kihel_top.index,
                    orientation="h",
                    labels={"x": "Fotode arv", "y": "Kihelkond"},
                    title="Top 15 kihelkonda",
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

        if "Fotograaf (normaliseeritud)" in df.columns:
            foto_top = df["Fotograaf (normaliseeritud)"].value_counts().head(12).dropna()
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
                cols = [c for c in ["PID", "Aasta", "Kihelkond", "Sisu kirjeldus", "failinimi"] if c in df_isik.columns]
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


# ══════════════════ TAB 5 – ANDMETABEL ═══════════════════════════════════════
with tab5:
    st.subheader("Andmetabel")
    vaikimisi = [
        c for c in [
            "PID", "Aasta", "Kihelkond", "Fotograaf (normaliseeritud)",
            "Žanr", "Sisu kirjeldus", "ERA märksõnad (koondatud)", "failinimi"
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
        if "Fotograaf (normaliseeritud)" in df_show.columns:
            mask = mask | safe_str_contains(df_show["Fotograaf (normaliseeritud)"], otsing)

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
