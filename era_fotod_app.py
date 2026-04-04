import streamlit as st
import pandas as pd
import plotly.express as px
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

# ── Andmete laadimine ────────────────────────────────────────────────────────

@st.cache_data
def load_data():
    xlsx_path = os.path.join(BASE_DIR, "ERA_fotod_piiridega.xlsx")
    xl = pd.ExcelFile(xlsx_path)

    fotod = xl.parse("fotod_koordinaatidega")
    marksoned = xl.parse("märksõnad_pikk")
    isikud = xl.parse("isikud_fotol_pikk")

    if "Kihelkond_keskpunktid" in xl.sheet_names:
        kihelkonnad_kp = xl.parse("Kihelkond_keskpunktid")
    else:
        kihelkonnad_kp = pd.DataFrame()

    if "Fotograaf (puhastatud)" in fotod.columns:
        fotod["Fotograaf (normaliseeritud)"] = fotod["Fotograaf (puhastatud)"].map(
            lambda x: FOTOGRAAF_MAPPING.get(x, x) if pd.notna(x) else x
        )
    else:
        fotod["Fotograaf (normaliseeritud)"] = None

    return fotod, marksoned, isikud, kihelkonnad_kp


@st.cache_data
def load_geojson(filename):
    path = os.path.join(BASE_DIR, filename)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


fotod, marksoned, isikud, kihelkonnad_kp = load_data()

# ── Abi veerud ────────────────────────────────────────────────────────────────

for col in ["maakond", "vald", "asula", "kihelkond_kaart", "Kihelkond"]:
    if col in fotod.columns:
        fotod[col] = fotod[col].astype(str).replace("nan", "").replace("None", "").str.strip()

if "Aasta" in fotod.columns:
    fotod["Aasta_num"] = pd.to_numeric(fotod["Aasta"], errors="coerce")
else:
    fotod["Aasta_num"] = pd.NA

fotod["on_koordinaadid"] = fotod["lõplik_latitude"].notna() & fotod["lõplik_longitude"].notna()

# ── Filtreerimise funktsioon ─────────────────────────────────────────────────

def get_filtered_df(
    fotod, marksoned, isikud,
    aasta_vahemik, valitud_zanr,
    valitud_marksona, marksona_loogika,
    valitud_fotograaf, valitud_isik
):
    df = fotod.copy()

    if "Aasta_num" in df.columns:
        df_a = df[df["Aasta_num"].notna()]
        df_a = df_a[df_a["Aasta_num"].astype(int).between(aasta_vahemik[0], aasta_vahemik[1])]
        df = pd.concat([df_a, df[df["Aasta_num"].isna()]])

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

aastad = fotod["Aasta_num"].dropna()
if len(aastad) > 0:
    aasta_vahemik = st.sidebar.slider(
        "Aasta vahemik",
        min_value=int(aastad.min()),
        max_value=int(aastad.max()),
        value=(int(aastad.min()), int(aastad.max())),
    )
else:
    aasta_vahemik = (1800, 2025)

zanrid = sorted(fotod["Žanr"].dropna().astype(str).unique().tolist()) if "Žanr" in fotod.columns else []
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
    df_ilma_ft_isik["Fotograaf (normaliseeritud)"].dropna().astype(str).unique().tolist()
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
c2.metric("Koordinaatidega", f"{df['on_koordinaadid'].sum():,}")
c3.metric("Erinevaid kihelkondi", f"{df[df['Kihelkond'] != '']['Kihelkond'].nunique()}")
c4.metric(
    "Ajavahemik",
    f"{int(df['Aasta_num'].min()) if df['Aasta_num'].notna().any() else '?'}–"
    f"{int(df['Aasta_num'].max()) if df['Aasta_num'].notna().any() else '?'}",
)
st.divider()

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["🗺️ Kaart", "📊 Statistika", "🏷️ Märksõnad", "👤 Isikud", "📋 Andmetabel"]
)

# ══════════════════ TAB 1 – KAART ════════════════════════════════════════════

