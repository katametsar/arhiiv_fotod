import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import os
import json

st.set_page_config(page_title="ERA Fotode Andmebaas", page_icon="📷", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FOTOGRAAF_MAPPING = {
    'A. Jaaksoo': 'Aleksander Jaaksoo', 'A. Kiisla': 'August Kiisla',
    'A. Kitzberg': 'August Kitzberg', 'A. Meikop': 'August Meikop',
    'A. Saar': 'Ada Saar', 'A. Trumm': 'Avo Trumm',
    'E. Veskisaar': 'Ellen Veskisaar', 'G. Ränk': 'Gustav Ränk',
    'H. Kään': 'Heino Kään', 'H. Surva': 'Hugo Surva',
    'H. Tamm': 'Heldi Tamm', 'H. Tampere': 'Herbert Tampere',
    'J. Hallikas': 'Johannes Hallikas', 'J. Lepik': 'Johanna Lepik',
    'J. Mager': 'Julius Mager', 'J. Mikk': 'Johannes Mikk',
    'J. Mägi': 'Juhan Mägi', 'J. Pääsuke': 'Johannes Pääsuke',
    'K. Akel': 'Karl Akel', 'K. Grepp': 'Karl Grepp',
    'K. Mihkelson': 'Kalju Mihkelson', 'K. Raud': 'Kristjan Raud',
    'P. Voolaine': 'Paulopriit Voolaine', 'R. Koppel': 'Rein Koppel',
    'R. Ploom': 'Richard Ploom', 'R. Viidalepp': 'Richard Viidalepp',
    'T. Võimula': 'Tõnu Võimula', 'V. Säägi': 'Vilhelmine Säägi',
}

# ── Andmete laadimine ─────────────────────────────────────────────────────────

@st.cache_data
def load_data():
    xlsx_path = os.path.join(BASE_DIR, "ERA_fotod_10.03.26_koordinaatidega_v2.xlsx")
    xl = pd.ExcelFile(xlsx_path)
    fotod = xl.parse("fotod_koordinaatidega")
    marksoned = xl.parse("märksõnad_pikk")
    isikud = xl.parse("isikud_fotol_pikk")
    kihelkonnad_kp = xl.parse("Kihelkond_keskpunktid")
    fotod['Fotograaf (normaliseeritud)'] = fotod['Fotograaf (puhastatud)'].map(
        lambda x: FOTOGRAAF_MAPPING.get(x, x) if pd.notna(x) else x
    )
    return fotod, marksoned, isikud, kihelkonnad_kp

@st.cache_data
def load_kihelkond_geojson():
    path = os.path.join(BASE_DIR, "kih1922_region.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None

@st.cache_data
def load_maakond_geojson():
    path = os.path.join(BASE_DIR, "maakond.geojson")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None

@st.cache_data
def load_vald_geojson():
    path = os.path.join(BASE_DIR, "vald.geojson")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None

@st.cache_data(ttl=86400)
def geocode_batch(coords_tuple):
    results = {}
    for lat, lon, pid in list(coords_tuple)[:3000]:
        try:
            r = requests.get(
                f"https://inaadress.maaamet.ee/inaadress/gazetteer?lat={lat}&lon={lon}&results=1&appartment=0",
                timeout=5
            )
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list):
                    item = data[0]
                    results[pid] = {
                        "maakond": item.get("maakond", ""),
                        "vald": item.get("omavalitsus", ""),
                        "asula": item.get("asustusyksus", ""),
                    }
        except Exception:
            pass
    return results

fotod, marksoned, isikud, kihelkonnad_kp = load_data()

# ── Filtreerimise funktsioon ──────────────────────────────────────────────────

def get_filtered_df(fotod, marksoned, isikud,
                    aasta_vahemik, valitud_zanr,
                    valitud_marksona, marksona_loogika,
                    valitud_fotograaf, valitud_isik):
    df = fotod.copy()
    df_a = df[df["Aasta"].notna()]
    df_a = df_a[df_a["Aasta"].astype(int).between(aasta_vahemik[0], aasta_vahemik[1])]
    df = pd.concat([df_a, df[df["Aasta"].isna()]])

    if valitud_zanr:
        df = df[df["Žanr"].isin(valitud_zanr)]
    if valitud_fotograaf:
        df = df[df["Fotograaf (normaliseeritud)"].isin(valitud_fotograaf)]
    if valitud_marksona:
        if marksona_loogika == "JA – kõik korraga":
            pids = None
            for ms in valitud_marksona:
                ms_pids = set(marksoned[marksoned["Märksõna"] == ms]["PID"].unique())
                pids = ms_pids if pids is None else pids & ms_pids
        else:
            pids = set(marksoned[marksoned["Märksõna"].isin(valitud_marksona)]["PID"].unique())
        df = df[df["PID"].isin(pids)]
    if valitud_isik:
        isik_pids = set(isikud[isikud["Isik"].isin(valitud_isik)]["PID"].unique())
        df = df[df["PID"].isin(isik_pids)]
    return df

# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("🗂️ Filtrid")

asukoha_valik = st.sidebar.radio(
    "Asukoha kuvamise viis",
    ["🏛️ Kihelkonnapõhine (ajalooline)", "📍 Tänapäevane (koordinaadid)"],
)

aastad = fotod["Aasta"].dropna().astype(int)
aasta_vahemik = st.sidebar.slider(
    "Aasta vahemik",
    min_value=int(aastad.min()), max_value=int(aastad.max()),
    value=(int(aastad.min()), int(aastad.max())),
)

zanrid = sorted(fotod["Žanr"].dropna().unique().tolist())
valitud_zanr = st.sidebar.multiselect("Žanr", zanrid, placeholder="Kõik žanrid")

top_marksoned = marksoned["Märksõna"].value_counts().head(40).index.tolist()
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
    valitud_marksona, marksona_loogika,
    [], []
)

