"""
Dashboard RST — Hofmann
Análisis de calidad de leads por fuente, país y estado.
Fuente de datos: formularios FORM_HighTicket_CA / EN / ES.
"""
import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import os

load_dotenv()

try:
    TOKEN = st.secrets["HUBSPOT_TOKEN"]
except Exception:
    TOKEN = os.getenv("HUBSPOT_TOKEN", "")

try:
    ACCOUNT_NAME = st.secrets["ACCOUNT_NAME"]
except Exception:
    ACCOUNT_NAME = os.getenv("ACCOUNT_NAME", "Hofmann")

if not TOKEN:
    st.error("❌ HUBSPOT_TOKEN no encontrado. Configúralo en Streamlit Cloud → Settings → Secrets.")
    st.stop()

# ── Paleta oficial Barça ───────────────────────────────────────────────────────
BARCA = {
    "blue":         "#004D98",
    "blue_deep":    "#003B7A",
    "blue_ink":     "#001A40",
    "garnet":       "#A50044",
    "garnet_deep":  "#850036",
    "gold":         "#EDBB00",
    "yellow":       "#FFED02",
    "white":        "#FFFFFF",
    "paper":        "#FAFAFA",
    "bone":         "#F4F2EE",
    "line":         "#E5E5E5",
    "line2":        "#D9D9D9",
    "ink100":       "#111111",
    "ink80":        "#2A2A2A",
    "ink60":        "#555555",
    "ink40":        "#8A8A8A",
    "ink20":        "#BFBFBF",
}

COLOR_ESTADOS = {
    "Cierre ganado":        BARCA["gold"],
    "Deal abierto":         BARCA["garnet"],
    "Contactado":           BARCA["blue"],
    "Intentando contactar": BARCA["yellow"],
    "Nuevo":                BARCA["blue_deep"],
    "Perdido":              BARCA["garnet_deep"],
    "Sin estado":           BARCA["line2"],
}

COLOR_FUENTES = [
    BARCA["blue_ink"], BARCA["blue_deep"], BARCA["blue"],
    BARCA["garnet_deep"], BARCA["garnet"],
    BARCA["gold"], BARCA["yellow"],
    BARCA["ink60"], BARCA["ink40"], BARCA["ink20"],
]

