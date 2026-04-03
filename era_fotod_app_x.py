import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests

st.set_page_config(page_title="ERA Fotode Andmebaas", page_icon="📷", layout="wide")

import os

@st.cache_data
def load_data():
    # Leiab faili alati samast kaustast kui skript ise
    base_dir = os.path.dirname(os.path.abspath(__file__))
    xlsx_path = os.path.join(base_dir, "ERA_fotod_10.03.26_koordinaatidega_v2.xlsx")
    
    xl = pd.ExcelFile(xlsx_path)
    fotod = xl.parse("fotod_koordinaatidega")
    marksoned = xl.parse("märksõnad_pikk")
    kihelkonnad_kp = xl.parse("Kihelkond_keskpunktid")
    return fotod, marksoned, kihelkonnad_kp

@st.cache_data(ttl=86400)
def load_kihelkond_geojson():
    urls = [
        (
            "https://kaart.maaamet.ee/wfs?service=WFS&version=2.0.0"
            "&request=GetFeature&typeName=AJALOOLINE_HALDUSJAOTUS:kihelkond"
            "&outputFormat=application/json&srsName=EPSG:4326"
        ),
        (
            "https://raw.githubusercontent.com/tormi/"
            "Eesti-ajaloolised-halduspiirid-20ndal-sajandil/master/kihelkonnad.json"
        ),
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                data = r.json()
                if data.get("features"):
                    return data
        except Exception:
            continue
    return None

@st.cache_data(ttl=86400)
def geocode_batch(coords_tuple):
    results = {}
    for lat, lon, pid in list(coords_tuple)[:3000]:
        try:
            url = (
                f"https://inaadress.maaamet.ee/inaadress/gazetteer"
                f"?lat={lat}&lon={lon}&results=1&appartment=0"
            )
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) > 0:
                    item = data[0]
                    results[pid] = {
                        "maakond": item.get("maakond", ""),
                        "vald": item.get("omavalitsus", ""),
                        "asula": item.get("asustusyksus", ""),
                    }
        except Exception:
            pass
    return results


fotod, marksoned, kihelkonnad_kp = load_data()

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

fotograafid = sorted(fotod["Fotograaf (puhastatud)"].dropna().unique().tolist())
valitud_fotograaf = st.sidebar.multiselect("Fotograaf", fotograafid, placeholder="Kõik fotograafid")

# ── Filtreerimine ─────────────────────────────────────────────────────────────
df = fotod.copy()
df_a = df[df["Aasta"].notna()]
df_a = df_a[df_a["Aasta"].astype(int).between(aasta_vahemik[0], aasta_vahemik[1])]
df = pd.concat([df_a, df[df["Aasta"].isna()]])
if valitud_zanr:
    df = df[df["Žanr"].isin(valitud_zanr)]
if valitud_fotograaf:
    df = df[df["Fotograaf (puhastatud)"].isin(valitud_fotograaf)]
if valitud_marksona:
    pids = marksoned[marksoned["Märksõna"].isin(valitud_marksona)]["PID"].unique()
    df = df[df["PID"].isin(pids)]

# ── KPI rida ─────────────────────────────────────────────────────────────────
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