fotograafid_saadaval = sorted(
    df_ilma_ft_isik["Fotograaf (normaliseeritud)"].dropna().unique().tolist()
)
valitud_fotograaf = st.sidebar.multiselect(
    "Fotograaf", fotograafid_saadaval, placeholder="Kõik fotograafid"
)

pids_ilma_isik = set(df_ilma_ft_isik["PID"].unique())
isikud_saadaval = (
    isikud[isikud["PID"].isin(pids_ilma_isik)]["Isik"].value_counts().head(80).index.tolist()
)
valitud_isik = st.sidebar.multiselect(
    "Isik pildil", isikud_saadaval, placeholder="Kõik isikud"
)

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
c2.metric("Koordinaatidega", f"{df['koordinaadid_leitud'].eq('jah').sum():,}")
c3.metric("Erinevaid kihelkondi", f"{df['Kihelkond'].nunique()}")
c4.metric(
    "Ajavahemik",
    f"{int(df['Aasta'].min()) if df['Aasta'].notna().any() else '?'}–"
    f"{int(df['Aasta'].max()) if df['Aasta'].notna().any() else '?'}",
)
st.divider()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["🗺️ Kaart", "📊 Statistika", "🏷️ Märksõnad", "👤 Isikud", "📋 Andmetabel"])

