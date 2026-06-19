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
import time

load_dotenv()

# ── Autenticación por contraseña ──────────────────────────────────────────────
def _check_password():
    if st.session_state.get("autenticado"):
        return True

    try:
        pwd_correcta = st.secrets["APP_PASSWORD"]
    except Exception:
        pwd_correcta = os.getenv("APP_PASSWORD", "")

    st.markdown("""
    <div style="max-width:380px;margin:80px auto 0;text-align:center">
        <h2 style="margin-bottom:8px">🔒 Hofmann CRM Dashboard</h2>
        <p style="color:#555;font-size:14px;margin-bottom:24px">
            Acceso restringido — introduce la contraseña para continuar
        </p>
    </div>
    """, unsafe_allow_html=True)

    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        pwd = st.text_input("Contraseña", type="password", label_visibility="collapsed",
                            placeholder="Contraseña...")
        if st.button("Entrar", use_container_width=True, type="primary"):
            if pwd and pwd == pwd_correcta:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta")
    return False

if not _check_password():
    st.stop()

# ── Credenciales HubSpot ──────────────────────────────────────────────────────
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

# ── Paleta Hofmann ────────────────────────────────────────────────────────────
HOFMANN = {
    "blue":         "#0053B3",   # azul medio (gráficos)
    "blue_deep":    "#001E8C",   # azul oscuro
    "blue_ink":     "#000a3f",   # navy brand (sidebar, headers)
    "garnet":       "#D95F02",   # naranja-rojo (negativos / pérdidas)
    "garnet_deep":  "#A63D00",   # naranja-rojo oscuro
    "gold":         "#ECAB0F",   # naranja brand (ganancias, botones)
    "yellow":       "#F5C842",   # naranja-amarillo suave
    "white":        "#FFFFFF",
    "paper":        "#F8FBFD",   # fondo casi blanco
    "bone":         "#e6f3fb",   # azul claro brand
    "line":         "#D4EBFA",   # borde claro
    "line2":        "#C8E2F5",   # borde medio
    "ink100":       "#111111",
    "ink80":        "#2A2A2A",
    "ink60":        "#555555",
    "ink40":        "#8A8A8A",
    "ink20":        "#BFBFBF",
}
BARCA = HOFMANN  # alias para compatibilidad interna

COLOR_ESTADOS = {
    "Cierre Ganado":   BARCA["gold"],
    "Negocio Abierto": BARCA["garnet"],
    "Conectado":       BARCA["blue"],
    "En Curso":        BARCA["yellow"],
    "Sin Respuesta":   BARCA["ink40"],
    "Nuevo":           BARCA["blue_deep"],
    "Perdido":         BARCA["garnet_deep"],
    "No válido":       BARCA["ink20"],
    "Sin estado":      BARCA["line2"],
}

COLOR_FUENTES = [
    BARCA["blue_ink"], BARCA["blue_deep"], BARCA["blue"],
    BARCA["garnet_deep"], BARCA["garnet"],
    BARCA["gold"], BARCA["yellow"],
    BARCA["ink60"], BARCA["ink40"], BARCA["ink20"],
]

st.set_page_config(
    page_title=f"RST Dashboard — {ACCOUNT_NAME}",
    page_icon="📊",
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


def _hs_search(path, payload, max_retries=5):
    """POST a HubSpot search con reintentos automáticos en 429 (rate limit)."""
    for attempt in range(max_retries):
        try:
            r = requests.post(f"{BASE}{path}", headers=HEADERS,
                              json=payload, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait)
                continue
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(1)
            continue
    return None


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
    "new":                  "Nuevo",
    "in_progress":          "En Curso",
    "attempted_to_contact": "Sin Respuesta",
    "connected":            "Conectado",
    "open_deal":            "Negocio Abierto",
    "cierre ganado":        "Cierre Ganado",
    "perdido":              "Perdido",
}

ESTADOS_ORDEN = [
    "Cierre Ganado", "Negocio Abierto", "Conectado",
    "En Curso", "Sin Respuesta", "Nuevo",
    "Perdido", "Sin estado",
]

LATAM_COUNTRIES = {
    # Nombres completos y variantes
    "argentina", "bolivia", "brasil", "brazil", "chile", "colombia",
    "costa rica", "cuba", "dominican republic", "república dominicana",
    "ecuador", "el salvador", "guatemala", "honduras", "mexico", "méxico",
    "nicaragua", "panama", "panamá", "paraguay", "peru", "perú",
    "puerto rico", "uruguay", "venezuela",
    # Códigos ISO 2 letras (HubSpot ip_country puede devolver en minúsculas)
    "ar", "bo", "br", "cl", "co", "cr", "cu", "do", "ec", "sv",
    "gt", "hn", "mx", "ni", "pa", "py", "pe", "pr", "uy", "ve",
}
ESPAÑA_COUNTRIES = {
    # Nombres y variantes frecuentes
    "spain", "españa", "espana", "espanya",
    # Regiones/ciudades que algunos usuarios rellenan
    "catalunya", "cataluña", "barcelona", "madrid", "andalucia",
    # Andorra (mismo mercado)
    "andorra",
    # Códigos ISO 2 letras
    "es", "ad",
}

LATAM_PAIS_ES = {
    "Argentina": "Argentina", "Bolivia": "Bolivia", "Brasil": "Brasil",
    "Brazil": "Brasil", "Chile": "Chile", "Colombia": "Colombia",
    "Costa Rica": "Costa Rica", "Cuba": "Cuba",
    "Dominican Republic": "Rep. Dominicana", "República Dominicana": "Rep. Dominicana",
    "Ecuador": "Ecuador", "El Salvador": "El Salvador",
    "Guatemala": "Guatemala", "Honduras": "Honduras",
    "Mexico": "México", "México": "México",
    "Nicaragua": "Nicaragua", "Panama": "Panamá", "Panamá": "Panamá",
    "Paraguay": "Paraguay", "Peru": "Perú", "Perú": "Perú",
    "Puerto Rico": "Puerto Rico", "Uruguay": "Uruguay", "Venezuela": "Venezuela",
}

_JUNK_PAIS = {"seleccione su país...", "selecciona tu país", "seleccione su pais", "other", "otros"}

def resolve_mercado(pais: str) -> str:
    p = pais.lower().strip()
    if not p or p == "sin datos" or p in _JUNK_PAIS:
        return "Sin datos"
    if p in ESPAÑA_COUNTRIES:
        return "España"
    if p in LATAM_COUNTRIES:
        return "Latam"
    return "Otro"

CURSO_LABELS = {
    '597': 'Arroces', '26716': 'Arroces de Verano', '519': 'Arroces y Fideuás',
    '26699': 'Asia Street Food', 'bono regalo': 'Bono Regalo',
    '8135': 'Cocina Catalana', '624': 'Cocina con Estrella para Fin de Año',
    '616': 'Cocina Francesa con Albert Boronat', '24389': 'Cocina Italiana',
    '601': 'Cocina Japonesa', '11579': 'Cocina Japonesa con Mutsuo Kowaki',
    '26336': 'Cocina Marinera', '602': 'Cocina Nocturna', '599': 'Cocina Vegana',
    '497': 'Curso de Cocina y Desarrollo Profesional',
    '499': 'Curso de Iniciación a la Cocina Profesional',
    '502': 'Curso de Pastelería y Repostería Profesional',
    '498': 'Curso de Perfeccionamiento de Cocina - Nivel 2',
    '503': 'Curso de Perfeccionamiento de Pastelería - Nivel 2',
    '496': 'Diploma de Cocina Profesional', '501': 'Diploma de Pastelería Profesional',
    '634': 'Dulces Navidades Hofmann', '25807': 'Esmorzars de Forquilla',
    'experiencias': 'Experiencias', '11581': 'Food Stylist',
    'Fotografía y estrategias digitales': 'Fotografía y Estrategias Digitales',
    '483': 'Gran Diploma de Hostelería y Pastelería',
    '18160': 'Grandes Platos Hofmann',
    'Inicio a la Pastelería': 'Inicio a la Pastelería',
    '504': 'Intensivo de Pastelería',
    '25767': 'Maridaje y Cata de Vinos', '481': 'Marketing y Gestión',
    '603': 'Menús de Temporada', '623': 'Menú de Gala para Cena de Navidad',
    'Menú de otoño': 'Menú de Otoño', 'No lo tengo claro': 'No lo tengo claro',
    'Nuevas técnicas de vanguardia': 'Nuevas Técnicas de Vanguardia',
    'Prepara tu navidad': 'Prepara tu Navidad',
    '26706': 'Restyling Tapas', '26804': 'Sabores de la India',
    '25777': 'Sabores de la India con Anjalina Chugani',
    '600': 'Técnicas Culinarias', '26366': 'Técnicas de Chocolate',
    '25797': 'Técnicas de Vanguardia con Oliver Peña',
    'Chef experto plant-based': 'Chef Experto Plant-based',
    'Chef experto arroces y fideuas': 'Chef Experto Arroces y Fideuás',
    'Curso especialización en Gestión Operativa': 'Esp. Gestión Operativa',
    'Chef experto en Cocina Japonesa': 'Chef Experto Cocina Japonesa',
    'Chef experto en Alta Cocina de Vanguardia': 'Chef Experto Alta Cocina Vanguardia',
    'Máster Online en Dirección y Creación de Negocios Gastronómicos': 'Máster Online Innovación y Gestión Gastronómica',
    'Curso especialización en Cata y Enología': 'Esp. Cata y Enología',
    'Curso especialización en Marketing Gastronómico': 'Esp. Marketing Gastronómico',
    'Curso Nocturno de Cocina': 'Curso Nocturno de Cocina',
    'Curso Pastelería y Repostería Intensivo Verano': 'Pastelería Intensivo Verano',
    'Curso especialización en Finanzas y Rentabilidad para Restaurantes': 'Esp. Finanzas y Rentabilidad',
    'Curso de Cocina Avanzada y Técnicas de Vanguardia': 'Cocina Avanzada y Vanguardia',
    'Curso de Cocina Mediterránea Tradicional y Renovada': 'Cocina Mediterránea',
    'Curso de Pastelería y Repostería Avanzada': 'Pastelería y Repostería Avanzada',
    'Máster en Dirección y Gestión de Restaurantes': 'Máster Dirección y Gestión Rest.',
    'Máster Online en Comunicación y Marketing Gastronómico': 'Máster Online Food Branding & Growth',
    'Máster Online en Enología y Gestión del Vino': 'Máster Online Enología y Vino',
    'Máster Online en Nutrición y Gastronomía Saludable': 'Máster Online Nutrición y Gastronomía',
    'Curso Cocina Profesional Intensivo Verano': 'Cocina Profesional Intensivo Verano',
    'Curso especialización en Gestión Operativa de Restaurantes': 'Esp. Gestión Operativa Rest.',
    'Diploma Profesional de Coctelería y Mixología': 'Diploma Coctelería y Mixología',
    'Curso Temático Arroces': 'Temático Arroces',
    'Curso Temático Cocina Catalana': 'Temático Cocina Catalana',
    'Curso Temático Técnicas Culinarias': 'Temático Técnicas Culinarias',
    'Curso Temático Pastelería Plant-based': 'Temático Pastelería Plant-based',
    'Curso Temático Cocina Nocturno': 'Temático Cocina Nocturno',
    'Curso Monográfico Cocina Francesa Albert Boronat': 'Monográfico Cocina Francesa',
    'Curso Cocina Saludable': 'Cocina Saludable',
    'Gran Diploma de Pastelería y Repostería': 'Gran Diploma Pastelería y Repostería',
    'Máster Online en Dirección y Gestión de Restaurantes': 'Máster Online Dirección y Gestión Rest.',
    'Máster Online en Gestión y Negocio Global del Vino': 'Máster Online Negocio Global del Vino',
    'Máster Online en Gastronomía Saludable y Nutrición Aplicada': 'Máster Online Gastronomía Saludable',
    'Máster Online en Global Luxury Food & Beverage Management': 'Máster Online Luxury F&B Management',
    'Pastry & Confectionery Summer Intensive Course': 'Pastry Summer Intensive',
    'Professional Culinary Summer Intensive Course': 'Culinary Summer Intensive',
    'Máster Beyond Food Experience': 'Máster Beyond Food Experience',
    'Curso de Bollería y Briocheria Profesional': 'Bollería y Briochería Profesional',
    'Máster Online en Gestión y Estrategia del Sector del Vino': 'Máster Online Estrategia del Vino',
}

CONTACT_PROPS = [
    "email",
    "pais_de_residencia", "ip_country", "country", "billing_country",
    "pais_de_la_ip_capabilia",
    "hs_lead_status", "lead_valido", "num_contacted_notes",
    "motivos_de_cierre_perdido_rst",
    "hs_analytics_source", "hs_analytics_source_data_1",
    "hs_latest_source", "hs_latest_source_data_1",
    "modalidad_curso", "curso",
    "categoria_lead",
    "hs_object_source",
    "first_conversion_event_name",
]

_CATEGORIAS_OPTS = [
    "Formulario",
    "Chatbot HubSpot",
    "Chatbot Serviceform",
    "Forms NO Hubspot antiguos",
    "Sesión Informativa Online",
    "Lead Consultoría Empresa",
    "Inscrito Manualmente",
    "Open Day",
    "Open Day Digital",
    "Webinar",
    "Compra NO curso",
    "Compra Regala Hofmann",
    "Formulario Regala Hofmann",
    "Compra curso web",
    "Compra Cancelada",
    "Importación Classlife",
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


def _resolve_categoria(cp: dict) -> str:
    raw = (cp.get("categoria_lead") or "").strip()
    if raw:
        return raw
    src = (cp.get("hs_object_source") or "").upper()
    if src == "FORM":
        # Afinar por nombre del formulario
        form_name = (cp.get("first_conversion_event_name") or "").lower()
        if "open day" in form_name or "openday" in form_name or "puertas abiertas" in form_name:
            if "digital" in form_name or "online" in form_name:
                return "Open Day Digital"
            return "Open Day"
        if "webinar" in form_name:
            return "Webinar"
        if "sesión informativa" in form_name or "sesion informativa" in form_name:
            return "Sesión Informativa Online"
        if "regala hofmann" in form_name or "regalo" in form_name:
            return "Formulario Regala Hofmann"
        if "linkedin lead" in form_name:
            return "Formulario"
        return "Formulario"
    if src == "MEETINGS":
        return "Inscrito Manualmente"
    if src == "INTEGRATION":
        return "Importación Classlife"
    return "Sin categoría"


# ── Fetching de contactos (con caché) ─────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
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
        data = _hs_search("/crm/v3/objects/contacts/search", payload)
        if data is None:
            break

        for c in data.get("results", []):
            cp = c["properties"]
            email = (cp.get("email") or "").lower().strip()
            fuente, origen = resolve_fuente(cp)
            createdate = (cp.get("createdate") or "")[:10]
            rows.append({
                "email":       email,
                "fecha":       createdate,
                "mes":         createdate[:7] if createdate else "",
                "pais":        resolve_pais(cp),
                "lead_status": norm_status(cp.get("hs_lead_status")),
                "lead_valido": cp.get("lead_valido") or "Sin datos",
                "intentos":    int(cp.get("num_contacted_notes") or 0),
                "motivo_cierre": cp.get("motivos_de_cierre_perdido_rst") or "Sin especificar",
                "fuente":      fuente,
                "origen_fuente": origen,
                "modalidad":   (cp.get("modalidad_curso") or "Sin modalidad").strip().title(),
                "programa":    CURSO_LABELS.get(
                                   cp.get("curso") or "",
                                   (cp.get("curso") or "Sin programa").strip()
                               ) or "Sin programa",
                "mercado":     resolve_mercado(resolve_pais(cp)),
                "categoria":   _resolve_categoria(cp),
            })

        pg = data.get("paging", {})
        if not pg or "next" not in pg:
            break
        after = pg["next"]["after"]

    _COLS = ["email", "fecha", "mes", "pais", "lead_status", "lead_valido",
             "intentos", "motivo_cierre", "fuente", "origen_fuente", "modalidad", "programa", "mercado", "categoria"]
    df = pd.DataFrame(rows, columns=_COLS) if rows else pd.DataFrame(columns=_COLS)
    # Derive calidad from lead_valido + lead_status for program analysis
    def _calidad(row):
        if row["lead_valido"] == "No válido":
            return "No válido"
        if row["lead_status"] == "Cierre Ganado":
            return "Cierre Ganado"
        if row["lead_status"] == "Perdido":
            return "Perdido"
        return "En proceso"
    if not df.empty:
        df["calidad"] = df.apply(_calidad, axis=1)
    else:
        df["calidad"] = pd.Series(dtype=str)
    return df


@st.cache_data(ttl=600, show_spinner=False)
def fetch_matriculados_total(fecha_inicio: str, fecha_fin: str) -> pd.DataFrame:
    """
    Contactos Matriculados (Cierre ganado) del equipo RST.
    Filtra por createdate en una ventana amplia para limitar el volumen,
    luego usa el historial para obtener la fecha EXACTA de matriculación.
    """
    # 1. Obtener contactos con status=Cierre ganado, acotados por fecha
    filters_mat = [{"propertyName": "hs_lead_status", "operator": "EQ", "value": "Cierre ganado"}]
    if fecha_inicio != "todos":
        # Ventana ampliada: 18 meses antes del inicio para capturar conversiones tardías
        fi_dt = datetime.fromisoformat(fecha_inicio)
        fi_amplio = (fi_dt - timedelta(days=548)).replace(tzinfo=timezone.utc)
        ff_dt = (datetime.fromisoformat(fecha_fin)
                 .replace(tzinfo=timezone.utc) + timedelta(days=1))
        filters_mat += [
            {"propertyName": "createdate", "operator": "GTE",
             "value": str(int(fi_amplio.timestamp() * 1000))},
            {"propertyName": "createdate", "operator": "LTE",
             "value": str(int(ff_dt.timestamp() * 1000))},
        ]

    contact_ids = []
    contact_props_map = {}
    after = None
    while True:
        payload = {
            "filterGroups": [{"filters": filters_mat}],
            "properties": CONTACT_PROPS,
            "limit": 100,
        }
        if after:
            payload["after"] = after
        data = _hs_search("/crm/v3/objects/contacts/search", payload)
        if data is None:
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
            "email":       (cp.get("email") or "").lower().strip(),
            "fecha":       fecha_mat,
            "mes":         fecha_mat[:7] if fecha_mat else "",
            "pais":        resolve_pais(cp),
            "lead_status": "Cierre Ganado",
            "lead_valido": "Válido",
            "fuente":      fuente,
            "origen_fuente": origen,
            "intentos":    int(cp.get("num_contacted_notes") or 0),
            "motivo_cierre": cp.get("motivos_de_cierre_perdido_rst") or "Sin especificar",
        })
    return pd.DataFrame(rows)