tab1, tab2, tab3, tab4 = st.tabs(["🗺️ Kaart", "📊 Statistika", "🏷️ Märksõnad", "📋 Andmetabel"])

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
                (k for k in sample_props if "kihel" in k.lower() or "nimi" in k.lower()),
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
                title="Fotod kihelkondade kaupa",
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

        fig.update_layout(height=560, margin={"r": 0, "t": 40, "l": 0, "b": 0})
        st.plotly_chart(fig, use_container_width=True)
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
            st.caption(
                "Tänapäevased haldusüksused saadakse Maa-ameti geokodeerimisest. "
                "Esimene käivitamine võtab aega (kuni 3000 fotot) – tulemus salvestatakse vahemällu."
            )

            with st.spinner("Geokodeerin koordinaate Maa-ameti API kaudu..."):
                coords_tuple = tuple(zip(
                    df_coord["lõplik_latitude"].tolist(),
                    df_coord["lõplik_longitude"].tolist(),
                    df_coord["PID"].tolist(),
                ))
                geo_results = geocode_batch(coords_tuple)

            if geo_results:
                df_coord["maakond"] = df_coord["PID"].map(
                    lambda p: geo_results.get(p, {}).get("maakond", ""))
                df_coord["vald"] = df_coord["PID"].map(
                    lambda p: geo_results.get(p, {}).get("vald", ""))
                df_coord["asula"] = df_coord["PID"].map(
                    lambda p: geo_results.get(p, {}).get("asula", ""))

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
                        asulad = sorted([
                            a for a in df_coord[df_coord["vald"] == val_vald]["asula"].dropna().unique() if a
                        ])
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
                st.warning("Geokodeerimine ei õnnestunud. Näidatakse kõik koordinaadiga fotod.")
                df_map = df_coord.copy()
                hover_extra = {}

            sample_size = min(5000, len(df_map))
            df_sample = df_map.sample(n=sample_size, random_state=42) if len(df_map) > 5000 else df_map

            hover_data = {
                "Aasta": True, "Kihelkond": True, "Fotograaf (puhastatud)": True,
                "lõplik_latitude": False, "lõplik_longitude": False,
                **hover_extra,
            }

            fig = px.scatter_mapbox(
                df_sample,
                lat="lõplik_latitude", lon="lõplik_longitude",
                hover_name="Sisu kirjeldus",
                hover_data=hover_data,
                color="lõplik_täpsus",
                zoom=6, center={"lat": 58.7, "lon": 25.0},
                mapbox_style="open-street-map",
                title=(
                    f"Koordinaatidega fotod: {len(df_map):,}"
                    + (f" (näidatakse {sample_size})" if len(df_map) > 5000 else "")
                ),
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

        foto_top = df["Fotograaf (puhastatud)"].value_counts().head(12).dropna()
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
            ms_tr = mf[mf["Märksõna"].str.lower() == ms_in.lower()]
            ms_tr = ms_tr.merge(df[["PID", "Aasta"]], on="PID", how="left")
            ms_tr = ms_tr[ms_tr["Aasta"].notna()]
            ms_tr["Aastakümne"] = (ms_tr["Aasta"].astype(int) // 10 * 10).astype(str) + "ndad"
            tc = ms_tr["Aastakümne"].value_counts().sort_index()
            if len(tc) > 0:
                fig = px.line(x=tc.index, y=tc.values, markers=True,
                              labels={"x": "Aastakümme", "y": "Esinemiste arv"},
                              title=f"'{ms_in}' ajas")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Selle märksõnaga fotosid ei leitud.")

        st.markdown("#### ERA märksõnad (koondatud, top 20)")
        era_ms = df["ERA märksõnad (koondatud)"].dropna()
        era_split = era_ms.str.split(" | ").explode().str.strip()
        era_c = era_split.value_counts().head(20)
        fig = px.bar(x=era_c.values, y=era_c.index, orientation="h",
                     labels={"x": "Esinemiste arv", "y": "ERA märksõna"},
                     color=era_c.values, color_continuous_scale="RdBu")
        fig.update_layout(yaxis={"autorange": "reversed"}, coloraxis_showscale=False, height=420)
        st.plotly_chart(fig, use_container_width=True)

# ══════════════════ TAB 4 – ANDMETABEL ═══════════════════════════════════════
with tab4:
    st.subheader("Andmetabel")
    vaikimisi = ["PID", "Aasta", "Kihelkond", "Fotograaf (puhastatud)",
                 "Žanr", "Sisu kirjeldus", "ERA märksõnad (koondatud)", "failinimi"]
    show_cols = st.multiselect("Vali kuvatavad veerud", options=list(df.columns), default=vaikimisi)

    otsing = st.text_input("🔍 Otsi (sisu, kihelkond, fotograaf)")
    df_show = df.copy()
    if otsing:
        mask = (
            df_show["Sisu kirjeldus"].fillna("").str.contains(otsing, case=False)
            | df_show["Kihelkond"].fillna("").str.contains(otsing, case=False)
            | df_show["Fotograaf (puhastatud)"].fillna("").str.contains(otsing, case=False)
        )
        df_show = df_show[mask]

    st.markdown(f"Näidatakse **{len(df_show):,}** rida")
    st.dataframe(df_show[show_cols].head(500), use_container_width=True, height=420)
    if len(df_show) > 500:
        st.caption("ℹ️ Tabelis on esimesed 500 rida. Kitsenda filtritega.")

    csv = df_show[show_cols].to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Lae alla CSV", data=csv,
                       file_name="era_fotod_filteeritud.csv", mime="text/csv")