with tab1:

    if "Kihelkonnapõhine" in asukoha_valik:
        st.subheader("Fotod kihelkondade kaupa")

        kihel_col = "kihelkond_kaart" if "kihelkond_kaart" in df.columns else "Kihelkond"

        kihel_counts = (
            df[df[kihel_col].notna() & (df[kihel_col] != "") & ~df[kihel_col].isin(["teadmata", "välismaa"])]
            .groupby(kihel_col)
            .size()
            .reset_index(name="Fotode arv")
            .rename(columns={kihel_col: "Kihelkond"})
        )

        geojson = load_geojson("kih1922_region.json")

        if not kihel_counts.empty and not kihelkonnad_kp.empty:
            kp_col = "Kihelkond või linn" if "Kihelkond või linn" in kihelkonnad_kp.columns else "Kihelkond"
            kihel_map = kihel_counts.merge(
                kihelkonnad_kp.rename(columns={kp_col: "Kihelkond"}),
                on="Kihelkond",
                how="left"
            )
        else:
            kihel_map = kihel_counts.copy()

        if geojson and not kihel_counts.empty:
            sample_props = geojson["features"][0]["properties"]
            nimiveerg = "KIHELKOND" if "KIHELKOND" in sample_props else list(sample_props.keys())[0]

            fig = px.choropleth_map(
                kihel_counts,
                geojson=geojson,
                locations="Kihelkond",
                featureidkey=f"properties.{nimiveerg}",
                color="Fotode arv",
                color_continuous_scale="YlOrRd",
                hover_name="Kihelkond",
                hover_data={"Fotode arv": True},
                center={"lat": 58.7, "lon": 25.0},
                zoom=6,
                opacity=0.55,
                height=520,
            )

            if "latitude" in kihel_map.columns and "longitude" in kihel_map.columns:
                kihel_pts = kihel_map.dropna(subset=["latitude", "longitude"])
                if not kihel_pts.empty:
                    fig_pts = px.scatter_map(
                        kihel_pts,
                        lat="latitude",
                        lon="longitude",
                        size="Fotode arv",
                        hover_name="Kihelkond",
                        hover_data={"Fotode arv": True, "latitude": False, "longitude": False},
                        size_max=24,
                    )
                    for tr in fig_pts.data:
                        fig.add_trace(tr)

            fig.update_layout(margin={"r": 0, "t": 40, "l": 0, "b": 0})
            st.plotly_chart(fig, use_container_width=True)

        elif not kihel_map.empty and "latitude" in kihel_map.columns and "longitude" in kihel_map.columns:
            kihel_map = kihel_map.dropna(subset=["latitude", "longitude"])
            fig = px.scatter_map(
                kihel_map,
                lat="latitude",
                lon="longitude",
                size="Fotode arv",
                color="Fotode arv",
                hover_name="Kihelkond",
                hover_data={"Fotode arv": True, "latitude": False, "longitude": False},
                size_max=45,
                center={"lat": 58.7, "lon": 25.0},
                zoom=6,
                height=520,
            )
            fig.update_layout(margin={"r": 0, "t": 40, "l": 0, "b": 0})
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Kihelkonna kaardi kuvamiseks pole piisavalt andmeid.")

        st.subheader("Kihelkonna detailvaade")

        if not kihel_counts.empty:
            kihel_valikud = sorted(kihel_counts["Kihelkond"].dropna().tolist())
            val_kihel = st.selectbox("Vali kihelkond", ["—"] + kihel_valikud)

            if val_kihel != "—":
                df_kihel = df[df[kihel_col] == val_kihel]

                k1, k2, k3 = st.columns(3)
                k1.metric("Fotosid", len(df_kihel))
                k2.metric("Koordinaatidega", int(df_kihel["on_koordinaadid"].sum()))
                k3.metric(
                    "Ajavahemik",
                    f"{int(df_kihel['Aasta_num'].min()) if df_kihel['Aasta_num'].notna().any() else '?'}–"
                    f"{int(df_kihel['Aasta_num'].max()) if df_kihel['Aasta_num'].notna().any() else '?'}"
                )

                col_kd1, col_kd2 = st.columns(2)

                with col_kd1:
                    ft = df_kihel["Fotograaf (normaliseeritud)"].value_counts().head(8).reset_index()
                    ft.columns = ["Fotograaf", "Arv"]
                    st.markdown("**Fotograafid**")
                    st.dataframe(ft, hide_index=True, use_container_width=True)

                with col_kd2:
                    ms_kihel = (
                        marksoned[marksoned["PID"].isin(df_kihel["PID"])]["Märksõna"]
                        .value_counts()
                        .head(8)
                        .reset_index()
                    )
                    ms_kihel.columns = ["Märksõna", "Arv"]
                    st.markdown("**Top märksõnad**")
                    st.dataframe(ms_kihel, hide_index=True, use_container_width=True)

        st.caption("Näidatakse ajaloolisi kihelkondi.")

    else:
        st.subheader("Fotod koordinaatide järgi – tänapäevane haldusjaotus")

        df_coord = df[df["on_koordinaadid"]].copy()

        if df_coord.empty:
            st.warning("Valitud filtritega koordinaatidega fotosid ei leitud.")
        else:
            col_f1, col_f2, col_f3, col_f4 = st.columns(4)

            with col_f1:
                maakonnad = sorted([m for m in df_coord["maakond"].dropna().unique() if str(m).strip()])
                val_maakond = st.selectbox("Maakond", ["Kõik"] + maakonnad)

            tmp = df_coord if val_maakond == "Kõik" else df_coord[df_coord["maakond"] == val_maakond]

            with col_f2:
                vallad = sorted([v for v in tmp["vald"].dropna().unique() if str(v).strip()])
                val_vald = st.selectbox("Vald", ["Kõik"] + vallad)

            tmp2 = tmp if val_vald == "Kõik" else tmp[tmp["vald"] == val_vald]

            with col_f3:
                kihelkonnad = sorted([k for k in tmp2["kihelkond_kaart"].dropna().unique() if str(k).strip()])
                val_kihel = st.selectbox("Kihelkond", ["Kõik"] + kihelkonnad)

            with col_f4:
                tapsused = sorted([t for t in df_coord["lõplik_täpsus"].dropna().unique() if str(t).strip()])
                val_tapsus = st.selectbox("Koordinaadi täpsus", ["Kõik"] + tapsused)

            df_map = df_coord.copy()

            if val_maakond != "Kõik":
                df_map = df_map[df_map["maakond"] == val_maakond]
            if val_vald != "Kõik":
                df_map = df_map[df_map["vald"] == val_vald]
            if val_kihel != "Kõik":
                df_map = df_map[df_map["kihelkond_kaart"] == val_kihel]
            if val_tapsus != "Kõik":
                df_map = df_map[df_map["lõplik_täpsus"] == val_tapsus]

            if df_map.empty:
                st.warning("Nende filtritega kaardile midagi ei jäänud.")
            else:
                sample_size = min(5000, len(df_map))
                df_sample = df_map.sample(n=sample_size, random_state=42) if len(df_map) > 5000 else df_map

                fig = px.scatter_map(
                    df_sample,
                    lat="lõplik_latitude",
                    lon="lõplik_longitude",
                    color="lõplik_täpsus",
                    hover_name="Sisu kirjeldus",
                    hover_data={
                        "Aasta": True,
                        "Kihelkond": True,
                        "kihelkond_kaart": True,
                        "maakond": True,
                        "vald": True,
                        "asula": True,
                        "Fotograaf (normaliseeritud)": True,
                        "lõplik_latitude": False,
                        "lõplik_longitude": False,
                    },
                    center={"lat": 58.7, "lon": 25.0},
                    zoom=6,
                    height=560,
                )

                fig.update_traces(marker=dict(size=7, opacity=0.75))
                fig.update_layout(margin={"r": 0, "t": 40, "l": 0, "b": 0})
                st.plotly_chart(fig, use_container_width=True)

                st.caption(f"Koordinaatidega fotosid: {len(df_coord):,} | Filtreeritud: {len(df_map):,}")