PIPELINE_ID   = "default"
STAGE_GANADO  = "closedwon"
STAGE_PERDIDO = "closedlost"

PIPELINE_STAGES = {
    "536469454":        "Pendiente de contactar",
    "520725436":        "Ilocalizado",
    "appointmentscheduled": "Contacto Inicial",
    "qualifiedtobuy":   "Concertado",
    "518154939":        "No se presenta",
    "presentationscheduled": "Entrevista Realizada",
    "decisionmakerboughtin": "Envío de Inscripción",
    "closedwon":        "Cierre Ganado",
    "closedlost":       "Cierre Perdido",
    "388512980":        "Pendiente Transferencia",
    "5154403562":       "Estudio Financiación",
    "585451254":        "Cierre Ganado (histórico)",
}

PIPELINE_ORDEN = [
    "Pendiente de contactar", "Contacto Inicial", "Concertado",
    "Entrevista Realizada", "Envío de Inscripción", "Estudio Financiación",
    "Pendiente Transferencia", "Cierre Ganado", "Cierre Ganado (histórico)",
    "No se presenta", "Ilocalizado", "Cierre Perdido",
]

STAGE_COLORS = {
    "Pendiente de contactar":  BARCA["ink20"],
    "Contacto Inicial":        BARCA["blue_deep"],
    "Concertado":              BARCA["blue"],
    "Entrevista Realizada":    BARCA["gold"],
    "Envío de Inscripción":    BARCA["yellow"],
    "Estudio Financiación":    BARCA["garnet"],
    "Pendiente Transferencia": BARCA["garnet_deep"],
    "Cierre Ganado":           "#2E7D32",
    "Cierre Ganado (histórico)": "#66BB6A",
    "No se presenta":          BARCA["ink40"],
    "Ilocalizado":             BARCA["ink60"],
    "Cierre Perdido":          BARCA["garnet_deep"],
}