st.set_page_config(
    page_title=f"RST Dashboard — {ACCOUNT_NAME}",
    page_icon="🔵",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(f"""
<style>
  [data-testid="stAppViewContainer"] {{ background:{BARCA['paper']}; }}
  [data-testid="stSidebar"] {{ background:{BARCA['blue_ink']} !important; }}
  [data-testid="stSidebar"] label,
  [data-testid="stSidebar"] p,
  [data-testid="stSidebar"] small {{ color:{BARCA['white']} !important; }}
  .stButton>button {{
      background:{BARCA['garnet']} !important;
      color:{BARCA['white']} !important;
      border:none !important; font-weight:700;
  }}
  .stButton>button:hover {{ background:{BARCA['garnet_deep']} !important; }}
  h1,h2,h3 {{ color:{BARCA['blue_ink']}; }}
  hr {{ border-color:{BARCA['line']}; }}
</style>
""", unsafe_allow_html=True)

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"

FUENTES_ES = {
    "ORGANIC_SEARCH":  "Búsqueda orgánica",
    "PAID_SEARCH":     "Búsqueda pagada",
    "EMAIL_MARKETING": "Email marketing",
    "SOCIAL_MEDIA":    "Redes sociales",
    "REFERRALS":       "Referencias",
    "OTHER_CAMPAIGNS": "Otras campañas",
    "DIRECT_TRAFFIC":  "Tráfico directo",
    "OFFLINE":         "Offline",
    "PAID_SOCIAL":     "Social pagado",
    "AI_REFERRALS":    "Referral IA",
}

LEAD_STATUS_NORM = {
    "new":            "Nuevo",
    "in_progress":    "Intentando contactar",
    "connected":      "Contactado",
    "open_deal":      "Deal abierto",
    "cierre ganado":  "Cierre ganado",
    "perdido":        "Perdido",
}

ESTADOS_ORDEN = [
    "Cierre ganado", "Deal abierto", "Contactado",
    "Intentando contactar", "Nuevo",
    "Perdido", "Sin estado",
]

CONTACT_PROPS = [
    "email",
    "pais_de_residencia", "ip_country", "country", "billing_country",
    "pais_de_la_ip_capabilia",
    "hs_lead_status", "num_contacted_notes", "estado_de_lead_no_valido",
    "motivos_de_cierre_perdido_rst",
    "hs_analytics_source", "hs_analytics_source_data_1",
    "hs_latest_source", "hs_latest_source_data_1",
]


# ── Data helpers ──────────────────────────────────────────────────────────────

def resolve_pais(cp):
    for f in ["pais_de_residencia", "ip_country", "pais_de_la_ip_capabilia",
              "country", "billing_country"]:
        v = (cp.get(f) or "").strip()
        if v:
            return v.title()
    return "Sin datos"


def resolve_pais_form(sub, cp):
    """Prefer pais_de_residencia from the form submission, fallback to contact props."""
    pais = (sub.get("pais_form") or "").strip()
    if pais:
        return pais.title()
    return resolve_pais(cp)


def resolve_fuente(cp):
    raw_o = (cp.get("hs_analytics_source") or "").strip()
    raw_r = (cp.get("hs_latest_source") or "").strip()
    if raw_o:
        return FUENTES_ES.get(raw_o, raw_o.replace("_", " ").title()), "Original"
    if raw_r:
        return FUENTES_ES.get(raw_r, raw_r.replace("_", " ").title()), "Más reciente"
    return "Sin datos", "—"


def norm_status(raw):
    if not raw:
        return "Sin estado"
    return LEAD_STATUS_NORM.get(raw.lower().strip(), raw.strip().title())


# ── Fetching de contactos (con caché) ─────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_data(fecha_inicio: str, fecha_fin: str) -> pd.DataFrame:
    test = requests.get(f"{BASE}/crm/v3/objects/contacts?limit=1", headers=HEADERS)
    if test.status_code == 401:
        st.error("❌ Token de HubSpot inválido. Revisa el Secret HUBSPOT_TOKEN.")
        st.stop()

    filters = []
    if fecha_inicio != "todos":
        fi_ts = int(datetime.fromisoformat(fecha_inicio)
                    .replace(tzinfo=timezone.utc).timestamp() * 1000)
        ff_ts = (int(datetime.fromisoformat(fecha_fin)
                     .replace(tzinfo=timezone.utc).timestamp() * 1000)
                 + 86_400_000 - 1)
        filters = [
            {"propertyName": "createdate", "operator": "GTE", "value": str(fi_ts)},
            {"propertyName": "createdate", "operator": "LTE", "value": str(ff_ts)},
        ]

    rows = []
    after = None
    while True:
        payload = {
            "filterGroups": [{"filters": filters}],
            "properties": CONTACT_PROPS + ["createdate"],
            "limit": 100,
        }
        if after:
            payload["after"] = after
        try:
            r = requests.post(f"{BASE}/crm/v3/objects/contacts/search",
                              headers=HEADERS, json=payload, timeout=30)
            if r.status_code != 200:
                break
            data = r.json()
        except Exception:
            break

        for c in data.get("results", []):
            cp = c["properties"]
            email = (cp.get("email") or "").lower().strip()
            if not email:
                continue
            fuente, origen = resolve_fuente(cp)
            createdate = (cp.get("createdate") or "")[:10]
            rows.append({
                "email":            email,
                "fecha":            createdate,
                "mes":              createdate[:7] if createdate else "",
                "pais":             resolve_pais(cp),
                "lead_status":      norm_status(cp.get("hs_lead_status")),
                "intentos":         int(cp.get("num_contacted_notes") or 0),
                "motivo_no_valido": cp.get("estado_de_lead_no_valido") or "Sin especificar",
                "motivo_cierre":    cp.get("motivos_de_cierre_perdido_rst") or "Sin especificar",
                "fuente":           fuente,
                "origen_fuente":    origen,
            })

        pg = data.get("paging", {})
        if not pg or "next" not in pg:
            break
        after = pg["next"]["after"]

    return pd.DataFrame(rows)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_matriculados_total() -> pd.DataFrame:
    """
    Todos los contactos Matriculados del equipo RST.
    Usa el historial de la propiedad hs_lead_status para obtener la fecha
    EXACTA en que cada contacto pasó a estado 'Matriculado'.
    """
    # 1. Obtener todos los contactos con status=Matriculado
    contact_ids = []
    contact_props_map = {}
    after = None
    while True:
        payload = {
            "filterGroups": [{
                "filters": [
                    {"propertyName": "hs_lead_status", "operator": "EQ", "value": "Cierre ganado"},
                ]
            }],
            "properties": CONTACT_PROPS,
            "limit": 100,
        }
        if after:
            payload["after"] = after
        try:
            r = requests.post(f"{BASE}/crm/v3/objects/contacts/search",
                              headers=HEADERS, json=payload, timeout=30)
            if r.status_code != 200:
                break
            data = r.json()
        except Exception:
            break
        for c in data.get("results", []):
            contact_ids.append(c["id"])
            contact_props_map[c["id"]] = c["properties"]
        pg = data.get("paging", {})
        if not pg or "next" not in pg:
            break
        after = pg["next"]["after"]

    if not contact_ids:
        return pd.DataFrame()

    # 2. Batch read con historial de hs_lead_status → fecha real de matriculación
    matriculation_dates = {}
    for i in range(0, len(contact_ids), 100):
        batch = contact_ids[i:i + 100]
        try:
            r = requests.post(
                f"{BASE}/crm/v3/objects/contacts/batch/read",
                headers=HEADERS,
                json={
                    "inputs": [{"id": cid} for cid in batch],
                    "properties": ["email"],
                    "propertiesWithHistory": ["hs_lead_status"],
                },
                timeout=30,
            )
            if r.status_code != 200:
                continue
            for c in r.json().get("results", []):
                history = (c.get("propertiesWithHistory") or {}).get("hs_lead_status", [])
                for change in history:
                    if change.get("value") == "Cierre ganado":
                        matriculation_dates[c["id"]] = (change.get("timestamp") or "")[:10]
                        break
        except Exception:
            pass

    # 3. Construir dataframe
    rows = []
    for cid in contact_ids:
        cp = contact_props_map[cid]
        fuente, origen = resolve_fuente(cp)
        # Fecha de matriculación real; fallback a createdate si no hay historial
        fecha_mat = matriculation_dates.get(cid) or (cp.get("createdate") or "")[:10]
        rows.append({
            "email":            (cp.get("email") or "").lower().strip(),
            "fecha":            fecha_mat,
            "mes":              fecha_mat[:7] if fecha_mat else "",
            "pais":             resolve_pais(cp),
            "lead_status":      "Cierre ganado",
            "fuente":           fuente,
            "origen_fuente":    origen,
            "intentos":         int(cp.get("num_contacted_notes") or 0),
            "motivo_no_valido": cp.get("estado_de_lead_no_valido") or "Sin especificar",
            "motivo_cierre":    cp.get("motivos_de_cierre_perdido_rst") or "Sin especificar",
        })
    return pd.DataFrame(rows)


PIPELINE_ID   = "default"
STAGE_GANADO  = "closedwon"    # Cierre Ganado
STAGE_PERDIDO = "closedlost"   # Cierre Perdido

MOTIVOS_CIERRE_ORDEN = [
    "Motivos económicos", "Ilocalizado", "No se presenta a la reunión",
    "Motivos de producto", "Próxima convocatoria", "Horarios no compatibles",
    "Interés en otra escuela", "Sin especificar",
]


@st.cache_data(ttl=300, show_spinner=False)
def fetch_negocios_cerrados() -> pd.DataFrame:
    """
    Todos los deals cerrados (ganado + perdido) del pipeline RST.
    Enriquece cada deal con la fuente de tráfico del contacto asociado.
    Fecha de filtrado: closedate del deal.
    Los motivos múltiples (separados por ';') se expanden a una fila por motivo.
    """
    # 1. Recoger todos los deals cerrados con sus propiedades base
    deal_map = {}   # deal_id → {etapa, motivos, fecha_cierre}
    for stage_id, stage_label in [(STAGE_GANADO, "Cierre ganado"),
                                   (STAGE_PERDIDO, "Cierre perdido")]:
        after = None
        while True:
            payload = {
                "filterGroups": [{"filters": [
                    {"propertyName": "pipeline",  "operator": "EQ", "value": PIPELINE_ID},
                    {"propertyName": "dealstage", "operator": "EQ", "value": stage_id},
                ]}],
                "properties": ["dealname", "closedate", "createdate",
                               "motivos_de_cierre_perdido_rst"],
                "limit": 100,
            }
            if after:
                payload["after"] = after
            try:
                r = requests.post(f"{BASE}/crm/v3/objects/deals/search",
                                  headers=HEADERS, json=payload, timeout=30)
                if r.status_code != 200:
                    break
                data = r.json()
            except Exception:
                break

            for d in data.get("results", []):
                p = d["properties"]
                fecha_cierre = (p.get("closedate") or p.get("createdate") or "")[:10]
                raw_motivos  = (p.get("motivos_de_cierre_perdido_rst") or "Sin especificar").strip()
                motivos = [m.strip() for m in raw_motivos.split(";") if m.strip()] or ["Sin especificar"]
                deal_map[d["id"]] = {
                    "etapa":        stage_label,
                    "motivos":      motivos,
                    "fecha_cierre": fecha_cierre,
                    "mes":          fecha_cierre[:7] if fecha_cierre else "",
                }

            pg = data.get("paging", {})
            if not pg or "next" not in pg:
                break
            after = pg["next"]["after"]

    if not deal_map:
        return pd.DataFrame()

    # 2. Obtener contacto asociado a cada deal (batch associations)
    deal_ids = list(deal_map.keys())
    deal_to_contact = {}
    for i in range(0, len(deal_ids), 100):
        batch = deal_ids[i:i + 100]
        try:
            r = requests.post(
                f"{BASE}/crm/v4/associations/deals/contacts/batch/read",
                headers=HEADERS,
                json={"inputs": [{"id": did} for did in batch]},
                timeout=30,
            )
            if r.status_code == 200:
                for item in r.json().get("results", []):
                    did = str(item.get("from", {}).get("id", ""))
                    tos = item.get("to", [])
                    if tos:
                        deal_to_contact[did] = str(tos[0]["toObjectId"])
        except Exception:
            pass

    # 3. Batch read fuente de tráfico y país de los contactos asociados
    contact_ids = list(set(deal_to_contact.values()))
    contact_data = {}   # contact_id → {fuente, pais}
    for i in range(0, len(contact_ids), 100):
        batch = contact_ids[i:i + 100]
        try:
            r = requests.post(
                f"{BASE}/crm/v3/objects/contacts/batch/read",
                headers=HEADERS,
                json={"inputs": [{"id": c} for c in batch],
                      "properties": [
                          "hs_analytics_source", "hs_latest_source",
                          "pais_de_residencia", "ip_country", "country",
                          "billing_country", "pais_de_la_ip_capabilia",
                      ]},
                timeout=30,
            )
            if r.status_code == 200:
                for c in r.json().get("results", []):
                    cp = c["properties"]
                    fuente, _ = resolve_fuente(cp)
                    contact_data[str(c["id"])] = {
                        "fuente": fuente,
                        "pais":   resolve_pais(cp),
                    }
        except Exception:
            pass

    # 4. Construir dataframe expandiendo motivos múltiples
    rows = []
    for did, info in deal_map.items():
        cid  = deal_to_contact.get(did, "")
        data = contact_data.get(cid, {"fuente": "Sin datos", "pais": "Sin datos"})
        for motivo in info["motivos"]:
            rows.append({
                "deal_id":      did,
                "etapa":        info["etapa"],
                "motivo":       motivo,
                "fuente":       data["fuente"],
                "pais":         data["pais"],
                "fecha_cierre": info["fecha_cierre"],
                "mes":          info["mes"],
            })

    return pd.DataFrame(rows)


# ── Helpers de gráficos ───────────────────────────────────────────────────────

def barca_layout(fig, height=340):
    fig.update_layout(
        height=height,
        paper_bgcolor=BARCA["white"],
        plot_bgcolor=BARCA["white"],
        font_color=BARCA["ink80"],
        title_font=dict(size=14, color=BARCA["blue_ink"]),
        margin=dict(t=44, b=12, l=12, r=12),
        legend=dict(font=dict(size=10)),
    )
    fig.update_xaxes(gridcolor=BARCA["line"], linecolor=BARCA["line2"])
    fig.update_yaxes(gridcolor=BARCA["line"], linecolor=BARCA["line2"])
    return fig


def kpi_card(col, label, value, color=BARCA["blue"]):
    with col:
        st.markdown(f"""
        <div style="background:{BARCA['white']};
                    border-left:5px solid {color};
                    border-radius:8px;padding:18px 20px;
                    box-shadow:0 1px 4px rgba(0,0,0,.08)">
            <div style="font-size:11px;color:{BARCA['ink60']};font-weight:700;
                        text-transform:uppercase;letter-spacing:.7px;
                        margin-bottom:6px">{label}</div>
            <div style="font-size:34px;font-weight:800;
                        color:{color};line-height:1">{value}</div>
        </div>""", unsafe_allow_html=True)


def chart_donut(df, col, title, color_map=None):
    counts = df[col].value_counts().reset_index()
    counts.columns = [col, "Total"]
    fig = px.pie(counts, names=col, values="Total", title=title,
                 hole=0.55, color=col,
                 color_discrete_map=color_map or {})
    fig.update_traces(textposition="outside", textinfo="percent+label",
                      marker=dict(line=dict(color=BARCA["white"], width=2)))
    return barca_layout(fig, 320)


def conclusiones(df, df_mat, df_deals_periodo):
    """
    df               → leads del período (por fecha de envío de formulario)
    df_mat           → matriculados del período (por fecha real de matriculación)
    df_deals_periodo → deals cerrados del período (por closedate)
    """
    total = len(df)
    if total == 0:
        return

    perdido      = df[df["lead_status"] == "Perdido"]
    contactados  = df[df["lead_status"] == "Contactado"]
    intentando   = df[df["lead_status"] == "Intentando contactar"]
    mala_calidad = perdido

    # Cierres ganados y perdidos vienen de sus fuentes correctas
    n_mat          = len(df_mat)
    tasa_mala      = len(mala_calidad) / total * 100
    tasa_mat       = n_mat / total * 100 if total else 0

    perdidos = (df_deals_periodo[df_deals_periodo["etapa"] == "Cierre perdido"]
                if not df_deals_periodo.empty else pd.DataFrame())
    ganados  = (df_deals_periodo[df_deals_periodo["etapa"] == "Cierre ganado"]
                if not df_deals_periodo.empty else pd.DataFrame())

    st.markdown(f"""<hr style="border:1px solid {BARCA['line']};margin:32px 0 24px">""",
                unsafe_allow_html=True)
    st.markdown("## 🔍 Análisis y Conclusiones")

    # ── Resumen ejecutivo + Embudo ─────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 📌 Resumen ejecutivo")
        st.markdown(f"""
- **Leads nuevos en el período:** {total}
- **Cierre ganado en el período:** **{n_mat}** (fecha real de cierre)
- **Tasa de conversión leads → cierre ganado:** **{tasa_mat:.1f}%**
- **Tasa de perdidos:** **{tasa_mala:.1f}%** ({len(mala_calidad)})
- **Cierre perdido:** {len(perdidos)} deals · **Cierre ganado:** {len(ganados)} deals
""")

    with col2:
        # Embudo correcto: usa df_mat para la etapa final de matriculación
        funnel_df = pd.DataFrame({
            "Etapa": [
                f"Leads nuevos ({total})",
                f"Contactados ({len(contactados) + n_mat})",
                f"Intentando contactar ({len(intentando)})",
                f"Cierre ganado ({n_mat})",
            ],
            "Cantidad": [
                total,
                len(contactados) + n_mat,
                len(intentando),
                n_mat,
            ],
        })
        fig = px.funnel(funnel_df, x="Cantidad", y="Etapa",
                        title="Embudo de conversión del período",
                        color_discrete_sequence=[BARCA["blue"], BARCA["blue_deep"],
                                                  BARCA["garnet"], BARCA["gold"]])
        barca_layout(fig, 300)
        st.plotly_chart(fig, use_container_width=True)

    # ── Fuentes con mayor tasa de mala calidad ─────────────────────────────────
    st.markdown("### ⚠️ Fuentes con mayor tasa de mala calidad")
    if len(mala_calidad) > 0:
        mq = mala_calidad.groupby("fuente").size().reset_index(name="Mala_calidad")
        tf = df.groupby("fuente").size().reset_index(name="Total")
        merge = mq.merge(tf, on="fuente")
        merge["Tasa %"] = (merge["Mala_calidad"] / merge["Total"] * 100).round(1)
        merge = merge.sort_values("Tasa %", ascending=False)

        col1, col2 = st.columns([2, 1])
        with col1:
            fig = px.bar(merge, x="fuente", y="Tasa %",
                         color="Tasa %", text="Tasa %",
                         title="% de mala calidad por fuente",
                         color_continuous_scale=[BARCA["blue"], BARCA["gold"],
                                                  BARCA["garnet"]])
            fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
            fig.update_layout(coloraxis_showscale=False)
            barca_layout(fig, 320)
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            st.dataframe(
                merge[["fuente", "Total", "Mala_calidad", "Tasa %"]]
                .rename(columns={"fuente": "Fuente", "Mala_calidad": "Mala calidad"}),
                hide_index=True, use_container_width=True
            )

        st.markdown("#### 💡 Acciones recomendadas")
        acciones = {
            "Social pagado":     "Revisar segmentación de audiencias en Meta/TikTok. Excluir audiencias con alta tasa de no válidos. Testear creatividades con mensajes más cualificadores.",
            "Búsqueda pagada":   "Auditar palabras clave negativas. Añadir preguntas de cualificación en landing pages. Revisar match types.",
            "Redes sociales":    "Leads orgánicos de RRSS con menor intención. Implementar formulario de pre-cualificación antes de entrar al CRM.",
            "Otras campañas":    "Identificar qué campañas específicas generan este tráfico. Revisar UTMs y optimizar las de peor calidad.",
            "Tráfico directo":   "Alta variabilidad. Implementar mejor tracking para identificar el origen real de estos leads.",
            "Búsqueda orgánica": "Revisar qué páginas/keywords atraen leads de baja calidad. Ajustar el copy para cualificar mejor la intención.",
            "Offline":           "Mejorar el briefing a los captadores. Definir criterios mínimos de cualificación antes de registrar en CRM.",
            "Referencias":       "Comunicar mejor el perfil de cliente ideal a los referidores.",
        }
        for _, row in merge.head(5).iterrows():
            fuente = row["fuente"]
            tasa = row["Tasa %"]
            if tasa > 5:
                accion = acciones.get(fuente, "Revisar la fuente y ajustar la estrategia de captación.")
                border = BARCA["garnet"] if tasa > 25 else BARCA["gold"]
                bg = "#FFF5F7" if tasa > 25 else "#FFFDE7"
                badge = "🔴 ALTA" if tasa > 25 else "🟡 MEDIA"
                st.markdown(f"""
<div style="background:{bg};border-left:4px solid {border};
            padding:12px 16px;border-radius:6px;margin:6px 0">
  <span style="font-weight:700;color:{BARCA['blue_ink']}">{badge} · {fuente}</span>
  <span style="color:{BARCA['ink60']};font-size:13px;margin-left:8px">
    {tasa:.1f}% mala calidad · {int(row['Mala_calidad'])} de {int(row['Total'])} contactos
  </span><br>
  <span style="color:{BARCA['ink60']};font-size:13px">→ {accion}</span>
</div>""", unsafe_allow_html=True)

    # ── Países con mayor tasa de mala calidad ─────────────────────────────────
    st.markdown("### 🌍 Países con mayor tasa de mala calidad")
    if len(mala_calidad) > 0:
        mq_p  = mala_calidad.groupby("pais").size().reset_index(name="Mala calidad")
        tot_p = df.groupby("pais").size().reset_index(name="Total leads")
        mp    = mq_p.merge(tot_p, on="pais")
        mp["Buenos"]  = mp["Total leads"] - mp["Mala calidad"]
        mp["Tasa %"]  = (mp["Mala calidad"] / mp["Total leads"] * 100).round(1)
        mp_min5 = mp[mp["Total leads"] >= 5].sort_values("Tasa %", ascending=False)
        mp_top  = mp_min5.head(10)

        col_g, col_t = st.columns([3, 2])
        with col_g:
            fig = px.bar(mp_top, x="pais", y="Tasa %", text="Tasa %",
                         color="Tasa %",
                         title="Top 10 países — % mala calidad (mín. 5 leads)",
                         color_continuous_scale=[BARCA["blue"], BARCA["gold"], BARCA["garnet"]])
            fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
            fig.update_layout(coloraxis_showscale=False)
            barca_layout(fig, 340)
            st.plotly_chart(fig, use_container_width=True)

        with col_t:
            tabla_pais = (mp_min5[["pais", "Total leads", "Mala calidad", "Buenos", "Tasa %"]]
                          .rename(columns={"pais": "País"})
                          .reset_index(drop=True))
            # Colorear la columna Tasa % por severidad
            st.dataframe(
                tabla_pais.style.background_gradient(
                    subset=["Tasa %"],
                    cmap="RdYlGn_r",
                    vmin=0, vmax=100,
                ).format({"Tasa %": "{:.1f}%"}),
                use_container_width=True,
                hide_index=True,
                height=min(500, len(tabla_pais) * 36 + 40),
            )

    # ── Tabla pivote: Contactos por País × Fuente de tráfico ──────────────────
    st.markdown("### 🗺️ Contactos por País y Fuente de tráfico")
    if not df.empty:
        pivot = (df.groupby(["pais", "fuente"])
                 .size()
                 .reset_index(name="Contactos")
                 .pivot(index="pais", columns="fuente", values="Contactos")
                 .fillna(0)
                 .astype(int))
        # Añadir columna Total y ordenar por ella
        pivot.insert(0, "Total", pivot.sum(axis=1))
        pivot = pivot.sort_values("Total", ascending=False)
        pivot.index.name = "País"

        st.dataframe(
            pivot.style.background_gradient(
                subset=pivot.columns.tolist(),
                cmap="Blues",
                vmin=0,
            ).format("{:,}"),
            use_container_width=True,
            height=min(600, len(pivot) * 36 + 60),
        )
        st.download_button(
            "⬇️ Descargar tabla País × Fuente",
            data=pivot.reset_index().to_csv(index=False, encoding="utf-8-sig"),
            file_name="pais_fuente_trafico.csv",
            mime="text/csv",
            key="dl_pivot",
        )

    # ── Matriculados del período: desglose por fuente y país ──────────────────
    if n_mat > 0:
        st.markdown("### 🎓 Fuente y país de los cierres ganados del período")
        col1, col2 = st.columns(2)
        with col1:
            mat_f = df_mat.groupby("fuente").size().reset_index(name="Cierre ganado")
            fig = px.bar(mat_f.sort_values("Cierre ganado", ascending=True),
                         x="Cierre ganado", y="fuente", orientation="h",
                         text_auto=True, title="Cierre ganado por fuente",
                         color_discrete_sequence=[BARCA["gold"]])
            fig.update_layout(yaxis=dict(categoryorder="total ascending"))
            barca_layout(fig, 300)
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            mat_p = (df_mat.groupby("pais").size().reset_index(name="Cierre ganado")
                     .sort_values("Cierre ganado", ascending=False).head(10))
            fig = px.bar(mat_p.sort_values("Cierre ganado", ascending=True),
                         x="Cierre ganado", y="pais", orientation="h",
                         text_auto=True, title="Cierre ganado por país (Top 10)",
                         color_discrete_sequence=[BARCA["gold"]])
            fig.update_layout(yaxis=dict(categoryorder="total ascending"))
            barca_layout(fig, 300)
            st.plotly_chart(fig, use_container_width=True)

    # ── Leads Perdidos por fuente ──────────────────────────────────────────────
    if len(mala_calidad) > 0:
        st.markdown("### ❌ Leads Perdidos por fuente")
        tot_fuente = df.groupby("fuente").size().reset_index(name="Total leads")
        perd_fuente = mala_calidad.groupby("fuente").size().reset_index(name="Perdidos")
        tabla_p = tot_fuente.merge(perd_fuente, on="fuente", how="left").fillna(0)
        tabla_p["Perdidos"] = tabla_p["Perdidos"].astype(int)
        tabla_p["% Perdidos"] = (tabla_p["Perdidos"] / tabla_p["Total leads"] * 100).round(1)
        tabla_p = tabla_p.sort_values("Perdidos", ascending=False).rename(
            columns={"fuente": "Fuente de tráfico"})

        col_g, col_t = st.columns([3, 2])
        with col_g:
            fig = px.bar(tabla_p, x="Fuente de tráfico", y="Perdidos",
                         text_auto=True, title="Leads Perdidos por fuente",
                         color_discrete_sequence=[BARCA["garnet"]])
            barca_layout(fig, 320)
            st.plotly_chart(fig, use_container_width=True)
        with col_t:
            st.dataframe(
                tabla_p[["Fuente de tráfico", "Total leads", "Perdidos", "% Perdidos"]]
                .style.background_gradient(subset=["% Perdidos"], cmap="Reds", vmin=0, vmax=100)
                .format({"% Perdidos": "{:.1f}%"}),
                use_container_width=True, hide_index=True,
                height=min(420, len(tabla_p) * 36 + 40),
            )


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{BARCA['blue_ink']} 0%,
                {BARCA['blue_deep']} 60%,{BARCA['blue']} 100%);
                padding:28px 36px;border-radius:12px;
                margin-bottom:28px;
                border-bottom:4px solid {BARCA['garnet']}">
        <div style="display:flex;align-items:center;gap:12px">
            <div>
                <h1 style="color:{BARCA['white']};margin:0;font-size:26px;
                           font-weight:800;letter-spacing:-.3px">
                    Dashboard RST — {ACCOUNT_NAME}
                </h1>
                <p style="color:{BARCA['line']};margin:5px 0 0;font-size:14px">
                    Análisis de calidad de leads · HubSpot CRM
                </p>
            </div>
        </div>
    </div>""", unsafe_allow_html=True)

    # ── Sidebar — bloque 1: fecha y fuente (antes de cargar datos) ───────────────
    with st.sidebar:
        st.markdown(f"<h2 style='color:{BARCA['gold']};margin-bottom:16px'>⚙️ Filtros</h2>",
                    unsafe_allow_html=True)

        modo = st.radio("Modo de fecha", ["Período predefinido", "Rango personalizado"])

        if modo == "Período predefinido":
            hoy = date.today()
            periodo = st.selectbox("Período", [
                "Últimos 30 días", "Últimos 60 días", "Últimos 90 días",
                "Abril 2026", "Mayo 2026", "Abril + Mayo 2026",
                "Enero–Mayo 2026", "2025 completo",
                "Todos (desde 2024)",
            ], index=0)
            mapa = {
                "Últimos 30 días":   (hoy - timedelta(30), hoy),
                "Últimos 60 días":   (hoy - timedelta(60), hoy),
                "Últimos 90 días":   (hoy - timedelta(90), hoy),
                "Abril 2026":        (date(2026, 4, 1),  date(2026, 4, 30)),
                "Mayo 2026":         (date(2026, 5, 1),  date(2026, 5, 31)),
                "Abril + Mayo 2026": (date(2026, 4, 1),  date(2026, 5, 31)),
                "Enero–Mayo 2026":   (date(2026, 1, 1),  date(2026, 5, 31)),
                "2025 completo":     (date(2025, 1, 1),  date(2025, 12, 31)),
            }
            if periodo == "Todos (desde 2024)":
                fi, ff = "todos", "todos"
            else:
                fi, ff = mapa.get(periodo, (hoy - timedelta(30), hoy))
        else:
            fi = st.date_input("Desde", value=date(2026, 1, 1))
            ff = st.date_input("Hasta",  value=date.today())

        st.markdown("---")
        filtro_fuente = st.multiselect("Fuente de tráfico", options=[
            "Social pagado", "Búsqueda pagada", "Búsqueda orgánica",
            "Tráfico directo", "Otras campañas", "Redes sociales",
            "Offline", "Referencias", "Referral IA", "Email marketing", "Sin datos"
        ])

        st.markdown("---")
        if st.button("🔄 Actualizar datos", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.markdown(f"<small style='color:{BARCA['ink20']}'>Cache 5 min · "
                    f"Fuente: HubSpot CRM</small>", unsafe_allow_html=True)

    # ── Carga en paralelo ──────────────────────────────────────────────────────
    with st.spinner("Cargando datos de HubSpot..."):
        with ThreadPoolExecutor(max_workers=3) as ex:
            fut_data  = ex.submit(fetch_data, str(fi), str(ff))
            fut_mat   = ex.submit(fetch_matriculados_total)
            fut_deals = ex.submit(fetch_negocios_cerrados)
        df         = fut_data.result()
        df_mat_all = fut_mat.result()
        df_deals   = fut_deals.result()

    if df.empty and df_mat_all.empty:
        st.warning("No hay datos para el período seleccionado.")
        return

    # Filtrar matriculados por el período seleccionado (usando fecha real de matriculación)
    if fi == "todos" or df_mat_all.empty:
        df_mat = df_mat_all
    else:
        df_mat = df_mat_all[
            (df_mat_all["fecha"] >= str(fi)) &
            (df_mat_all["fecha"] <= str(ff))
        ]

    # Filtrar deals cerrados por closedate del período
    if fi == "todos" or df_deals.empty:
        df_deals_periodo = df_deals
    else:
        df_deals_periodo = df_deals[
            (df_deals["fecha_cierre"] >= str(fi)) &
            (df_deals["fecha_cierre"] <= str(ff))
        ]

    # ── Sidebar — bloque 2: países dinámicos (unión de los tres datasets) ───────
    with st.sidebar:
        # Combinar países de leads, matriculados y deals para la lista completa
        paises_all = set()
        for _d, _col in [(df, "pais"), (df_mat, "pais"), (df_deals_periodo, "pais")]:
            if not _d.empty and _col in _d.columns:
                paises_all.update(_d[_col].dropna().unique())
        paises_opts = sorted([p for p in paises_all if p not in ("Sin datos", "")])
        if "Sin datos" in paises_all:
            paises_opts.append("Sin datos")
        filtro_pais = st.multiselect("País", options=paises_opts)

    # ── Aplicar filtros a los TRES datasets ───────────────────────────────────
    def _apply(frame):
        if frame.empty:
            return frame
        if filtro_fuente and "fuente" in frame.columns:
            frame = frame[frame["fuente"].isin(filtro_fuente)]
        if filtro_pais and "pais" in frame.columns:
            frame = frame[frame["pais"].isin(filtro_pais)]
        return frame

    df               = _apply(df)
    df_mat           = _apply(df_mat)
    df_deals_periodo = _apply(df_deals_periodo)

    total        = len(df)
    n_mat        = len(df_mat)        # matriculados en el período (fecha real de matriculación)
    n_cerrado    = (df["lead_status"] == "Deal abierto").sum()
    n_contactado = (df["lead_status"] == "Contactado").sum()
    n_mala       = (df["lead_status"] == "Perdido").sum()

    periodo_txt = "Todos (desde 2024)" if fi == "todos" else \
                  f"{fi.strftime('%d/%m/%Y')} → {ff.strftime('%d/%m/%Y')}"
    st.markdown(
        f"<span style='color:{BARCA['ink60']};font-size:13px'>"
        f"📅 <b>{periodo_txt}</b> · "
        f"<b>{total}</b> leads nuevos · <b>{n_mat}</b> cierres ganados en el período · "
        f"{df['pais'].nunique()} países</span>",
        unsafe_allow_html=True
    )
    st.markdown("<br>", unsafe_allow_html=True)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    kpi_card(c1, "Leads nuevos",    total,         BARCA["blue"])
    kpi_card(c2, "Cierre ganado",   n_mat,         BARCA["gold"])
    kpi_card(c3, "Deal abierto",    n_cerrado,     BARCA["garnet"])
    kpi_card(c4, "Contactados",     n_contactado,  BARCA["blue_deep"])
    kpi_card(c5, "Perdidos",
             f"{n_mala} ({n_mala/total*100:.0f}%)" if total else "0",
             BARCA["garnet_deep"])

    st.markdown(
        f"<div style='font-size:12px;color:{BARCA['ink40']};margin-top:6px'>"
        f"ℹ️ <b>Leads nuevos</b>: contactos creados en el período · "
        f"<b>Cierre ganado</b>: fecha real en que pasaron a ese estado "
        f"(el contacto puede haber llegado en otro momento)</div>",
        unsafe_allow_html=True
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Distribución general ───────────────────────────────────────────────────
    st.markdown("### Distribución general")
    col1, col2, col3 = st.columns([1.2, 1.2, 1.6])

    with col1:
        st.plotly_chart(
            chart_donut(df, "lead_status", "Por Estado de Lead", COLOR_ESTADOS),
            use_container_width=True
        )
    with col2:
        fuente_counts = df["fuente"].value_counts().reset_index()
        fuente_counts.columns = ["fuente", "Total"]
        fig = px.pie(fuente_counts, names="fuente", values="Total",
                     title="Por Fuente de Tráfico", hole=0.55,
                     color_discrete_sequence=COLOR_FUENTES)
        fig.update_traces(textposition="outside", textinfo="percent+label",
                          marker=dict(line=dict(color=BARCA["white"], width=2)))
        barca_layout(fig, 320)
        st.plotly_chart(fig, use_container_width=True)
    with col3:
        pais_top = (df.groupby("pais").size().reset_index(name="Total")
                    .sort_values("Total", ascending=False).head(12))
        fig = px.bar(pais_top, x="Total", y="pais", orientation="h",
                     text_auto=True, title="Top 12 países",
                     color="Total",
                     color_continuous_scale=[BARCA["line2"], BARCA["blue_deep"],
                                              BARCA["blue_ink"]])
        fig.update_layout(coloraxis_showscale=False,
                          yaxis=dict(categoryorder="total ascending"))
        barca_layout(fig, 340)
        st.plotly_chart(fig, use_container_width=True)

    # ── Fuente × Estado ────────────────────────────────────────────────────────
    st.markdown("### Estado de lead por fuente de tráfico")
    grp = df.groupby(["fuente", "lead_status"]).size().reset_index(name="Total")
    col1, col2 = st.columns(2)
    with col1:
        fig = px.bar(grp, x="fuente", y="Total", color="lead_status",
                     barmode="stack", title="Volumen absoluto por fuente",
                     color_discrete_map=COLOR_ESTADOS,
                     category_orders={"lead_status": ESTADOS_ORDEN})
        fig.update_layout(legend=dict(orientation="h", y=-0.5, title="Estado",
                                       font_size=10))
        barca_layout(fig, 400)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        tot_f = df.groupby("fuente").size().reset_index(name="Total_fuente")
        grp2 = grp.merge(tot_f, on="fuente")
        grp2["Pct"] = (grp2["Total"] / grp2["Total_fuente"] * 100).round(1)
        fig = px.bar(grp2, x="fuente", y="Pct", color="lead_status",
                     barmode="stack", title="Composición % por fuente",
                     color_discrete_map=COLOR_ESTADOS,
                     category_orders={"lead_status": ESTADOS_ORDEN})
        fig.update_layout(yaxis_title="%",
                          legend=dict(orientation="h", y=-0.5, title="Estado",
                                       font_size=10))
        barca_layout(fig, 400)
        st.plotly_chart(fig, use_container_width=True)

    # ── País × Estado ──────────────────────────────────────────────────────────
    st.markdown("### Estado de lead por país (Top 10)")
    top10 = df.groupby("pais").size().nlargest(10).index.tolist()
    df_top = df[df["pais"].isin(top10)]
    grp3 = df_top.groupby(["pais", "lead_status"]).size().reset_index(name="Total")
    col1, col2 = st.columns(2)
    with col1:
        fig = px.bar(grp3, x="pais", y="Total", color="lead_status",
                     barmode="stack", title="Volumen por país",
                     color_discrete_map=COLOR_ESTADOS,
                     category_orders={"lead_status": ESTADOS_ORDEN})
        fig.update_layout(legend=dict(orientation="h", y=-0.5, font_size=10))
        barca_layout(fig, 400)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        tot_p = df_top.groupby("pais").size().reset_index(name="Total_pais")
        grp4 = grp3.merge(tot_p, on="pais")
        grp4["Pct"] = (grp4["Total"] / grp4["Total_pais"] * 100).round(1)
        fig = px.bar(grp4, x="pais", y="Pct", color="lead_status",
                     barmode="stack", title="Composición % por país",
                     color_discrete_map=COLOR_ESTADOS,
                     category_orders={"lead_status": ESTADOS_ORDEN})
        fig.update_layout(yaxis_title="%",
                          legend=dict(orientation="h", y=-0.5, font_size=10))
        barca_layout(fig, 400)
        st.plotly_chart(fig, use_container_width=True)

    # ── Tendencia mensual ─────────────────────────────────────────────────────
    if df["mes"].nunique() > 1:
        st.markdown("### Tendencia mensual")
        col1, col2 = st.columns(2)
        with col1:
            gm = df.groupby(["mes", "lead_status"]).size().reset_index(name="Total")
            fig = px.bar(gm, x="mes", y="Total", color="lead_status",
                         barmode="stack", title="Evolución mensual por estado",
                         color_discrete_map=COLOR_ESTADOS,
                         category_orders={"lead_status": ESTADOS_ORDEN})
            fig.update_layout(legend=dict(orientation="h", y=-0.45, font_size=10))
            barca_layout(fig, 340)
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            gm2 = df.groupby(["mes", "fuente"]).size().reset_index(name="Total")
            fig = px.line(gm2, x="mes", y="Total", color="fuente",
                          markers=True, title="Evolución por fuente de tráfico",
                          color_discrete_sequence=COLOR_FUENTES)
            fig.update_layout(legend=dict(orientation="h", y=-0.45, font_size=10))
            barca_layout(fig, 340)
            st.plotly_chart(fig, use_container_width=True)

    # ── Matriculaciones del período ────────────────────────────────────────────
    if not df_mat.empty:
        st.markdown(f"""<hr style="border:1px solid {BARCA['line']};margin:32px 0 20px">""",
                    unsafe_allow_html=True)
        mat_label = "todos los tiempos" if fi == "todos" else periodo_txt
        st.markdown(
            f"### 🎓 Matriculaciones del período "
            f"<span style='font-size:14px;color:{BARCA['ink60']};font-weight:400'>"
            f"({len(df_mat)} matriculados · fecha real de matriculación · {mat_label})</span>",
            unsafe_allow_html=True
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            mp = (df_mat.groupby("pais").size()
                  .reset_index(name="Total")
                  .sort_values("Total", ascending=False).head(12))
            fig = px.bar(mp, x="Total", y="pais", orientation="h", text_auto=True,
                         title="Matriculados por país (Top 12)",
                         color="Total",
                         color_continuous_scale=[BARCA["line2"], BARCA["gold"],
                                                  BARCA["blue_ink"]])
            fig.update_layout(coloraxis_showscale=False,
                              yaxis=dict(categoryorder="total ascending"))
            barca_layout(fig, 360)
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            mf = (df_mat.groupby("fuente").size()
                  .reset_index(name="Total")
                  .sort_values("Total", ascending=False))
            fig = px.bar(mf, x="fuente", y="Total", text_auto=True,
                         title="Matriculados por fuente de tráfico",
                         color="Total",
                         color_continuous_scale=[BARCA["line2"], BARCA["gold"],
                                                  BARCA["garnet"]])
            fig.update_layout(coloraxis_showscale=False)
            barca_layout(fig, 360)
            st.plotly_chart(fig, use_container_width=True)
        with col3:
            mm = (df_mat.groupby("mes").size()
                  .reset_index(name="Matriculados")
                  .sort_values("mes"))
            if len(mm) > 1:
                fig = px.line(mm, x="mes", y="Matriculados", markers=True,
                              title="Evolución mensual de matriculaciones",
                              color_discrete_sequence=[BARCA["gold"]])
                fig.update_traces(line_width=3, marker_size=8)
            else:
                fig = px.bar(mm, x="mes", y="Matriculados", text_auto=True,
                             title="Matriculaciones por mes",
                             color_discrete_sequence=[BARCA["gold"]])
            barca_layout(fig, 360)
            st.plotly_chart(fig, use_container_width=True)

    # ── Negocios cerrados — tabla y gráficos ──────────────────────────────────
    st.markdown(f"""<hr style="border:1px solid {BARCA['line']};margin:32px 0 20px">""",
                unsafe_allow_html=True)
    st.markdown("### 📊 Negocios Cerrados — Estados y Motivos de Cierre")

    if df_deals_periodo.empty:
        st.info("No hay negocios cerrados en el período seleccionado.")
    else:
        ganados  = df_deals_periodo[df_deals_periodo["etapa"] == "Cierre ganado"]
        perdidos = df_deals_periodo[df_deals_periodo["etapa"] == "Cierre perdido"]
        # KPIs rápidos
        k1, k2, k3 = st.columns(3)
        kpi_card(k1, "Total cerrados",   df_deals_periodo["deal_id"].nunique(), BARCA["blue"])
        kpi_card(k2, "Cierre ganado",    ganados["deal_id"].nunique(),          BARCA["gold"])
        kpi_card(k3, "Cierre perdido",   perdidos["deal_id"].nunique(),         BARCA["garnet"])
        st.markdown("<br>", unsafe_allow_html=True)

        col1, col2 = st.columns(2)

        # ── Gráfico: Motivos de cierre perdido ────────────────────────────────
        with col1:
            if not perdidos.empty:
                mp = (perdidos.groupby("motivo")["deal_id"]
                      .nunique().reset_index(name="Deals")
                      .sort_values("Deals", ascending=True))
                fig = px.bar(mp, x="Deals", y="motivo", orientation="h",
                             text_auto=True,
                             title=f"Motivos — Cierre perdido ({perdidos['deal_id'].nunique()} deals)",
                             color="Deals",
                             color_continuous_scale=[BARCA["line2"], BARCA["garnet_deep"],
                                                      BARCA["garnet"]])
                fig.update_layout(coloraxis_showscale=False,
                                  yaxis=dict(categoryorder="total ascending"))
                barca_layout(fig, 360)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Sin cierres perdidos en el período.")

        # ── Gráfico: Evolución mensual ganado vs perdido ───────────────────────
        with col2:
            if df_deals_periodo["mes"].nunique() > 0:
                gm = (df_deals_periodo.groupby(["mes", "etapa"])["deal_id"]
                      .nunique().reset_index(name="Deals")
                      .sort_values("mes"))
                fig = px.bar(gm, x="mes", y="Deals", color="etapa",
                             barmode="group", text_auto=True,
                             title="Evolución mensual: Ganado vs Perdido",
                             color_discrete_map={
                                 "Cierre ganado":  BARCA["gold"],
                                 "Cierre perdido": BARCA["garnet"],
                             })
                fig.update_layout(legend=dict(orientation="h", y=-0.25, title=""))
                barca_layout(fig, 360)
                st.plotly_chart(fig, use_container_width=True)

        # ── Donut: distribución de motivos cierre perdido ─────────────────────
        if not perdidos.empty:
            col3, col4 = st.columns(2)
            with col3:
                mp_pie = (perdidos.groupby("motivo")["deal_id"]
                          .nunique().reset_index(name="Deals"))
                fig = px.pie(mp_pie, names="motivo", values="Deals",
                             title="Distribución motivos cierre perdido",
                             hole=0.5,
                             color_discrete_sequence=[
                                 BARCA["garnet"], BARCA["garnet_deep"], BARCA["blue"],
                                 BARCA["gold"], BARCA["ink60"], BARCA["ink40"],
                                 BARCA["yellow"], BARCA["blue_deep"],
                             ])
                fig.update_traces(textposition="outside", textinfo="percent+label",
                                  marker=dict(line=dict(color=BARCA["white"], width=2)))
                barca_layout(fig, 340)
                st.plotly_chart(fig, use_container_width=True)

            with col4:
                if not ganados.empty:
                    mg = (ganados.groupby("motivo")["deal_id"]
                          .nunique().reset_index(name="Deals")
                          .sort_values("Deals", ascending=True))
                    fig = px.bar(mg, x="Deals", y="motivo", orientation="h",
                                 text_auto=True,
                                 title=f"Motivos — Cierre ganado ({ganados['deal_id'].nunique()} deals)",
                                 color_discrete_sequence=[BARCA["gold"]])
                    fig.update_layout(yaxis=dict(categoryorder="total ascending"))
                    barca_layout(fig, 340)
                    st.plotly_chart(fig, use_container_width=True)

        # ── Motivo × Fuente de tráfico ────────────────────────────────────────
        st.markdown("#### 🔗 Motivo de cierre por fuente de tráfico")

        for etapa_label, color_etapa in [("Cierre perdido", BARCA["garnet"]),
                                          ("Cierre ganado",  BARCA["gold"])]:
            subset = df_deals_periodo[df_deals_periodo["etapa"] == etapa_label]
            if subset.empty:
                continue

            st.markdown(
                f"<div style='font-weight:700;color:{color_etapa};"
                f"font-size:15px;margin:16px 0 8px'>● {etapa_label}</div>",
                unsafe_allow_html=True,
            )
            col_g, col_t = st.columns([3, 2])

            with col_g:
                grp = (subset.groupby(["motivo", "fuente"])["deal_id"]
                       .nunique().reset_index(name="Deals"))
                # ordenar motivos por total
                orden_motivos = (grp.groupby("motivo")["Deals"]
                                 .sum().sort_values(ascending=False).index.tolist())
                fig = px.bar(
                    grp, x="Deals", y="motivo", color="fuente",
                    barmode="stack", orientation="h",
                    title=f"{etapa_label} — Motivo × Fuente",
                    category_orders={"motivo": orden_motivos},
                    color_discrete_sequence=[
                        BARCA["blue_ink"], BARCA["blue_deep"], BARCA["blue"],
                        BARCA["garnet_deep"], BARCA["garnet"],
                        BARCA["gold"], BARCA["yellow"],
                        BARCA["ink60"], BARCA["ink40"], BARCA["ink20"],
                    ],
                )
                fig.update_layout(
                    legend=dict(orientation="h", y=-0.35, title="Fuente"),
                    yaxis=dict(categoryorder="array", categoryarray=orden_motivos[::-1]),
                )
                barca_layout(fig, max(300, len(orden_motivos) * 45 + 80))
                st.plotly_chart(fig, use_container_width=True)

            with col_t:
                tabla_mf = (subset.groupby(["motivo", "fuente"])["deal_id"]
                            .nunique().reset_index(name="Deals")
                            .sort_values(["Deals"], ascending=False))
                total_etapa = tabla_mf["Deals"].sum()
                tabla_mf["% total"] = (tabla_mf["Deals"] / total_etapa * 100).round(1).astype(str) + "%"
                tabla_mf.columns = ["Motivo", "Fuente", "Deals", "% total"]
                st.dataframe(tabla_mf, use_container_width=True, hide_index=True,
                             height=min(400, len(tabla_mf) * 36 + 40))

        # ── Tabla resumen general ──────────────────────────────────────────────
        with st.expander("📋 Ver tabla completa de negocios cerrados"):
            tabla = (df_deals_periodo
                     .groupby(["etapa", "motivo", "fuente"])["deal_id"]
                     .nunique()
                     .reset_index(name="Nº Deals")
                     .sort_values(["etapa", "Nº Deals"], ascending=[True, False]))
            totales = tabla.groupby("etapa")["Nº Deals"].transform("sum")
            tabla["% sobre etapa"] = (tabla["Nº Deals"] / totales * 100).round(1).astype(str) + "%"
            tabla.columns = ["Etapa", "Motivo de cierre", "Fuente", "Nº Deals", "% sobre etapa"]
            st.dataframe(tabla, use_container_width=True, hide_index=True)
            st.download_button(
                "⬇️ Descargar CSV",
                data=tabla.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"negocios_cerrados_{fi}_{ff}.csv",
                mime="text/csv",
                key="dl_negocios",
            )

    # ── Análisis y conclusiones ────────────────────────────────────────────────
    conclusiones(df, df_mat, df_deals_periodo)

    # ── Tabla y descarga ───────────────────────────────────────────────────────
    with st.expander("📋 Ver datos completos"):
        st.dataframe(
            df[["fecha", "mes", "pais", "fuente", "lead_status",
                "intentos", "motivo_no_valido", "motivo_cierre"]]
            .sort_values(["fuente", "lead_status"]),
            use_container_width=True, hide_index=True,
        )
        st.download_button(
            "⬇️ Descargar CSV",
            data=df.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"{ACCOUNT_NAME.lower()}_rst_{fi}_{ff}.csv",
            mime="text/csv",
        )

    st.markdown(
        f"<br><div style='text-align:center;color:{BARCA['ink40']};font-size:12px'>"
        f"{ACCOUNT_NAME} · Formularios HighTicket RST · Datos actualizados automáticamente cada 5 min</div>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