# ══════════════════ TAB 2 – STATISTIKA ═══════════════════════════════════════

with tab2:
    col_left, col_right = st.columns(2)

    with col_left:
        df_a2 = df[df["Aasta_num"].notna()].copy()
        if not df_a2.empty:
            df_a2["Aastakümme"] = (df_a2["Aasta_num"].astype(int) // 10 * 10).astype(str) + "ndad"
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

        kihel_col = "kihelkond_kaart" if "kihelkond_kaart" in df.columns else "Kihelkond"
        kihel_top = (
            df[df[kihel_col].notna() & (df[kihel_col] != "") & ~df[kihel_col].isin(["teadmata", "välismaa"])]
            [kihel_col].value_counts().head(15)
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
        zanr_c = df["Žanr"].dropna().value_counts().head(15)
        if len(zanr_c) > 0:
            fig = px.pie(values=zanr_c.values, names=zanr_c.index, title="Žanrite jaotus (top 15)")
            fig.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig, use_container_width=True)

        foto_top = df["Fotograaf (normaliseeritud)"].dropna().value_counts().head(12)
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
        proj_c = df["Projekt"].dropna().value_counts().head(10)
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
    df_pids = set(df["PID"].unique())
    mf = marksoned[marksoned["PID"].isin(df_pids)]

    col_m1, col_m2 = st.columns(2)

    with col_m1:
        top_n = st.slider("Näita top N märksõna", 10, 50, 20)
        ms_c = mf["Märksõna"].value_counts().head(top_n)
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

        if ms_in:
            ms_tr = mf[mf["Märksõna"].str.lower() == ms_in.lower()][["PID"]].copy()
            ms_tr = ms_tr.merge(df[["PID", "Aasta_num"]].drop_duplicates("PID"), on="PID", how="left")
            ms_tr = ms_tr[ms_tr["Aasta_num"].notna()]

            if len(ms_tr) > 0:
                ms_tr["Aastakümme"] = (ms_tr["Aasta_num"].astype(int) // 10 * 10).astype(str) + "ndad"
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

        st.markdown("#### ERA märksõnad (koondatud, top 20)")
        if "ERA märksõnad (koondatud)" in df.columns:
            era_ms = df["ERA märksõnad (koondatud)"].dropna()
            era_split = era_ms.str.split(" \\| ").explode().str.strip()
            era_c = era_split.value_counts().head(20)

            if len(era_c) > 0:
                fig = px.bar(
                    x=era_c.values,
                    y=era_c.index,
                    orientation="h",
                    labels={"x": "Esinemiste arv", "y": "ERA märksõna"},
                    color=era_c.values,
                    color_continuous_scale="RdBu",
                )
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
                    use_container_width=True,
                    hide_index=True,
                )
        else:
            if "Isikute arv" in df.columns:
                isikute_arv = df["Isikute arv"].value_counts().sort_index().head(10)
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
        "PID", "Aasta", "Kihelkond", "kihelkond_kaart", "maakond", "vald",
        "Fotograaf (normaliseeritud)", "Žanr", "Sisu kirjeldus",
        "ERA märksõnad (koondatud)", "failinimi"
    ]
    vaikimisi = [c for c in vaikimisi if c in df.columns]

    show_cols = st.multiselect(
        "Vali kuvatavad veerud",
        options=list(df.columns),
        default=vaikimisi
    )

    otsing = st.text_input("🔍 Otsi (sisu, kihelkond, fotograaf)")
    df_show = df.copy()

    if otsing:
        mask = (
            df_show["Sisu kirjeldus"].fillna("").astype(str).str.contains(otsing, case=False)
            | df_show["Kihelkond"].fillna("").astype(str).str.contains(otsing, case=False)
            | df_show["Fotograaf (normaliseeritud)"].fillna("").astype(str).str.contains(otsing, case=False)
        )
        df_show = df_show[mask]

    st.markdown(f"Näidatakse **{len(df_show):,}** rida")
    st.dataframe(df_show[show_cols].head(500), use_container_width=True, height=420)

    if len(df_show) > 500:
        st.caption("Tabelis on esimesed 500 rida. Kitsenda filtritega.")

    csv = df_show[show_cols].to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Lae alla CSV",
        data=csv,
        file_name="era_fotod_filteeritud.csv",
        mime="text/csv"
    )