# ══════════════════ TAB 1 – KAART ════════════════════════════════════════════
with tab1:

    if "Kihelkonnapõhine" in asukoha_valik:
        st.subheader("Fotod kihelkondade kaupa")

        kihel_counts = (
            df[df["Kihelkond"].notna() & ~df["Kihelkond"].isin(["teadmata", "välismaa"])]
            .groupby("Kihelkond").size().reset_index(name="Fotode arv")
        )
        kihel_map = kihel_counts.merge(
            kihelkonnad_kp.rename(columns={"Kihelkond või linn": "Kihelkond"}),
            on="Kihelkond", how="left"
        ).dropna(subset=["latitude"])

        geojson = load_kihelkond_geojson()

        if geojson:
            sample_props = geojson["features"][0]["properties"]
            nimiveerg = next(
                (k for k in sample_props if any(s in k.lower() for s in ["kihel", "nimi", "name", "nm"])),
                list(sample_props.keys())[0]
            )
            fig = px.choropleth_mapbox(
                kihel_map, geojson=geojson,
                locations="Kihelkond",
                featureidkey=f"properties.{nimiveerg}",
                color="Fotode arv",
                color_continuous_scale="YlOrRd",
                hover_name="Kihelkond",
                hover_data={"Fotode arv": True},
                mapbox_style="open-street-map",
                zoom=6, center={"lat": 58.7, "lon": 25.0}, opacity=0.6,
                title="Klõpsa kihelkonnale lähemaks",
            )
            fig.add_trace(go.Scattermapbox(
                lat=kihel_map["latitude"], lon=kihel_map["longitude"],
                mode="markers",
                marker=dict(size=7, color="rgba(40,40,180,0.5)"),
                hovertemplate="<b>%{customdata[0]}</b><br>Fotosid: %{customdata[1]}<extra></extra>",
                customdata=list(zip(kihel_map["Kihelkond"], kihel_map["Fotode arv"])),
                showlegend=False,
            ))
        else:
            st.info("ℹ️ Kihelkonnapiiride GeoJSON ei ole kättesaadav – näidatakse mullid.")
            fig = px.scatter_mapbox(
                kihel_map, lat="latitude", lon="longitude",
                size="Fotode arv", color="Fotode arv",
                hover_name="Kihelkond",
                hover_data={"Fotode arv": True, "latitude": False, "longitude": False},
                color_continuous_scale="YlOrRd", size_max=45,
                zoom=6, center={"lat": 58.7, "lon": 25.0},
                mapbox_style="open-street-map",
            )

        fig.update_layout(height=520, margin={"r": 0, "t": 40, "l": 0, "b": 0})
        st.plotly_chart(fig, use_container_width=True)

        # Kihelkonna detailvaade
        st.subheader("Kihelkonna detailvaade")
        kihel_valikud = sorted(kihel_map["Kihelkond"].tolist())
        val_kihel = st.selectbox("Vali kihelkond", ["—"] + kihel_valikud)
        if val_kihel != "—":
            df_kihel = df[df["Kihelkond"] == val_kihel]
            k1, k2, k3 = st.columns(3)
            k1.metric("Fotosid", len(df_kihel))
            k2.metric("Koordinaatidega", df_kihel["koordinaadid_leitud"].eq("jah").sum())
            k3.metric(
                "Ajavahemik",
                f"{int(df_kihel['Aasta'].min()) if df_kihel['Aasta'].notna().any() else '?'}–"
                f"{int(df_kihel['Aasta'].max()) if df_kihel['Aasta'].notna().any() else '?'}"
            )
            col_kd1, col_kd2 = st.columns(2)
            with col_kd1:
                ft = df_kihel["Fotograaf (normaliseeritud)"].value_counts().head(8).reset_index()
                ft.columns = ["Fotograaf", "Arv"]
                st.markdown("**Fotograafid**")
                st.dataframe(ft, hide_index=True, use_container_width=True)
            with col_kd2:
                ms_kihel = (
                    marksoned[marksoned["PID"].isin(df_kihel["PID"])]
                    ["Märksõna"].value_counts().head(8).reset_index()
                )
                ms_kihel.columns = ["Märksõna", "Arv"]
                st.markdown("**Top märksõnad**")
                st.dataframe(ms_kihel, hide_index=True, use_container_width=True)

        st.caption(f"ℹ️ Näidatakse {len(kihel_map)} kihelkonda. 'Teadmata' ja välismaa on välja jäetud.")

    else:
        st.subheader("Fotod koordinaatide järgi – tänapäevane haldusjaotus")

        df_coord = df[
            (df["koordinaadid_leitud"] == "jah")
            & df["lõplik_latitude"].notna()
            & df["lõplik_longitude"].notna()
        ].copy()

        if df_coord.empty:
            st.warning("Valitud filtritega koordinaatidega fotosid ei leitud.")
        else:
            st.markdown("#### Piirkonna täpsustus (maakond → vald → asula)")
            st.caption("Maa-ameti geokodeerimine – esimene kord võtab aega, tulemus salvestatakse vahemällu.")

            with st.spinner("Geokodeerin Maa-ameti API kaudu..."):
                coords_tuple = tuple(zip(
                    df_coord["lõplik_latitude"].tolist(),
                    df_coord["lõplik_longitude"].tolist(),
                    df_coord["PID"].tolist(),
                ))
                geo_results = geocode_batch(coords_tuple)

            if geo_results:
                df_coord["maakond"] = df_coord["PID"].map(lambda p: geo_results.get(p, {}).get("maakond", ""))
                df_coord["vald"] = df_coord["PID"].map(lambda p: geo_results.get(p, {}).get("vald", ""))
                df_coord["asula"] = df_coord["PID"].map(lambda p: geo_results.get(p, {}).get("asula", ""))

                maakonnad = sorted([m for m in df_coord["maakond"].dropna().unique() if m])
                col_f1, col_f2, col_f3 = st.columns(3)
                with col_f1:
                    val_maakond = st.selectbox("Maakond", ["Kõik"] + maakonnad)
                with col_f2:
                    subset = df_coord if val_maakond == "Kõik" else df_coord[df_coord["maakond"] == val_maakond]
                    vallad = sorted([v for v in subset["vald"].dropna().unique() if v])
                    val_vald = st.selectbox("Vald", ["Kõik"] + vallad)
                with col_f3:
                    if val_vald != "Kõik":
                        asulad = sorted([a for a in df_coord[df_coord["vald"] == val_vald]["asula"].dropna().unique() if a])
                        val_asula = st.selectbox("Asula / küla", ["Kõik"] + asulad)
                    else:
                        st.selectbox("Asula / küla", ["Kõik"])
                        val_asula = "Kõik"

                df_map = df_coord.copy()
                if val_maakond != "Kõik":
                    df_map = df_map[df_map["maakond"] == val_maakond]
                if val_vald != "Kõik":
                    df_map = df_map[df_map["vald"] == val_vald]
                if val_asula != "Kõik":
                    df_map = df_map[df_map["asula"] == val_asula]
                hover_extra = {"maakond": True, "vald": True, "asula": True}
            else:
                st.warning("Geokodeerimine ei õnnestunud.")
                df_map = df_coord.copy()
                hover_extra = {}

            sample_size = min(5000, len(df_map))
            df_sample = df_map.sample(n=sample_size, random_state=42) if len(df_map) > 5000 else df_map

            hover_data = {
                "Aasta": True, "Kihelkond": True, "Fotograaf (normaliseeritud)": True,
                "lõplik_latitude": False, "lõplik_longitude": False, **hover_extra,
            }
            fig = px.scatter_mapbox(
                df_sample, lat="lõplik_latitude", lon="lõplik_longitude",
                hover_name="Sisu kirjeldus", hover_data=hover_data,
                color="lõplik_täpsus", zoom=6, center={"lat": 58.7, "lon": 25.0},
                mapbox_style="open-street-map",
                title=f"Koordinaatidega fotod: {len(df_map):,}" + (f" (näidatakse {sample_size})" if len(df_map) > 5000 else ""),
            )
            fig.update_traces(marker=dict(size=7, opacity=0.75))
            fig.update_layout(height=560, margin={"r": 0, "t": 40, "l": 0, "b": 0})
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"ℹ️ Koordinaatidega fotosid: {len(df_coord):,} | Filtreeritud: {len(df_map):,}")