MOTIVOS_CIERRE_ORDEN = [
    "Motivos económicos", "Ilocalizado", "No se presenta a la reunión",
    "Motivos de producto", "Próxima convocatoria", "Horarios no compatibles",
    "Interés en otra escuela", "Sin especificar",
]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_negocios_cerrados(fecha_inicio: str, fecha_fin: str) -> pd.DataFrame:
    """
    Deals cerrados (ganado + perdido) del pipeline RST en el período indicado.
    Filtra por closedate a nivel de API para limitar el volumen.
    """
    filters_base = [
        {"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_ID},
    ]
    if fecha_inicio != "todos":
        fi_ts = int(datetime.fromisoformat(fecha_inicio)
                    .replace(tzinfo=timezone.utc).timestamp() * 1000)
        ff_ts = (int(datetime.fromisoformat(fecha_fin)
                     .replace(tzinfo=timezone.utc).timestamp() * 1000)
                 + 86_400_000 - 1)
        filters_base += [
            {"propertyName": "closedate", "operator": "GTE", "value": str(fi_ts)},
            {"propertyName": "closedate", "operator": "LTE", "value": str(ff_ts)},
        ]

    # 1. Recoger deals cerrados con sus propiedades base
    deal_map = {}   # deal_id → {etapa, motivos, fecha_cierre}
    for stage_id, stage_label in [(STAGE_GANADO, "Cierre ganado"),
                                   (STAGE_PERDIDO, "Cierre perdido")]:
        after = None
        while True:
            payload = {
                "filterGroups": [{"filters": filters_base + [
                    {"propertyName": "dealstage", "operator": "EQ", "value": stage_id},
                ]}],
                "properties": ["dealname", "closedate", "createdate",
                               "motivo_de_cierre_del_negocio"],
                "limit": 100,
            }
            if after:
                payload["after"] = after
            data = _hs_search("/crm/v3/objects/deals/search", payload)
            if data is None:
                break

            for d in data.get("results", []):
                p = d["properties"]
                fecha_cierre = (p.get("closedate") or p.get("createdate") or "")[:10]
                motivo = (p.get("motivo_de_cierre_del_negocio") or "Sin especificar").strip()
                deal_map[d["id"]] = {
                    "etapa":        stage_label,
                    "motivo_cierre": motivo,
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

    # 4. Construir dataframe
    rows = []
    for did, info in deal_map.items():
        cid  = deal_to_contact.get(did, "")
        data = contact_data.get(cid, {"fuente": "Sin datos", "pais": "Sin datos"})
        rows.append({
            "deal_id":       did,
            "etapa":         info["etapa"],
            "motivo_cierre": info["motivo_cierre"],
            "fuente":        data["fuente"],
            "pais":          data["pais"],
            "fecha_cierre":  info["fecha_cierre"],
            "mes":           info["mes"],
        })

    return pd.DataFrame(rows)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_pipeline(fecha_inicio: str, fecha_fin: str) -> pd.DataFrame:
    """Deals del pipeline activos o cerrados en el período indicado."""
    # Filtro API: creado antes del fin del período
    filters = [{"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_ID}]
    if fecha_inicio != "todos":
        ff_ts = (int(datetime.fromisoformat(fecha_fin)
                     .replace(tzinfo=timezone.utc).timestamp() * 1000)
                 + 86_400_000 - 1)
        # Solo deals creados antes o durante el período
        filters.append({"propertyName": "createdate", "operator": "LTE",
                         "value": str(ff_ts)})

    rows = []
    after = None
    while True:
        payload = {
            "filterGroups": [{"filters": filters}],
            "properties": ["dealname", "dealstage", "amount", "closedate",
                           "createdate", "motivo_de_cierre_del_negocio", "modalidad"],
            "limit": 100,
        }
        if after:
            payload["after"] = after
        data = _hs_search("/crm/v3/objects/deals/search", payload)
        if data is None:
            break

        for d in data.get("results", []):
            p = d["properties"]
            stage_id = p.get("dealstage", "")
            etapa = PIPELINE_STAGES.get(stage_id, stage_id)
            fecha_creacion = (p.get("createdate") or "")[:10]
            fecha_cierre   = (p.get("closedate") or "")[:10]
            fecha_ref      = fecha_cierre or fecha_creacion
            motivo_cierre = (p.get("motivo_de_cierre_del_negocio") or "Sin especificar").strip()
            amount = float(p.get("amount") or 0)
            rows.append({
                "deal_id":        d["id"],
                "etapa":          etapa,
                "fecha_creacion": fecha_creacion,
                "fecha_cierre":   fecha_cierre,
                "fecha":          fecha_ref,
                "mes":            fecha_ref[:7] if fecha_ref else "",
                "amount":         amount,
                "motivo_cierre":  motivo_cierre,
                "modalidad":      (p.get("modalidad") or "Sin modalidad").strip().title(),
            })

        pg = data.get("paging", {})
        if not pg or "next" not in pg:
            break
        after = pg["next"]["after"]

    return pd.DataFrame(rows)


# ── Email Marketing fetch ─────────────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def _fetch_list_names(list_ids_tuple: tuple) -> dict:
    def _get(lid):
        try:
            # Try v1 first (regular contact lists)
            r = requests.get(f"{BASE}/contacts/v1/lists/{lid}",
                             headers=HEADERS, params={"count": 0}, timeout=10)
            if r.status_code == 200:
                return lid, r.json().get("name", lid)
            # Fall back to v3 for ILS lists (return 404 in v1)
            if r.status_code == 404:
                r2 = requests.get(f"{BASE}/crm/v3/lists/{lid}",
                                  headers=HEADERS, timeout=10)
                if r2.status_code == 200:
                    name = r2.json().get("list", {}).get("name", lid)
                    return lid, name
        except Exception:
            pass
        return lid, lid

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_get, lid) for lid in list_ids_tuple]
    return dict(f.result() for f in futs)


def _email_list_ids(e):
    to = e.get("to") or {}
    result = []
    for section in ["contactLists", "contactIlsLists"]:
        section_data = to.get(section)
        if not isinstance(section_data, dict):
            continue
        for item in section_data.get("include") or []:
            if isinstance(item, dict):
                lid  = str(item.get("listId") or item.get("id") or "")
                name = str(item.get("name") or item.get("listName") or "")
            elif isinstance(item, (str, int)):
                # API returns plain IDs: ["1103", "1079"]
                lid  = str(item)
                name = ""
            else:
                continue
            if lid:
                result.append((lid, name))
    return result


@st.cache_data(ttl=600, show_spinner=False)
def fetch_emails_enviados(fecha_inicio: str, fecha_fin: str) -> pd.DataFrame:
    raw = []
    params: dict = {"state": "PUBLISHED", "limit": 100, "orderBy": "-publishDate"}
    after = None
    while True:
        if after:
            params["after"] = after
        else:
            params.pop("after", None)
        r = requests.get(f"{BASE}/marketing/v3/emails", headers=HEADERS,
                         params=params, timeout=30)
        if r.status_code != 200:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        raw.extend(results)
        pg = data.get("paging", {})
        if not pg or "next" not in pg:
            break
        after = pg["next"]["after"]

    if not raw:
        return pd.DataFrame()

    def _pub(e):
        return (e.get("publishDate") or "")[:10]

    if fecha_inicio != "todos":
        fi_str, ff_str = str(fecha_inicio), str(fecha_fin)
        raw = [e for e in raw if fi_str <= _pub(e) <= ff_str]

    if not raw:
        return pd.DataFrame()

    # Build list-name map (use inline names if available, else fetch)
    list_id_name_map: dict = {}
    unknown_ids: set = set()
    for e in raw:
        for lid, lname in _email_list_ids(e):
            if lname:
                list_id_name_map[lid] = lname
            else:
                unknown_ids.add(lid)
    if unknown_ids:
        list_id_name_map.update(_fetch_list_names(tuple(sorted(unknown_ids))))

    # Fetch campaign stats in parallel
    campaign_ids = list({e.get("primaryEmailCampaignId")
                         for e in raw if e.get("primaryEmailCampaignId")})

    def _stats(cid):
        try:
            r = requests.get(f"{BASE}/email/public/v1/campaigns/{cid}",
                             headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return cid, r.json().get("counters", {})
        except Exception:
            pass
        return cid, {}

    with ThreadPoolExecutor(max_workers=4) as ex:
        sfuts = [ex.submit(_stats, cid) for cid in campaign_ids]
    stats_map = dict(f.result() for f in sfuts)

    rows = []
    for e in raw:
        cid   = e.get("primaryEmailCampaignId")
        stats = stats_map.get(cid, {})
        listas = ", ".join(list_id_name_map.get(lid, lid)
                           for lid, _ in _email_list_ids(e)) or "—"
        content   = e.get("content") or {}
        subject   = (e.get("subject") or content.get("subject") or e.get("name") or "")
        from_name = ((e.get("from") or {}).get("fromName") or
                     (content.get("from") or {}).get("fromName") or "")
        pub_date  = _pub(e)
        sent      = int(stats.get("sent",         0) or 0)
        delivered = int(stats.get("delivered",    0) or 0)
        opens     = int(stats.get("open",         0) or 0)
        clicks    = int(stats.get("click",        0) or 0)
        bounces   = int(stats.get("bounce",       0) or 0)
        unsubs    = int(stats.get("unsubscribed", 0) or 0)
        spam      = int(stats.get("spamreport",   0) or 0)
        raw_ids = [lid for lid, _ in _email_list_ids(e)]
        rows.append({
            "campaign_id":   str(cid or ""),
            "nombre":        e.get("name", ""),
            "asunto":        subject,
            "fecha":         pub_date,
            "mes":           pub_date[:7] if pub_date else "",
            "remitente":     from_name,
            "listas":        listas,
            "list_ids_raw":  ",".join(raw_ids),
            "enviados":      sent,
            "entregados":    delivered,
            "aperturas":     opens,
            "tasa_apertura": round(opens  / sent  * 100, 1) if sent  else 0.0,
            "clicks":        clicks,
            "ctr":           round(clicks / sent  * 100, 1) if sent  else 0.0,
            "ctor":          round(clicks / opens * 100, 1) if opens else 0.0,
            "rebotes":       bounces,
            "bajas":         unsubs,
            "spam":          spam,
        })

    return pd.DataFrame(rows).sort_values("fecha", ascending=False) if rows else pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def fetch_emails_programados() -> pd.DataFrame:
    raw = []
    params: dict = {"state": "SCHEDULED", "limit": 100, "orderBy": "publishDate"}
    after = None
    while True:
        if after:
            params["after"] = after
        else:
            params.pop("after", None)
        r = requests.get(f"{BASE}/marketing/v3/emails", headers=HEADERS,
                         params=params, timeout=30)
        if r.status_code != 200:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        raw.extend(results)
        pg = data.get("paging", {})
        if not pg or "next" not in pg:
            break
        after = pg["next"]["after"]

    if not raw:
        return pd.DataFrame()

    list_id_name_map: dict = {}
    unknown_ids: set = set()
    for e in raw:
        for lid, lname in _email_list_ids(e):
            if lname:
                list_id_name_map[lid] = lname
            else:
                unknown_ids.add(lid)
    if unknown_ids:
        list_id_name_map.update(_fetch_list_names(tuple(sorted(unknown_ids))))

    hoy_prog = date.today()
    rows = []
    for e in raw:
        listas = ", ".join(list_id_name_map.get(lid, lid)
                           for lid, _ in _email_list_ids(e)) or "—"
        pub = e.get("publishDate") or ""
        if pub:
            pub_date_str = pub[:10]
            pub_display  = pub[:16].replace("T", " ")
            try:
                pub_d = date.fromisoformat(pub_date_str)
                dias  = (pub_d - hoy_prog).days
                if dias > 0:
                    estado = f"Próximo ({dias}d)"
                elif dias == 0:
                    estado = "Hoy"
                else:
                    estado = f"Pendiente ({abs(dias)}d atrás)"
            except Exception:
                pub_date_str = ""
                dias = None
                estado = "—"
        else:
            pub_date_str = ""
            pub_display  = "Sin fecha"
            dias = None
            estado = "Sin fecha"

        content   = e.get("content") or {}
        subject   = (e.get("subject") or content.get("subject") or e.get("name") or "")
        from_name = ((e.get("from") or {}).get("fromName") or
                     (content.get("from") or {}).get("fromName") or "")
        rows.append({
            "estado":           estado,
            "nombre":           e.get("name", ""),
            "asunto":           subject,
            "fecha_programada": pub_display,
            "fecha_sort":       pub_date_str,
            "remitente":        from_name,
            "listas":           listas,
        })

    df_p = pd.DataFrame(rows) if rows else pd.DataFrame()
    if not df_p.empty and "fecha_sort" in df_p.columns:
        df_p = df_p.sort_values("fecha_sort")
    return df_p


@st.cache_data(ttl=600, show_spinner=False)
def fetch_click_urls(campaign_id: str) -> list:
    """Returns [(url, clicks)] sorted desc for a campaign."""
    events: list = []
    params: dict = {"campaignId": campaign_id, "eventType": "CLICK", "limit": 300}
    for _ in range(5):
        r = requests.get(f"{BASE}/email/public/v1/events",
                         headers=HEADERS, params=params, timeout=20)
        if r.status_code != 200:
            break
        data = r.json()
        batch = data.get("events", [])
        if not batch:
            break
        events.extend(batch)
        if not data.get("hasMore"):
            break
        params["offset"] = data.get("offset", 0) + len(batch)

    url_counts: dict = {}
    for ev in events:
        url = (ev.get("url") or "").strip()
        if url and "unsubscribe" not in url.lower() and not url.startswith("mailto:"):
            url_counts[url] = url_counts.get(url, 0) + 1
    return sorted(url_counts.items(), key=lambda x: x[1], reverse=True)[:15]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_all_lists() -> pd.DataFrame:
    lists: list = []
    offset = 0
    while True:
        r = requests.get(f"{BASE}/contacts/v1/lists",
                         headers=HEADERS,
                         params={"count": 250, "offset": offset},
                         timeout=30)
        if r.status_code != 200:
            break
        data = r.json()
        batch = data.get("lists", [])
        if not batch:
            break
        for lst in batch:
            meta = lst.get("metaData") or {}
            ca   = int(lst.get("createdAt") or 0)
            ua   = int(lst.get("updatedAt") or 0)
            lists.append({
                "list_id": str(lst.get("listId", "")),
                "nombre":  lst.get("name", ""),
                "tipo":    lst.get("listType", ""),
                "size":    int(meta.get("size", 0) or 0),
                "created": datetime.fromtimestamp(ca / 1000).strftime("%Y-%m-%d") if ca else "",
                "updated": datetime.fromtimestamp(ua / 1000).strftime("%Y-%m-%d") if ua else "",
            })
        if not data.get("has-more"):
            break
        offset += len(batch)
    return pd.DataFrame(lists) if lists else pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def fetch_workflows() -> pd.DataFrame:
    import json as _json

    r = requests.get(f"{BASE}/automation/v3/workflows", headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return pd.DataFrame()
    wfs_raw = r.json().get("workflows", [])

    def _detail(wf):
        wid = wf["id"]
        r2 = requests.get(f"{BASE}/automation/v3/workflows/{wid}", headers=HEADERS, timeout=15)
        return wid, r2.json() if r2.status_code == 200 else {}

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(_detail, w) for w in wfs_raw]
    detail_map = dict(f.result() for f in futs)

    # Collect unique emailContentId and emailCampaignId pairs
    email_content_ids: set = set()
    email_campaign_ids: set = set()
    content_to_campaign: dict = {}
    for d in detail_map.values():
        for a in (d or {}).get("actions", []):
            if a.get("type") == "EMAIL":
                eid = str(a.get("emailContentId") or "")
                cid = str(a.get("emailCampaignId") or "")
                if eid:
                    email_content_ids.add(eid)
                if cid:
                    email_campaign_ids.add(cid)
                if eid and cid:
                    content_to_campaign[eid] = cid

    def _ename(eid):
        r = requests.get(f"{BASE}/marketing/v3/emails/{eid}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return eid, r.json().get("name", eid)
        return eid, eid

    def _cstats(cid):
        r = requests.get(f"{BASE}/email/public/v1/campaigns/{cid}",
                         headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return cid, None
        c = r.json().get("counters", {})
        sent     = int(c.get("sent",          0) or 0)
        opens    = int(c.get("open",          0) or 0)
        clicks   = int(c.get("click",         0) or 0)
        bounces  = int(c.get("bounce",        0) or 0)
        unsubs   = int(c.get("unsubscribed",  0) or 0)
        return cid, {
            "sent":           sent,
            "opens":          opens,
            "clicks":         clicks,
            "bounces":        bounces,
            "unsubs":         unsubs,
            "tasa_apertura":  round(opens   / sent  * 100, 1) if sent  else 0.0,
            "ctr":            round(clicks  / sent  * 100, 1) if sent  else 0.0,
            "ctor":           round(clicks  / opens * 100, 1) if opens else 0.0,
            "tasa_rebote":    round(bounces / sent  * 100, 1) if sent  else 0.0,
        }

    with ThreadPoolExecutor(max_workers=8) as ex:
        efuts = [ex.submit(_ename,   eid) for eid in email_content_ids]
        cfuts = [ex.submit(_cstats,  cid) for cid in email_campaign_ids]
    ename_map  = dict(f.result() for f in efuts)
    cstats_map = dict(f.result() for f in cfuts)

    ACTION_LABEL = {
        "EMAIL":                "📧 Email",
        "DEAL":                 "💼 Deal",
        "SET_CONTACT_PROPERTY": "✏️ Prop. contacto",
        "SET_COMPANY_PROPERTY": "✏️ Prop. empresa",
        "SEQUENCE":             "🔗 Secuencia",
        "TASK":                 "✅ Tarea",
        "DELAY":                "⏱ Espera",
        "ADD_TO_LIST":          "📋 Añadir a lista",
        "REMOVE_FROM_LIST":     "📋 Quitar de lista",
        "NOTIFICATION_EMAIL":   "🔔 Notif. interna",
        "WEBHOOK":              "🔌 Webhook",
    }

    rows = []
    for wf in wfs_raw:
        wid     = wf["id"]
        d       = detail_map.get(wid) or {}
        ia      = int(wf.get("insertedAt") or 0)
        ua      = int(wf.get("updatedAt")  or 0)
        actions = d.get("actions", [])

        seen_types: list = []
        seen_set: set    = set()
        for a in actions:
            t = a.get("type", "")
            if t and t not in seen_set:
                seen_set.add(t)
                seen_types.append(ACTION_LABEL.get(t, t))

        # Per-email detail with stats
        email_detail: list = []
        seen_names: set = set()
        for a in actions:
            if a.get("type") == "EMAIL" and a.get("emailContentId"):
                eid  = str(a["emailContentId"])
                name = ename_map.get(eid, eid)
                if name in seen_names:
                    continue
                seen_names.add(name)
                cid   = content_to_campaign.get(eid, "")
                stats = cstats_map.get(cid) if cid else None
                email_detail.append({
                    "nombre":         name,
                    "sent":           (stats or {}).get("sent",          0),
                    "tasa_apertura":  (stats or {}).get("tasa_apertura", None),
                    "ctr":            (stats or {}).get("ctr",           None),
                    "ctor":           (stats or {}).get("ctor",          None),
                    "tasa_rebote":    (stats or {}).get("tasa_rebote",   None),
                    "unsubs":         (stats or {}).get("unsubs",        0),
                })

        email_names = [e["nombre"] for e in email_detail]

        # Aggregate metrics for summary columns (only emails with sent > 0)
        sent_emails = [e for e in email_detail if e["sent"] > 0]
        avg_apertura = round(sum(e["tasa_apertura"] for e in sent_emails) / len(sent_emails), 1) if sent_emails else None
        avg_ctr      = round(sum(e["ctr"]           for e in sent_emails) / len(sent_emails), 1) if sent_emails else None
        avg_ctor     = round(sum(e["ctor"]          for e in sent_emails) / len(sent_emails), 1) if sent_emails else None
        total_sent   = sum(e["sent"] for e in email_detail)

        rows.append({
            "id":            wid,
            "nombre":        wf.get("name", ""),
            "activo":        bool(wf.get("enabled")),
            "acciones":      ", ".join(seen_types) if seen_types else "—",
            "emails":        "; ".join(email_names) if email_names else "—",
            "n_emails":      len(email_names),
            "email_detail":  _json.dumps(email_detail, ensure_ascii=False),
            "enviados_total": total_sent,
            "avg_apertura":  avg_apertura,
            "avg_ctr":       avg_ctr,
            "avg_ctor":      avg_ctor,
            "creado":        datetime.fromtimestamp(ia / 1000).strftime("%Y-%m-%d") if ia else "",
            "actualizado":   datetime.fromtimestamp(ua / 1000).strftime("%Y-%m-%d") if ua else "",
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def fetch_sequences() -> pd.DataFrame:
    r = requests.get(f"{BASE}/settings/v3/users", headers=HEADERS, timeout=15)
    if r.status_code != 200:
        return pd.DataFrame()
    users = r.json().get("results", [])

    seq_map: dict = {}
    for u in users:
        uid = u.get("id")
        r2 = requests.get(f"{BASE}/automation/v4/sequences",
                          headers=HEADERS,
                          params={"userId": uid, "limit": 200},
                          timeout=10)
        if r2.status_code != 200:
            continue
        email_val = u.get("email", str(uid))
        for s in r2.json().get("results", []):
            sid = s.get("id")
            if not sid:
                continue
            if sid not in seq_map:
                seq_map[sid] = {"raw": s, "uid": uid, "owners": [email_val]}
            elif email_val not in seq_map[sid]["owners"]:
                seq_map[sid]["owners"].append(email_val)

    def _seq_detail(sid, uid):
        r = requests.get(f"{BASE}/automation/v4/sequences/{sid}",
                         headers=HEADERS, params={"userId": uid}, timeout=10)
        return sid, r.json() if r.status_code == 200 else None

    with ThreadPoolExecutor(max_workers=6) as ex:
        sfuts = [ex.submit(_seq_detail, sid, info["uid"]) for sid, info in seq_map.items()]
    sdetail_map = dict(f.result() for f in sfuts)

    rows = []
    for sid, info in seq_map.items():
        d     = sdetail_map.get(sid) or info["raw"]
        steps = d.get("steps", [])

        n_email = sum(1 for s in steps if s.get("actionType") == "EMAIL")
        n_task  = sum(1 for s in steps if s.get("actionType") == "TASK")

        day_accum = 0
        step_parts: list = []
        for s in sorted(steps, key=lambda x: x.get("stepOrder", 0)):
            atype    = s.get("actionType", "")
            delay_ms = int(s.get("delayMillis") or 0)
            if delay_ms:
                day_accum += max(1, round(delay_ms / 86400000))
            if atype == "EMAIL":
                step_parts.append(f"Día {day_accum}: 📧 Email")
            elif atype == "TASK":
                tp   = ((s.get("taskPattern") or {}).get("taskType") or "TASK")
                subj = ((s.get("taskPattern") or {}).get("subject") or "")[:35]
                label = tp.replace("CALL", "Llamada").replace("TODO", "Tarea").replace("EMAIL", "Email")
                step_parts.append(f"Día {day_accum}: ✅ {label}" + (f" – {subj}" if subj else ""))

        ca = (d.get("createdAt") or "")[:10]
        ua = (d.get("updatedAt") or "")[:10]

        rows.append({
            "id":          sid,
            "nombre":      d.get("name", ""),
            "total_pasos": n_email + n_task,
            "emails":      n_email,
            "tareas":      n_task,
            "pasos":       " → ".join(step_parts) if step_parts else "—",
            "responsables": ", ".join(sorted(info["owners"])),
            "n_resp":      len(info["owners"]),
            "creado":      ca,
            "actualizado": ua,
        })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("nombre").reset_index(drop=True)


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

    contactados  = df[df["lead_status"] == "Conectado"]
    intentando   = df[df["lead_status"].isin(["En Curso", "Sin Respuesta"])]
    mala_calidad = df[df["lead_valido"] == "No válido"]

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
            mes_inicio = date(hoy.year, hoy.month, 1)
            periodo = st.selectbox("Período", [
                "Este mes",
                "Hoy", "Ayer",
                "Últimos 7 días", "Últimos 30 días", "Últimos 60 días", "Últimos 90 días",
                "Abril 2026", "Mayo 2026",
                "2025 completo",
                "Todos (desde 2024)",
            ], index=0)
            mapa = {
                "Este mes":        (mes_inicio, hoy),
                "Hoy":             (hoy, hoy),
                "Ayer":            (hoy - timedelta(1), hoy - timedelta(1)),
                "Últimos 7 días":  (hoy - timedelta(7), hoy),
                "Últimos 30 días": (hoy - timedelta(30), hoy),
                "Últimos 60 días": (hoy - timedelta(60), hoy),
                "Últimos 90 días": (hoy - timedelta(90), hoy),
                "Abril 2026":      (date(2026, 4, 1), date(2026, 4, 30)),
                "Mayo 2026":       (date(2026, 5, 1), date(2026, 5, 31)),
                "2025 completo":   (date(2025, 1, 1), date(2025, 12, 31)),
            }
            if periodo == "Todos (desde 2024)":
                fi, ff = "todos", "todos"
            else:
                fi, ff = mapa.get(periodo, (mes_inicio, hoy))
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
        filtro_categoria = st.multiselect(
            "Tipo de contacto",
            options=_CATEGORIAS_OPTS,
            help="Filtra por el origen/tipo del contacto (captación, evento, compra, etc.)",
        )

        st.markdown("---")
        filtro_modalidad_contacto = st.multiselect(
            "Modalidad contacto",
            options=["Presencial", "Online", "Sin modalidad"],
        )
        filtro_modalidad_negocio = st.multiselect(
            "Modalidad negocio",
            options=["Presencial", "Online", "Sin modalidad"],
        )

        st.markdown("---")
        if st.button("🔄 Actualizar datos", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.markdown(f"<small style='color:{BARCA['ink20']}'>Cache 5 min · "
                    f"Fuente: HubSpot CRM</small>", unsafe_allow_html=True)

    # ── Carga en paralelo ──────────────────────────────────────────────────────
    with st.spinner("Cargando datos de HubSpot..."):
        with ThreadPoolExecutor(max_workers=6) as ex:
            fut_data     = ex.submit(fetch_data,               str(fi), str(ff))
            fut_mat      = ex.submit(fetch_matriculados_total,  str(fi), str(ff))
            fut_deals    = ex.submit(fetch_negocios_cerrados,   str(fi), str(ff))
            fut_pipeline = ex.submit(fetch_pipeline,            str(fi), str(ff))
            fut_emails   = ex.submit(fetch_emails_enviados,     str(fi), str(ff))
            fut_prog     = ex.submit(fetch_emails_programados)
        df           = fut_data.result()
        df_mat_all   = fut_mat.result()
        df_deals     = fut_deals.result()
        df_pipeline  = fut_pipeline.result()
        df_emails    = fut_emails.result()
        df_prog      = fut_prog.result()

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

    # Filtrar pipeline: deals activos o cerrados en el período
    # Un deal entra si fue creado antes del fin del período
    # Y no fue cerrado antes del inicio del período
    if fi == "todos" or df_pipeline.empty:
        df_pipeline_periodo = df_pipeline
    else:
        fi_str = str(fi)
        ff_str = str(ff)
        df_pipeline_periodo = df_pipeline[
            (df_pipeline["fecha_creacion"] <= ff_str) &
            (
                (df_pipeline["fecha_cierre"] == "") |
                (df_pipeline["fecha_cierre"] >= fi_str)
            )
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

    # ── Aplicar filtros a los datasets de contactos ───────────────────────────
    def _apply(frame):
        if frame.empty:
            return frame
        if filtro_fuente and "fuente" in frame.columns:
            frame = frame[frame["fuente"].isin(filtro_fuente)]
        if filtro_pais and "pais" in frame.columns:
            frame = frame[frame["pais"].isin(filtro_pais)]
        if filtro_modalidad_contacto and "modalidad" in frame.columns:
            frame = frame[frame["modalidad"].isin(filtro_modalidad_contacto)]
        if filtro_categoria and "categoria" in frame.columns:
            frame = frame[frame["categoria"].isin(filtro_categoria)]
        return frame

    df               = _apply(df)
    df_mat           = _apply(df_mat)
    df_deals_periodo = _apply(df_deals_periodo)

    # Aplicar filtro de modalidad de negocio al pipeline
    if filtro_modalidad_negocio and not df_pipeline_periodo.empty:
        df_pipeline_periodo = df_pipeline_periodo[
            df_pipeline_periodo["modalidad"].isin(filtro_modalidad_negocio)
        ]

    total        = len(df)
    n_mat        = int((df["lead_status"] == "Cierre Ganado").sum())   if not df.empty else 0
    n_cerrado    = int((df["lead_status"] == "Negocio Abierto").sum()) if not df.empty else 0
    n_contactado = int((df["lead_status"] == "Conectado").sum())       if not df.empty else 0
    n_mala       = int((df["lead_valido"] == "No válido").sum())        if not df.empty else 0

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
    kpi_card(c2, "Cierre Ganado",   n_mat,         BARCA["gold"])
    kpi_card(c3, "Negocio Abierto", n_cerrado,     BARCA["garnet"])
    kpi_card(c4, "Conectados",      n_contactado,  BARCA["blue_deep"])
    kpi_card(c5, "No Válidos",
             f"{n_mala} ({n_mala/total*100:.0f}%)" if total else "0",
             BARCA["garnet_deep"])

    st.markdown(
        f"<div style='font-size:12px;color:{BARCA['ink40']};margin-top:6px'>"
        f"ℹ️ Estado actual de los contactos creados en el período seleccionado</div>",
        unsafe_allow_html=True
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Secciones que dependen de df (leads) ──────────────────────────────────
    if df.empty:
        st.info("No hay leads para el período y filtros seleccionados.")
    else:
        # ── Distribución general ───────────────────────────────────────────────
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

        # ── Lead Válido ────────────────────────────────────────────────────────
        st.markdown("### Calidad de leads")
        col1, col2 = st.columns(2)
        with col1:
            valido_counts = df["lead_valido"].value_counts().reset_index()
            valido_counts.columns = ["lead_valido", "Total"]
            fig = px.pie(valido_counts, names="lead_valido", values="Total",
                         title="Lead Válido", hole=0.55,
                         color="lead_valido",
                         color_discrete_map={
                             "Válido":    BARCA["blue"],
                             "No válido": BARCA["garnet"],
                             "Sin datos": BARCA["line2"],
                         })
            fig.update_traces(textposition="outside", textinfo="percent+label",
                              marker=dict(line=dict(color=BARCA["white"], width=2)))
            barca_layout(fig, 320)
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            grp_v = df.groupby(["fuente", "lead_valido"]).size().reset_index(name="Total")
            fig = px.bar(grp_v, x="fuente", y="Total", color="lead_valido",
                         barmode="stack", title="Válido / No válido por fuente",
                         color_discrete_map={
                             "Válido":    BARCA["blue"],
                             "No válido": BARCA["garnet"],
                             "Sin datos": BARCA["line2"],
                         })
            fig.update_layout(legend=dict(orientation="h", y=-0.3))
            barca_layout(fig, 320)
            st.plotly_chart(fig, use_container_width=True)

        # ── Modalidad de Contacto ──────────────────────────────────────────────
        st.markdown("### Modalidad de contacto")
        COLOR_MODALIDAD = {
            "Presencial":    BARCA["blue_ink"],
            "Online":        BARCA["gold"],
            "Sin modalidad": BARCA["line2"],
        }
        col1, col2 = st.columns(2)
        with col1:
            mod_counts = df["modalidad"].value_counts().reset_index()
            mod_counts.columns = ["modalidad", "Total"]
            fig = px.pie(mod_counts, names="modalidad", values="Total",
                         title="Distribución por modalidad", hole=0.55,
                         color="modalidad", color_discrete_map=COLOR_MODALIDAD)
            fig.update_traces(textposition="outside", textinfo="percent+label",
                              marker=dict(line=dict(color=BARCA["white"], width=2)))
            barca_layout(fig, 320)
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            grp_mf = df.groupby(["modalidad", "fuente"]).size().reset_index(name="Leads")
            fig = px.bar(grp_mf, x="fuente", y="Leads", color="modalidad",
                         barmode="stack", title="Modalidad por fuente de tráfico",
                         color_discrete_map=COLOR_MODALIDAD)
            fig.update_layout(legend=dict(orientation="h", y=-0.3))
            barca_layout(fig, 320)
            st.plotly_chart(fig, use_container_width=True)

        # Tabla: leads por modalidad × fuente
        st.markdown("#### Leads por modalidad y fuente de tráfico")
        pivot_mod = (df.groupby(["modalidad", "fuente"])
                     .size().reset_index(name="Leads")
                     .pivot(index="modalidad", columns="fuente", values="Leads")
                     .fillna(0).astype(int))
        pivot_mod.insert(0, "Total", pivot_mod.sum(axis=1))
        pivot_mod = pivot_mod.sort_values("Total", ascending=False)
        pivot_mod.index.name = "Modalidad"
        st.dataframe(
            pivot_mod.style.background_gradient(subset=["Total"], cmap="Blues"),
            use_container_width=True,
            height=min(300, len(pivot_mod) * 36 + 50),
        )

        # ── Fuente × Estado ────────────────────────────────────────────────────
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

        # ── País × Estado ──────────────────────────────────────────────────────
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

        # ── Tendencia mensual ──────────────────────────────────────────────────
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

    # ── Pipeline de Ventas ────────────────────────────────────────────────────
    st.markdown(f"""<hr style="border:1px solid {BARCA['line']};margin:32px 0 20px">""",
                unsafe_allow_html=True)
    st.markdown("## 📊 Pipeline de Ventas")

    if df_pipeline_periodo.empty:
        st.info("No hay negocios en el pipeline para el período seleccionado.")
    else:
        # KPIs del pipeline
        total_deals  = df_pipeline_periodo["deal_id"].nunique()
        ganados_pip  = df_pipeline_periodo[df_pipeline_periodo["etapa"] == "Cierre Ganado"]["deal_id"].nunique()
        perdidos_pip = df_pipeline_periodo[df_pipeline_periodo["etapa"] == "Cierre Perdido"]["deal_id"].nunique()
        activos_pip  = total_deals - ganados_pip - perdidos_pip - \
                       df_pipeline_periodo[df_pipeline_periodo["etapa"] == "Cierre Ganado (histórico)"]["deal_id"].nunique()

        k1, k2, k3, k4 = st.columns(4)
        kpi_card(k1, "Total deals",    total_deals,  BARCA["blue"])
        kpi_card(k2, "Cierre Ganado",  ganados_pip,  "#2E7D32")
        kpi_card(k3, "Cierre Perdido", perdidos_pip, BARCA["garnet"])
        kpi_card(k4, "En proceso",     activos_pip,  BARCA["blue_deep"])

        st.markdown("<br>", unsafe_allow_html=True)

        # Deals por etapa — funnel + barra
        col1, col2 = st.columns([1, 1])
        with col1:
            etapa_counts = (df_pipeline_periodo.drop_duplicates("deal_id")
                            .groupby("etapa").size().reset_index(name="Deals"))
            etapa_counts["orden"] = etapa_counts["etapa"].map(
                {e: i for i, e in enumerate(PIPELINE_ORDEN)}).fillna(99)
            etapa_counts = etapa_counts.sort_values("orden")
            fig = px.funnel(etapa_counts, x="Deals", y="etapa",
                            title="Embudo del pipeline",
                            color="etapa",
                            color_discrete_map=STAGE_COLORS)
            barca_layout(fig, 420)
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            fig = px.bar(etapa_counts.sort_values("Deals", ascending=True),
                         x="Deals", y="etapa", orientation="h",
                         text_auto=True, title="Deals por etapa",
                         color="etapa", color_discrete_map=STAGE_COLORS)
            fig.update_layout(showlegend=False,
                              yaxis=dict(categoryorder="total ascending"))
            barca_layout(fig, 420)
            st.plotly_chart(fig, use_container_width=True)

        # Evolución mensual por etapa
        if df_pipeline_periodo["mes"].nunique() > 1:
            st.markdown("### Evolución mensual")
            gm = (df_pipeline_periodo.drop_duplicates(["deal_id", "mes"])
                  .groupby(["mes", "etapa"])["deal_id"].nunique().reset_index(name="Deals"))
            fig = px.bar(gm, x="mes", y="Deals", color="etapa",
                         barmode="stack", title="Deals por mes y etapa",
                         color_discrete_map=STAGE_COLORS,
                         category_orders={"etapa": PIPELINE_ORDEN})
            fig.update_layout(legend=dict(orientation="h", y=-0.3, font_size=10))
            barca_layout(fig, 360)
            st.plotly_chart(fig, use_container_width=True)

        # ── Modalidad de Negocio ───────────────────────────────────────────────
        st.markdown("### Modalidad de negocio")
        COLOR_MODALIDAD_N = {
            "Presencial":    BARCA["blue_ink"],
            "Online":        BARCA["gold"],
            "Sin modalidad": BARCA["line2"],
        }
        col1, col2 = st.columns(2)
        with col1:
            mod_pip = (df_pipeline_periodo.drop_duplicates("deal_id")
                       ["modalidad"].value_counts().reset_index())
            mod_pip.columns = ["modalidad", "Deals"]
            fig = px.pie(mod_pip, names="modalidad", values="Deals",
                         title="Deals por modalidad", hole=0.55,
                         color="modalidad", color_discrete_map=COLOR_MODALIDAD_N)
            fig.update_traces(textposition="outside", textinfo="percent+label",
                              marker=dict(line=dict(color=BARCA["white"], width=2)))
            barca_layout(fig, 320)
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            grp_me = (df_pipeline_periodo.drop_duplicates("deal_id")
                      .groupby(["modalidad", "etapa"])["deal_id"]
                      .nunique().reset_index(name="Deals"))
            fig = px.bar(grp_me, x="etapa", y="Deals", color="modalidad",
                         barmode="stack", title="Modalidad por etapa del pipeline",
                         color_discrete_map=COLOR_MODALIDAD_N,
                         category_orders={"etapa": PIPELINE_ORDEN})
            fig.update_layout(legend=dict(orientation="h", y=-0.3),
                              xaxis_tickangle=-30)
            barca_layout(fig, 340)
            st.plotly_chart(fig, use_container_width=True)

        # Tabla: deals por modalidad × etapa
        st.markdown("#### Deals por modalidad y etapa")
        pivot_mn = (df_pipeline_periodo.drop_duplicates("deal_id")
                    .groupby(["modalidad", "etapa"])["deal_id"]
                    .nunique().reset_index(name="Deals")
                    .pivot(index="modalidad", columns="etapa", values="Deals")
                    .fillna(0).astype(int))
        pivot_mn.insert(0, "Total", pivot_mn.sum(axis=1))
        pivot_mn = pivot_mn.sort_values("Total", ascending=False)
        pivot_mn.index.name = "Modalidad"
        st.dataframe(
            pivot_mn.style.background_gradient(subset=["Total"], cmap="Blues"),
            use_container_width=True,
            height=min(300, len(pivot_mn) * 36 + 50),
        )

        # Motivos de cierre
        st.markdown("### Motivo de cierre del negocio")
        cerrados_df = df_pipeline_periodo[df_pipeline_periodo["etapa"].isin(
            ["Cierre Ganado", "Cierre Perdido", "Cierre Ganado (histórico)"]
        )]
        if not cerrados_df.empty:
            col1, col2 = st.columns(2)

            perdidos_df = df_pipeline_periodo[df_pipeline_periodo["etapa"] == "Cierre Perdido"]
            ganados_df  = df_pipeline_periodo[df_pipeline_periodo["etapa"].isin(
                ["Cierre Ganado", "Cierre Ganado (histórico)"]
            )]

            with col1:
                if not perdidos_df.empty:
                    mc = (perdidos_df.groupby("motivo_cierre").size()
                          .reset_index(name="Total")
                          .sort_values("Total", ascending=True))
                    fig = px.bar(mc, x="Total", y="motivo_cierre", orientation="h",
                                 text_auto=True, title="Motivos — Cierre Perdido",
                                 color_discrete_sequence=[BARCA["garnet"]])
                    fig.update_layout(yaxis_title="")
                    barca_layout(fig, max(320, len(mc) * 28 + 80))
                    st.plotly_chart(fig, use_container_width=True)

            with col2:
                if not ganados_df.empty:
                    mc_g = (ganados_df.groupby("motivo_cierre").size()
                            .reset_index(name="Total")
                            .sort_values("Total", ascending=True))
                    fig = px.bar(mc_g, x="Total", y="motivo_cierre", orientation="h",
                                 text_auto=True, title="Motivos — Cierre Ganado",
                                 color_discrete_sequence=["#2E7D32"])
                    fig.update_layout(yaxis_title="")
                    barca_layout(fig, max(320, len(mc_g) * 28 + 80))
                    st.plotly_chart(fig, use_container_width=True)

            # Tabla resumen: todos los motivos con etapa
            st.markdown("#### Detalle por motivo y etapa")
            tabla_m = (cerrados_df.groupby(["motivo_cierre", "etapa"]).size()
                       .reset_index(name="Deals")
                       .pivot(index="motivo_cierre", columns="etapa", values="Deals")
                       .fillna(0).astype(int))
            tabla_m.insert(0, "Total", tabla_m.sum(axis=1))
            tabla_m = tabla_m.sort_values("Total", ascending=False)
            tabla_m.index.name = "Motivo"
            st.dataframe(
                tabla_m.style.background_gradient(subset=["Total"], cmap="Blues"),
                use_container_width=True,
                height=min(600, len(tabla_m) * 36 + 40),
            )

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
                mp = (perdidos.groupby("motivo_cierre")["deal_id"]
                      .nunique().reset_index(name="Deals")
                      .sort_values("Deals", ascending=True))
                fig = px.bar(mp, x="Deals", y="motivo_cierre", orientation="h",
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
                mp_pie = (perdidos.groupby("motivo_cierre")["deal_id"]
                          .nunique().reset_index(name="Deals"))
                fig = px.pie(mp_pie, names="motivo_cierre", values="Deals",
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
                    mg = (ganados.groupby("motivo_cierre")["deal_id"]
                          .nunique().reset_index(name="Deals")
                          .sort_values("Deals", ascending=True))
                    fig = px.bar(mg, x="Deals", y="motivo_cierre", orientation="h",
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
                grp = (subset.groupby(["motivo_cierre", "fuente"])["deal_id"]
                       .nunique().reset_index(name="Deals"))
                # ordenar motivos por total
                orden_motivos = (grp.groupby("motivo_cierre")["Deals"]
                                 .sum().sort_values(ascending=False).index.tolist())
                fig = px.bar(
                    grp, x="Deals", y="motivo_cierre", color="fuente",
                    barmode="stack", orientation="h",
                    title=f"{etapa_label} — Motivo × Fuente",
                    category_orders={"motivo_cierre": orden_motivos},
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
                tabla_mf = (subset.groupby(["motivo_cierre", "fuente"])["deal_id"]
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
                     .groupby(["etapa", "motivo_cierre", "fuente"])["deal_id"]
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
    if not df.empty:
        conclusiones(df, df_mat, df_deals_periodo)

    # ── Tabla y descarga ───────────────────────────────────────────────────────
    if not df.empty:
        with st.expander("📋 Ver datos completos"):
            st.dataframe(
                df[["fecha", "mes", "pais", "fuente", "lead_status", "lead_valido",
                    "intentos", "motivo_cierre"]]
                .sort_values(["fuente", "lead_status"]),
                use_container_width=True, hide_index=True,
            )
            st.download_button(
                "⬇️ Descargar CSV",
                data=df.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"{ACCOUNT_NAME.lower()}_rst_{fi}_{ff}.csv",
                mime="text/csv",
            )

    # ══════════════════════════════════════════════════════════════════════════
    # EMAIL MARKETING
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("<hr style='margin:44px 0 32px'>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{BARCA['blue_ink']} 0%,
                {BARCA['blue_deep']} 100%);
                padding:20px 28px;border-radius:10px;margin-bottom:24px;
                border-bottom:4px solid {BARCA['gold']}">
        <h2 style="color:{BARCA['white']};margin:0;font-size:20px;font-weight:800">
            📧 Email Marketing
        </h2>
        <p style="color:{BARCA['line']};margin:4px 0 0;font-size:13px">
            Análisis completo de campañas · HubSpot · período seleccionado
        </p>
    </div>""", unsafe_allow_html=True)

    # ── Pre-compute aggregate stats (shared across tabs) ──────────────────────
    if not df_emails.empty:
        total_campanas   = len(df_emails)
        total_enviados   = int(df_emails["enviados"].sum())
        total_entregados = int(df_emails["entregados"].sum())
        total_aperturas  = int(df_emails["aperturas"].sum())
        total_clicks     = int(df_emails["clicks"].sum())
        total_bajas      = int(df_emails["bajas"].sum())
        total_rebotes    = int(df_emails["rebotes"].sum())
        total_spam       = int(df_emails["spam"].sum()) if "spam" in df_emails.columns else 0
        tasa_ap_global   = round(total_aperturas / total_enviados * 100, 1) if total_enviados else 0.0
        ctr_global       = round(total_clicks    / total_enviados * 100, 1) if total_enviados else 0.0
        ctor_global      = round(total_clicks    / total_aperturas * 100, 1) if total_aperturas else 0.0
        tasa_baja_global = round(total_bajas     / total_enviados * 100, 2) if total_enviados else 0.0
        bounce_rate      = round(total_rebotes   / total_enviados * 100, 2) if total_enviados else 0.0
    else:
        total_campanas = total_enviados = total_entregados = 0
        total_aperturas = total_clicks = total_bajas = total_rebotes = total_spam = 0
        tasa_ap_global = ctr_global = ctor_global = tasa_baja_global = bounce_rate = 0.0

    em_tab1, em_tab2, em_tab3, em_tab4, em_tab5 = st.tabs([
        "📊 Campañas enviadas",
        "📈 Rendimiento",
        "💡 Consejos",
        "📅 Programados",
        "📋 Listas y Segmentos",
    ])

    # ── Tab 1: Campañas enviadas ───────────────────────────────────────────────
    with em_tab1:
        if df_emails.empty:
            st.info("No hay emails enviados en el período seleccionado.")
        else:
            ek1, ek2, ek3, ek4, ek5, ek6 = st.columns(6)
            kpi_card(ek1, "Campañas enviadas",    total_campanas,         BARCA["blue"])
            kpi_card(ek2, "Contactos impactados", f"{total_enviados:,}",  BARCA["blue_deep"])
            kpi_card(ek3, "Tasa apertura",        f"{tasa_ap_global}%",   BARCA["gold"])
            kpi_card(ek4, "CTR",                  f"{ctr_global}%",       BARCA["garnet"])
            kpi_card(ek5, "CTOR",                 f"{ctor_global}%",      BARCA["blue"])
            kpi_card(ek6, "Tasa de baja",         f"{tasa_baja_global}%", BARCA["garnet_deep"])
            st.markdown("<br>", unsafe_allow_html=True)

            ec1, ec2 = st.columns(2)
            with ec1:
                if df_emails["mes"].nunique() > 1:
                    monthly = (df_emails.groupby("mes")
                               .agg(Enviados=("enviados", "sum"),
                                    Aperturas=("aperturas", "sum"),
                                    Clicks=("clicks", "sum"))
                               .reset_index().rename(columns={"mes": "Mes"}))
                    fig = px.line(monthly, x="Mes", y=["Enviados", "Aperturas", "Clicks"],
                                  title="Evolución mensual", markers=True,
                                  color_discrete_sequence=[BARCA["blue"], BARCA["gold"],
                                                            BARCA["garnet"]])
                    barca_layout(fig, 340)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    top_v = df_emails.nlargest(10, "enviados")
                    fig = px.bar(top_v.sort_values("enviados"), x="enviados", y="nombre",
                                 orientation="h", text_auto=True,
                                 title="Emails por volumen enviado",
                                 color_discrete_sequence=[BARCA["blue"]])
                    fig.update_layout(yaxis=dict(categoryorder="total ascending"))
                    barca_layout(fig, 340)
                    st.plotly_chart(fig, use_container_width=True)

            with ec2:
                top_open = df_emails[df_emails["enviados"] >= 10].nlargest(10, "tasa_apertura")
                if not top_open.empty:
                    fig = px.bar(top_open.sort_values("tasa_apertura"),
                                 x="tasa_apertura", y="nombre", orientation="h",
                                 text_auto=True, title="Top 10 por tasa de apertura (%)",
                                 color="tasa_apertura",
                                 color_continuous_scale=[BARCA["line2"], BARCA["gold"]])
                    fig.update_layout(coloraxis_showscale=False,
                                      yaxis=dict(categoryorder="total ascending"))
                    barca_layout(fig, 340)
                    st.plotly_chart(fig, use_container_width=True)

            ec3, ec4 = st.columns([2, 1])
            with ec3:
                fig = px.scatter(df_emails[df_emails["enviados"] > 0],
                                 x="tasa_apertura", y="ctr", hover_name="nombre",
                                 size="enviados", size_max=40,
                                 title="Apertura vs CTR (tamaño = enviados)",
                                 labels={"tasa_apertura": "Apertura (%)", "ctr": "CTR (%)"},
                                 color_discrete_sequence=[BARCA["blue"]])
                barca_layout(fig, 340)
                st.plotly_chart(fig, use_container_width=True)
            with ec4:
                st.markdown("#### Totales del período")
                st.dataframe(pd.DataFrame([
                    {"Métrica": "Campañas",      "Valor": f"{total_campanas}"},
                    {"Métrica": "Enviados",      "Valor": f"{total_enviados:,}"},
                    {"Métrica": "Entregados",    "Valor": f"{total_entregados:,}"},
                    {"Métrica": "Aperturas",     "Valor": f"{total_aperturas:,}"},
                    {"Métrica": "Tasa apertura", "Valor": f"{tasa_ap_global}%"},
                    {"Métrica": "Clicks",        "Valor": f"{total_clicks:,}"},
                    {"Métrica": "CTR",           "Valor": f"{ctr_global}%"},
                    {"Métrica": "CTOR",          "Valor": f"{ctor_global}%"},
                    {"Métrica": "Rebotes",       "Valor": f"{total_rebotes:,}"},
                    {"Métrica": "Bajas",         "Valor": f"{total_bajas:,}"},
                    {"Métrica": "Tasa baja",     "Valor": f"{tasa_baja_global}%"},
                    {"Métrica": "Spam reports",  "Valor": f"{total_spam:,}"},
                ]), use_container_width=True, hide_index=True, height=480)

            # Full table
            with st.expander("📋 Tabla completa de emails enviados"):
                rename_em = {
                    "nombre": "Nombre", "fecha": "Fecha", "asunto": "Asunto",
                    "listas": "Listas", "enviados": "Enviados",
                    "entregados": "Entregados", "aperturas": "Aperturas únicas",
                    "tasa_apertura": "Apertura %", "clicks": "Clicks únicos",
                    "ctr": "CTR %", "ctor": "CTOR %",
                    "rebotes": "Rebotes", "bajas": "Bajas", "spam": "Spam",
                }
                cols_show = [c for c in rename_em if c in df_emails.columns]
                tabla_em = df_emails[cols_show].rename(columns=rename_em)
                st.dataframe(
                    tabla_em.style
                    .background_gradient(subset=["Apertura %", "CTR %"],
                                         cmap="Blues", vmin=0, vmax=50)
                    .format({"Apertura %": "{:.1f}%", "CTR %": "{:.1f}%",
                             "CTOR %": "{:.1f}%"}),
                    use_container_width=True, hide_index=True,
                )
                fi_label = str(fi) if fi != "todos" else "todos"
                ff_label = str(ff) if ff != "todos" else "todos"
                st.download_button("⬇️ Descargar CSV",
                    data=tabla_em.to_csv(index=False, encoding="utf-8-sig"),
                    file_name=f"email_marketing_{fi_label}_{ff_label}.csv",
                    mime="text/csv", key="dl_emails")

            # URL click breakdown (lazy per-campaign)
            with st.expander("🔗 URLs más clickeadas por campaña"):
                nombres_cid = (df_emails[df_emails["campaign_id"] != ""][["nombre", "campaign_id"]]
                               .drop_duplicates("nombre") if "campaign_id" in df_emails.columns
                               else pd.DataFrame())
                if nombres_cid.empty:
                    st.info("No hay datos de campañas disponibles.")
                else:
                    sel_email = st.selectbox("Selecciona un email:",
                                             nombres_cid["nombre"].tolist(),
                                             key="sel_url_email")
                    row_cid = nombres_cid[nombres_cid["nombre"] == sel_email]
                    if not row_cid.empty:
                        cid_sel = row_cid["campaign_id"].values[0]
                        with st.spinner("Cargando URLs clickeadas..."):
                            urls = fetch_click_urls(str(cid_sel))
                        if urls:
                            df_urls = pd.DataFrame(urls, columns=["URL", "Clicks"])
                            st.dataframe(df_urls, use_container_width=True, hide_index=True)
                        else:
                            st.info("No se encontraron clicks registrados para este email.")

    # ── Tab 2: Rendimiento ─────────────────────────────────────────────────────
    with em_tab2:
        if df_emails.empty:
            st.info("No hay datos suficientes para el análisis.")
        else:
            # Benchmarks
            st.markdown("### 📊 Métricas vs Benchmarks del sector")
            st.caption("Referencias para email marketing de formación/educación · fuente: Mailchimp / HubSpot Industry Benchmarks")
            bench_rows = []
            for metrica, actual, bench, unit, es_negativo in [
                ("Tasa apertura",  tasa_ap_global,  25.0, "%", False),
                ("CTR",            ctr_global,       2.6, "%", False),
                ("CTOR",           ctor_global,     10.0, "%", False),
                ("Tasa rebote",    bounce_rate,      0.63, "%", True),
                ("Tasa de baja",   tasa_baja_global, 0.25, "%", True),
            ]:
                diff = round(actual - bench, 2)
                if es_negativo:
                    ok = actual <= bench
                    estado = "✅ OK" if ok else "⚠️ Alto"
                else:
                    ok = actual >= bench
                    estado = "✅ OK" if ok else "⚠️ Bajo"
                bench_rows.append({
                    "Métrica": metrica,
                    "Actual": f"{actual}{unit}",
                    "Benchmark": f"{bench}{unit}",
                    "Diferencia": f"{'+' if diff >= 0 else ''}{diff}{unit}",
                    "Estado": estado,
                })
            st.dataframe(pd.DataFrame(bench_rows), use_container_width=True, hide_index=True)
            st.markdown("<br>", unsafe_allow_html=True)

            # Top 5 / Bottom 5
            st.markdown("### 🏆 Mejores y peores campañas")
            valid_df = df_emails[df_emails["enviados"] >= 20].copy()
            if len(valid_df) >= 4:
                rb1, rb2 = st.columns(2)
                with rb1:
                    top5 = valid_df.nlargest(5, "tasa_apertura")
                    fig = px.bar(top5.sort_values("tasa_apertura"),
                                 x="tasa_apertura", y="nombre", orientation="h",
                                 text_auto=True, title="🏆 Top 5 — Mayor apertura",
                                 color_discrete_sequence=[BARCA["gold"]])
                    fig.update_layout(yaxis=dict(categoryorder="total ascending"))
                    barca_layout(fig, 300)
                    st.plotly_chart(fig, use_container_width=True)
                with rb2:
                    bot5 = valid_df.nsmallest(5, "tasa_apertura")
                    fig = px.bar(bot5.sort_values("tasa_apertura", ascending=False),
                                 x="tasa_apertura", y="nombre", orientation="h",
                                 text_auto=True, title="📉 Bottom 5 — Menor apertura",
                                 color_discrete_sequence=[BARCA["garnet"]])
                    fig.update_layout(yaxis=dict(categoryorder="total ascending"))
                    barca_layout(fig, 300)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Se necesitan al menos 4 campañas con >20 enviados para este análisis.")

            # Day of week
            if len(df_emails) >= 5:
                st.markdown("### 📅 Rendimiento por día de la semana")
                DIAS_ES = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
                           4: "Viernes", 5: "Sábado", 6: "Domingo"}
                df_dow = df_emails[df_emails["fecha"] != ""].copy()
                df_dow["dia_num"] = pd.to_datetime(df_dow["fecha"], errors="coerce").dt.dayofweek
                df_dow = df_dow.dropna(subset=["dia_num"])
                df_dow["dia_num"] = df_dow["dia_num"].astype(int)
                df_dow["dia"] = df_dow["dia_num"].map(DIAS_ES)
                dow_agg = (df_dow.groupby("dia_num")
                           .agg(campanas=("nombre", "count"),
                                avg_apertura=("tasa_apertura", "mean"),
                                avg_ctr=("ctr", "mean"))
                           .reset_index())
                dow_agg["dia"] = dow_agg["dia_num"].map(DIAS_ES)
                dow_agg["avg_apertura"] = dow_agg["avg_apertura"].round(1)
                dow_agg = dow_agg.sort_values("dia_num")
                rd1, rd2 = st.columns(2)
                with rd1:
                    fig = px.bar(dow_agg, x="dia", y="campanas",
                                 title="Campañas enviadas por día de la semana",
                                 color_discrete_sequence=[BARCA["blue"]])
                    barca_layout(fig, 300)
                    st.plotly_chart(fig, use_container_width=True)
                with rd2:
                    fig = px.bar(dow_agg, x="dia", y="avg_apertura",
                                 title="Apertura promedio por día de la semana (%)",
                                 color="avg_apertura",
                                 color_continuous_scale=[BARCA["line2"], BARCA["gold"]])
                    fig.update_layout(coloraxis_showscale=False)
                    barca_layout(fig, 300)
                    st.plotly_chart(fig, use_container_width=True)

            # Subject length
            st.markdown("### 📝 Longitud del asunto vs tasa de apertura")
            df_subj = df_emails[(df_emails["asunto"] != "") & (df_emails["enviados"] >= 10)].copy()
            if len(df_subj) >= 5:
                df_subj["largo_asunto"] = df_subj["asunto"].str.len()
                fig = px.scatter(df_subj, x="largo_asunto", y="tasa_apertura",
                                 hover_name="nombre", size="enviados", size_max=30,
                                 title="Nº caracteres del asunto vs Tasa de apertura",
                                 labels={"largo_asunto": "Caracteres", "tasa_apertura": "Apertura (%)"},
                                 color_discrete_sequence=[BARCA["blue_deep"]])
                barca_layout(fig, 340)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Se necesitan más campañas para este análisis.")

    # ── Tab 3: Consejos ────────────────────────────────────────────────────────
    with em_tab3:
        if df_emails.empty:
            st.info("No hay datos para generar consejos.")
        else:
            st.markdown("### 💡 Diagnóstico del canal — basado en tus datos")

            def _card_consejo(emoji, titulo, texto, color):
                st.markdown(f"""
                <div style="border-left:4px solid {color};padding:12px 16px;
                            margin-bottom:12px;background:{BARCA['white']};
                            border-radius:0 8px 8px 0;
                            box-shadow:0 1px 3px rgba(0,0,0,.07)">
                    <div style="font-weight:700;font-size:14px;
                                color:{BARCA['blue_ink']};margin-bottom:4px">
                        {emoji} {titulo}
                    </div>
                    <div style="font-size:13px;color:{BARCA['ink60']}">{texto}</div>
                </div>""", unsafe_allow_html=True)

            # Open rate
            if tasa_ap_global >= 30:
                _card_consejo("✅", f"Tasa apertura excelente ({tasa_ap_global}%)",
                    "Muy por encima del benchmark (25%). Continúa con la estrategia actual de asuntos y segmentación.",
                    BARCA["gold"])
            elif tasa_ap_global >= 20:
                _card_consejo("🟡", f"Tasa apertura aceptable ({tasa_ap_global}%)",
                    "Cerca del benchmark (25%). Prueba A/B testing en asuntos: preguntas, urgencia, emojis. "
                    "También optimiza el nombre del remitente para que sea reconocible.",
                    "#f0a500")
            else:
                _card_consejo("🔴", f"Tasa apertura baja ({tasa_ap_global}%)",
                    "Por debajo del benchmark (25%). Acciones clave: 1) Mejora los asuntos, 2) Revisa la hora/día de envío, "
                    "3) Limpia los contactos inactivos de tus listas.",
                    BARCA["garnet"])

            # CTR
            if ctr_global >= 3:
                _card_consejo("✅", f"CTR sólido ({ctr_global}%)",
                    "Por encima del benchmark (2.6%). Tu contenido y CTAs funcionan bien.",
                    BARCA["gold"])
            elif ctr_global >= 1.5:
                _card_consejo("🟡", f"CTR mejorable ({ctr_global}%)",
                    "Por debajo del benchmark (2.6%). Asegúrate de tener un CTA único, claro y con texto de acción: "
                    "'Ver programa', 'Reservar plaza', 'Descubrir más'.",
                    "#f0a500")
            else:
                _card_consejo("🔴", f"CTR bajo ({ctr_global}%)",
                    "Significativamente bajo (benchmark 2.6%). Revisa: ¿hay un CTA visible above the fold? "
                    "¿El diseño guía al lector hacia el click? ¿El CTA tiene contraste suficiente?",
                    BARCA["garnet"])

            # CTOR
            if ctor_global >= 12:
                _card_consejo("✅", f"CTOR excelente ({ctor_global}%)",
                    "Quien abre el email, hace click. El contenido es relevante y el CTA efectivo.",
                    BARCA["gold"])
            elif ctor_global >= 7:
                _card_consejo("🟡", f"CTOR correcto ({ctor_global}%)",
                    "Benchmark ~10%. Hay margen. Prueba a posicionar el CTA más arriba en el email "
                    "y a reducir el texto previo al mismo.",
                    "#f0a500")
            else:
                _card_consejo("🔴", f"CTOR bajo ({ctor_global}%)",
                    "Quienes abren el email no hacen click. El contenido puede no conectar con la expectativa "
                    "generada por el asunto, o el CTA no es lo suficientemente atractivo.",
                    BARCA["garnet"])

            # Bounce
            if bounce_rate > 2:
                _card_consejo("🔴", f"Tasa de rebote alta ({bounce_rate}%)",
                    "Benchmark <0.63%. Urgente: limpia la lista eliminando emails inválidos. "
                    "Un bounce alto afecta la reputación del dominio enviador.",
                    BARCA["garnet"])
            elif bounce_rate > 0.63:
                _card_consejo("🟡", f"Tasa de rebote moderada ({bounce_rate}%)",
                    "Por encima del benchmark. Considera limpiar listas periódicamente con un proceso de validación de emails.",
                    "#f0a500")
            else:
                _card_consejo("✅", f"Tasa de rebote saludable ({bounce_rate}%)",
                    "Dentro del rango óptimo (<0.63%). Las listas están en buen estado.",
                    BARCA["gold"])

            # Unsubscribe
            if tasa_baja_global > 0.5:
                _card_consejo("🔴", f"Tasa de bajas alta ({tasa_baja_global}%)",
                    "Benchmark <0.25%. Causas frecuentes: frecuencia excesiva, contenido irrelevante o "
                    "listas captadas sin double opt-in. Revisa el calendario y la segmentación.",
                    BARCA["garnet"])
            elif tasa_baja_global > 0.25:
                _card_consejo("🟡", f"Tasa de bajas moderada ({tasa_baja_global}%)",
                    "Ligeramente por encima del benchmark. Considera segmentar mejor el contenido "
                    "por interés o programa.",
                    "#f0a500")
            else:
                _card_consejo("✅", f"Tasa de bajas saludable ({tasa_baja_global}%)",
                    "Dentro del rango óptimo (<0.25%). Los contactos valoran el contenido.",
                    BARCA["gold"])

            # Frequency
            meses_activos = max(df_emails["mes"].nunique(), 1)
            freq = round(total_campanas / meses_activos, 1)
            if freq < 2:
                _card_consejo("🟡", f"Frecuencia de envío baja (~{freq}/mes)",
                    "Para mantener el engagement se recomienda al menos 2-4 envíos/mes. "
                    "La presencia constante refuerza el recall de marca.",
                    "#f0a500")
            elif freq > 12:
                _card_consejo("🟡", f"Alta frecuencia (~{freq}/mes)",
                    "Más de 3 envíos/semana puede generar fatiga. Monitoriza la tasa de bajas "
                    "y considera segmentar para no saturar a toda la base.",
                    "#f0a500")
            else:
                _card_consejo("✅", f"Frecuencia adecuada (~{freq}/mes)",
                    "Frecuencia saludable para mantener presencia sin saturar.",
                    BARCA["gold"])

            st.markdown("<br>")
            st.markdown("### 🚀 Oportunidades de mejora")
            oportunidades = [
                ("🧪", "A/B Testing de asuntos",
                 "Prueba 2 versiones de asunto en cada envío relevante. HubSpot permite A/B testing nativo "
                 "en emails de marketing. Aprenderás qué estilo conecta mejor con tu audiencia."),
                ("⏰", "Optimización del horario de envío",
                 "Analiza el día y la hora con mejor apertura en tu historial (ver pestaña Rendimiento). "
                 "Estandariza los envíos importantes en ese slot."),
                ("🎯", "Segmentación avanzada",
                 "En lugar de enviar a toda la lista, crea segmentos por comportamiento: "
                 "abrieron los últimos 3 emails, hicieron click, visitaron la web de un programa concreto."),
                ("♻️", "Campaña de re-engagement",
                 "Identifica contactos sin actividad en >90 días. Envía una campaña de reactivación "
                 "('¿Sigues ahí?'). Elimina los que no reaccionan para mantener la reputación del dominio."),
                ("📊", "Lead scoring por email",
                 "Asigna puntos en HubSpot a los contactos que abren y hacen click sistemáticamente. "
                 "Prioriza estos leads en el CRM para el equipo de ventas."),
                ("🔄", "Automatización post-formulario",
                 "Crea una secuencia automática tras cada formulario: bienvenida → contenido de valor "
                 "→ propuesta → seguimiento. Reduce la carga manual del equipo RST."),
            ]
            for icon, titulo, texto in oportunidades:
                st.markdown(f"""
                <div style="padding:12px 16px;margin-bottom:10px;
                            background:{BARCA['white']};border-radius:8px;
                            box-shadow:0 1px 3px rgba(0,0,0,.06)">
                    <div style="font-weight:700;font-size:14px;
                                color:{BARCA['blue_deep']};margin-bottom:4px">
                        {icon} {titulo}
                    </div>
                    <div style="font-size:13px;color:{BARCA['ink60']}">{texto}</div>
                </div>""", unsafe_allow_html=True)

    # ── Tab 4: Programados ─────────────────────────────────────────────────────
    with em_tab4:
        st.markdown("### 📅 Emails Programados")
        if df_prog.empty:
            st.info("No hay emails programados actualmente.")
        else:
            hoy_str = str(date.today())
            rename_prog = {
                "estado": "Estado", "nombre": "Nombre",
                "fecha_programada": "Fecha programada", "asunto": "Asunto",
                "remitente": "Remitente", "listas": "Listas",
            }
            disp_cols = [c for c in rename_prog if c in df_prog.columns]

            if "fecha_sort" in df_prog.columns:
                proximos = df_prog[df_prog["fecha_sort"] >= hoy_str]
                pasados  = df_prog[df_prog["fecha_sort"] <  hoy_str]
            else:
                proximos = df_prog
                pasados  = pd.DataFrame()

            if not proximos.empty:
                st.markdown(f"#### 🔜 Próximos envíos ({len(proximos)})")
                st.dataframe(proximos[disp_cols].rename(columns=rename_prog),
                             use_container_width=True, hide_index=True)
            else:
                st.info("No hay envíos futuros programados.")

            if not pasados.empty:
                st.markdown(f"#### ⚠️ Con fecha pasada — posiblemente pendientes ({len(pasados)})")
                st.caption("Estos emails tienen fecha de envío en el pasado pero siguen en estado SCHEDULED.")
                st.dataframe(pasados[disp_cols].rename(columns=rename_prog),
                             use_container_width=True, hide_index=True)

    # ── Tab 5: Listas y Segmentos ──────────────────────────────────────────────
    with em_tab5:
        st.markdown("### 📋 Listas y Segmentos de HubSpot")
        with st.spinner("Cargando listas..."):
            df_lists = fetch_all_lists()

        # Add ILS lists found in email campaigns that aren't in v1 lists
        if not df_emails.empty and "list_ids_raw" in df_emails.columns:
            known_ids = set(df_lists["list_id"].tolist()) if not df_lists.empty else set()
            email_ids: set = set()
            for ids_str in df_emails["list_ids_raw"].dropna():
                for lid in str(ids_str).split(","):
                    lid = lid.strip()
                    if lid:
                        email_ids.add(lid)
            missing_ids = email_ids - known_ids
            if missing_ids:
                with st.spinner(f"Cargando {len(missing_ids)} listas adicionales (ILS)..."):
                    ils_names = _fetch_list_names(tuple(sorted(missing_ids)))
                ils_rows = []
                for lid, name in ils_names.items():
                    ils_rows.append({
                        "list_id": lid, "nombre": name,
                        "tipo": "ILS", "size": 0,
                        "created": "", "updated": "",
                    })
                if ils_rows:
                    df_ils = pd.DataFrame(ils_rows)
                    df_lists = pd.concat([df_lists, df_ils], ignore_index=True)

        if df_lists.empty:
            st.info("No se pudieron obtener las listas.")
        else:
            # Cross-reference lists with email sends using list names
            def _avg(lst):
                return round(sum(lst) / len(lst), 1) if lst else 0.0

            if not df_emails.empty:
                list_stats: dict = {}
                for _, row_e in df_emails.iterrows():
                    listas_str = str(row_e.get("listas") or "")
                    if listas_str and listas_str != "—":
                        for lname in listas_str.split(", "):
                            lname = lname.strip()
                            if lname and lname != "—":
                                if lname not in list_stats:
                                    list_stats[lname] = {"n": 0, "ap": [], "ctr": []}
                                list_stats[lname]["n"] += 1
                                list_stats[lname]["ap"].append(row_e["tasa_apertura"])
                                list_stats[lname]["ctr"].append(row_e["ctr"])

                df_lists["emails_enviados"] = df_lists["nombre"].apply(
                    lambda n: list_stats.get(n, {}).get("n", 0))
                df_lists["avg_apertura"] = df_lists["nombre"].apply(
                    lambda n: _avg(list_stats.get(n, {}).get("ap", [])))
                df_lists["avg_ctr"] = df_lists["nombre"].apply(
                    lambda n: _avg(list_stats.get(n, {}).get("ctr", [])))
            else:
                df_lists["emails_enviados"] = 0
                df_lists["avg_apertura"]    = 0.0
                df_lists["avg_ctr"]         = 0.0

            # KPIs
            kl1, kl2, kl3 = st.columns(3)
            kpi_card(kl1, "Total listas/segmentos", len(df_lists), BARCA["blue"])
            kpi_card(kl2, "Total contactos",
                     f"{int(df_lists['size'].sum()):,}" if "size" in df_lists.columns else "—",
                     BARCA["blue_deep"])
            kpi_card(kl3, "Listas con envíos",
                     int((df_lists["emails_enviados"] > 0).sum()),
                     BARCA["gold"])
            st.markdown("<br>", unsafe_allow_html=True)

            # Filter controls
            fcol1, fcol2, fcol3 = st.columns([3, 1, 1])
            with fcol1:
                busqueda = st.text_input("🔍 Buscar lista por nombre",
                                         placeholder="Escribe para filtrar...",
                                         key="busq_lista")
            with fcol2:
                solo_con_envios = st.checkbox("Solo con envíos", key="chk_envios")
            with fcol3:
                tipo_filtro = st.selectbox("Tipo", ["Todos", "DYNAMIC", "STATIC", "ILS"],
                                           key="tipo_lista")

            # Apply filters
            df_disp = df_lists.copy()
            if busqueda:
                df_disp = df_disp[df_disp["nombre"].str.contains(busqueda, case=False, na=False)]
            if solo_con_envios:
                df_disp = df_disp[df_disp["emails_enviados"] > 0]
            if tipo_filtro != "Todos":
                df_disp = df_disp[df_disp["tipo"] == tipo_filtro]

            df_disp = df_disp.sort_values("emails_enviados", ascending=False)

            st.caption(f"Mostrando {len(df_disp)} de {len(df_lists)} listas")

            rename_lists = {
                "nombre": "Nombre", "tipo": "Tipo", "size": "Contactos",
                "emails_enviados": "Emails enviados",
                "avg_apertura": "Apertura % prom.",
                "avg_ctr": "CTR % prom.",
                "created": "Creada", "updated": "Actualizada",
            }
            cols_disp = [c for c in rename_lists if c in df_disp.columns]
            st.dataframe(df_disp[cols_disp].rename(columns=rename_lists),
                         use_container_width=True, hide_index=True,
                         height=600)

            # Per-list email detail
            if not df_emails.empty:
                listas_con_envios = (df_lists[df_lists["emails_enviados"] > 0]
                                     .sort_values("emails_enviados", ascending=False)["nombre"]
                                     .tolist())
                if listas_con_envios:
                    with st.expander("📧 Ver campañas asociadas a una lista"):
                        sel_lista = st.selectbox("Selecciona una lista:",
                                                 listas_con_envios, key="sel_lista_email")
                        emails_lista = df_emails[
                            df_emails["listas"].str.contains(sel_lista, na=False, regex=False)
                        ]
                        if not emails_lista.empty:
                            cols_em = [c for c in ["nombre", "fecha", "asunto", "enviados",
                                                    "tasa_apertura", "ctr", "ctor", "bajas"]
                                       if c in emails_lista.columns]
                            ren_em = {"nombre": "Email", "fecha": "Fecha",
                                      "asunto": "Asunto", "enviados": "Enviados",
                                      "tasa_apertura": "Apertura %", "ctr": "CTR %",
                                      "ctor": "CTOR %", "bajas": "Bajas"}
                            st.dataframe(emails_lista[cols_em].rename(columns=ren_em),
                                         use_container_width=True, hide_index=True)

    # ── WORKFLOWS & SECUENCIAS ─────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='border-top:2px solid {BARCA['gold']};margin:24px 0 16px 0'></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<h2 style='color:{BARCA['blue_ink']};font-size:22px;font-weight:700;margin-bottom:4px'>"
        "⚡ Workflows &amp; Secuencias</h2>"
        f"<p style='color:{BARCA['ink60']};font-size:13px;margin-top:0'>"
        "Automatizaciones activas en HubSpot – workflows de marketing y secuencias de ventas</p>",
        unsafe_allow_html=True,
    )

    wf_tab1, wf_tab2 = st.tabs(["⚡ Workflows activos", "📨 Secuencias de ventas"])

    with wf_tab1:
        with st.spinner("Cargando workflows... (primera vez puede tardar ~10 s)"):
            df_wf = fetch_workflows()

        if df_wf.empty:
            st.warning("No se pudieron obtener los workflows.")
        else:
            wf_total    = len(df_wf)
            wf_active   = int(df_wf["activo"].sum())
            wf_disabled = wf_total - wf_active
            wf_email    = int((df_wf["n_emails"] > 0).sum())

            wk1, wk2, wk3, wk4 = st.columns(4)
            kpi_card(wk1, "Total workflows",   wf_total,    BARCA["blue"])
            kpi_card(wk2, "Workflows activos", wf_active,   BARCA["gold"])
            kpi_card(wk3, "Desactivados",      wf_disabled, BARCA["blue_deep"])
            kpi_card(wk4, "Disparan email",    wf_email,    "#2e7d32")
            st.markdown("<br>", unsafe_allow_html=True)

            wfc1, wfc2 = st.columns([2, 3])
            with wfc1:
                wf_estado = st.radio("Mostrar:", ["Activos", "Todos", "Desactivados"],
                                     horizontal=True, key="wf_estado")
            with wfc2:
                wf_busq = st.text_input("🔍 Buscar por nombre o email", key="wf_busq")

            df_wf_show = df_wf.copy()
            if wf_estado == "Activos":
                df_wf_show = df_wf_show[df_wf_show["activo"]]
            elif wf_estado == "Desactivados":
                df_wf_show = df_wf_show[~df_wf_show["activo"]]
            if wf_busq:
                wf_mask = (
                    df_wf_show["nombre"].str.contains(wf_busq, case=False, na=False) |
                    df_wf_show["emails"].str.contains(wf_busq, case=False, na=False)
                )
                df_wf_show = df_wf_show[wf_mask]

            # ---- Main table (includes avg metrics for workflows with emails) ----
            def _fmt_pct(v):
                return f"{v}%" if v is not None else "—"

            table_rows = []
            for _, wrow in df_wf_show.iterrows():
                table_rows.append({
                    "Nombre del Workflow":  wrow["nombre"],
                    "Activo":               wrow["activo"],
                    "Tipo de acción":       wrow["acciones"],
                    "Email(s) que dispara": wrow["emails"],
                    "Enviados (total)":     int(wrow["enviados_total"]) if wrow["n_emails"] > 0 else "—",
                    "Apertura %":           _fmt_pct(wrow["avg_apertura"]) if wrow["n_emails"] > 0 else "—",
                    "CTR %":                _fmt_pct(wrow["avg_ctr"])      if wrow["n_emails"] > 0 else "—",
                    "CTOR %":               _fmt_pct(wrow["avg_ctor"])     if wrow["n_emails"] > 0 else "—",
                    "Actualizado":          wrow["actualizado"],
                })
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

            # ---- Drilldown: per-email metrics ----
            df_wf_email = df_wf_show[df_wf_show["n_emails"] > 0]
            if not df_wf_email.empty:
                with st.expander(
                    f"📧 Métricas detalladas — {len(df_wf_email)} workflow(s) con email"
                ):
                    import json as _json_disp
                    for _, wrow in df_wf_email.sort_values("nombre").iterrows():
                        estado_icon = "✅ Activo" if wrow["activo"] else "⏸ Desactivado"
                        st.markdown(
                            f"**{wrow['nombre']}** &nbsp;·&nbsp; {estado_icon}",
                            unsafe_allow_html=True,
                        )
                        try:
                            email_detail = _json_disp.loads(wrow["email_detail"] or "[]")
                        except Exception:
                            email_detail = []
                        if email_detail:
                            detail_rows = []
                            for em in email_detail:
                                ap  = em.get("tasa_apertura")
                                ctr = em.get("ctr")
                                ctor = em.get("ctor")
                                reb  = em.get("tasa_rebote")
                                detail_rows.append({
                                    "Email":       em.get("nombre", "—"),
                                    "Enviados":    int(em.get("sent", 0)),
                                    "Apertura %":  f"{ap}%"   if ap  is not None else "—",
                                    "CTR %":       f"{ctr}%"  if ctr is not None else "—",
                                    "CTOR %":      f"{ctor}%" if ctor is not None else "—",
                                    "Rebote %":    f"{reb}%"  if reb is not None else "—",
                                    "Bajas":       int(em.get("unsubs", 0)),
                                })
                            st.dataframe(
                                pd.DataFrame(detail_rows),
                                use_container_width=True,
                                hide_index=True,
                            )
                        st.markdown(
                            f"<small style='color:{BARCA['ink40']}'>Actualizado: {wrow['actualizado']}</small>",
                            unsafe_allow_html=True,
                        )
                        st.markdown("---")

            # Breakdown chart: workflows by action type
            action_counts: dict = {}
            for row_ac in df_wf_show["acciones"]:
                for ac in str(row_ac).split(", "):
                    ac = ac.strip()
                    if ac and ac != "—":
                        action_counts[ac] = action_counts.get(ac, 0) + 1
            if action_counts:
                import plotly.graph_objects as go
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(
                    f"<p style='font-weight:600;color:{BARCA['blue_ink']};font-size:14px'>"
                    "Distribución por tipo de acción</p>",
                    unsafe_allow_html=True,
                )
                ac_df = pd.DataFrame(
                    sorted(action_counts.items(), key=lambda x: x[1], reverse=True),
                    columns=["Tipo", "Workflows"],
                )
                fig_ac = go.Figure(go.Bar(
                    x=ac_df["Tipo"],
                    y=ac_df["Workflows"],
                    marker_color=BARCA["blue"],
                    text=ac_df["Workflows"],
                    textposition="outside",
                ))
                fig_ac = barca_layout(fig_ac, height=300)
                fig_ac.update_layout(xaxis_title="", yaxis_title="Nº workflows")
                st.plotly_chart(fig_ac, use_container_width=True)

    with wf_tab2:
        with st.spinner("Cargando secuencias... (primera vez puede tardar ~15 s)"):
            df_seq = fetch_sequences()

        if df_seq.empty:
            st.info("No se encontraron secuencias de ventas.")
        else:
            sq1, sq2, sq3, sq4 = st.columns(4)
            kpi_card(sq1, "Secuencias únicas",          len(df_seq),                BARCA["blue"])
            kpi_card(sq2, "Total emails en secuencias", int(df_seq["emails"].sum()), BARCA["gold"])
            kpi_card(sq3, "Total tareas en secuencias", int(df_seq["tareas"].sum()), BARCA["blue_deep"])
            kpi_card(sq4, "Promedio pasos/secuencia",
                     round(df_seq["total_pasos"].mean(), 1) if not df_seq.empty else 0,
                     "#2e7d32")
            st.markdown("<br>", unsafe_allow_html=True)

            seq_busq = st.text_input("🔍 Buscar secuencia", key="seq_busq")
            df_seq_show = df_seq.copy()
            if seq_busq:
                df_seq_show = df_seq_show[
                    df_seq_show["nombre"].str.contains(seq_busq, case=False, na=False)
                ]

            st.dataframe(
                df_seq_show[["nombre", "total_pasos", "emails", "tareas", "n_resp", "creado"]]
                .rename(columns={
                    "nombre":      "Nombre de la secuencia",
                    "total_pasos": "Total pasos",
                    "emails":      "Emails",
                    "tareas":      "Tareas",
                    "n_resp":      "Comerciales asignados",
                    "creado":      "Creada",
                }),
                use_container_width=True,
                hide_index=True,
            )

            with st.expander("🔍 Ver pasos completos de cada secuencia"):
                for _, srow in df_seq_show.iterrows():
                    st.markdown(
                        f"**{srow['nombre']}** &nbsp;·&nbsp; "
                        f"{srow['total_pasos']} pasos &nbsp;·&nbsp; "
                        f"{srow['emails']} emails &nbsp;·&nbsp; "
                        f"{srow['tareas']} tareas",
                        unsafe_allow_html=True,
                    )
                    pasos_str = str(srow["pasos"])
                    if pasos_str and pasos_str != "—":
                        for p in pasos_str.split(" → "):
                            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;→ {p.strip()}")
                    owners_str = str(srow["responsables"])
                    if owners_str:
                        st.markdown(
                            f"<small style='color:{BARCA['ink40']}'>Comerciales: {owners_str}</small>",
                            unsafe_allow_html=True,
                        )
                    st.markdown("---")

    # ── Análisis por Programa ───────────────────────────────────────────────────
    st.markdown(
        f"<h2 style='color:{BARCA['garnet']};margin-top:2rem'>🎓 Análisis por Programa</h2>",
        unsafe_allow_html=True,
    )

    df_prog = df if not df.empty else pd.DataFrame(columns=df.columns)
    df_prog_sin = df_prog[df_prog["programa"] != "Sin programa"]

    # ── Filtro local de modalidad ──────────────────────────────────────────────
    _modal_opts = ["Todas las modalidades", "Presencial", "Online", "Sin modalidad"]
    _modal_sel  = st.radio(
        "Modalidad",
        _modal_opts,
        index=0,
        horizontal=True,
        key="prog_modal_filter",
    )
    if _modal_sel != "Todas las modalidades":
        df_prog     = df_prog[df_prog["modalidad"] == _modal_sel]
        df_prog_sin = df_prog_sin[df_prog_sin["modalidad"] == _modal_sel]

    prog_tab1, prog_tab2, prog_tab3, prog_tab4 = st.tabs([
        "📊 Leads por Programa",
        "🔀 Programa × Fuente",
        "✅ Calidad por Programa",
        "🌍 Mercado",
    ])

    with prog_tab1:
        if df_prog_sin.empty:
            st.info("No hay contactos con programa asignado en el período seleccionado.")
        else:
            # KPIs
            n_prog_total = len(df_prog_sin)
            n_programas  = df_prog_sin["programa"].nunique()
            top_prog     = df_prog_sin["programa"].value_counts().idxmax()
            top_prog_n   = df_prog_sin["programa"].value_counts().max()

            kc1, kc2, kc3 = st.columns(3)
            kc1.metric("Leads con programa", f"{n_prog_total:,}")
            kc2.metric("Programas distintos", f"{n_programas}")
            kc3.metric("Programa más solicitado", top_prog, f"{top_prog_n} leads")

            st.markdown("---")

            # Bar chart — top 25 programs
            prog_counts = (df_prog_sin["programa"]
                           .value_counts()
                           .reset_index()
                           .rename(columns={"index": "Programa", "programa": "Leads"}))
            prog_counts.columns = ["Programa", "Leads"]
            top25 = prog_counts.head(25)
            fig_prog = px.bar(
                top25, x="Leads", y="Programa", orientation="h",
                title=f"Top {len(top25)} programas por número de leads",
                color="Leads",
                color_continuous_scale=[[0, BARCA["yellow"]], [1, BARCA["garnet"]]],
                text="Leads",
            )
            fig_prog.update_layout(
                yaxis={"categoryorder": "total ascending"},
                showlegend=False,
                coloraxis_showscale=False,
                height=max(400, len(top25) * 28),
                margin={"l": 0, "r": 20, "t": 40, "b": 20},
            )
            fig_prog.update_traces(textposition="outside")
            st.plotly_chart(fig_prog, use_container_width=True)

            with st.expander("📋 Tabla completa de leads por programa"):
                prog_full = prog_counts.copy()
                prog_full["% del total"] = (prog_full["Leads"] / prog_full["Leads"].sum() * 100).round(1)
                # Añadir columnas por modalidad
                for _mod in ["Presencial", "Online", "Sin modalidad"]:
                    prog_full[_mod] = (
                        df_prog_sin[df_prog_sin["modalidad"] == _mod]
                        .groupby("programa").size()
                        .reindex(prog_full["Programa"]).fillna(0).astype(int).values
                    )
                st.dataframe(
                    prog_full.style
                        .background_gradient(subset=["Leads"], cmap="Reds")
                        .format({"% del total": "{:.1f}%"}),
                    use_container_width=True,
                    hide_index=True,
                )

            # Gráfico: programa × modalidad
            if _modal_sel == "Todas las modalidades":
                pm_grp2 = (df_prog_sin[df_prog_sin["programa"].isin(top25["Programa"])]
                            .groupby(["programa", "modalidad"])
                            .size().reset_index(name="Leads"))
                fig_pm2 = px.bar(
                    pm_grp2, x="Leads", y="programa", color="modalidad", orientation="h",
                    title="Leads por programa y modalidad",
                    barmode="stack",
                    color_discrete_map={
                        "Presencial": BARCA["garnet"],
                        "Online":     BARCA["blue"],
                        "Sin modalidad": BARCA["ink20"],
                    },
                    text="Leads",
                )
                fig_pm2.update_layout(
                    yaxis={"categoryorder": "total ascending"},
                    height=max(400, len(top25) * 28),
                    margin={"l": 0, "r": 20, "t": 40, "b": 20},
                    legend={"title": "Modalidad"},
                )
                fig_pm2.update_traces(textposition="inside", textfont_size=11)
                st.plotly_chart(fig_pm2, use_container_width=True)

    with prog_tab2:
        if df_prog_sin.empty:
            st.info("No hay contactos con programa asignado en el período seleccionado.")
        else:
            # Filter: choose top-N programs to avoid visual overload
            top_n_opts = [10, 15, 20, 30]
            top_n = st.selectbox("Mostrar top N programas", top_n_opts, index=0, key="prog_topn")
            top_progs = (df_prog_sin["programa"].value_counts().head(top_n).index.tolist())
            df_pf = df_prog_sin[df_prog_sin["programa"].isin(top_progs)]

            # Stacked bar: programa × fuente
            pf_grp = (df_pf.groupby(["programa", "fuente"])
                       .size()
                       .reset_index(name="Leads"))
            fig_pf = px.bar(
                pf_grp, x="Leads", y="programa", color="fuente", orientation="h",
                title=f"Leads por programa y fuente de tráfico (Top {top_n})",
                barmode="stack",
                text="Leads",
            )
            fig_pf.update_layout(
                yaxis={"categoryorder": "total ascending"},
                height=max(450, top_n * 30),
                margin={"l": 0, "r": 20, "t": 40, "b": 20},
                legend={"title": "Fuente"},
            )
            fig_pf.update_traces(textposition="inside", textfont_size=11)
            st.plotly_chart(fig_pf, use_container_width=True)

            st.markdown("#### Tabla pivote: Programa × Fuente")
            pivot_pf = (pf_grp.pivot(index="programa", columns="fuente", values="Leads")
                        .fillna(0).astype(int))
            pivot_pf["Total"] = pivot_pf.sum(axis=1)
            # Añadir columnas de modalidad
            for _mod in ["Presencial", "Online", "Sin modalidad"]:
                pivot_pf[_mod] = (
                    df_pf[df_pf["modalidad"] == _mod]
                    .groupby("programa").size()
                    .reindex(pivot_pf.index).fillna(0).astype(int)
                )
            pivot_pf = pivot_pf.sort_values("Total", ascending=False)
            st.dataframe(
                pivot_pf.style.background_gradient(subset=["Total"], cmap="Reds"),
                use_container_width=True,
            )

            # Programa × Modalidad (solo cuando no hay filtro activo)
            if _modal_sel == "Todas las modalidades":
                st.markdown("#### Tabla pivote: Programa × Modalidad")
                pm_pivot = (df_pf.groupby(["programa", "modalidad"])
                             .size().unstack(fill_value=0))
                pm_pivot["Total"] = pm_pivot.sum(axis=1)
                pm_pivot = pm_pivot.sort_values("Total", ascending=False)
                st.dataframe(
                    pm_pivot.style.background_gradient(subset=["Total"], cmap="Reds"),
                    use_container_width=True,
                )

    with prog_tab3:
        if df_prog_sin.empty:
            st.info("No hay contactos con programa asignado en el período seleccionado.")
        else:
            CALIDAD_ORDER   = ["Cierre Ganado", "En proceso", "Perdido", "No válido"]
            CALIDAD_COLORS  = {
                "Cierre Ganado": BARCA["gold"],
                "En proceso":    BARCA["blue"],
                "Perdido":       BARCA["garnet"],
                "No válido":     BARCA["ink40"],
            }

            # Top-N filter
            top_n_cal = st.selectbox("Mostrar top N programas", [10, 15, 20, 30],
                                      index=0, key="cal_topn")
            top_progs_cal = (df_prog_sin["programa"].value_counts()
                             .head(top_n_cal).index.tolist())
            df_cal = df_prog_sin[df_prog_sin["programa"].isin(top_progs_cal)]

            # KPIs de calidad global
            q_ganado  = (df_prog_sin["calidad"] == "Cierre Ganado").sum()
            q_proceso = (df_prog_sin["calidad"] == "En proceso").sum()
            q_perdido = (df_prog_sin["calidad"] == "Perdido").sum()
            q_novalid = (df_prog_sin["calidad"] == "No válido").sum()
            q_total   = len(df_prog_sin)

            kq1, kq2, kq3, kq4 = st.columns(4)
            kq1.metric("Cierre Ganado", f"{q_ganado}",
                       f"{q_ganado/q_total*100:.1f}%" if q_total else "—")
            kq2.metric("En proceso",    f"{q_proceso}",
                       f"{q_proceso/q_total*100:.1f}%" if q_total else "—")
            kq3.metric("Perdidos",      f"{q_perdido}",
                       f"{q_perdido/q_total*100:.1f}%" if q_total else "—")
            kq4.metric("No válido",     f"{q_novalid}",
                       f"{q_novalid/q_total*100:.1f}%" if q_total else "—")

            st.markdown("---")

            # Stacked bar: calidad por programa
            cal_grp = (df_cal.groupby(["programa", "calidad"])
                        .size()
                        .reset_index(name="Leads"))
            cal_grp["calidad"] = pd.Categorical(cal_grp["calidad"],
                                                 categories=CALIDAD_ORDER, ordered=True)
            fig_cal = px.bar(
                cal_grp.sort_values("calidad"),
                x="Leads", y="programa", color="calidad", orientation="h",
                title=f"Calidad de leads por programa (Top {top_n_cal})",
                barmode="stack",
                color_discrete_map=CALIDAD_COLORS,
                text="Leads",
                category_orders={"calidad": CALIDAD_ORDER},
            )
            fig_cal.update_layout(
                yaxis={"categoryorder": "total ascending"},
                height=max(450, top_n_cal * 30),
                margin={"l": 0, "r": 20, "t": 40, "b": 20},
                legend={"title": "Calidad"},
            )
            fig_cal.update_traces(textposition="inside", textfont_size=11)
            st.plotly_chart(fig_cal, use_container_width=True)

            # Stacked bar: calidad por fuente dentro de un programa seleccionado
            st.markdown("#### Calidad por fuente — detalle por programa")
            prog_sel = st.selectbox(
                "Selecciona un programa",
                top_progs_cal,
                key="cal_prog_sel",
            )
            df_prog_det = df_prog_sin[df_prog_sin["programa"] == prog_sel]
            det_grp = (df_prog_det.groupby(["fuente", "calidad"])
                        .size()
                        .reset_index(name="Leads"))
            if det_grp.empty:
                st.info("Sin datos para el programa seleccionado.")
            else:
                fig_det = px.bar(
                    det_grp, x="fuente", y="Leads", color="calidad",
                    title=f"Calidad de leads para «{prog_sel}» por fuente",
                    barmode="stack",
                    color_discrete_map=CALIDAD_COLORS,
                    text="Leads",
                    category_orders={"calidad": CALIDAD_ORDER},
                )
                fig_det.update_layout(
                    margin={"l": 0, "r": 20, "t": 40, "b": 20},
                    legend={"title": "Calidad"},
                )
                fig_det.update_traces(textposition="inside")
                st.plotly_chart(fig_det, use_container_width=True)

            # Pivot table: programa × calidad
            with st.expander("📋 Tabla pivote: Programa × Calidad"):
                pivot_cal = (df_prog_sin.groupby(["programa", "calidad"])
                             .size()
                             .unstack(fill_value=0))
                for col in CALIDAD_ORDER:
                    if col not in pivot_cal.columns:
                        pivot_cal[col] = 0
                pivot_cal = pivot_cal[
                    [c for c in CALIDAD_ORDER if c in pivot_cal.columns]
                ]
                pivot_cal["Total"] = pivot_cal.sum(axis=1)
                if "Cierre Ganado" in pivot_cal.columns and "Total" in pivot_cal.columns:
                    pivot_cal["Tasa CG %"] = (
                        pivot_cal["Cierre Ganado"] / pivot_cal["Total"] * 100
                    ).round(1)
                # Añadir columnas de modalidad
                for _mod in ["Presencial", "Online", "Sin modalidad"]:
                    pivot_cal[_mod] = (
                        df_prog_sin[df_prog_sin["modalidad"] == _mod]
                        .groupby("programa").size()
                        .reindex(pivot_cal.index).fillna(0).astype(int)
                    )
                pivot_cal = pivot_cal.sort_values("Total", ascending=False)
                st.dataframe(
                    pivot_cal.style
                        .background_gradient(subset=["Total"], cmap="Reds")
                        .format({"Tasa CG %": "{:.1f}%"}, na_rep="—"),
                    use_container_width=True,
                )

            st.markdown("---")
            st.markdown("#### Leads válidos / no válidos por programa")

            VALIDO_COLORS = {
                "Válido":        BARCA["blue"],
                "No válido":     BARCA["garnet"],
                "Sin clasificar": BARCA["ink20"],
            }
            VALIDO_ORDER = ["Válido", "No válido", "Sin clasificar"]

            df_val = df_prog_sin.copy()
            df_val["validez"] = df_val["lead_valido"].apply(
                lambda v: "Válido" if v == "Válido"
                          else ("No válido" if v == "No válido" else "Sin clasificar")
            )

            # KPIs validez
            v_val   = (df_val["validez"] == "Válido").sum()
            v_noval = (df_val["validez"] == "No válido").sum()
            v_sin   = (df_val["validez"] == "Sin clasificar").sum()
            v_total = len(df_val)
            kv1, kv2, kv3 = st.columns(3)
            kv1.metric("Válidos",         f"{v_val}",
                       f"{v_val/v_total*100:.1f}%" if v_total else "—")
            kv2.metric("No válidos",       f"{v_noval}",
                       f"{v_noval/v_total*100:.1f}%" if v_total else "—")
            kv3.metric("Sin clasificar",   f"{v_sin}",
                       f"{v_sin/v_total*100:.1f}%" if v_total else "—")

            top_progs_val = (df_prog_sin["programa"].value_counts()
                             .head(top_n_cal).index.tolist())
            df_val_top = df_val[df_val["programa"].isin(top_progs_val)]

            val_grp = (df_val_top.groupby(["programa", "validez"])
                       .size().reset_index(name="Leads"))
            val_grp["validez"] = pd.Categorical(val_grp["validez"],
                                                 categories=VALIDO_ORDER, ordered=True)
            fig_val = px.bar(
                val_grp.sort_values("validez"),
                x="Leads", y="programa", color="validez", orientation="h",
                title=f"Válidos / No válidos por programa (Top {top_n_cal})",
                barmode="stack",
                color_discrete_map=VALIDO_COLORS,
                text="Leads",
                category_orders={"validez": VALIDO_ORDER},
            )
            fig_val.update_layout(
                yaxis={"categoryorder": "total ascending"},
                height=max(420, top_n_cal * 30),
                margin={"l": 0, "r": 20, "t": 40, "b": 20},
                legend={"title": "Validez"},
            )
            fig_val.update_traces(textposition="inside", textfont_size=11)
            st.plotly_chart(fig_val, use_container_width=True)

            st.markdown("#### Leads válidos / no válidos por fuente — detalle por programa")
            prog_sel_val = st.selectbox(
                "Selecciona un programa",
                top_progs_val,
                key="val_prog_sel",
            )
            df_val_det = df_val[df_val["programa"] == prog_sel_val]
            val_det_grp = (df_val_det.groupby(["fuente", "validez"])
                           .size().reset_index(name="Leads"))
            if val_det_grp.empty:
                st.info("Sin datos para el programa seleccionado.")
            else:
                fig_val_det = px.bar(
                    val_det_grp, x="fuente", y="Leads", color="validez",
                    title=f"Validez de leads para «{prog_sel_val}» por fuente",
                    barmode="stack",
                    color_discrete_map=VALIDO_COLORS,
                    text="Leads",
                    category_orders={"validez": VALIDO_ORDER},
                )
                fig_val_det.update_layout(
                    margin={"l": 0, "r": 20, "t": 40, "b": 20},
                    legend={"title": "Validez"},
                )
                fig_val_det.update_traces(textposition="inside")
                st.plotly_chart(fig_val_det, use_container_width=True)

            with st.expander("📋 Tabla pivote: Programa × Validez"):
                pivot_val = (df_val.groupby(["programa", "validez"])
                             .size().unstack(fill_value=0))
                for col in VALIDO_ORDER:
                    if col not in pivot_val.columns:
                        pivot_val[col] = 0
                pivot_val = pivot_val[[c for c in VALIDO_ORDER if c in pivot_val.columns]]
                pivot_val["Total"] = pivot_val.sum(axis=1)
                if "No válido" in pivot_val.columns:
                    pivot_val["Tasa No válido %"] = (
                        pivot_val["No válido"] / pivot_val["Total"] * 100
                    ).round(1)
                # Añadir columnas de modalidad
                for _mod in ["Presencial", "Online", "Sin modalidad"]:
                    pivot_val[_mod] = (
                        df_val[df_val["modalidad"] == _mod]
                        .groupby("programa").size()
                        .reindex(pivot_val.index).fillna(0).astype(int)
                    )
                pivot_val = pivot_val.sort_values("Total", ascending=False)
                st.dataframe(
                    pivot_val.style
                        .background_gradient(subset=["Total"], cmap="Reds")
                        .format({"Tasa No válido %": "{:.1f}%"}, na_rep="—"),
                    use_container_width=True,
                )

    with prog_tab4:
        MERCADO_COLORS = {
            "España":    BARCA["garnet"],
            "Latam":     BARCA["blue"],
            "Otro":      BARCA["yellow"],
            "Sin datos": BARCA["ink20"],
        }
        MERCADO_ORDER = ["España", "Latam", "Otro", "Sin datos"]

        n_es  = (df["mercado"] == "España").sum()
        n_lat = (df["mercado"] == "Latam").sum()
        n_ot  = (df["mercado"] == "Otro").sum()
        n_sd  = (df["mercado"] == "Sin datos").sum()
        n_tot = len(df)

        km1, km2, km3, km4 = st.columns(4)
        km1.metric("España",    f"{n_es:,}",  f"{n_es/n_tot*100:.1f}%" if n_tot else "—")
        km2.metric("Latam",     f"{n_lat:,}", f"{n_lat/n_tot*100:.1f}%" if n_tot else "—")
        km3.metric("Otro",      f"{n_ot:,}",  f"{n_ot/n_tot*100:.1f}%" if n_tot else "—")
        km4.metric("Sin datos", f"{n_sd:,}",  f"{n_sd/n_tot*100:.1f}%" if n_tot else "—")

        if n_sd > 0:
            pct_sd = n_sd / n_tot * 100 if n_tot else 0
            st.info(
                f"ℹ️ **{n_sd:,} contactos ({pct_sd:.1f}%) no tienen país registrado** en HubSpot "
                f"(ningún campo `ip_country`, `country` ni `pais_de_residencia` relleno). "
                f"Pueden incluir leads de España y Latam que no se geocodificaron. "
                f"España captura: Spain, España, ES, Espanya, Andorra y variantes regionales."
            )

        st.markdown("---")

        col_pie, col_src = st.columns([1, 2])

        with col_pie:
            merc_dist = (df.groupby("mercado").size()
                          .reset_index(name="Leads")
                          .sort_values("Leads", ascending=False))
            fig_pie = px.pie(
                merc_dist, names="mercado", values="Leads",
                title="Distribución por mercado",
                hole=0.55,
                color="mercado",
                color_discrete_map=MERCADO_COLORS,
            )
            fig_pie.update_traces(textposition="outside", textinfo="percent+label")
            fig_pie.update_layout(showlegend=False,
                                   margin={"l": 0, "r": 0, "t": 40, "b": 0})
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_src:
            merc_src = (df.groupby(["mercado", "fuente"])
                         .size().reset_index(name="Leads"))
            fig_ms = px.bar(
                merc_src, x="fuente", y="Leads", color="mercado",
                title="Fuente de tráfico por mercado",
                barmode="stack",
                color_discrete_map=MERCADO_COLORS,
                category_orders={"mercado": MERCADO_ORDER},
            )
            fig_ms.update_layout(
                margin={"l": 0, "r": 0, "t": 40, "b": 20},
                legend={"title": "Mercado"},
            )
            st.plotly_chart(fig_ms, use_container_width=True)

        # ── Latam: leads por país ───────────────────────────────────────────
        st.markdown("### 🌎 Latam — Leads por país")
        df_lat = df[df["mercado"] == "Latam"].copy()

        if df_lat.empty:
            st.info("No hay leads de Latam en el período seleccionado.")
        else:
            df_lat["pais_lat"] = df_lat["pais"].apply(
                lambda p: LATAM_PAIS_ES.get(p, p)
            )

            # KPIs latam
            n_paises_lat = df_lat["pais_lat"].nunique()
            top_pais_lat = df_lat["pais_lat"].value_counts().idxmax()
            top_pais_n   = df_lat["pais_lat"].value_counts().max()
            lk1, lk2 = st.columns(2)
            lk1.metric("Países Latam", f"{n_paises_lat}")
            lk2.metric("País top", top_pais_lat, f"{top_pais_n} leads")

            lat_paises = (df_lat.groupby("pais_lat").size()
                           .reset_index(name="Leads")
                           .sort_values("Leads", ascending=False))

            fig_lat_p = px.bar(
                lat_paises, x="Leads", y="pais_lat", orientation="h",
                title="Leads por país — Latam",
                color="Leads",
                color_continuous_scale=[[0, BARCA["bone"]], [1, BARCA["blue"]]],
                text="Leads",
            )
            fig_lat_p.update_layout(
                yaxis={"categoryorder": "total ascending"},
                coloraxis_showscale=False,
                height=max(350, len(lat_paises) * 30),
                margin={"l": 0, "r": 20, "t": 40, "b": 20},
            )
            fig_lat_p.update_traces(textposition="outside")
            st.plotly_chart(fig_lat_p, use_container_width=True)

            # País × Fuente — Latam
            st.markdown("#### País × Fuente de tráfico (Latam)")
            lat_pf = (df_lat.groupby(["pais_lat", "fuente"])
                       .size().reset_index(name="Leads"))
            fig_lat_pf = px.bar(
                lat_pf, x="Leads", y="pais_lat", color="fuente", orientation="h",
                title="Fuente de tráfico por país — Latam",
                barmode="stack",
                text="Leads",
            )
            fig_lat_pf.update_layout(
                yaxis={"categoryorder": "total ascending"},
                height=max(350, len(lat_paises) * 30),
                margin={"l": 0, "r": 20, "t": 40, "b": 20},
                legend={"title": "Fuente"},
            )
            fig_lat_pf.update_traces(textposition="inside", textfont_size=11)
            st.plotly_chart(fig_lat_pf, use_container_width=True)

            # País × Calidad — Latam
            st.markdown("#### Calidad de leads por país — Latam")
            CALIDAD_ORDER_M = ["Cierre Ganado", "En proceso", "Perdido", "No válido"]
            CALIDAD_COLORS_M = {
                "Cierre Ganado": BARCA["gold"],
                "En proceso":    BARCA["blue"],
                "Perdido":       BARCA["garnet"],
                "No válido":     BARCA["ink40"],
            }
            lat_cal = (df_lat.groupby(["pais_lat", "calidad"])
                        .size().reset_index(name="Leads"))
            lat_cal["calidad"] = pd.Categorical(lat_cal["calidad"],
                                                  categories=CALIDAD_ORDER_M, ordered=True)
            fig_lat_cal = px.bar(
                lat_cal.sort_values("calidad"),
                x="Leads", y="pais_lat", color="calidad", orientation="h",
                title="Calidad de leads por país — Latam",
                barmode="stack",
                color_discrete_map=CALIDAD_COLORS_M,
                text="Leads",
                category_orders={"calidad": CALIDAD_ORDER_M},
            )
            fig_lat_cal.update_layout(
                yaxis={"categoryorder": "total ascending"},
                height=max(350, len(lat_paises) * 30),
                margin={"l": 0, "r": 20, "t": 40, "b": 20},
                legend={"title": "Calidad"},
            )
            fig_lat_cal.update_traces(textposition="inside", textfont_size=11)
            st.plotly_chart(fig_lat_cal, use_container_width=True)

            # Tabla completa Latam
            with st.expander("📋 Tabla completa — Leads Latam por país"):
                lat_tabla = (df_lat.groupby("pais_lat")
                              .agg(
                                  Total=("email", "count"),
                                  Cierre_Ganado=("calidad", lambda x: (x == "Cierre Ganado").sum()),
                                  En_proceso=("calidad", lambda x: (x == "En proceso").sum()),
                                  Perdidos=("calidad", lambda x: (x == "Perdido").sum()),
                                  No_valido=("calidad", lambda x: (x == "No válido").sum()),
                              )
                              .reset_index()
                              .rename(columns={"pais_lat": "País",
                                               "Cierre_Ganado": "Cierre Ganado",
                                               "En_proceso": "En proceso",
                                               "No_valido": "No válido"})
                              .sort_values("Total", ascending=False))
                lat_tabla["Tasa CG %"] = (
                    lat_tabla["Cierre Ganado"] / lat_tabla["Total"] * 100
                ).round(1)
                # Columnas por modalidad
                for _mod in ["Presencial", "Online", "Sin modalidad"]:
                    lat_tabla[_mod] = (
                        df_lat[df_lat["modalidad"] == _mod]
                        .groupby("pais_lat").size()
                        .reindex(lat_tabla["País"]).fillna(0).astype(int).values
                    )
                st.dataframe(
                    lat_tabla.style
                        .background_gradient(subset=["Total"], cmap="Blues")
                        .format({"Tasa CG %": "{:.1f}%"}, na_rep="—"),
                    use_container_width=True,
                    hide_index=True,
                )

        # ── Programa por Mercado ────────────────────────────────────────────
        st.markdown("---")
        st.markdown("### 📚 Programas por mercado")
        df_prog_merc = df[df["programa"] != "Sin programa"]
        if not df_prog_merc.empty:
            top_n_merc = st.selectbox("Top N programas", [10, 15, 20], key="merc_topn")
            top_progs_m = (df_prog_merc["programa"].value_counts()
                           .head(top_n_merc).index.tolist())
            pm_grp = (df_prog_merc[df_prog_merc["programa"].isin(top_progs_m)]
                       .groupby(["programa", "mercado"])
                       .size().reset_index(name="Leads"))
            fig_pm = px.bar(
                pm_grp, x="Leads", y="programa", color="mercado", orientation="h",
                title=f"Leads por programa y mercado (Top {top_n_merc})",
                barmode="stack",
                color_discrete_map=MERCADO_COLORS,
                text="Leads",
                category_orders={"mercado": MERCADO_ORDER},
            )
            fig_pm.update_layout(
                yaxis={"categoryorder": "total ascending"},
                height=max(420, top_n_merc * 30),
                margin={"l": 0, "r": 20, "t": 40, "b": 20},
                legend={"title": "Mercado"},
            )
            fig_pm.update_traces(textposition="inside", textfont_size=11)
            st.plotly_chart(fig_pm, use_container_width=True)

            with st.expander("📋 Tabla pivote: Programa × Mercado"):
                pivot_pm = (pm_grp.pivot(index="programa", columns="mercado", values="Leads")
                             .fillna(0).astype(int))
                pivot_pm["Total"] = pivot_pm.sum(axis=1)
                # Añadir columnas de modalidad
                _df_pm_base = df_prog_merc[df_prog_merc["programa"].isin(top_progs_m)]
                for _mod in ["Presencial", "Online", "Sin modalidad"]:
                    pivot_pm[_mod] = (
                        _df_pm_base[_df_pm_base["modalidad"] == _mod]
                        .groupby("programa").size()
                        .reindex(pivot_pm.index).fillna(0).astype(int)
                    )
                pivot_pm = pivot_pm.sort_values("Total", ascending=False)
                st.dataframe(
                    pivot_pm.style.background_gradient(subset=["Total"], cmap="Reds"),
                    use_container_width=True,
                )

    # ── Footer ──────────────────────────────────────────────────────────────────
    st.markdown(
        f"<br><div style='text-align:center;color:{BARCA['ink40']};font-size:12px'>"
        f"{ACCOUNT_NAME} · Formularios HighTicket RST · Datos actualizados automáticamente cada 5 min</div>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