# ══════════════════ TAB 2 – STATISTIKA ═══════════════════════════════════════
with tab2:
    col_left, col_right = st.columns(2)
    with col_left:
        df_a2 = df[df["Aasta"].notna()].copy()
        df_a2["Aastakümme"] = (df_a2["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
        ak = df_a2["Aastakümme"].value_counts().sort_index()
        fig = px.bar(x=ak.index, y=ak.values, labels={"x": "Aastakümme", "y": "Fotode arv"},
                     title="Fotod aastakümne kaupa", color=ak.values, color_continuous_scale="Blues")
        fig.update_layout(coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

        kihel_top = (
            df[df["Kihelkond"].notna() & ~df["Kihelkond"].isin(["teadmata", "välismaa"])]
            ["Kihelkond"].value_counts().head(15)
        )
        fig = px.bar(x=kihel_top.values, y=kihel_top.index, orientation="h",
                     labels={"x": "Fotode arv", "y": "Kihelkond"}, title="Top 15 kihelkonda",
                     color=kihel_top.values, color_continuous_scale="Greens")
        fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        zanr_c = df["Žanr"].value_counts().head(15).dropna()
        if len(zanr_c) > 0:
            fig = px.pie(values=zanr_c.values, names=zanr_c.index, title="Žanrite jaotus (top 15)")
            fig.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig, use_container_width=True)

        foto_top = df["Fotograaf (normaliseeritud)"].value_counts().head(12).dropna()
        fig = px.bar(x=foto_top.values, y=foto_top.index, orientation="h",
                     labels={"x": "Fotode arv", "y": "Fotograaf"}, title="Top 12 fotograafi",
                     color=foto_top.values, color_continuous_scale="Oranges")
        fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Projektid")
    proj_c = df["Projekt"].value_counts().head(10).dropna()
    if len(proj_c) > 0:
        fig = px.bar(x=proj_c.values, y=proj_c.index, orientation="h",
                     labels={"x": "Fotode arv", "y": "Projekt"}, title="Top 10 projekti",
                     color=proj_c.values, color_continuous_scale="Purples")
        fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=350)
        st.plotly_chart(fig, use_container_width=True)

# ══════════════════ TAB 3 – MÄRKSÕNAD ════════════════════════════════════════
with tab3:
    st.subheader("Märksõnade analüüs")
    df_pids = set(df["PID"].unique())
    mf = marksoned[marksoned["PID"].isin(df_pids)]

    col_m1, col_m2 = st.columns(2)
    with col_m1:
        top_n = st.slider("Näita top N märksõna", 10, 50, 20)
        ms_c = mf["Märksõna"].value_counts().head(top_n)
        fig = px.bar(x=ms_c.values, y=ms_c.index, orientation="h",
                     labels={"x": "Esinemiste arv", "y": "Märksõna"},
                     title=f"Top {top_n} märksõna",
                     color=ms_c.values, color_continuous_scale="Teal")
        fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
        st.plotly_chart(fig, use_container_width=True)

    with col_m2:
        st.markdown("#### Märksõna ajaline trend")
        ms_in = st.text_input("Sisesta märksõna", value="portree")
        if ms_in:
            ms_tr = mf[mf["Märksõna"].str.lower() == ms_in.lower()][["PID"]].copy()
            ms_tr = ms_tr.merge(
                df[["PID", "Aasta"]].drop_duplicates("PID"),
                on="PID", how="left"
            )
            ms_tr = ms_tr[ms_tr["Aasta"].notna()]
            if len(ms_tr) > 0:
                ms_tr["Aastakümme"] = (ms_tr["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
                tc = ms_tr["Aastakümme"].value_counts().sort_index()
                fig = px.line(x=tc.index, y=tc.values, markers=True,
                              labels={"x": "Aastakümme", "y": "Esinemiste arv"},
                              title=f"'{ms_in}' ajas")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Selle märksõnaga aastaga fotosid ei leitud.")

        st.markdown("#### ERA märksõnad (koondatud, top 20)")
        era_ms = df["ERA märksõnad (koondatud)"].dropna()
        era_split = era_ms.str.split(" | ").explode().str.strip()
        era_c = era_split.value_counts().head(20)
        fig = px.bar(x=era_c.values, y=era_c.index, orientation="h",
                     labels={"x": "Esinemiste arv", "y": "ERA märksõna"},
                     color=era_c.values, color_continuous_scale="RdBu")
        fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=420)
        st.plotly_chart(fig, use_container_width=True)

# ══════════════════ TAB 4 – ISIKUD ═══════════════════════════════════════════
with tab4:
    st.subheader("Isikud fotodel")
    df_pids = set(df["PID"].unique())
    isikud_filtered = isikud[isikud["PID"].isin(df_pids)]

    col_i1, col_i2 = st.columns(2)
    with col_i1:
        top_isik_n = st.slider("Näita top N isikut", 10, 50, 20, key="isik_slider")
        isik_top = isikud_filtered["Isik"].value_counts().head(top_isik_n)
        fig = px.bar(x=isik_top.values, y=isik_top.index, orientation="h",
                     labels={"x": "Fotode arv", "y": "Isik"},
                     title=f"Top {top_isik_n} isikut fotodel",
                     color=isik_top.values, color_continuous_scale="Magenta")
        fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=500)
        st.plotly_chart(fig, use_container_width=True)

    with col_i2:
        st.markdown("#### Isiku otsing")
        isik_otsing = st.text_input("Otsi isiku nime järgi")
        if isik_otsing:
            isik_matches = isikud_filtered[
                isikud_filtered["Isik"].str.contains(isik_otsing, case=False, na=False)
            ]
            leitud_pids = isik_matches["PID"].unique()
            df_isik = df[df["PID"].isin(leitud_pids)]
            st.markdown(f"Leitud **{len(df_isik)}** fotot isikuga '{isik_otsing}'")
            if len(df_isik) > 0:
                st.dataframe(
                    df_isik[["PID", "Aasta", "Kihelkond", "Sisu kirjeldus", "failinimi"]].head(50),
                    use_container_width=True, hide_index=True
                )
        else:
            st.markdown("#### Isikute arv fotol")
            isikute_arv = df["Isikute arv"].value_counts().sort_index().head(10)
            fig2 = px.bar(x=isikute_arv.index.astype(str), y=isikute_arv.values,
                          labels={"x": "Isikute arv fotol", "y": "Fotode arv"},
                          title="Kui palju isikuid on fotodel?",
                          color=isikute_arv.values, color_continuous_scale="Teal")
            fig2.update_layout(coloraxis_showscale=False)
            st.plotly_chart(fig2, use_container_width=True)

# ══════════════════ TAB 5 – ANDMETABEL ═══════════════════════════════════════
with tab5:
    st.subheader("Andmetabel")
    vaikimisi = ["PID", "Aasta", "Kihelkond", "Fotograaf (normaliseeritud)",
                 "Žanr", "Sisu kirjeldus", "ERA märksõnad (koondatud)", "failinimi"]
    show_cols = st.multiselect("Vali kuvatavad veerud", options=list(df.columns), default=vaikimisi)

    otsing = st.text_input("🔍 Otsi (sisu, kihelkond, fotograaf)")
    df_show = df.copy()
    if otsing:
        mask = (
            df_show["Sisu kirjeldus"].fillna("").str.contains(otsing, case=False)
            | df_show["Kihelkond"].fillna("").str.contains(otsing, case=False)
            | df_show["Fotograaf (normaliseeritud)"].fillna("").str.contains(otsing, case=False)
        )
        df_show = df_show[mask]

    st.markdown(f"Näidatakse **{len(df_show):,}** rida")
    st.dataframe(df_show[show_cols].head(500), use_container_width=True, height=420)
    if len(df_show) > 500:
        st.caption("ℹ️ Tabelis on esimesed 500 rida. Kitsenda filtritega.")

    csv = df_show[show_cols].to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Lae alla CSV", data=csv,
                       file_name="era_fotod_filteeritud.csv", mime="text/csv")
