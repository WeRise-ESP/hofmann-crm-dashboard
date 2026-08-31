"""
Dashboard RST — Hofmann
Análisis de calidad de leads por fuente, país y estado.
Fuente de datos: formularios FORM_HighTicket_CA / EN / ES.
"""
import streamlit as st
import streamlit.components.v1 as components
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from io import StringIO
import os
import time
import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import unquote_plus
import threading

load_dotenv()


# ── Persistencia de filtros en la URL (sobreviven a reconexiones/reinicios) ────
def _qp_load(key, kind):
    """Antes de crear el widget: vuelca el valor guardado en la URL a session_state."""
    if key in st.session_state:
        return
    raw = st.query_params.get(key)
    if raw is None:
        return
    try:
        if kind == "multi":
            st.session_state[key] = json.loads(raw)
        elif kind == "date":
            st.session_state[key] = date.fromisoformat(raw)
        else:
            st.session_state[key] = raw
    except Exception:
        pass


def _qp_save(key, value, kind):
    """Después del widget: guarda su valor actual en la URL."""
    try:
        if kind == "multi":
            st.query_params[key] = json.dumps(list(value))
        elif kind == "date":
            st.query_params[key] = value.isoformat()
        else:
            st.query_params[key] = str(value)
    except Exception:
        pass

# ── Autenticación por contraseña ──────────────────────────────────────────────
# La sesión se recuerda mediante un token en la URL (query param), que sobrevive
# a reconexiones y reinicios del contenedor de Streamlit Cloud. Así no vuelve a
# pedir contraseña cada pocos minutos (session_state se pierde en cada reconexión).
def _token_for(pwd: str) -> str:
    return hashlib.sha256(f"hofmann-crm-dashboard::{pwd}".encode()).hexdigest()[:32]


def _check_password():
    try:
        pwd_correcta = st.secrets["APP_PASSWORD"]
    except Exception:
        pwd_correcta = os.getenv("APP_PASSWORD", "")
    token_ok = _token_for(pwd_correcta) if pwd_correcta else ""

    # Ya autenticado en esta sesión
    if st.session_state.get("autenticado"):
        return True
    # Token válido en la URL → recuperar sesión sin pedir contraseña
    if token_ok and st.query_params.get("k") == token_ok:
        st.session_state["autenticado"] = True
        return True

    st.markdown("""
    <style>
    input[type="password"] {
        background:#ffffff !important; color:#111111 !important;
        border:2px solid #d0d5dd !important; border-radius:8px !important;
    }
    input[type="password"]::placeholder { color:#9aa5b4 !important; opacity:1 !important; }
    [data-baseweb="input"] { background:#ffffff !important; border-radius:8px !important; }
    [data-baseweb="input"] button svg { fill:#555 !important; }
    </style>
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
                # Persistir el acceso en la URL para que dure la sesión
                if token_ok:
                    st.query_params["k"] = token_ok
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


# ── Helper: leer secrets con fallback a .env ──────────────────────────────────
def _s(key, default=""):
    try:
        return st.secrets[key]
    except Exception:
        return os.getenv(key, default)


# ── Credenciales Google Ads (opcionales) ──────────────────────────────────────
GA_DEVELOPER_TOKEN = _s("GOOGLE_ADS_DEVELOPER_TOKEN")
GA_CLIENT_ID       = _s("GOOGLE_ADS_CLIENT_ID")
GA_CLIENT_SECRET   = _s("GOOGLE_ADS_CLIENT_SECRET")
GA_REFRESH_TOKEN   = _s("GOOGLE_ADS_REFRESH_TOKEN")
GA_LOGIN_CID       = _s("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "4885772142")
GA_CUSTOMER_ID     = _s("GOOGLE_ADS_CUSTOMER_ID", "9010916591")
GA_AVAILABLE = bool(GA_DEVELOPER_TOKEN and GA_CLIENT_ID and GA_CLIENT_SECRET and GA_REFRESH_TOKEN)

# ── Credenciales Meta Ads (opcionales) ────────────────────────────────────────
META_TOKEN      = _s("META_ACCESS_TOKEN")
META_ACCOUNT_ID = _s("META_AD_ACCOUNT_ID", "2649358358505616")
META_AVAILABLE  = bool(META_TOKEN and META_ACCOUNT_ID)

# ── Credenciales LinkedIn Ads vía Google Sheets (opcional) ────────────────────
LINKEDIN_SHEET_URL = _s("LINKEDIN_SHEET_URL")
LINKEDIN_AVAILABLE = bool(LINKEDIN_SHEET_URL)

# ── Credenciales TikTok Ads (opcional) ────────────────────────────────────────
TIKTOK_TOKEN         = _s("TIKTOK_ACCESS_TOKEN")
TIKTOK_ADVERTISER_ID = _s("TIKTOK_ADVERTISER_ID")
TIKTOK_AVAILABLE     = bool(TIKTOK_TOKEN and TIKTOK_ADVERTISER_ID)

# ── Paleta Hofmann ────────────────────────────────────────────────────────────
# Colores de marca Hofmann — tomados del logo y de hofmann-bcn.com
#   azul  #0D0E95  ·  oro  #EAAB12  ·  amarillo  #FFE900
HOFMANN = {
    "blue":         "#2A2BC4",   # azul medio (gráficos)
    "blue_deep":    "#1414A8",   # azul oscuro
    "blue_ink":     "#0D0E95",   # AZUL DE MARCA (sidebar, headers)
    "garnet":       "#D95F02",   # naranja-rojo (negativos / pérdidas)
    "garnet_deep":  "#A63D00",   # naranja-rojo oscuro
    "gold":         "#EAAB12",   # oro brand (ganancias, botones)
    "yellow":       "#FFE900",   # amarillo brand
    "white":        "#FFFFFF",
    "paper":        "#F8F9FE",   # fondo casi blanco
    "bone":         "#E9E9FB",   # azul claro brand
    "line":         "#DDDDF7",   # borde claro
    "line2":        "#C9C9F0",   # borde medio
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

# ── Logo de marca (se empotra en base64 para que no dependa de la red) ────────
LOGO_URL = ("https://www.hofmann-bcn.com/wp-content/uploads/2026/02/"
            "logo-hofmann-292x148-1WEBP2.webp")


@st.cache_data(ttl=86_400, max_entries=6, show_spinner=False)
def _logo_src() -> str:
    """data-URI del logo local; si no está, cae al URL remoto."""
    import base64
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "logo-hofmann.webp")
    try:
        with open(ruta, "rb") as fh:
            return "data:image/webp;base64," + base64.b64encode(fh.read()).decode()
    except Exception:
        return LOGO_URL


st.set_page_config(
    page_title=f"Dashboard · {ACCOUNT_NAME}",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(f"""
<style>
  /* ── Fondo app ── */
  [data-testid="stAppViewContainer"] {{ background:{BARCA['paper']}; }}

  /* ── Sidebar: fondo navy ── */
  [data-testid="stSidebar"] {{ background:{BARCA['blue_ink']} !important; }}

  /* Textos genéricos en sidebar */
  [data-testid="stSidebar"] label,
  [data-testid="stSidebar"] p,
  [data-testid="stSidebar"] span,
  [data-testid="stSidebar"] small,
  [data-testid="stSidebar"] .stMarkdown {{ color:{BARCA['white']} !important; }}

  /* ── Sidebar: inputs de texto y contraseña ── */
  [data-testid="stSidebar"] input,
  [data-testid="stSidebar"] textarea {{
      background:#ffffff !important;
      color:#111111 !important;
      border:1.5px solid {BARCA['line2']} !important;
      border-radius:6px !important;
  }}
  [data-testid="stSidebar"] input::placeholder,
  [data-testid="stSidebar"] textarea::placeholder {{
      color:#888888 !important;
      opacity:1 !important;
  }}

  /* ── Sidebar: date_input ── */
  [data-testid="stSidebar"] [data-baseweb="input"] {{
      background:#ffffff !important;
      border:1.5px solid {BARCA['line2']} !important;
      border-radius:6px !important;
  }}
  [data-testid="stSidebar"] [data-baseweb="input"] input {{
      color:#111111 !important;
      background:transparent !important;
  }}

  /* ── Sidebar: selectbox (select/dropdown) ── */
  [data-testid="stSidebar"] [data-baseweb="select"] > div:first-child {{
      background:#ffffff !important;
      border:1.5px solid {BARCA['line2']} !important;
      border-radius:6px !important;
  }}
  [data-testid="stSidebar"] [data-baseweb="select"] [data-testid="stSelectboxLabel"],
  [data-testid="stSidebar"] [data-baseweb="select"] span,
  [data-testid="stSidebar"] [data-baseweb="select"] div[aria-selected] {{
      color:#111111 !important;
  }}
  /* Flecha del select */
  [data-testid="stSidebar"] [data-baseweb="select"] svg {{ fill:#555555 !important; }}

  /* ── Sidebar: multiselect — caja contenedora ── */
  [data-testid="stSidebar"] [data-baseweb="base-input"] {{
      background:#ffffff !important;
      border-radius:6px !important;
  }}
  [data-testid="stSidebar"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div {{
      background:#ffffff !important;
      border:1.5px solid {BARCA['line2']} !important;
      border-radius:6px !important;
  }}
  /* Texto de placeholder del multiselect */
  [data-testid="stSidebar"] [data-baseweb="select"] input {{
      color:#111111 !important;
  }}

  /* ── Sidebar: chips/tags del multiselect ── */
  [data-testid="stSidebar"] [data-baseweb="tag"] {{
      background:{BARCA['garnet']} !important;
      color:#ffffff !important;
      border-radius:4px !important;
  }}
  [data-testid="stSidebar"] [data-baseweb="tag"] span {{
      color:#ffffff !important;
  }}
  [data-testid="stSidebar"] [data-baseweb="tag"] svg {{
      fill:#ffffff !important;
  }}

  /* ── Sidebar: radio buttons — texto blanco y marcador visible ── */
  [data-testid="stSidebar"] [data-testid="stRadio"] label {{
      color:{BARCA['white']} !important;
  }}
  /* El círculo del radio: el color primario coincide con el fondo del sidebar,
     así que se fuerza contorno claro y relleno dorado al seleccionar. */
  [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] > label
      > div:first-child {{
      background-color:transparent !important;
      border-color:rgba(255,255,255,.65) !important;
  }}
  [data-testid="stSidebar"] [data-testid="stRadio"]
      [role="radiogroup"] > label[data-checked="true"] > div:first-child,
  [data-testid="stSidebar"] [data-testid="stRadio"]
      [role="radiogroup"] > label:has(input:checked) > div:first-child {{
      background-color:{BARCA['gold']} !important;
      border-color:{BARCA['gold']} !important;
  }}
  /* Punto interior del radio marcado */
  [data-testid="stSidebar"] [data-testid="stRadio"]
      [role="radiogroup"] > label:has(input:checked) > div:first-child > div {{
      background-color:{BARCA['blue_ink']} !important;
  }}

  /* ── Sidebar: checkbox ── */
  [data-testid="stSidebar"] [data-testid="stCheckbox"] label {{
      color:{BARCA['white']} !important;
  }}

  /* ── GLOBAL: todos los selects/inputs del área principal ── */
  /* Selectbox y multiselect — caja exterior */
  [data-baseweb="select"] > div:first-child {{
      background:#ffffff !important;
      border:1.5px solid {BARCA['line2']} !important;
      border-radius:6px !important;
  }}
  /* Texto seleccionado dentro del select */
  [data-baseweb="select"] span,
  [data-baseweb="select"] div[aria-selected],
  [data-baseweb="select"] input {{
      color:#111111 !important;
  }}
  /* Flecha del select */
  [data-baseweb="select"] svg {{ fill:#555555 !important; }}
  /* Multiselect contenedor */
  [data-baseweb="base-input"] {{
      background:#ffffff !important;
  }}
  /* Chips/tags del multiselect en el área principal */
  [data-baseweb="tag"] {{
      background:{BARCA['blue']} !important;
      color:#ffffff !important;
      border-radius:4px !important;
  }}
  [data-baseweb="tag"] span {{ color:#ffffff !important; }}
  [data-baseweb="tag"] svg  {{ fill:#ffffff !important; }}

  /* ── Login / inputs globales ── */
  input[type="password"] {{
      background:#ffffff !important;
      color:#111111 !important;
      border:2px solid #d0d5dd !important;
      border-radius:8px !important;
  }}
  input[type="password"]::placeholder {{
      color:#9aa5b4 !important;
      opacity:1 !important;
  }}
  [data-baseweb="input"] {{
      background:#ffffff !important;
      border-radius:8px !important;
  }}
  [data-baseweb="input"] input {{ color:#111111 !important; }}
  [data-baseweb="input"] button svg {{ fill:#555 !important; }}

  /* ── Botones ── */
  .stButton>button {{
      background:{BARCA['garnet']} !important;
      color:{BARCA['white']} !important;
      border:none !important; font-weight:700;
  }}
  .stButton>button:hover {{ background:{BARCA['garnet_deep']} !important; }}

  /* ── Botones de filtrado (segmented_control) — píldoras de marca ── */
  [data-testid="stButtonGroup"] {{ gap:8px !important; }}
  [data-testid="stButtonGroup"] button {{
      border-radius:999px !important;
      border:1px solid {BARCA['line2']} !important;
      background:#ffffff !important;
      color:{BARCA['ink80']} !important;
      font-weight:600 !important;
      font-size:13.5px !important;
      padding:7px 18px !important;
      box-shadow:none !important;
      transition:none !important;
  }}
  [data-testid="stButtonGroup"] button p {{
      color:{BARCA['ink80']} !important; font-weight:600 !important;
  }}
  [data-testid="stButtonGroup"] button:hover {{
      border-color:{BARCA['blue']} !important;
  }}
  /* Opción activa → azul de marca */
  [data-testid="stButtonGroup"] button[aria-checked="true"],
  [data-testid="stButtonGroup"] button[kind="segmented_controlActive"],
  [data-testid="stBaseButton-segmented_controlActive"] {{
      background:{BARCA['blue_ink']} !important;
      border-color:{BARCA['blue_ink']} !important;
      color:#ffffff !important;
  }}
  [data-testid="stButtonGroup"] button[aria-checked="true"] p,
  [data-testid="stButtonGroup"] button[kind="segmented_controlActive"] p,
  [data-testid="stBaseButton-segmented_controlActive"] p {{
      color:#ffffff !important;
  }}

  /* ── Calendar popup (date_input) — portal renderizado en el body ── */
  [data-baseweb="calendar"] {{
      background:#ffffff !important;
      border:1px solid {BARCA['line2']} !important;
      border-radius:8px !important;
  }}
  [data-baseweb="calendar"] * {{
      color:#111111 !important;
  }}
  /* Cabecera del mes/año */
  [data-baseweb="calendar"] [data-baseweb="select"] > div:first-child {{
      background:#f0f4f8 !important;
      color:#111111 !important;
  }}
  /* Días de la semana (Mo Tu We…) */
  [data-baseweb="calendar"] [role="columnheader"] {{
      color:{BARCA['ink60']} !important;
      font-weight:600 !important;
  }}
  /* Celdas de día */
  [data-baseweb="calendar"] [role="gridcell"] button {{
      color:#111111 !important;
      background:transparent !important;
  }}
  /* Día seleccionado */
  [data-baseweb="calendar"] [aria-selected="true"] button,
  [data-baseweb="calendar"] button[aria-selected="true"] {{
      background:{BARCA['blue']} !important;
      color:#ffffff !important;
      border-radius:50% !important;
  }}
  /* Hover sobre día */
  [data-baseweb="calendar"] [role="gridcell"] button:hover {{
      background:{BARCA['bone']} !important;
      color:{BARCA['blue_ink']} !important;
  }}
  /* Navegación prev/next */
  [data-baseweb="calendar"] button[aria-label*="previous"],
  [data-baseweb="calendar"] button[aria-label*="next"],
  [data-baseweb="calendar"] button[aria-label*="anterior"],
  [data-baseweb="calendar"] button[aria-label*="siguiente"] {{
      color:{BARCA['blue']} !important;
      background:transparent !important;
  }}
  /* Popover contenedor */
  [data-baseweb="popover"] {{
      background:#ffffff !important;
  }}

  /* ── Títulos ── */
  h1,h2,h3 {{ color:{BARCA['blue_ink']}; }}
  hr {{ border-color:{BARCA['line']}; }}
</style>
""", unsafe_allow_html=True)

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
BASE = "https://api.hubapi.com"


# ── Regulador de caudal hacia HubSpot ─────────────────────────────────────────
# Las descargas se lanzan en paralelo y HubSpot tiene un límite por segundo.
# Sin regular, una de cada cinco peticiones volvía con 429 y el reintento dormía
# ~40 s por carga: salía más lento que ir en serie. El semáforo limita cuántas
# viajan a la vez y el intervalo mínimo reparte el resto en el tiempo.
# HubSpot limita /search a 4 peticiones por segundo, bastante menos que el resto
# de endpoints, así que cada familia lleva su propio ritmo.
_HS_SEMAFORO = threading.Semaphore(6)
_HS_CANDADO  = threading.Lock()
_HS_ULTIMA   = {"search": 0.0, "otros": 0.0}
_HS_RITMO    = {"search": 0.27, "otros": 0.06}


def _hs_pedir(metodo, url, **kw):
    """Llamada a HubSpot pasando por el regulador."""
    _tipo = "search" if "/search" in url else "otros"
    with _HS_SEMAFORO:
        with _HS_CANDADO:
            _espera = _HS_RITMO[_tipo] - (time.monotonic() - _HS_ULTIMA[_tipo])
            if _espera > 0:
                time.sleep(_espera)
            _HS_ULTIMA[_tipo] = time.monotonic()
        kw.setdefault("headers", HEADERS)
        kw.setdefault("timeout", 30)
        return getattr(requests, metodo)(url, **kw)


def _hs_post(url, **kw):
    return _hs_pedir("post", url, **kw)


def _hs_get(url, **kw):
    return _hs_pedir("get", url, **kw)


def _hs_search(path, payload, max_retries=5):
    """POST a HubSpot search con reintentos automáticos en 429 (rate limit)."""
    for attempt in range(max_retries):
        try:
            r = _hs_post(f"{BASE}{path}", json=payload)
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

# ── Nomenclatura de campañas de Ads ───────────────────────────────────────────
# Mercado   : "- ES" / "- CAT" / "- NAC"        → Nacional
#             "- LATAM" / "- LAT" / "LATAM 2"   → Latam
# Modalidad : contiene "Online"                 → Online
#             cualquier otra                    → Presencial
# Los delimitadores evitan falsos positivos ("Maestrías" no es ES, "Coctelería"
# no es CAT). Funciona con los nombres del panel de Google Ads
# ("PMAX - Diploma de Cocina - ES") y con los de las UTM ("pmax_nac_diploma_cocina").
_RE_CAMP_LATAM = re.compile(r"(?<![A-Z])(LATAM|LAT)(?![A-Z])")
_RE_CAMP_NAC   = re.compile(r"(?<![A-Z])(NAC|NACIONAL|CAT|ES|SPAIN)(?![A-Z])")
_RE_CAMP_INT   = re.compile(r"(?<![A-Z])(INT|INTL|ROW|WW|GLOBAL)(?![A-Z])")

# Tokens que no identifican el producto: plataforma, mercado y palabras vacías
_STOP_CAMP = {
    "PMAX", "SEARCH", "SRCH", "S", "DISPLAY", "YOUTUBE", "YT", "PERFORMANCE",
    "MAX", "AUTO", "TAGGED", "PPC",
    "LATAM", "LAT", "NAC", "NACIONAL", "CAT", "ES", "SPAIN", "INT", "INTL", "ROW", "WW",
    "DE", "DEL", "LA", "EL", "LOS", "LAS", "Y", "EN", "A",
}


def _sin_acentos(txt: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", txt or "")
                   if unicodedata.category(c) != "Mn")


# Campañas cuyo nombre no lleva token de mercado y se clasifican a mano.
# Clave = fragmento del nombre (sin acentos, mayúsculas) · valor = mercado real.
_OVERRIDE_MERCADO = {
    # Curso de verano en Barcelona impartido en inglés: es nacional aunque el
    # nombre no lleve token y su UTM venga con prefijo "int_".
    "COCINA_VERANO_INGLES": "Nacional",
    "CULINARY_SUMMER":      "Nacional",
}


def clasificar_mercado_camp(nombre: str) -> str:
    """Nacional / Latam / ROW / '—' a partir del nombre de la campaña."""
    u = _sin_acentos(nombre).upper()
    for _frag, _merc in _OVERRIDE_MERCADO.items():
        if _frag in u.replace(" ", "_"):
            return _merc
    if _RE_CAMP_LATAM.search(u):
        return "Latam"
    if _RE_CAMP_NAC.search(u):
        return "Nacional"
    if _RE_CAMP_INT.search(u):        # INT_ / ROW_ / WW_ → resto del mundo
        return "ROW"
    return "—"


_RE_CAMP_WEBINAR = re.compile(r"WEBINAR|OPEN\s*DAY|OPENDAY|PUERTAS\s*ABIERTAS|"
                              r"SESION\s*INFORMATIVA")
# Campañas que por diseño no captan leads de formulario: tráfico, notoriedad,
# vídeo. Nunca tendrán un utm_campaign en el CRM contra el que casar el gasto.
_RE_CAMP_SIN_LEADS = re.compile(r"DEMAND\s*GEN|DEMANDGEN|TRAFICO|TRAFFIC|AWARENESS|"
                                r"NOTORIEDAD|BRANDING|ALCANCE|COBERTURA|"
                                r"VISUALIZACIONES|VIDEO\s*VIEWS")


def es_campana_webinar(nombre: str) -> bool:
    """Campañas de captación a webinar / open day."""
    return bool(_RE_CAMP_WEBINAR.search(_sin_acentos(nombre).upper()))


def es_campana_no_captacion(nombre: str) -> bool:
    """Campañas cuyo gasto no debe entrar en el análisis de captación RST.

    Dos familias: las de webinar / open day, cuyos leads tampoco entran, y las de
    tráfico o notoriedad, que no generan leads de formulario. Dejar su gasto
    dentro infla el CPL y hunde el ROI de las que sí captan.
    """
    u = _sin_acentos(nombre).upper()
    return bool(_RE_CAMP_WEBINAR.search(u) or _RE_CAMP_SIN_LEADS.search(u))


def clasificar_modalidad_camp(nombre: str) -> str:
    """Online si el nombre contiene 'Online'; el resto es Presencial."""
    if not (nombre or "").strip():
        return "Sin asignar"
    return "Online" if "ONLINE" in _sin_acentos(nombre).upper() else "Presencial"


def clave_campana(nombre: str) -> str:
    """Clave canónica para casar el nombre de Google Ads con el de la UTM.

    "PMAX - Diploma de Cocina - ES"  y  "pmax_nac_diploma_cocina"
    producen la misma clave: "Nacional|COCINA-DIPLOMA".
    """
    u = _sin_acentos(nombre).upper()
    toks = {t for t in re.split(r"[^A-Z0-9]+", u) if t and t not in _STOP_CAMP}
    if not toks:
        return ""
    return clasificar_mercado_camp(nombre) + "|" + "-".join(sorted(toks))


# ── Emparejador de campañas Ads ↔ UTM de HubSpot ──────────────────────────────
# El nombre en el panel de anuncios y el utm_campaign que llega a HubSpot casi
# nunca son idénticos:
#   Google  "PMAX - Diploma de Cocina - ES"        ↔  "pmax_nac_diploma_cocina"
#   Meta    "LAT_Maestría_Online_Gestión_Vinícola" ↔  "lat_maestria_online_vinos_video"
# Se comparan por tokens, ignorando plataforma, mercado y sufijos de variante
# creativa (_video, _sinconv, _v2…), y exigiendo que el mejor candidato gane al
# segundo por un margen. Así una campaña ambigua se queda sin emparejar en vez
# de atribuirse mal: es preferible avisar del gasto suelto que repartirlo torcido.
# Umbral y margen calibrados contra las 38 campañas de Meta y 21 de Google reales.
_UMBRAL_MATCH = 0.60
_MARGEN_MATCH = 0.05

# Ruido: plataforma, mercado, relleno y sufijos de variante creativa
_STOP_MATCH = _STOP_CAMP | {
    "LEAD", "LEADS", "VIDEO", "IMAGEN", "CONVERS", "CONVERSION", "CONV", "SINCONV", "SINCONVO",
    "NOCONV", "INTERESES", "INTERES", "PROFESIONAL", "PROFESIONALES",
    "ESPECIALIZACION", "V2", "V3", "V4", "V5", "V6", "CAMPAIGN", "NAME",
    "TRAFICO", "DEMANDGEN", "SRCH",
}

# Variantes que designan el mismo producto
_SINON_MATCH = {
    "VINOS": "VINO", "VINO": "VINO", "VINICOLA": "VINO", "VINICOLAS": "VINO",
    "MAESTRIA": "MASTER", "MAESTRIAS": "MASTER", "MASTER": "MASTER",
    "MASTERS": "MASTER",
    "CULINARY": "COCINA", "COCINA": "COCINA", "COCINAS": "COCINA",
    "SUMMER": "VERANO", "VERANO": "VERANO",
    "COURSE": "CURSO", "CURSO": "CURSO", "CURSOS": "CURSO",
    "ENGLISH": "INGLES", "INGLES": "INGLES",
    # El máster se renombró de Comunicación a Marketing: mismo programa
    "COMUNICACION": "MARKETING", "MARKETING": "MARKETING", "GROWTH": "MARKETING",
}


def _toks_match(nombre: str) -> list:
    """Tokens significativos, sin repetir.

    La deduplicación importa: "Cursos de Pastelería › Pastelería Avanzada" repite
    PASTELERIA y, contándola dos veces, inflaría el denominador del solape.
    """
    u = _sin_acentos(nombre).upper()
    out = []
    for t in re.split(r"[^A-Z0-9]+", u):
        if not t:
            continue
        t = re.sub(r"V\d+$", "", t)              # experiencev2 → experience
        if t and t not in _STOP_MATCH and len(t) > 1:
            t = _SINON_MATCH.get(t, t)
            if t not in out:
                out.append(t)
    return out


def _tok_equiv(a: str, b: str) -> bool:
    """Dos tokens designan lo mismo si comparten raíz larga o se parecen mucho."""
    if a == b:
        return True
    n = min(len(a), len(b))
    if n >= 4 and (a.startswith(b) or b.startswith(a)):
        return True                              # REST / RESTAURANTES
    if n >= 5 and a[:5] == b[:5]:                # DIRECCION / DIRECCIONES
        return True
    if n >= 4 and a[:4] == b[:4] and SequenceMatcher(None, a, b).ratio() >= .6:
        return True
    return SequenceMatcher(None, a, b).ratio() >= .85


def _score_match(a: str, b: str) -> float:
    ta, tb = _toks_match(a), _toks_match(b)
    if not ta or not tb:
        return 0.0
    usados, comunes = set(), 0
    for x in ta:
        for j, y in enumerate(tb):
            if j not in usados and _tok_equiv(x, y):
                usados.add(j)
                comunes += 1
                break
    return comunes / max(len(ta), len(tb))


def _modalidad_explicita(nombre: str):
    """Online / Presencial solo si el nombre lo dice; None si no se pronuncia.

    Para clasificar el gasto vale la regla "lo que no diga Online es Presencial",
    pero para emparejar no: muchas UTM omiten la modalidad ("direc_rest_convers_
    latam" es de una campaña Online) y bloquearlas perdería la pareja.
    """
    u = _sin_acentos(nombre).upper()
    if "ONLINE" in u:
        return "Online"
    if "PRESENCIAL" in u:
        return "Presencial"
    return None


# UTM rotas cuya campaña ha confirmado el equipo. Se usan solo si esa campaña
# sigue existiendo en la plataforma, así que dejan de aplicarse solas cuando la
# campaña deja de emitir. Caso de julio 2026: el macro de TikTok no se sustituía
# y varios contactos entraron con el texto literal "__campaign_name__"; la URL ya
# está corregida, pero HubSpot no reescribe el origen de los contactos creados
# antes del arreglo.
_ALIAS_UTM = {
    ("TikTok Ads", "__campaign_name__"): "NAC_Presencial_Cursos_Cocina",
}


def emparejar_campana(nombre_hs: str, candidatos) -> str:
    """Campaña de Ads que corresponde a un utm_campaign, o "" si es ambigua."""
    if not nombre_hs or nombre_hs == "Sin campaña":
        return ""
    m  = clasificar_mercado_camp(nombre_hs)
    md = _modalidad_explicita(nombre_hs)
    puntuadas = []
    for c in candidatos:
        mc = clasificar_mercado_camp(c)
        if mc != "—" and m != "—" and mc != m:       # mercados incompatibles
            continue
        # Si ambos declaran modalidad y difieren, no son la misma campaña
        mdc = _modalidad_explicita(c)
        if md and mdc and md != mdc:
            continue
        puntuadas.append((_score_match(nombre_hs, c), c))
    if not puntuadas:
        return ""
    puntuadas.sort(key=lambda t: (-t[0], t[1]))
    mejor, cand = puntuadas[0]
    segunda = puntuadas[1][0] if len(puntuadas) > 1 else 0.0
    if mejor >= _UMBRAL_MATCH and (mejor - segunda) >= _MARGEN_MATCH:
        return cand
    return ""

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
    "pais", "pais_formulario", "nacionalidad",
    "pais_de_la_ip_capabilia",
    "hs_lead_status", "lead_valido", "num_contacted_notes",
    "motivos_de_cierre_perdido_rst",
    "hs_analytics_source", "hs_analytics_source_data_1", "hs_analytics_source_data_2",
    "hs_latest_source", "hs_latest_source_data_1", "hs_latest_source_data_2",
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

# Campos que rellena la persona (formulario, ficha) frente a los inferidos por IP.
_PAIS_DECLARADO = ["pais_de_residencia", "pais", "pais_formulario",
                   "country", "billing_country"]
_PAIS_CADENA = ["pais_de_residencia", "ip_country", "pais_de_la_ip_capabilia",
                "country", "billing_country", "pais", "pais_formulario",
                "nacionalidad"]


def resolve_pais(cp):
    """País del contacto.

    Si lo declarado por la persona es España o un país de Latam, manda sobre el
    IP: la geolocalización se falsea con VPN y buena parte del tráfico
    latinoamericano sale enrutado por Estados Unidos. Medido en julio 2026, esto
    recupera 28 de los 48 leads que caían en ROW y 3 de sus 4 cierres ganados.

    Fuera de esos dos mercados no se fuerza nada y se sigue la cadena habitual,
    para no sacar a nadie de un mercado conocido por un dato suelto.
    """
    for f in _PAIS_DECLARADO:
        v = (cp.get(f) or "").strip()
        if v and resolve_mercado(v.title()) in ("España", "Latam"):
            return v.title()
    for f in _PAIS_CADENA:
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


def resolve_campana(source: str, d1: str, d2: str) -> str:
    """Nombre real de la campaña. HubSpot invierte los campos según la fuente:

      PAID_SEARCH (Google)          data_1 = campaña   · data_2 = tipo (pmax/keyword)
      PAID_SOCIAL (Meta/LI/TikTok)  data_1 = red social · data_2 = campaña (utm_campaign)

    Por eso no vale leer siempre data_1: en Meta devolvería "Facebook" para todo.
    """
    s  = (source or "").upper().strip()
    d1 = (d1 or "").strip()
    d2 = (d2 or "").strip()
    if s == "PAID_SOCIAL":
        return d2 or d1 or "Sin campaña"
    return d1 or d2 or "Sin campaña"


def resolve_tipo_medio(source: str, d1: str, d2: str) -> str:
    """El campo complementario a la campaña: red social en Meta, medio en Google."""
    s  = (source or "").upper().strip()
    d1 = (d1 or "").strip()
    d2 = (d2 or "").strip()
    return (d1 if s == "PAID_SOCIAL" else d2) or "—"


def resolve_campana_cp(cp: dict, reciente: bool = False) -> str:
    """resolve_campana a partir del dict de propiedades del contacto."""
    if reciente:
        return resolve_campana(cp.get("hs_latest_source"),
                               cp.get("hs_latest_source_data_1"),
                               cp.get("hs_latest_source_data_2"))
    return resolve_campana(cp.get("hs_analytics_source"),
                           cp.get("hs_analytics_source_data_1"),
                           cp.get("hs_analytics_source_data_2"))


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


@st.cache_data(ttl=86_400, max_entries=6, show_spinner=False)
def fetch_usuarios() -> dict:
    """{id de usuario: nombre}. Sirve para etiquetar las altas manuales."""
    try:
        r = _hs_get(f"{BASE}/settings/v3/users", params={"limit": 100}, timeout=20)
        if r.status_code != 200:
            return {}
        return {str(u["id"]): (f"{u.get('firstName','')} {u.get('lastName','')}".strip()
                               or u.get("email", ""))
                for u in r.json().get("results", [])}
    except Exception:
        return {}


def etiqueta_campana(campana: str, data2: str, usuarios: dict) -> str:
    """Convierte los códigos internos de HubSpot en algo legible.

    CRM_UI no es una campaña: es un contacto dado de alta a mano en HubSpot, y
    data_2 trae el id de quien lo creó. "Unknown keywords (SSL)" tampoco: es una
    visita orgánica en la que el buscador no comparte el término por HTTPS.
    """
    c = (campana or "").strip()
    d2 = (data2 or "").strip()
    if c.upper() == "CRM_UI":
        uid = d2.split("userId:")[-1] if "userId:" in d2 else ""
        quien = usuarios.get(uid, "")
        return f"Alta manual · {quien}" if quien else "Alta manual en el CRM"
    if c.lower().startswith("unknown keywords"):
        buscador = d2.strip().title() if d2 else "buscador"
        return f"Orgánica sin término · {buscador}"
    return c


# ── Fetching de contactos (con caché) ─────────────────────────────────────────

@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def fetch_data(fecha_inicio: str, fecha_fin: str) -> pd.DataFrame:
    test = _hs_get(f"{BASE}/crm/v3/objects/contacts?limit=1", headers=HEADERS)
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

    _usuarios = fetch_usuarios()
    rows = []
    after = None
    while True:
        payload = {
            "filterGroups": [{"filters": filters}],
            "properties": CONTACT_PROPS + ["createdate"],
            "limit": 200,
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
                "fuente_reciente":    (cp.get("hs_latest_source") or "").strip(),
                "fuente_reciente_d1": (cp.get("hs_latest_source_data_1") or "").strip(),
                "fuente_reciente_d2": (cp.get("hs_latest_source_data_2") or "").strip(),
                "fuente_original":    (cp.get("hs_analytics_source") or "").strip(),
                "fuente_original_d1": (cp.get("hs_analytics_source_data_1") or "").strip(),
                "fuente_original_d2": (cp.get("hs_analytics_source_data_2") or "").strip(),
                # Campaña real, resolviendo la inversión data_1/data_2 según la fuente
                "campana":          etiqueta_campana(
                                        resolve_campana_cp(cp),
                                        cp.get("hs_analytics_source_data_2"), _usuarios),
                "campana_reciente": etiqueta_campana(
                                        resolve_campana_cp(cp, reciente=True),
                                        cp.get("hs_latest_source_data_2"), _usuarios),
                "tipo_medio":       resolve_tipo_medio(
                                        cp.get("hs_latest_source"),
                                        cp.get("hs_latest_source_data_1"),
                                        cp.get("hs_latest_source_data_2")),
                # Red concreta del social pagado, para no confundir plataformas
                "red_social":       (resolve_tipo_medio(
                                        cp.get("hs_analytics_source"),
                                        cp.get("hs_analytics_source_data_1"),
                                        cp.get("hs_analytics_source_data_2"))
                                     if (cp.get("hs_analytics_source") or "").upper()
                                        == "PAID_SOCIAL"
                                     else resolve_tipo_medio(
                                        cp.get("hs_latest_source"),
                                        cp.get("hs_latest_source_data_1"),
                                        cp.get("hs_latest_source_data_2"))),
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
             "intentos", "motivo_cierre", "fuente", "origen_fuente",
             "fuente_reciente", "fuente_reciente_d1", "fuente_reciente_d2",
             "fuente_original", "fuente_original_d1", "fuente_original_d2",
             "campana", "campana_reciente", "tipo_medio", "red_social",
             "modalidad", "programa", "mercado", "categoria"]
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


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
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
            "limit": 200,
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


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
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
                "limit": 200,
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
    _usuarios = fetch_usuarios()
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
                          "pais", "pais_formulario", "nacionalidad",
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


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def fetch_pipeline(fecha_inicio: str, fecha_fin: str) -> pd.DataFrame:
    """Deals del pipeline vivos en algún momento del período indicado.

    El filtro se hace en la API, no en pandas. Pedir "creados antes del fin del
    período" traía los 28.000 deals históricos del pipeline, y como la búsqueda
    de HubSpot corta en 10.000 registros ordenados por fecha ascendente, lo que
    llegaba eran los más ANTIGUOS (hasta abril de 2024) y se perdía todo lo
    reciente. Con los dos grupos de abajo —cerrados a partir del inicio, o aún
    abiertos— bajan a menos de 2.000 y dejan de truncarse.
    """
    _base = [{"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_ID}]
    if fecha_inicio != "todos":
        fi_ts = int(datetime.fromisoformat(fecha_inicio)
                    .replace(tzinfo=timezone.utc).timestamp() * 1000)
        ff_ts = (int(datetime.fromisoformat(fecha_fin)
                     .replace(tzinfo=timezone.utc).timestamp() * 1000)
                 + 86_400_000 - 1)
        _creado = [{"propertyName": "createdate", "operator": "LTE", "value": str(ff_ts)}]
    else:
        # "Todos" arranca en 2024, que es cuando empieza el pipeline
        fi_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        _creado = []
    # Los filterGroups se combinan con OR: cerrados en/después del inicio, o abiertos
    filter_groups = [
        {"filters": _base + _creado + [{"propertyName": "closedate",
                                        "operator": "GTE", "value": str(fi_ts)}]},
        {"filters": _base + _creado + [{"propertyName": "closedate",
                                        "operator": "NOT_HAS_PROPERTY"}]},
    ]

    rows = []
    after = None
    while True:
        payload = {
            "filterGroups": filter_groups,
            # Más recientes primero: si alguna vez se rozara el tope de la API,
            # se perdería lo antiguo y no lo del período que se está mirando.
            "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
            "properties": ["dealname", "dealstage", "amount", "closedate",
                           "createdate", "motivo_de_cierre_del_negocio", "modalidad"],
            "limit": 200,
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


# ── Deals del período enriquecidos con datos de contacto ──────────────────────
# Criterio de negocio (igual que el informe de referencia):
#   · Ganados  → deals que ENTRARON en Cierre Ganado dentro del período
#                (hs_v2_date_entered_closedwon), incluyan leads de meses anteriores
#   · Perdidos → deals que ENTRARON en Cierre Perdido dentro del período
#   · Embudo   → deals cuyo CONTACTO se creó en el período (cohorte del período)
PROP_ENTRO_GANADO  = "hs_v2_date_entered_closedwon"
PROP_ENTRO_PERDIDO = "hs_v2_date_entered_closedlost"


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def fetch_pipeline_full(fecha_inicio: str, fecha_fin: str) -> pd.DataFrame:
    """Deals del período con fuente/campaña/país/programa del contacto.

    Devuelve un flag por fila:
      gano_periodo   — entró en Cierre Ganado dentro del período
      perdio_periodo — entró en Cierre Perdido dentro del período
      cohorte        — el contacto asociado se creó dentro del período
    """
    _DEAL_PROPS = ["dealstage", "amount", "closedate", "createdate",
                   "motivo_de_cierre_del_negocio", "modalidad",
                   PROP_ENTRO_GANADO, PROP_ENTRO_PERDIDO]

    def _buscar(filters):
        res, after = [], None
        while True:
            payload = {"filterGroups": [{"filters": filters}],
                       "properties": _DEAL_PROPS, "limit": 200}
            if after:
                payload["after"] = after
            data = _hs_search("/crm/v3/objects/deals/search", payload)
            if not data:
                break
            res += data.get("results", [])
            pg = data.get("paging", {})
            if not pg or "next" not in pg:
                break
            after = pg["next"]["after"]
        return res

    _pipe_f = [{"propertyName": "pipeline", "operator": "EQ", "value": PIPELINE_ID}]

    if fecha_inicio == "todos":
        grupos = [("cohorte", _pipe_f)]
    else:
        fi_ts = int(datetime.fromisoformat(fecha_inicio)
                    .replace(tzinfo=timezone.utc).timestamp() * 1000)
        ff_ts = (int(datetime.fromisoformat(fecha_fin)
                     .replace(tzinfo=timezone.utc).timestamp() * 1000)
                 + 86_400_000 - 1)

        def _rango(prop):
            return [{"propertyName": prop, "operator": "GTE", "value": str(fi_ts)},
                    {"propertyName": prop, "operator": "LTE", "value": str(ff_ts)}]

        grupos = [
            ("gano",    _pipe_f + [{"propertyName": "dealstage", "operator": "EQ",
                                    "value": STAGE_GANADO}] + _rango(PROP_ENTRO_GANADO)),
            ("perdio",  _pipe_f + [{"propertyName": "dealstage", "operator": "EQ",
                                    "value": STAGE_PERDIDO}] + _rango(PROP_ENTRO_PERDIDO)),
            ("cohorte", _pipe_f + _rango("createdate")),
        ]

    deal_map = {}
    for flag, filters in grupos:
        for d in _buscar(filters):
            p = d["properties"]
            info = deal_map.setdefault(d["id"], {
                "etapa":  PIPELINE_STAGES.get(p.get("dealstage", ""),
                                              p.get("dealstage", "")),
                "amount": float(p.get("amount") or 0),
                "motivo_cierre": (p.get("motivo_de_cierre_del_negocio")
                                  or "Sin especificar").strip(),
                "modalidad": (p.get("modalidad") or "Sin modalidad").strip().title(),
                "fecha_cierre":   (p.get("closedate") or "")[:10],
                "fecha_creacion": (p.get("createdate") or "")[:10],
                "gano_periodo": False, "perdio_periodo": False, "deal_cohorte": False,
            })
            if flag == "gano":
                info["gano_periodo"] = True
            elif flag == "perdio":
                info["perdio_periodo"] = True
            else:
                info["deal_cohorte"] = True

    if not deal_map:
        return pd.DataFrame()

    # ── Asociación deal → contacto ────────────────────────────────────────────
    deal_ids = list(deal_map)
    deal_to_contact = {}
    for i in range(0, len(deal_ids), 100):
        r = _hs_post(f"{BASE}/crm/v4/associations/deals/contacts/batch/read",
                          headers=HEADERS,
                          json={"inputs": [{"id": d} for d in deal_ids[i:i + 100]]},
                          timeout=30)
        if r.status_code == 200:
            for item in r.json().get("results", []):
                did = str(item.get("from", {}).get("id", ""))
                tos = item.get("to", [])
                if did and tos:
                    deal_to_contact[did] = str(tos[0].get("toObjectId", ""))

    # ── Propiedades del contacto ──────────────────────────────────────────────
    _usuarios = fetch_usuarios()
    contact_ids = list(set(deal_to_contact.values()))
    contact_data = {}
    for i in range(0, len(contact_ids), 100):
        r = _hs_post(f"{BASE}/crm/v3/objects/contacts/batch/read",
                          headers=HEADERS,
                          json={"inputs": [{"id": c} for c in contact_ids[i:i + 100]],
                                "properties": ["email", "createdate", "lead_valido",
                                               "curso", "categoria_lead",
                                               "hs_object_source",
                                               "first_conversion_event_name",
                                               "hs_latest_source",
                                               "hs_latest_source_data_1",
                                               "hs_latest_source_data_2",
                                               "hs_analytics_source",
                                               "hs_analytics_source_data_1",
                                               "hs_analytics_source_data_2",
                                               "pais", "pais_formulario", "nacionalidad",
                                               "ip_country", "pais_de_residencia",
                                               "country", "billing_country",
                                               "pais_de_la_ip_capabilia",
                                               "modalidad_curso"]},
                          timeout=30)
        if r.status_code == 200:
            for c in r.json().get("results", []):
                p = c["properties"]
                fuente, _ = resolve_fuente(p)
                _camp = etiqueta_campana(resolve_campana_cp(p),
                                         p.get("hs_analytics_source_data_2"), _usuarios)
                if _camp == "Sin campaña":
                    _camp = etiqueta_campana(resolve_campana_cp(p, reciente=True),
                                             p.get("hs_latest_source_data_2"), _usuarios)
                contact_data[c["id"]] = {
                    "fuente":   fuente,
                    "campaña":  _camp,
                    "pais":     resolve_pais(p),
                    "programa": CURSO_LABELS.get(p.get("curso") or "",
                                    (p.get("curso") or "Sin programa").strip())
                                or "Sin programa",
                    "email":    (p.get("email") or "").lower().strip(),
                    "cont_creado":  (p.get("createdate") or "")[:10],
                    "cont_valido":  (p.get("lead_valido") or "Sin datos").strip(),
                    "cont_curso":   bool((p.get("curso") or "").strip()),
                    "cont_categoria": _resolve_categoria(p),
                }

    _VACIO = {"fuente": "Sin datos", "campaña": "Sin campaña", "pais": "Sin datos",
              "programa": "Sin programa", "email": "", "cont_creado": "",
              "cont_valido": "Sin datos", "cont_curso": False,
              "cont_categoria": "Sin categoría"}

    rows = []
    for did, info in deal_map.items():
        cnt = contact_data.get(deal_to_contact.get(did, ""), _VACIO)
        rows.append({**info, "deal_id": did, **cnt})

    dfp = pd.DataFrame(rows)

    # cohorte = el CONTACTO se creó dentro del período (no el deal)
    if fecha_inicio == "todos":
        dfp["cohorte"] = True
    else:
        dfp["cohorte"] = ((dfp["cont_creado"] >= fecha_inicio) &
                          (dfp["cont_creado"] <= fecha_fin))
    return dfp


# ── Conector Google Ads ───────────────────────────────────────────────────────
@st.cache_data(ttl=3600, max_entries=6, show_spinner=False)
def get_google_ads_data(start: str, end: str) -> pd.DataFrame:
    if not GA_AVAILABLE:
        return pd.DataFrame()
    try:
        from google.ads.googleads.client import GoogleAdsClient
        cfg = {
            "developer_token":   GA_DEVELOPER_TOKEN,
            "client_id":         GA_CLIENT_ID,
            "client_secret":     GA_CLIENT_SECRET,
            "refresh_token":     GA_REFRESH_TOKEN,
            "login_customer_id": GA_LOGIN_CID.replace("-", ""),
            "use_proto_plus":    True,
        }
        client     = GoogleAdsClient.load_from_dict(cfg)
        ga_service = client.get_service("GoogleAdsService")
        query = f"""
            SELECT campaign.name, campaign.tracking_url_template,
                   metrics.cost_micros, metrics.conversions,
                   metrics.clicks, metrics.impressions
            FROM campaign
            WHERE segments.date BETWEEN '{start}' AND '{end}'
              AND campaign.status != 'REMOVED'
              AND metrics.cost_micros > 0
        """
        cid = GA_CUSTOMER_ID.replace("-", "")

        def _utm(tpl):
            """utm_campaign que declara una plantilla de seguimiento."""
            m = re.search(r"utm_campaign=([^&]+)", tpl or "")
            return unquote_plus(m.group(1)).strip() if m else ""

        # ── Nivel campaña ─────────────────────────────────────────────────────
        camp = {}
        for batch in ga_service.search_stream(customer_id=cid, query=query):
            for row in batch.results:
                a = camp.setdefault(row.campaign.name,
                                    [0.0, 0.0, 0, _utm(row.campaign.tracking_url_template)])
                a[0] += row.metrics.cost_micros / 1_000_000
                a[1] += row.metrics.conversions
                a[2] += row.metrics.clicks

        # ── Grupos de anuncios con plantilla propia ───────────────────────────
        # Cuando un grupo declara su propio utm_campaign, es él —y no la campaña—
        # quien aparece en el CRM: "Search - Máster Dirección - Nac" agrupa la
        # versión presencial y la online, cada una con su UTM y su landing.
        q_ag = f"""
            SELECT campaign.name, ad_group.name, ad_group.tracking_url_template,
                   metrics.cost_micros, metrics.conversions, metrics.clicks
            FROM ad_group
            WHERE segments.date BETWEEN '{start}' AND '{end}'
              AND metrics.cost_micros > 0
        """
        adg = {}
        try:
            for batch in ga_service.search_stream(customer_id=cid, query=q_ag):
                for row in batch.results:
                    u = _utm(row.ad_group.tracking_url_template)
                    if not u:
                        continue                      # hereda la de la campaña
                    k = (row.campaign.name, row.ad_group.name)
                    a = adg.setdefault(k, [0.0, 0.0, 0, u])
                    a[0] += row.metrics.cost_micros / 1_000_000
                    a[1] += row.metrics.conversions
                    a[2] += row.metrics.clicks
        except Exception:
            adg = {}

        # ── Grupos de recursos de PMax ────────────────────────────────────────
        # No tienen plantilla propia: la UTM viene de la landing de cada grupo,
        # así que se desglosa cuando la campaña tiene más de uno con gasto.
        q_asg = f"""
            SELECT campaign.name, asset_group.name, metrics.cost_micros,
                   metrics.conversions, metrics.clicks
            FROM asset_group
            WHERE segments.date BETWEEN '{start}' AND '{end}'
              AND metrics.cost_micros > 0
        """
        asg = {}
        try:
            for batch in ga_service.search_stream(customer_id=cid, query=q_asg):
                for row in batch.results:
                    k = (row.campaign.name, row.asset_group.name)
                    a = asg.setdefault(k, [0.0, 0.0, 0])
                    a[0] += row.metrics.cost_micros / 1_000_000
                    a[1] += row.metrics.conversions
                    a[2] += row.metrics.clicks
        except Exception:
            asg = {}

        _n_asg = {}
        for (c, _g) in asg:
            _n_asg[c] = _n_asg.get(c, 0) + 1
        _pmax_multi = {c for c, n in _n_asg.items() if n > 1}

        rows = []

        def _fila(nombre, v, utm):
            rows.append({"campaña": nombre, "gasto": v[0], "conversiones": v[1],
                         "clics": v[2], "utm_declarada": utm,
                         "plataforma": "Google Ads"})

        # Grupos de anuncios con UTM propia
        _gastado = {}
        for (c, g), v in adg.items():
            _fila(f"{c} › {g}", v, v[3])
            _gastado[c] = _gastado.get(c, 0.0) + v[0]
        # Grupos de recursos de las PMax con varios
        for (c, g), v in asg.items():
            if c in _pmax_multi:
                _fila(f"{c} › {g}", v, "")
        # Resto de campañas, con el gasto que no se haya desglosado ya
        for c, v in camp.items():
            if c in _pmax_multi:
                continue
            resto = v[0] - _gastado.get(c, 0.0)
            if resto <= 0.01:
                continue
            _fila(c, [resto, v[1], v[2]], v[3])

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)
    except Exception as e:
        st.warning(f"Google Ads: {e}")
        return pd.DataFrame()


# ── Conector Meta Ads ─────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, max_entries=6, show_spinner=False)
def get_meta_ads_data(start: str, end: str) -> pd.DataFrame:
    if not META_AVAILABLE:
        return pd.DataFrame()
    try:
        url = f"https://graph.facebook.com/v21.0/act_{META_ACCOUNT_ID}/insights"
        params = {
            "access_token": META_TOKEN,
            "fields":       "campaign_name,spend,clicks,impressions,actions",
            "level":        "campaign",
            "time_range":   json.dumps({"since": start, "until": end}),
            "limit":        500,
        }
        rows = []
        while True:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            for item in data.get("data", []):
                # Hofmann mide los leads con el Pixel → complete_registration
                _leads = 0.0
                for _pref in ("offsite_conversion.fb_pixel_complete_registration",
                              "complete_registration", "lead"):
                    for a in item.get("actions", []):
                        if a.get("action_type") == _pref:
                            _leads = float(a.get("value", 0) or 0)
                            break
                    if _leads:
                        break
                rows.append({
                    "campaña":      item.get("campaign_name", ""),
                    "gasto":        float(item.get("spend", 0)),
                    "clics":        int(item.get("clicks", 0)),
                    "conversiones": _leads,
                    "plataforma":   "Meta Ads",
                })
            nxt = data.get("paging", {}).get("next")
            if not nxt:
                break
            url, params = nxt, {}
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)
    except Exception as e:
        st.warning(f"Meta Ads: {e}")
        return pd.DataFrame()


# ── Conector LinkedIn Ads vía Google Sheets ───────────────────────────────────
@st.cache_data(ttl=1800, max_entries=6, show_spinner=False)
def get_linkedin_sheets_data(start: str, end: str) -> pd.DataFrame:
    if not LINKEDIN_AVAILABLE:
        return pd.DataFrame()
    try:
        r = requests.get(LINKEDIN_SHEET_URL, timeout=20)
        r.raise_for_status()
        content = r.content.decode("utf-8-sig")
        df = pd.read_csv(StringIO(content))
        df.columns = [c.strip().lower() for c in df.columns]
        col_map = {
            "fecha": "fecha", "date": "fecha",
            "campaña": "campaña", "campana": "campaña", "campaign": "campaña",
            "gasto": "gasto", "spend": "gasto", "inversión": "gasto", "inversion": "gasto", "cost": "gasto",
            "clics": "clics", "clicks": "clics",
            "conversiones": "conversiones", "conversions": "conversiones",
            "leads": "conversiones",
        }
        df = df.rename(columns={c: col_map.get(c, c) for c in df.columns})
        for col in ["fecha", "campaña", "gasto"]:
            if col not in df.columns:
                return pd.DataFrame()
        def to_num(s):
            return pd.to_numeric(s.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce").fillna(0)
        df["fecha"] = pd.to_datetime(df["fecha"], format="%Y-%m-%d", errors="coerce")
        mask = df["fecha"].isna()
        if mask.any():
            df.loc[mask, "fecha"] = pd.to_datetime(df.loc[mask, "fecha"], dayfirst=True, errors="coerce")
        df = df.dropna(subset=["fecha"])
        df["gasto"] = to_num(df["gasto"])
        if "clics" not in df.columns:
            df["clics"] = 0
        df["conversiones"] = (to_num(df["conversiones"])
                              if "conversiones" in df.columns else 0)
        df = df[(df["fecha"] >= pd.to_datetime(start)) & (df["fecha"] <= pd.to_datetime(end))].copy()
        if df.empty:
            return pd.DataFrame()
        df["plataforma"] = "LinkedIn Ads"
        return df[["fecha", "campaña", "gasto", "clics", "conversiones",
                   "plataforma"]]
    except Exception as e:
        st.warning(f"LinkedIn Sheets: {e}")
        return pd.DataFrame()


# ── Conector TikTok Ads ───────────────────────────────────────────────────────
@st.cache_data(ttl=3600, max_entries=6, show_spinner=False)
def get_tiktok_ads_data(start: str, end: str) -> pd.DataFrame:
    if not TIKTOK_AVAILABLE:
        return pd.DataFrame()
    try:
        r = requests.get(
            "https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/",
            headers={"Access-Token": TIKTOK_TOKEN},
            params={
                "advertiser_id": TIKTOK_ADVERTISER_ID,
                "report_type":   "BASIC",
                "data_level":    "AUCTION_CAMPAIGN",
                "dimensions":    json.dumps(["campaign_id", "stat_time_day"]),
                "metrics":       json.dumps(["spend", "clicks", "conversion",
                                             "campaign_name"]),
                "start_date": start,
                "end_date":   end,
                "page_size":  1000,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            st.warning(f"TikTok Ads: {data.get('message', 'Error')}")
            return pd.DataFrame()
        rows = []
        for item in data.get("data", {}).get("list", []):
            dims    = item.get("dimensions", {})
            metrics = item.get("metrics", {})
            gasto   = float(metrics.get("spend", 0) or 0)
            if gasto == 0:
                continue
            rows.append({
                "fecha":      dims.get("stat_time_day", start)[:10],
                "campaña":    metrics.get("campaign_name", f"TK_{dims.get('campaign_id', '')}"),
                "gasto":      gasto,
                "clics":      int(metrics.get("clicks", 0) or 0),
                "conversiones": float(metrics.get("conversion", 0) or 0),
                "plataforma": "TikTok Ads",
            })
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["fecha"] = pd.to_datetime(df["fecha"])
        return df
    except Exception as e:
        st.warning(f"TikTok Ads: {e}")
        return pd.DataFrame()


# ── Email Marketing fetch ─────────────────────────────────────────────────────

@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def _fetch_list_names(list_ids_tuple: tuple) -> dict:
    def _get(lid):
        try:
            # Try v1 first (regular contact lists)
            r = _hs_get(f"{BASE}/contacts/v1/lists/{lid}",
                             headers=HEADERS, params={"count": 0}, timeout=10)
            if r.status_code == 200:
                return lid, r.json().get("name", lid)
            # Fall back to v3 for ILS lists (return 404 in v1)
            if r.status_code == 404:
                r2 = _hs_get(f"{BASE}/crm/v3/lists/{lid}",
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


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def fetch_emails_enviados(fecha_inicio: str, fecha_fin: str) -> pd.DataFrame:
    raw = []
    params: dict = {"state": "PUBLISHED", "limit": 100, "orderBy": "-publishDate"}
    after = None
    while True:
        if after:
            params["after"] = after
        else:
            params.pop("after", None)
        r = _hs_get(f"{BASE}/marketing/v3/emails", headers=HEADERS,
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
            r = _hs_get(f"{BASE}/email/public/v1/campaigns/{cid}",
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


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def fetch_emails_programados() -> pd.DataFrame:
    raw = []
    params: dict = {"state": "SCHEDULED", "limit": 100, "orderBy": "publishDate"}
    after = None
    while True:
        if after:
            params["after"] = after
        else:
            params.pop("after", None)
        r = _hs_get(f"{BASE}/marketing/v3/emails", headers=HEADERS,
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


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def fetch_click_urls(campaign_id: str) -> list:
    """Returns [(url, clicks)] sorted desc for a campaign."""
    events: list = []
    params: dict = {"campaignId": campaign_id, "eventType": "CLICK", "limit": 300}
    for _ in range(5):
        r = _hs_get(f"{BASE}/email/public/v1/events",
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


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def fetch_all_lists() -> pd.DataFrame:
    lists: list = []
    offset = 0
    while True:
        r = _hs_get(f"{BASE}/contacts/v1/lists",
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


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def fetch_workflows() -> pd.DataFrame:
    import json as _json

    r = _hs_get(f"{BASE}/automation/v3/workflows", headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return pd.DataFrame()
    wfs_raw = r.json().get("workflows", [])

    def _detail(wf):
        wid = wf["id"]
        r2 = _hs_get(f"{BASE}/automation/v3/workflows/{wid}", headers=HEADERS, timeout=15)
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
        r = _hs_get(f"{BASE}/marketing/v3/emails/{eid}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return eid, r.json().get("name", eid)
        return eid, eid

    def _cstats(cid):
        r = _hs_get(f"{BASE}/email/public/v1/campaigns/{cid}",
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


@st.cache_data(ttl=600, max_entries=6, show_spinner=False)
def fetch_sequences() -> pd.DataFrame:
    r = _hs_get(f"{BASE}/settings/v3/users", headers=HEADERS, timeout=15)
    if r.status_code != 200:
        return pd.DataFrame()
    users = r.json().get("results", [])

    seq_map: dict = {}
    for u in users:
        uid = u.get("id")
        r2 = _hs_get(f"{BASE}/automation/v4/sequences",
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
        r = _hs_get(f"{BASE}/automation/v4/sequences/{sid}",
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
    _title_text = (fig.layout.title.text or "") if fig.layout.title else ""
    fig.update_layout(
        height=height,
        paper_bgcolor=BARCA["white"],
        plot_bgcolor=BARCA["white"],
        font_color=BARCA["ink80"],
        title=dict(text=_title_text, font=dict(size=14, color=BARCA["blue_ink"])),
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
    # ── Cabecera compacta: logo + título ──────────────────────────────────────
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:14px;padding:2px 0 12px;
                border-bottom:2px solid {BARCA['gold']};margin-bottom:16px">
        <img src="{_logo_src()}" alt="{ACCOUNT_NAME}"
             style="height:38px;width:auto;display:block">
        <span style="font-size:23px;font-weight:800;color:{BARCA['blue_ink']};
                     letter-spacing:-.4px">Dashboard. {ACCOUNT_NAME}</span>
    </div>""", unsafe_allow_html=True)

    # ── Sidebar — bloque 1: fecha y fuente (antes de cargar datos) ───────────────
    with st.sidebar:
        st.markdown(f"<h2 style='color:{BARCA['gold']};margin-bottom:8px'>📁 Páginas</h2>",
                    unsafe_allow_html=True)
        _PAGINAS = ["💰 Contactos, Conversión & ROI", "📊 RST Dashboard", "📍 Leads por Campaña"]
        _qp_load("f_pagina", "str")
        if st.session_state.get("f_pagina") not in _PAGINAS:
            st.session_state.pop("f_pagina", None)
        _pagina = st.radio("Página", _PAGINAS, key="f_pagina", label_visibility="collapsed")
        _qp_save("f_pagina", _pagina, "str")

        st.markdown("---")
        st.markdown(f"<h2 style='color:{BARCA['gold']};margin-bottom:16px'>⚙️ Filtros</h2>",
                    unsafe_allow_html=True)

        _qp_load("f_modo", "str")
        modo = st.radio("Modo de fecha", ["Período predefinido", "Rango personalizado"],
                        key="f_modo")
        _qp_save("f_modo", modo, "str")

        if modo == "Período predefinido":
            hoy = date.today()
            mes_inicio = date(hoy.year, hoy.month, 1)
            _periodo_opts = [
                "Este mes",
                "Hoy", "Ayer",
                "Últimos 7 días", "Últimos 30 días", "Últimos 60 días", "Últimos 90 días",
                "Mes anterior",
                "2026 completo", "2025 completo",
                "Todos (desde 2024)",
            ]
            _qp_load("f_periodo", "str")
            if st.session_state.get("f_periodo") not in _periodo_opts:
                st.session_state.pop("f_periodo", None)
            periodo = st.selectbox("Período", _periodo_opts, key="f_periodo")
            _qp_save("f_periodo", periodo, "str")
            # Mes anterior: del día 1 al último, calculado sobre la fecha de hoy
            _fin_ant = mes_inicio - timedelta(days=1)
            _ini_ant = date(_fin_ant.year, _fin_ant.month, 1)
            mapa = {
                "Este mes":        (mes_inicio, hoy),
                "Mes anterior":    (_ini_ant, _fin_ant),
                "2026 completo":   (date(2026, 1, 1), date(2026, 12, 31)),
                "Hoy":             (hoy, hoy),
                "Ayer":            (hoy - timedelta(1), hoy - timedelta(1)),
                "Últimos 7 días":  (hoy - timedelta(7), hoy),
                "Últimos 30 días": (hoy - timedelta(30), hoy),
                "Últimos 60 días": (hoy - timedelta(60), hoy),
                "Últimos 90 días": (hoy - timedelta(90), hoy),
                "2025 completo":   (date(2025, 1, 1), date(2025, 12, 31)),
            }
            if periodo == "Todos (desde 2024)":
                fi, ff = "todos", "todos"
            else:
                fi, ff = mapa.get(periodo, (mes_inicio, hoy))
        else:
            _qp_load("f_fi", "date")
            _qp_load("f_ff", "date")
            st.session_state.setdefault("f_fi", date(2026, 1, 1))
            st.session_state.setdefault("f_ff", date.today())
            fi = st.date_input("Desde", key="f_fi")
            ff = st.date_input("Hasta", key="f_ff")
            _qp_save("f_fi", fi, "date")
            _qp_save("f_ff", ff, "date")

        st.markdown("---")
        _fuente_opts = [
            "Social pagado", "Búsqueda pagada", "Búsqueda orgánica",
            "Tráfico directo", "Otras campañas", "Redes sociales",
            "Offline", "Referencias", "Referral IA", "Email marketing", "Sin datos"
        ]
        _qp_load("f_fuente", "multi")
        filtro_fuente = st.multiselect("Fuente de tráfico", options=_fuente_opts, key="f_fuente")
        _qp_save("f_fuente", filtro_fuente, "multi")

        st.markdown("---")
        _qp_load("f_categoria", "multi")
        filtro_categoria = st.multiselect(
            "Tipo de contacto",
            options=_CATEGORIAS_OPTS,
            help="Filtra por el origen/tipo del contacto (captación, evento, compra, etc.)",
            key="f_categoria",
        )
        _qp_save("f_categoria", filtro_categoria, "multi")

        st.markdown("---")
        _modcont_opts = ["Presencial", "Online", "Sin modalidad"]
        _qp_load("f_modcont", "multi")
        filtro_modalidad_contacto = st.multiselect(
            "Modalidad contacto", options=_modcont_opts, key="f_modcont")
        _qp_save("f_modcont", filtro_modalidad_contacto, "multi")
        _qp_load("f_modneg", "multi")
        filtro_modalidad_negocio = st.multiselect(
            "Modalidad negocio", options=_modcont_opts, key="f_modneg")
        _qp_save("f_modneg", filtro_modalidad_negocio, "multi")

        st.markdown("---")
        if st.button("🔄 Actualizar datos", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.markdown(f"<small style='color:{BARCA['ink20']}'>Cache 5 min · "
                    f"Fuente: HubSpot CRM</small>", unsafe_allow_html=True)

    # ── Carga en paralelo ──────────────────────────────────────────────────────
    _ads_start = str(fi) if fi != "todos" else (date.today() - timedelta(days=90)).isoformat()
    _ads_end   = str(ff) if ff != "todos" else date.today().isoformat()
    with st.spinner("Cargando datos..."):
        with ThreadPoolExecutor(max_workers=10) as ex:
            fut_data     = ex.submit(fetch_data,               str(fi), str(ff))
            fut_mat      = ex.submit(fetch_matriculados_total,  str(fi), str(ff))
            fut_pipeline = ex.submit(fetch_pipeline,            str(fi), str(ff))
            fut_pip_full = ex.submit(fetch_pipeline_full,       str(fi), str(ff))
            fut_emails   = ex.submit(fetch_emails_enviados,     str(fi), str(ff))
            fut_prog     = ex.submit(fetch_emails_programados)
            fut_google   = ex.submit(get_google_ads_data,       _ads_start, _ads_end)
            fut_meta     = ex.submit(get_meta_ads_data,         _ads_start, _ads_end)
            fut_linkedin = ex.submit(get_linkedin_sheets_data,  _ads_start, _ads_end)
            fut_tiktok   = ex.submit(get_tiktok_ads_data,       _ads_start, _ads_end)
        df           = fut_data.result()
        df_mat_all   = fut_mat.result()
        df_pipeline  = fut_pipeline.result()
        df_pip_full  = fut_pip_full.result()
        df_emails    = fut_emails.result()
        df_prog      = fut_prog.result()
        df_google    = fut_google.result()
        df_meta      = fut_meta.result()
        df_linkedin  = fut_linkedin.result()
        df_tiktok    = fut_tiktok.result()

    # Clasificar cada campaña de Ads por mercado y modalidad según su nombre
    def _enriquecer_ads(d):
        if d.empty or "campaña" not in d.columns:
            return d
        d = d.copy()
        d["mercado_camp"]   = d["campaña"].apply(clasificar_mercado_camp)
        d["modalidad_camp"] = d["campaña"].apply(clasificar_modalidad_camp)
        d["clave_camp"]     = d["campaña"].apply(clave_campana)
        if "utm_declarada" not in d.columns:
            d["utm_declarada"] = ""
        return d

    df_google   = _enriquecer_ads(df_google)
    df_meta     = _enriquecer_ads(df_meta)
    df_linkedin = _enriquecer_ads(df_linkedin)
    df_tiktok   = _enriquecer_ads(df_tiktok)

    # Las campañas de webinar / open day salen del análisis, igual que sus leads:
    # dejar su gasto dentro inflaría el CPL y hundiría el ROI del resto.
    # EXCEPCIÓN: si una de ellas ha traído una matrícula, se queda dentro —con su
    # gasto y sus leads— para que su ROI se pueda medir.
    _wb_con_matricula = set()
    if not df_pip_full.empty and {"campaña", "gano_periodo"} <= set(df_pip_full.columns):
        _wg = df_pip_full[df_pip_full["gano_periodo"]
                          & df_pip_full["campaña"].fillna("").apply(es_campana_no_captacion)]
        _wb_con_matricula = set(_wg["campaña"].dropna().astype(str).unique())

    def webinar_excluible(nombre: str) -> bool:
        """Campaña fuera del análisis que además NO ha generado ninguna matrícula."""
        if not es_campana_no_captacion(nombre):
            return False
        for _u in _wb_con_matricula:
            if _u == nombre or emparejar_campana(_u, [nombre]):
                return False          # ha traído matrícula → se conserva
        return True

    def _sin_webinars(d):
        """Devuelve (df sin webinars estériles, gasto excluido, campañas excluidas)."""
        if d.empty or "campaña" not in d.columns:
            return d, 0.0, []
        _m = d["campaña"].apply(webinar_excluible)
        if not _m.any():
            return d, 0.0, []
        return (d[~_m].copy(), float(d.loc[_m, "gasto"].sum()),
                sorted(d.loc[_m, "campaña"].dropna().unique().tolist()))

    df_google,   _gw1, _cw1 = _sin_webinars(df_google)
    df_meta,     _gw2, _cw2 = _sin_webinars(df_meta)
    df_linkedin, _gw3, _cw3 = _sin_webinars(df_linkedin)
    df_tiktok,   _gw4, _cw4 = _sin_webinars(df_tiktok)
    gasto_webinars = _gw1 + _gw2 + _gw3 + _gw4
    camps_webinars = _cw1 + _cw2 + _cw3 + _cw4

    # Los negocios cerrados del período son los mismos deals que ya ha traído
    # fetch_pipeline_full (41 ganados + 845 perdidos en julio): pedirlos otra vez
    # costaba 27 peticiones y 10 s, así que se derivan.
    if df_pip_full.empty:
        df_deals = pd.DataFrame(columns=["deal_id", "etapa", "motivo_cierre",
                                         "fuente", "pais", "fecha_cierre", "mes"])
    else:
        _cerr = df_pip_full[df_pip_full["gano_periodo"] | df_pip_full["perdio_periodo"]].copy()
        _cerr["etapa"] = _cerr["gano_periodo"].map({True: "Cierre ganado",
                                                    False: "Cierre perdido"})
        _cerr["mes"] = _cerr["fecha_cierre"].str[:7]
        df_deals = _cerr[["deal_id", "etapa", "motivo_cierre", "fuente", "pais",
                          "fecha_cierre", "mes"]].reset_index(drop=True)

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
        _qp_load("f_pais", "multi")
        if "f_pais" in st.session_state:  # descartar países que ya no están en las opciones
            st.session_state["f_pais"] = [p for p in st.session_state["f_pais"] if p in paises_opts]
        filtro_pais = st.multiselect("País", options=paises_opts, key="f_pais")
        _qp_save("f_pais", filtro_pais, "multi")

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

    # ══════════════════════════════════════════════════════════════════════════
    # PÁGINA — Contactos, Conversión & ROI   (réplica mktsales.netlify.app)
    # ══════════════════════════════════════════════════════════════════════════
    _GANADO_ET = ["Cierre Ganado", "Cierre Ganado (histórico)"]

    # Paleta exacta de la referencia
    _RF = {
        "ink":      "#1A2233",
        "ink_soft": "#46516A",
        "muted":    "#7A8699",
        "th":       "#8A94A6",
        "line":     "#EFF1F5",
        "border":   "#E7EAF0",
        "card":     "#FFFFFF",
        "chip_bg":  "#E9E9FB",
        "chip_tx":  "#0D0E95",
        "tog_on":   "#0D0E95",
    }
    _PILL = {
        "green": ("#E3F5EC", "#1E7A4F"),
        "red":   ("#FDE8EC", "#B32B45"),
        "amber": ("#FEF3D9", "#8A6206"),
        "gray":  ("#F0F2F5", "#6B7688"),
        "blue":  ("#E9E9FB", "#0D0E95"),
    }
    # Colores del donut, en el orden de la referencia
    _DONUT = ["#0D0E95", "#2A2BC4", "#6B6BE0", "#34D399", "#9CA3AF",
              "#2DD4BF", "#D1D5DB", "#EC4899", "#F59E0B", "#6366F1"]
    _MERC_COLOR = {"Nacional": "#34D399", "LATAM": "#F59E0B",
                   "ROW": "#9CA3AF", "Sin país": "#D1D5DB", "—": "#5B8DEF"}

    def _fmt_eur(v):
        if v is None or (isinstance(v, float) and pd.isna(v)) or not v:
            return "—"
        return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

    def _fmt_eur0(v):
        if v is None or (isinstance(v, float) and pd.isna(v)) or not v:
            return "—"
        return f"{v:,.0f} €".replace(",", ".")

    def _fmt_int(v):
        if not v:
            return "—"
        return f"{int(v):,}".replace(",", ".")

    def _fmt_pct(v, dash_zero=False):
        if v is None or (dash_zero and not v):
            return "—"
        return f"{v:.1f}%".replace(".", ",")

    def _pill(txt, kind):
        bg, tx = _PILL[kind]
        return (f"<span style='background:{bg};color:{tx};font-size:11px;font-weight:700;"
                f"padding:3px 9px;border-radius:20px;white-space:nowrap'>{txt}</span>")

    def _pill_conv(v):
        """Pill de conversión con el color según el valor, como en la referencia."""
        if not v:
            return _pill("0%", "gray")
        kind = "green" if v >= 10 else ("amber" if v >= 3 else "red")
        return _pill(_fmt_pct(v), kind)

    def _pill_neg(v):
        """Pill para métricas donde MÁS es PEOR (ilocalizados, perdidos, motivos)."""
        if not v:
            return _pill("0%", "gray")
        kind = "green" if v < 5 else ("amber" if v < 15 else "red")
        return _pill(_fmt_pct(v), kind)

    def _pill_merc(m):
        if m in ("—", "", None):
            return f"<span style='color:{_RF['muted']}'>—</span>"
        kind = {"Nacional": "green", "LATAM": "amber"}.get(m, "gray")
        return _pill(m, kind)

    def _bar(pct, color, maxpct):
        w = (pct / maxpct * 100) if maxpct else 0
        return (f"<div style='background:{_RF['line']};border-radius:4px;height:6px;"
                f"width:100%;min-width:60px'>"
                f"<div style='background:{color};width:{w:.1f}%;height:6px;"
                f"border-radius:4px'></div></div>")

    def _table(cols, rows, total_row=None):
        """cols: [(label, align)] · rows: [[celda_html]] · total_row: [celda_html]"""
        th = "".join(
            f"<th style='text-align:{a};padding:9px 12px;font-size:10.5px;"
            f"font-weight:700;color:{_RF['th']};text-transform:uppercase;"
            f"letter-spacing:.5px;border-bottom:1px solid {_RF['border']};"
            f"white-space:nowrap'>{c}</th>"
            for c, a in cols)
        body = ""
        for r in rows:
            tds = "".join(
                f"<td style='text-align:{cols[i][1]};padding:10px 12px;font-size:13px;"
                f"color:{_RF['ink']};border-bottom:1px solid {_RF['line']};"
                f"white-space:nowrap'>{v}</td>"
                for i, v in enumerate(r))
            body += f"<tr>{tds}</tr>"
        if total_row:
            tds = "".join(
                f"<td style='text-align:{cols[i][1]};padding:11px 12px;font-size:13px;"
                f"font-weight:700;color:{_RF['ink']};border-top:2px solid {_RF['border']}'>"
                f"{v}</td>"
                for i, v in enumerate(total_row))
            body += f"<tr>{tds}</tr>"
        return (f"<div style='overflow-x:auto'><table style='width:100%;"
                f"border-collapse:collapse'><thead><tr>{th}</tr></thead>"
                f"<tbody>{body}</tbody></table></div>")

    def _tabla_ordenable(cols, rows, altura=640, total=None, nombre=None):
        """Igual que _table pero en un componente, para poder ordenar al clicar.

        Streamlit no ejecuta JavaScript dentro de st.markdown, así que la tabla
        se sirve en un iframe con su propio CSS y su ordenación.
        """
        if nombre:
            if _barra_tabla(nombre, cols, rows, total):
                # Ampliada: se enseña entera, con un tope razonable
                altura = min(2400, 150 + 41 * max(len(rows), 1))
        th = "".join(
            f"<th data-i='{i}' style='text-align:{a};'>{c}"
            f"<span class='ar'></span></th>" for i, (c, a) in enumerate(cols))
        body = "".join(
            "<tr>" + "".join(
                f"<td style='text-align:{cols[i][1]}'>{v}</td>"
                for i, v in enumerate(r)) + "</tr>" for r in rows)
        # El total va en <tfoot>: el script solo ordena <tbody>, así que se queda
        # abajo en vez de bailar con las filas.
        pie = ""
        if total:
            pie = ("<tfoot><tr>" + "".join(
                f"<td style='text-align:{cols[i][1]}'>{v}</td>"
                for i, v in enumerate(total)) + "</tr></tfoot>")
        html = rf"""
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;font-family:'Source Sans Pro',-apple-system,BlinkMacSystemFont,
       'Segoe UI',sans-serif;background:transparent}}
 .wrap{{overflow:auto;max-height:{altura - 10}px;border:1px solid {_RF['border']};
        border-radius:12px;background:#fff}}
 table{{width:100%;border-collapse:separate;border-spacing:0}}
 th{{position:sticky;top:0;z-index:2;background:#fff;padding:9px 12px;font-size:10.5px;
     font-weight:700;color:{_RF['th']};text-transform:uppercase;letter-spacing:.5px;
     border-bottom:1px solid {_RF['border']};white-space:nowrap;cursor:pointer;
     user-select:none}}
 th:hover{{color:{_RF['ink']};background:{_RF['line']}}}
 th .ar{{margin-left:5px;opacity:.35;font-size:9px}}
 th.asc .ar::after{{content:'▲';opacity:1}}
 th.desc .ar::after{{content:'▼';opacity:1}}
 th:not(.asc):not(.desc) .ar::after{{content:'⇅'}}
 td{{padding:10px 12px;font-size:13px;color:{_RF['ink']};
     border-bottom:1px solid {_RF['line']};white-space:nowrap}}
 tbody tr:hover td{{background:{_RF['line']}}}
 tfoot td{{position:sticky;bottom:0;background:#fff;font-weight:700;
           border-top:2px solid {_RF['border']};border-bottom:none}}
</style>
<div class="wrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody>{pie}</table></div>
<script>
(function(){{
  var tb=document.querySelector('tbody'), ths=document.querySelectorAll('th');
  function num(t){{
    t=(t||'').replace(/[\s ]/g,'').replace(/[€%x]/g,'');
    if(!t||t==='—') return null;
    t=t.replace(/\./g,'').replace(',','.');
    var v=parseFloat(t);
    return isNaN(v)?null:v;
  }}
  ths.forEach(function(th,i){{
    th.addEventListener('click',function(){{
      var asc=!th.classList.contains('asc');
      ths.forEach(function(o){{o.classList.remove('asc','desc');}});
      th.classList.add(asc?'asc':'desc');
      var rs=Array.prototype.slice.call(tb.querySelectorAll('tr'));
      var vals=rs.map(function(r){{return num(r.children[i].innerText);}});
      var esNum=vals.some(function(v){{return v!==null;}});
      rs.sort(function(a,b){{
        if(esNum){{
          var x=num(a.children[i].innerText), y=num(b.children[i].innerText);
          if(x===null&&y===null) return 0;
          if(x===null) return 1;          // los vacíos siempre al final
          if(y===null) return -1;
          return asc? x-y : y-x;
        }}
        var s1=a.children[i].innerText.trim(), s2=b.children[i].innerText.trim();
        return asc? s1.localeCompare(s2,'es') : s2.localeCompare(s1,'es');
      }});
      rs.forEach(function(r){{tb.appendChild(r);}});
    }});
  }});
}})();
</script>"""
        components.html(html, height=altura, scrolling=False)

    # Las dos tablas que van una al lado de otra comparten alto, así el bloque
    # queda alineado sin importar cuántas filas tenga cada una.
    _ALTO_PAR = 540

    # Cada tabla que se pinta se guarda aquí en versión limpia, para poder
    # descargarlas todas juntas en un único Excel al final de la página.
    _EXPORT: list = []

    def _sin_html(v) -> str:
        return re.sub(r"<[^>]+>", "", str(v)).replace("\xa0", " ").strip()

    def _df_plano(cols, rows, total=None) -> pd.DataFrame:
        """La tabla tal como se ve, pero en texto: lista para CSV o Excel."""
        _f = [[_sin_html(c) for c in r] for r in rows]
        if total:
            _f.append([_sin_html(c) for c in total])
        return pd.DataFrame(_f, columns=[c for c, _ in cols])

    def _barra_tabla(nombre, cols, rows, total=None, ampliable=True):
        """Descarga en CSV y, si procede, interruptor para ampliar la tabla."""
        _df = _df_plano(cols, rows, total)
        _EXPORT.append((nombre, _df))
        _amp = False
        _c = st.columns([1.15, 1.15, 5]) if ampliable else st.columns([1.15, 6.15])
        _i = 0
        if ampliable:
            with _c[0]:
                _amp = st.toggle("⛶ Ampliar", key=f"amp_{nombre}",
                                 help="Muestra la tabla completa, sin scroll interno")
            _i = 1
        with _c[_i]:
            st.download_button(
                "⬇️ CSV", _df.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{nombre.lower().replace(' ', '_')}_{fi}_{ff}.csv",
                mime="text/csv", key=f"dl_{nombre}", use_container_width=True)
        return _amp

    def _sec_title(title, sub):
        return (f"<div style='font-size:18px;font-weight:700;color:{_RF['ink']};"
                f"margin:0 0 2px'>{title}</div>"
                f"<div style='font-size:12.5px;color:{_RF['muted']};margin:0 0 12px'>"
                f"{sub}</div>")

    def _card(html):
        st.markdown(
            f"<div style='background:{_RF['card']};border:1px solid {_RF['border']};"
            f"border-radius:14px;padding:20px 22px;margin-bottom:18px;"
            f"box-shadow:0 1px 3px rgba(16,24,40,.04)'>{html}</div>",
            unsafe_allow_html=True)

    def _seg(label, opts, key, default=None):
        """Segmented control con fallback a radio en versiones antiguas."""
        d = default if default is not None else opts[0]
        try:
            v = st.segmented_control(label, opts, default=d, key=key,
                                     label_visibility="collapsed")
            return v if v is not None else d
        except Exception:
            return st.radio(label, opts, horizontal=True, key=key,
                            label_visibility="collapsed")

    def _mercado_from_name(name: str) -> str:
        m = clasificar_mercado_camp(name)
        return "LATAM" if m == "Latam" else m

    # resolve_mercado devuelve España/Latam/Otro/Sin datos → etiquetas de negocio
    _MERC_LBL = {"España": "Nacional", "Latam": "LATAM",
                 "Otro": "ROW", "Sin datos": "Sin país"}

    def _merc_lbl(m) -> str:
        return _MERC_LBL.get(str(m), "ROW")

    def _merc_of_pais(p) -> str:
        return _merc_lbl(resolve_mercado(p))

    def page_roi():
        _EXPORT.clear()
        # ── Cabecera ──────────────────────────────────────────────────────────
        st.markdown(f"""
        <div style="font-size:12px;color:{_RF['muted']};margin:0 0 6px">
            Dashboard › <b style="color:{_RF['ink_soft']}">Negocio · Contactos &amp; Conversión</b>
        </div>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 6px">
            <span style="font-size:29px;font-weight:800;color:{_RF['ink']};
                         letter-spacing:-.6px">📊 Contactos, Conversión &amp; ROI</span>
            <span style="background:{_RF['chip_bg']};color:{_RF['chip_tx']};font-size:12.5px;
                         font-weight:700;padding:4px 12px;border-radius:20px">{periodo_txt}</span>
        </div>
        <div style="color:{_RF['muted']};font-size:13.5px;margin:0 0 16px;max-width:1150px">
            Leads <b>válidos</b> con curso informado creados en el período —sin Webinar ni
            Open Day— cruzados con los negocios que <b>entraron</b> en Cierre Ganado /
            Cierre Perdido dentro del período · conversión por mercado, país y curso ·
            ROI/ROAS por modalidad — Fuente: HubSpot + Google&nbsp;Ads + Meta + LinkedIn + TikTok
        </div>
        """, unsafe_allow_html=True)

        # ── Barra de vistas + modalidad ───────────────────────────────────────
        _v1, _v2, _v3 = st.columns([1.5, .45, 1.35])
        with _v1:
            _vista = _seg("Vista", ["Conversión & ROI", "Cierres perdidos (leads del mes)"],
                         "roi_vista")
        with _v2:
            st.markdown(f"<div style='padding-top:7px;font-size:11px;font-weight:700;"
                        f"color:{_RF['th']};text-transform:uppercase;letter-spacing:.6px;"
                        f"text-align:right'>Modalidad</div>", unsafe_allow_html=True)
        with _v3:
            _modalidad_sel = _seg("Modalidad", ["Todo", "🌐 Online", "🏫 Presencial"],
                                  "roi_modalidad")
        _mod_filter = {"Todo": None, "🌐 Online": "Online",
                       "🏫 Presencial": "Presencial"}[_modalidad_sel]

        # ── Preparar datasets ─────────────────────────────────────────────────
        _leads = df.copy()
        _pipe  = df_pip_full.copy()

        # Los filtros globales del sidebar también aplican al pipeline
        if not _pipe.empty:
            if filtro_fuente:
                _pipe = _pipe[_pipe["fuente"].isin(filtro_fuente)]
            if filtro_pais:
                _pipe = _pipe[_pipe["pais"].isin(filtro_pais)]

        if _mod_filter:
            if not _leads.empty:
                _leads = _leads[_leads["modalidad"].str.contains(_mod_filter, case=False, na=False)]
            if not _pipe.empty:
                _pipe = _pipe[_pipe["modalidad"].str.contains(_mod_filter, case=False, na=False)]

        # ── Leads cualificados del análisis ───────────────────────────────────
        #   · lead_valido == "Válido"  (no basta con "≠ No válido")
        #   · curso informado
        #   · fuera Webinar y Open Day (no son leads comerciales del embudo RST)
        # Quien ha traído una matrícula no se excluye nunca, aunque venga de un
        # webinar o de un open day: es la excepción acordada para no perder de
        # vista negocios cerrados.
        _emails_ganadores = set()
        if not _pipe.empty and "gano_periodo" in _pipe.columns:
            _emails_ganadores = set(
                _pipe.loc[_pipe["gano_periodo"], "email"].dropna().astype(str)) - {""}

        _lv = _leads
        if not _lv.empty:
            _lv = _lv[_lv["lead_valido"] == "Válido"]
            _lv = _lv[_lv["programa"].fillna("").str.strip().ne("")
                      & _lv["programa"].ne("Sin programa")]
            _es_wb = _lv["categoria"].fillna("").str.lower().str.contains(
                "webinar|open day|openday", regex=True)
            _lv = _lv[~_es_wb | _lv["email"].astype(str).isin(_emails_ganadores)]
            # Algunos leads de campañas de webinar llegan con otra categoría
            # (Chatbot, Formulario): se filtran también por el nombre de campaña.
            _lv = _lv[~_lv["campana"].fillna("").apply(webinar_excluible)
                      & ~_lv["campana_reciente"].fillna("").apply(webinar_excluible)]

        if _lv.empty and _pipe.empty:
            st.info("No hay datos para el período y filtros seleccionados.")
            return

        if not _lv.empty:
            _lv = _lv.copy()
            _lv["mercado_lbl"] = _lv["mercado"].map(_merc_lbl)
        if not _pipe.empty:
            _pipe = _pipe.copy()
            _pipe["mercado_lbl"] = _pipe["pais"].apply(_merc_of_pais)
            # Mismo criterio de exclusión en los negocios
            _wb_cat = _pipe["cont_categoria"].fillna("").str.lower().str.contains(
                "webinar|open day|openday", regex=True)
            _pipe = _pipe[~_wb_cat | _pipe["gano_periodo"]]
            if "campaña" in _pipe.columns:
                _pipe = _pipe[~_pipe["campaña"].fillna("").apply(webinar_excluible)]

        # Ganados / perdidos = deals que ENTRARON en la etapa dentro del período
        # (incluyen leads captados en meses anteriores → visión de caja del período)
        _won = (_pipe[_pipe["gano_periodo"]] if "gano_periodo" in _pipe.columns
                else _pipe[_pipe["etapa"].isin(_GANADO_ET)]) \
               if not _pipe.empty else pd.DataFrame()
        # Perdidos: además el contacto se creó en el período (igual que la referencia)
        if not _pipe.empty and "perdio_periodo" in _pipe.columns:
            _lost = _pipe[_pipe["perdio_periodo"] & _pipe["cohorte"]]
        elif not _pipe.empty:
            _lost = _pipe[_pipe["etapa"] == "Cierre Perdido"]
        else:
            _lost = pd.DataFrame()
        # Embudo intermedio: deals de la cohorte de leads del período
        _cohorte = (_pipe[_pipe["cohorte"]] if "cohorte" in _pipe.columns
                    else _pipe) if not _pipe.empty else pd.DataFrame()

        _n_leads   = len(_lv)
        _n_ganados = _won["deal_id"].nunique()    if not _won.empty else 0
        _facturado = float(_won["amount"].sum())  if not _won.empty else 0.0

        def _won_by(key):
            if _won.empty or key not in _won.columns:
                return pd.DataFrame(columns=[key, "ganados", "facturado"])
            return (_won.groupby(key)
                        .agg(ganados=("deal_id", "nunique"), facturado=("amount", "sum"))
                        .reset_index())

        # ══════════════════════════════════════════════════════════════════════
        if _vista == "Cierres perdidos (leads del mes)":
            _render_perdidos(_lv, _pipe, _lost, _n_leads)
            return
        # ══════════════════════════════════════════════════════════════════════

        _conv_glob  = (_n_ganados / _n_leads * 100) if _n_leads else 0
        _fuente_top = _lv["fuente"].value_counts().idxmax() if _n_leads else "—"
        _mc = _lv["mercado_lbl"].value_counts() if _n_leads else pd.Series(dtype=int)
        _pct_nac = _mc.get("Nacional", 0) / _n_leads * 100 if _n_leads else 0
        _pct_lat = _mc.get("LATAM", 0)    / _n_leads * 100 if _n_leads else 0
        _pct_row = 100 - _pct_nac - _pct_lat if _n_leads else 0

        # ── 4 tarjetas KPI ────────────────────────────────────────────────────
        def _big(col, label, value, sub, grad):
            col.markdown(f"""
            <div style="background:{grad};border-radius:14px;padding:16px 20px;
                        min-height:104px;box-shadow:0 1px 4px rgba(16,24,40,.10)">
                <div style="color:rgba(255,255,255,.85);font-size:10.5px;font-weight:800;
                            letter-spacing:.8px;text-transform:uppercase">{label}</div>
                <div style="color:#fff;font-size:36px;font-weight:800;line-height:1.2;
                            margin:1px 0 3px">{value}</div>
                <div style="color:rgba(255,255,255,.88);font-size:12px">{sub}</div>
            </div>""", unsafe_allow_html=True)

        _k1, _k2, _k3, _k4 = st.columns(4)
        _big(_k1, "Contactos del período", _fmt_int(_n_leads),
             f"Válidos · con curso · Fuente top: {_fuente_top}",
             "linear-gradient(135deg,#1414A8 0%,#2A2BC4 100%)")
        _big(_k2, "Ganados", _fmt_int(_n_ganados), _fmt_eur(_facturado),
             "linear-gradient(135deg,#12A56B 0%,#2FBF84 100%)")
        _big(_k3, "Conversión lead → ganado", _fmt_pct(_conv_glob),
             "Ganados / leads del período",
             "linear-gradient(135deg,#EAAB12 0%,#F2BE3F 100%)")
        _big(_k4, "% Leads nacional", f"{_pct_nac:.0f}%",
             f"{_pct_lat:.0f}% LATAM · {_pct_row:.0f}% ROW",
             "linear-gradient(135deg,#0A0A6E 0%,#0D0E95 100%)")
        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

        # ── Contactos por fuente | Conversión por mercado ──────────────────────
        _cl, _cr = st.columns([1, 1.12])

        with _cl:
            with st.container(border=True):
                st.markdown(_sec_title(
                    "Contactos por fuente",
                    "Canal de captación (fuente original del tráfico) · leads válidos con curso"),
                    unsafe_allow_html=True)
                if _n_leads:
                    _fc = _lv["fuente"].value_counts().reset_index()
                    _fc.columns = ["fuente", "n"]
                    _fig = go.Figure(go.Pie(
                        labels=_fc["fuente"], values=_fc["n"], hole=.66, sort=True,
                        marker=dict(colors=_DONUT * 3, line=dict(color="#fff", width=2)),
                        textinfo="none",
                        hovertemplate="<b>%{label}</b><br>%{value} leads (%{percent})<extra></extra>",
                    ))
                    _fig.update_layout(
                        showlegend=False, height=232, margin=dict(t=4, b=4, l=4, r=4),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        annotations=[dict(
                            text=f"<b style='font-size:29px'>{_n_leads}</b><br>"
                                 f"<span style='font-size:9.5px;letter-spacing:1.2px;"
                                 f"color:{_RF['th']}'>CONTACTOS</span>",
                            x=.5, y=.5, showarrow=False, font=dict(color=_RF["ink"]))],
                    )
                    _d1, _d2 = st.columns([1, 1.15])
                    with _d1:
                        st.plotly_chart(_fig, use_container_width=True,
                                        config={"displayModeBar": False})
                    with _d2:
                        _lg = ""
                        for _i, _r in _fc.iterrows():
                            _lg += (
                                f"<div style='display:flex;align-items:center;gap:8px;"
                                f"padding:3.5px 0;font-size:12.5px'>"
                                f"<span style='width:9px;height:9px;border-radius:50%;"
                                f"background:{_DONUT[_i % len(_DONUT)]};flex-shrink:0'></span>"
                                f"<span style='flex:1;color:{_RF['ink_soft']}'>{_r['fuente']}</span>"
                                f"<b style='color:{_RF['ink']}'>{_r['n']}</b>"
                                f"<span style='color:{_RF['muted']};width:36px;"
                                f"text-align:right'>{_r['n'] / _n_leads * 100:.0f}%</span></div>")
                        st.markdown(_lg, unsafe_allow_html=True)

        with _cr:
            with st.container(border=True):
                st.markdown(_sec_title(
                    "Conversión por mercado",
                    "Leads del período vs matrículas (los ganados incluyen leads anteriores)"),
                    unsafe_allow_html=True)
                _lm = (_lv.groupby("mercado_lbl").size().reset_index(name="leads")
                          .rename(columns={"mercado_lbl": "mercado"})
                       if _n_leads else pd.DataFrame(columns=["mercado", "leads"]))
                _wm = (_won.groupby("mercado_lbl")
                           .agg(ganados=("deal_id", "nunique"), facturado=("amount", "sum"))
                           .reset_index().rename(columns={"mercado_lbl": "mercado"})
                       if not _won.empty else
                       pd.DataFrame(columns=["mercado", "ganados", "facturado"]))
                _mt = _lm.merge(_wm, on="mercado", how="outer").fillna(0)
                _mt["o"] = _mt["mercado"].map(
                    {"Nacional": 0, "LATAM": 1, "ROW": 2, "Sin país": 3}).fillna(4)
                _mt = _mt.sort_values("o")

                # Inversión y leads de Ads por mercado, según la nomenclatura
                _inv_merc, _inv_nc = {}, 0.0
                _lad_merc, _lad_nc = {}, 0.0
                _LBL = {"Latam": "LATAM", "Nacional": "Nacional"}
                for _d in [df_google, df_meta, df_linkedin, df_tiktok]:
                    if _d.empty or "mercado_camp" not in _d.columns:
                        continue
                    _agg = _d.groupby("mercado_camp").agg(
                        g=("gasto", "sum"),
                        c=("conversiones", "sum") if "conversiones" in _d.columns
                          else ("gasto", "size"))
                    if "conversiones" not in _d.columns:
                        _agg["c"] = 0
                    for _m, _r in _agg.iterrows():
                        _k = _LBL.get(str(_m))
                        if _k:
                            _inv_merc[_k] = _inv_merc.get(_k, 0.0) + float(_r["g"])
                            _lad_merc[_k] = _lad_merc.get(_k, 0.0) + float(_r["c"])
                        else:
                            _inv_nc += float(_r["g"])
                            _lad_nc += float(_r["c"])

                _rows = []
                for _, r in _mt.iterrows():
                    _cv  = (r["ganados"] / r["leads"] * 100) if r["leads"] else 0
                    _iv  = _inv_merc.get(r["mercado"], 0.0)
                    _la  = _lad_merc.get(r["mercado"], 0.0)
                    _cpl = (_iv / r["leads"]) if (r["leads"] and _iv) else None
                    _rows.append([
                        f"<b>{r['mercado']}</b>", _fmt_eur(_iv),
                        _fmt_eur(_cpl) if _cpl else "—",
                        _fmt_int(_la),
                        _fmt_int(r["leads"]),
                        f"<b>{_fmt_int(r['ganados'])}</b>", _pill_conv(_cv),
                        _fmt_eur(r["facturado"]),
                    ])
                _tl, _tg = _mt["leads"].sum(), _mt["ganados"].sum()
                _ti = sum(_inv_merc.values()) + _inv_nc
                _cols_m = [("Mercado", "left"), ("Inversión", "right"), ("CPL", "right"),
                           ("Leads Ads", "right"), ("Leads CRM", "right"),
                           ("Ganados", "right"), ("% Conversión", "center"),
                           ("Facturado", "right")]
                _tot_m = ["Total", _fmt_eur(_ti),
                     _fmt_eur(_ti / _tl) if (_tl and _ti) else "—",
                     _fmt_int(sum(_lad_merc.values()) + _lad_nc),
                     _fmt_int(_tl), _fmt_int(_tg),
                     _pill_conv(_tg / _tl * 100 if _tl else 0),
                     _fmt_eur(_mt["facturado"].sum())]
                st.markdown(_table(_cols_m, _rows, _tot_m), unsafe_allow_html=True)
                _barra_tabla("Conversión por mercado", _cols_m, _rows, _tot_m,
                             ampliable=False)
                if _inv_nc:
                    st.markdown(
                        f"<div style='font-size:12px;color:{_RF['muted']};margin-top:8px'>"
                        f"⚠️ {_fmt_eur(_inv_nc)} en campañas cuyo nombre no indica mercado "
                        f"(sin ES/CAT/NAC ni LATAM/LAT) — no se reparten por mercado, "
                        f"pero sí cuentan en el total.</div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div style='font-size:12px;color:{_RF['muted']};margin-top:10px'>"
                    f"<b style='color:{_RF['ink_soft']}'>Mercado</b> de los leads según el "
                    f"país del contacto (España → Nacional · Latinoamérica → LATAM · "
                    f"resto → ROW) · <b style='color:{_RF['ink_soft']}'>Inversión y Leads "
                    f"Ads</b> según la nomenclatura de la campaña (NAC/CAT/ES → Nacional · "
                    f"LAT/LATAM → LATAM) · CPL calculado sobre los leads del CRM.</div>",
                    unsafe_allow_html=True)

        # ── Conversión por país | por curso ───────────────────────────────────
        _pl, _pr = st.columns([1, 1.12])

        with _pl:
            with st.container(border=True):
                st.markdown(_sec_title(
                    "Conversión por país",
                    "Leads del período vs matrículas por país del contacto"),
                    unsafe_allow_html=True)
                _lp = (_lv.groupby("pais").size().reset_index(name="leads")
                       if _n_leads else pd.DataFrame(columns=["pais", "leads"]))
                _pt = _lp.merge(_won_by("pais"), on="pais", how="outer").fillna(0)
                if not _pt.empty:
                    _pt["merc"] = _pt["pais"].apply(_merc_of_pais)
                    _pt = _pt.sort_values(["leads", "facturado"], ascending=False)
                    _rows = []
                    for _, r in _pt.iterrows():
                        _cv = (r["ganados"] / r["leads"] * 100) if r["leads"] else 0
                        _rows.append([
                            f"<b>{r['pais']}</b>", _pill_merc(r["merc"]),
                            _fmt_int(r["leads"]), _fmt_int(r["ganados"]),
                            _pill_conv(_cv), _fmt_eur(r["facturado"]),
                        ])
                    _tl3, _tg3 = _pt["leads"].sum(), _pt["ganados"].sum()
                    _tabla_ordenable(
                        [("País", "left"), ("Mercado", "center"), ("Leads", "right"),
                         ("Ganados", "right"), ("% Conv.", "center"), ("Facturado", "right")],
                        _rows, altura=_ALTO_PAR, nombre="Conversión por país",
                        total=["Total", "", _fmt_int(_tl3), _fmt_int(_tg3),
                               _pill_conv(_tg3 / _tl3 * 100 if _tl3 else 0),
                               _fmt_eur(_pt["facturado"].sum())])
                    st.markdown(
                        f"<div style='font-size:12px;color:{_RF['muted']};margin-top:6px'>"
                        f"Haz clic en las cabeceras para ordenar.</div>",
                        unsafe_allow_html=True)

        with _pr:
            with st.container(border=True):
                st.markdown(_sec_title(
                    "Conversión por curso",
                    "Leads del período vs matrículas por curso · con facturación"),
                    unsafe_allow_html=True)
                _mt2 = _seg("Mercado", ["Todo", "Mercado Nacional", "Mercado LATAM",
                                        "Mercado ROW"], "roi_curso_merc")
                _mkey = {"Todo": None, "Mercado Nacional": "Nacional",
                         "Mercado LATAM": "LATAM", "Mercado ROW": "ROW"}[_mt2]
                _lvc = _lv if _mkey is None else _lv[_lv["mercado_lbl"] == _mkey]
                _wc  = _won if (_mkey is None or _won.empty) else _won[_won["mercado_lbl"] == _mkey]

                _lc = (_lvc.groupby(["programa", "modalidad"]).size().reset_index(name="leads")
                       if not _lvc.empty else
                       pd.DataFrame(columns=["programa", "modalidad", "leads"]))
                _wcg = (_wc.groupby("programa")
                           .agg(ganados=("deal_id", "nunique"), facturado=("amount", "sum"))
                           .reset_index() if not _wc.empty else
                        pd.DataFrame(columns=["programa", "ganados", "facturado"]))
                _ct = _lc.merge(_wcg, on="programa", how="outer")
                _ct[["leads", "ganados", "facturado"]] = _ct[
                    ["leads", "ganados", "facturado"]].fillna(0)
                _ct["modalidad"] = _ct["modalidad"].fillna("(Sin modalidad)")
                if not _ct.empty:
                    _ct = _ct.sort_values(["facturado", "leads"], ascending=False)
                    _rows = []
                    for _, r in _ct.iterrows():
                        _cv = (r["ganados"] / r["leads"] * 100) if r["leads"] else 0
                        _md = str(r["modalidad"])
                        _mp = _pill(_md, "blue" if "online" in _md.lower() else "gray")
                        _rows.append([
                            f"<b>{r['programa']}</b>", _mp, _fmt_int(r["leads"]),
                            _fmt_int(r["ganados"]), _pill_conv(_cv), _fmt_eur(r["facturado"]),
                        ])
                    _tl2, _tg2 = _ct["leads"].sum(), _ct["ganados"].sum()
                    _tabla_ordenable(
                        [("Curso", "left"), ("Modalidad", "center"), ("Leads", "right"),
                         ("Ganados", "right"), ("% Conv.", "center"), ("Facturado", "right")],
                        _rows, altura=_ALTO_PAR, nombre="Conversión por curso",
                        total=[f"Total {_mt2}", "", _fmt_int(_tl2), _fmt_int(_tg2),
                               _pill_conv(_tg2 / _tl2 * 100 if _tl2 else 0),
                               _fmt_eur(_ct["facturado"].sum())])
                    st.markdown(
                        f"<div style='font-size:12px;color:{_RF['muted']};margin-top:10px'>"
                        f"Haz clic en las cabeceras para ordenar · Vista: "
                        f"<b style='color:{_RF['ink_soft']}'>{_mt2}</b> según el país "
                        f"del contacto. Los ganados incluyen leads captados en meses "
                        f"anteriores, por lo que un curso puede mostrar matrículas sin leads "
                        f"nuevos en el período.</div>", unsafe_allow_html=True)

        # ── ROI y ROAS por modalidad ──────────────────────────────────────────
        with st.container(border=True):
            st.markdown(_sec_title(
                "💶 ROI y ROAS por modalidad de formación",
                "Gasto de Google Ads, Meta, LinkedIn y TikTok repartido por el nombre de "
                "la campaña: si contiene «Online» es Online, el resto es Presencial"),
                unsafe_allow_html=True)

            _ads_list = [d for d in [df_google, df_meta, df_linkedin, df_tiktok] if not d.empty]
            _ads_all = pd.concat(_ads_list, ignore_index=True) if _ads_list else pd.DataFrame()
            _ga  = {"Online": 0.0, "Presencial": 0.0, "Sin asignar": 0.0}
            _lad = {"Online": 0.0, "Presencial": 0.0, "Sin asignar": 0.0}
            if not _ads_all.empty and "modalidad_camp" in _ads_all.columns:
                if "conversiones" not in _ads_all.columns:
                    _ads_all = _ads_all.assign(conversiones=0.0)
                _agg = _ads_all.groupby("modalidad_camp").agg(
                    g=("gasto", "sum"), c=("conversiones", "sum"))
                for _m, _r in _agg.iterrows():
                    _ga[str(_m)]  = _ga.get(str(_m), 0.0) + float(_r["g"])
                    _lad[str(_m)] = _lad.get(str(_m), 0.0) + float(_r["c"])

            if gasto_webinars:
                st.markdown(
                    f"<div style='font-size:12.5px;color:{_RF['muted']};"
                    f"background:{_RF['line']};border-radius:8px;padding:9px 12px;"
                    f"margin:0 0 12px'>ℹ️ Se han excluido "
                    f"<b style='color:{_RF['ink_soft']}'>{_fmt_eur(gasto_webinars)}</b> en "
                    f"{len(camps_webinars)} campañas que no son de captación RST —webinar, "
                    f"open day, tráfico y notoriedad—: sus leads tampoco entran en el análisis "
                    f"y contar su gasto inflaría el CPL del resto. Las que sí han traído alguna "
                    f"matrícula se mantienen dentro, con su gasto y sus leads.</div>", unsafe_allow_html=True)

            _g1, _g2, _g3 = st.columns(3)
            with _g1:
                _gon = st.number_input("Gasto Ads · Online (€)", min_value=0.0, step=100.0,
                                       value=round(_ga["Online"], 2), key="roi_gasto_on")
            with _g2:
                _gpr = st.number_input("Gasto Ads · Presencial (€)", min_value=0.0, step=100.0,
                                       value=round(_ga["Presencial"], 2), key="roi_gasto_pr")
            with _g3:
                _n_camp = _ads_all["campaña"].nunique() if not _ads_all.empty else 0
                _sin_a  = _ga.get("Sin asignar", 0.0)
                st.markdown(
                    f"<div style='padding-top:6px'><div style='font-size:10.5px;"
                    f"font-weight:700;color:{_RF['th']};text-transform:uppercase;"
                    f"letter-spacing:.6px'>Campañas clasificadas</div>"
                    f"<div style='font-size:20px;font-weight:800;color:{_RF['ink']};"
                    f"margin-top:4px'>{_n_camp}</div>"
                    f"<div style='font-size:11.5px;color:{_RF['muted']}'>"
                    + (f"Sin nombre: {_fmt_eur(_sin_a)}" if _sin_a else
                       "Todo el gasto queda asignado por nomenclatura")
                    + f"</div></div>", unsafe_allow_html=True)

            _rows, _tl3, _tm3 = [], 0, 0
            for _mod, _g, _ic in [("Online", _gon, "🌐"), ("Presencial", _gpr, "🏫")]:
                _l = int(_lv["modalidad"].str.contains(_mod, case=False, na=False).sum()) \
                     if _n_leads else 0
                if not _won.empty:
                    _ws = _won[_won["modalidad"].str.contains(_mod, case=False, na=False)]
                    _m, _f = _ws["deal_id"].nunique(), float(_ws["amount"].sum())
                else:
                    _m, _f = 0, 0.0
                _tl3 += _l
                _tm3 += _m
                _roi = ((_f - _g) / _g * 100) if _g else None
                _rows.append([
                    f"<b>{_mod}</b> {_ic}", _fmt_int(_lad.get(_mod, 0)),
                    _fmt_int(_l), f"<b>{_fmt_int(_m)}</b>",
                    _fmt_eur(_f),
                    _fmt_eur(_g) if _g else
                        f"<span style='color:{_RF['muted']};font-style:italic'>introduce gasto</span>",
                    _fmt_eur(_g / _m) if (_m and _g) else "—",
                    _fmt_eur(_g / _l) if (_l and _g) else "—",
                    f"{_f / _g:.2f}x".replace(".", ",") if _g else "—",
                    _pill(_fmt_pct(_roi), "green" if _roi and _roi > 0 else "red")
                        if _roi is not None else "—",
                ])
            _tg3 = _gon + _gpr
            _troi = ((_facturado - _tg3) / _tg3 * 100) if _tg3 else None
            _cols_r = [("Modalidad", "left"), ("Leads Ads", "right"),
                       ("Leads CRM", "right"), ("Matrículas", "right"),
                       ("Facturado", "right"), ("Gasto Ads", "right"),
                       ("Coste/matrícula", "right"), ("CPL", "right"),
                       ("ROAS", "right"), ("ROI", "center")]
            _tot_r = ["Total", _fmt_int(sum(_lad.values())),
                 _fmt_int(_tl3), _fmt_int(_tm3), _fmt_eur(_facturado),
                 _fmt_eur(_tg3), _fmt_eur(_tg3 / _tm3) if (_tm3 and _tg3) else "—",
                 _fmt_eur(_tg3 / _tl3) if (_tl3 and _tg3) else "—",
                 f"{_facturado / _tg3:.2f}x".replace(".", ",") if _tg3 else "—",
                 _pill(_fmt_pct(_troi), "green" if _troi and _troi > 0 else "red")
                     if _troi is not None else "—"]
            st.markdown(_table(_cols_r, _rows, _tot_r), unsafe_allow_html=True)
            _barra_tabla("ROI y ROAS por modalidad", _cols_r, _rows, _tot_r,
                         ampliable=False)
            st.markdown(
                f"<div style='font-size:12px;color:{_RF['muted']};margin-top:10px'>"
                f"ROAS = facturado / gasto · ROI = (facturado − gasto) / gasto · "
                f"CPL sobre los leads del CRM. "
                f"<b style='color:{_RF['ink_soft']}'>Leads Ads</b> son las conversiones que "
                f"reporta cada plataforma y <b style='color:{_RF['ink_soft']}'>Leads CRM</b> "
                f"los que llegaron a HubSpot como válidos: la diferencia mide el desfase de "
                f"medición. Las matrículas del período incluyen leads captados anteriormente, "
                f"por lo que el ROAS es una aproximación de caja, no de cohorte.</div>", unsafe_allow_html=True)

        # ── Tabla maestra ─────────────────────────────────────────────────────
        _render_maestra(_lv, _cohorte, _won, _n_leads)

        # ── Detalle de campaña por canal ──────────────────────────────────────
        if _n_leads:
            _cd = _lv.copy()
            _cd["detalle"] = (_cd["campana"].replace(
                                  {"": pd.NA, "Sin campaña": pd.NA})
                              .fillna(_cd["campana_reciente"].replace(
                                  {"": pd.NA, "Sin campaña": pd.NA}))
                              .fillna("(sin campaña)"))
            _cdg = (_cd.groupby(["fuente", "detalle"]).size().reset_index(name="leads")
                       .sort_values("leads", ascending=False))
            _cdg["merc"] = _cdg["detalle"].apply(_mercado_from_name)
            _cdg["pct"]  = _cdg["leads"] / _n_leads * 100
            _mx = float(_cdg["pct"].max())
            _rows = [[
                r["fuente"],
                f"<span style='color:{_RF['ink_soft']}'>{r['detalle']}</span>",
                _pill_merc(r["merc"]), f"<b>{r['leads']}</b>", _fmt_pct(r["pct"]),
                _bar(r["pct"], _MERC_COLOR.get(r["merc"], "#5B8DEF"), _mx),
            ] for _, r in _cdg.iterrows()]
            with st.container(border=True):
                st.markdown(_sec_title(
                    "Detalle de campaña por canal",
                    "Fuente original + drill-down de campaña · ordenado por leads · "
                    "mercado inferido del naming (nac_/latam_)"), unsafe_allow_html=True)
                _tabla_ordenable(
                    [("Canal", "left"), ("Detalle campaña", "left"), ("Mercado", "center"),
                     ("Leads", "right"), ("%", "right"), ("Volumen", "left")],
                    _rows, altura=560, nombre="Detalle de campaña por canal")


        # ── Descarga completa en Excel ────────────────────────────────────────
        if _EXPORT:
            try:
                from io import BytesIO
                _buf = BytesIO()
                with pd.ExcelWriter(_buf, engine="openpyxl") as _xl:
                    for _nom, _dfx in _EXPORT:
                        # Excel limita el nombre de hoja a 31 caracteres
                        _dfx.to_excel(_xl, sheet_name=_nom[:31], index=False)
                _e1, _e2 = st.columns([1.6, 5])
                with _e1:
                    st.download_button(
                        f"📊 Descargar todo en Excel ({len(_EXPORT)} hojas)",
                        _buf.getvalue(),
                        file_name=f"hofmann_roi_{fi}_{ff}.xlsx",
                        mime=("application/vnd.openxmlformats-officedocument."
                              "spreadsheetml.sheet"),
                        use_container_width=True, key="dl_excel_roi")
                with _e2:
                    st.caption("Un libro con una hoja por tabla, tal como se ven "
                               "en pantalla y con el período aplicado.")
            except Exception as _e:
                st.caption(f"No se ha podido preparar el Excel: {_e}")

        # ── Footer de fuentes ─────────────────────────────────────────────────
        _cd_ = lambda t: (f"<code style='background:{_RF['line']};color:{_RF['ink_soft']};"
                          f"padding:1px 5px;border-radius:4px;font-size:11.5px'>{t}</code>")
        _canales = ["HubSpot CRM"]
        if GA_AVAILABLE:       _canales.append("Google Ads")
        if META_AVAILABLE:     _canales.append("Meta Ads")
        if LINKEDIN_AVAILABLE: _canales.append("LinkedIn Ads")
        if TIKTOK_AVAILABLE:   _canales.append("TikTok Ads")
        st.markdown(
            f"<div style='font-size:11.5px;color:{_RF['muted']};line-height:1.9;"
            f"margin-top:6px'>"
            f"<b style='color:{_RF['ink_soft']}'>Fuentes:</b> {' · '.join(_canales)} · "
            f"Período: {periodo_txt} · <b style='color:{_RF['ink_soft']}'>Leads:</b> "
            f"creados en el período con {_cd_('lead_valido = &quot;Válido&quot;')} y "
            f"{_cd_('curso')} informado, excluyendo Webinar y Open&nbsp;Day · "
            f"<b style='color:{_RF['ink_soft']}'>Ganados:</b> deals que entraron en "
            f"{_cd_('Cierre Ganado')} dentro del período según "
            f"{_cd_('hs_v2_date_entered_closedwon')} — incluyen leads de meses anteriores, "
            f"por eso es una visión de caja y no de cohorte · "
            f"<b style='color:{_RF['ink_soft']}'>Perdidos:</b> deals que entraron en "
            f"{_cd_('Cierre Perdido')} en el período y cuyo contacto se creó en el período · "
            f"Etapas intermedias del embudo: deals de la cohorte de leads del período · "
            f"Modalidad según {_cd_('modalidad_curso')} del contacto · "
            f"Importes en moneda de la cuenta (EUR) · "
            f"Inversión publicitaria vía API de cada plataforma, agregada por nombre de campaña."
            f"</div>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # VISTA — Cierres perdidos
    # ══════════════════════════════════════════════════════════════════════════
    def _render_perdidos(_lv, _pipe, _lost, _n_leads):
        if _lost.empty:
            st.info("No hay deals en Cierre Perdido para el período y filtros seleccionados.")
            return
        _n_perd = _lost["deal_id"].nunique()
        _f_perd = float(_lost["amount"].sum())
        _mot_top = _lost["motivo_cierre"].value_counts().idxmax()

        def _big(col, label, value, sub, grad):
            col.markdown(f"""
            <div style="background:{grad};border-radius:14px;padding:16px 20px;
                        min-height:104px;box-shadow:0 1px 4px rgba(16,24,40,.10)">
                <div style="color:rgba(255,255,255,.85);font-size:10.5px;font-weight:800;
                            letter-spacing:.8px;text-transform:uppercase">{label}</div>
                <div style="color:#fff;font-size:36px;font-weight:800;line-height:1.2;
                            margin:1px 0 3px">{value}</div>
                <div style="color:rgba(255,255,255,.88);font-size:12px">{sub}</div>
            </div>""", unsafe_allow_html=True)

        _k1, _k2, _k3, _k4 = st.columns(4)
        _big(_k1, "Cierres perdidos", _fmt_int(_n_perd), "Deals en Cierre Perdido",
             "linear-gradient(135deg,#C0392B 0%,#E05A47 100%)")
        _big(_k2, "% sobre leads válidos",
             _fmt_pct(_n_perd / _n_leads * 100 if _n_leads else 0),
             f"de {_fmt_int(_n_leads)} leads del período",
             "linear-gradient(135deg,#EAAB12 0%,#F2BE3F 100%)")
        _big(_k3, "Facturación perdida", _fmt_eur(_f_perd), "Importe de los deals perdidos",
             "linear-gradient(135deg,#0A0A6E 0%,#0D0E95 100%)")
        _big(_k4, "Motivo principal",
             f"<span style='font-size:19px;line-height:1.25'>{_mot_top}</span>",
             f"{_lost['motivo_cierre'].value_counts().iloc[0]} deals",
             "linear-gradient(135deg,#1414A8 0%,#2A2BC4 100%)")
        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

        # Motivos de cierre
        _mg = (_lost.groupby("motivo_cierre")
                    .agg(deals=("deal_id", "nunique"), importe=("amount", "sum"))
                    .reset_index().sort_values("deals", ascending=False))
        _mx = float(_mg["deals"].max())
        _rows = [[
            f"<b>{r['motivo_cierre']}</b>", _fmt_int(r["deals"]),
            _pill_neg(r["deals"] / _n_perd * 100),
            _fmt_pct(r["deals"] / _n_leads * 100 if _n_leads else 0),
            _fmt_eur(r["importe"]), _bar(r["deals"], "#E05A47", _mx),
        ] for _, r in _mg.iterrows()]
        _card(_sec_title(
            "❌ Motivos de cierre perdido",
            "Desglose de por qué se pierden los negocios · % sobre perdidos y sobre "
            "leads válidos del período") + _table(
            [("Motivo", "left"), ("Deals", "right"), ("% de perdidos", "center"),
             ("% de leads", "right"), ("Importe perdido", "right"), ("Volumen", "left")],
            _rows,
            ["Total", _fmt_int(_n_perd), _pill("100%", "gray"),
             _fmt_pct(_n_perd / _n_leads * 100 if _n_leads else 0),
             _fmt_eur(_f_perd), ""]))

        # Perdidos por fuente / país / curso
        for _titulo, _sub, _col, _lbl in [
            ("Perdidos por fuente", "Canal de captación de los deals perdidos",
             "fuente", "Fuente"),
            ("Perdidos por país", "País del contacto de los deals perdidos",
             "pais", "País"),
            ("Perdidos por curso", "Programa de los deals perdidos", "programa", "Curso"),
        ]:
            if _col not in _lost.columns:
                continue
            _g = (_lost.groupby(_col)
                       .agg(deals=("deal_id", "nunique"), importe=("amount", "sum"))
                       .reset_index().sort_values("deals", ascending=False))
            _leads_by = (_lv.groupby(_col).size().to_dict()
                         if not _lv.empty and _col in _lv.columns else {})
            _rows = []
            for _, r in _g.iterrows():
                _nl = _leads_by.get(r[_col], 0)
                _rows.append([
                    f"<b>{r[_col]}</b>", _fmt_int(_nl), _fmt_int(r["deals"]),
                    _pill_neg(r["deals"] / _nl * 100 if _nl else 0),
                    _fmt_eur(r["importe"]),
                ])
            _card(_sec_title(_titulo, _sub) + _table(
                [(_lbl, "left"), ("Leads período", "right"), ("Perdidos", "right"),
                 ("% Pérdida", "center"), ("Importe perdido", "right")], _rows))

    # ══════════════════════════════════════════════════════════════════════════
    # TABLA MAESTRA — embudo completo con selector multidimensional cruzado
    # ══════════════════════════════════════════════════════════════════════════
    def _render_maestra(_lv, _pipe, _won_periodo, _n_leads):
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#0A0A6E 0%,#1414A8 100%);
                    border-radius:14px;padding:20px 24px;margin:6px 0 16px;
                    border-left:5px solid #EAAB12">
            <div style="color:#fff;font-size:20px;font-weight:800">
                🔎 Tabla maestra — todo el negocio en una tabla
            </div>
            <div style="color:#C8C8F2;margin:5px 0 0;font-size:13px">
                Inversión → CPL → leads cualificados → entrevista → envío de inscripción →
                cierre ganado → ROI · y el lado a corregir: ilocalizados, no se presenta,
                motivos de cierre y cierre perdido
            </div>
        </div>""", unsafe_allow_html=True)

        _DIMS = {"Fuente": "fuente", "Campaña": "campaña",
                 "País": "pais", "Producto": "programa"}

        _ml = _lv.copy()
        _mp = _pipe.copy()
        _mw = _won_periodo.copy()
        if not _ml.empty:
            _ml["campaña"] = (_ml["campana"].replace(
                                  {"": pd.NA, "Sin campaña": pd.NA})
                              .fillna(_ml["campana_reciente"].replace("", pd.NA))
                              .fillna("Sin campaña"))

        _s1, _s2 = st.columns([1.25, 2])
        with _s1:
            _dims_sel = st.multiselect(
                "Agrupar por (1 o 2 dimensiones)", list(_DIMS),
                default=["Fuente", "Campaña"],
                max_selections=2, key="roi_dims",
                help="Elige una o dos dimensiones: serán las primeras columnas de la tabla.")
        if not _dims_sel:
            _dims_sel = ["Fuente"]
        _gcols = [_DIMS[d] for d in _dims_sel]

        # Filtros cruzados para las dimensiones que no agrupan
        _libres = [d for d in _DIMS if d not in _dims_sel]
        if _libres:
            with _s2:
                _fcols = st.columns(len(_libres))
            for _i, _d in enumerate(_libres):
                _c = _DIMS[_d]
                _opts = (["Todas"] + sorted(
                    x for x in _ml[_c].dropna().astype(str).unique() if x) ) \
                    if (not _ml.empty and _c in _ml.columns) else ["Todas"]
                with _fcols[_i]:
                    _v = st.selectbox(_d, _opts, key=f"roi_x_{_c}")
                if _v != "Todas":
                    if not _ml.empty and _c in _ml.columns:
                        _ml = _ml[_ml[_c].astype(str) == _v]
                    if not _mp.empty and _c in _mp.columns:
                        _mp = _mp[_mp[_c].astype(str) == _v]
                    if not _mw.empty and _c in _mw.columns:
                        _mw = _mw[_mw[_c].astype(str) == _v]

        _inv_key = "roi_inv_" + "|".join(_dims_sel)
        st.session_state.setdefault(_inv_key, {})

        _ET = {
            "entrevista": ["Entrevista Realizada"],
            "envio":      ["Envío de Inscripción", "Estudio Financiación",
                           "Pendiente Transferencia"],
            "ganado":     _GANADO_ET,
            "iloc":       ["Ilocalizado"],
            "nopres":     ["No se presenta"],
            "perdido":    ["Cierre Perdido"],
        }
        _motivos = (sorted(_mp[_mp["etapa"] == "Cierre Perdido"]["motivo_cierre"]
                           .dropna().unique()) if not _mp.empty else [])

        # Claves de agrupación presentes en cualquiera de los dos datasets
        def _keys(frame):
            if frame.empty or not all(c in frame.columns for c in _gcols):
                return set()
            return set(map(tuple, frame[_gcols].astype(str).values))
        _all_keys = sorted(_keys(_ml) | _keys(_mp) | _keys(_mw))
        if not _all_keys:
            st.info("No hay datos para los filtros seleccionados.")
            return

        def _mask(frame, key):
            if frame.empty or not all(c in frame.columns for c in _gcols):
                return frame.iloc[0:0]
            m = pd.Series(True, index=frame.index)
            for c, v in zip(_gcols, key):
                m &= frame[c].astype(str) == v
            return frame[m]

        # ── Inversión automática desde las APIs de Ads ─────────────────────────
        _srcs = [(df_google, "Búsqueda pagada"), (df_meta, "Social pagado"),
                 (df_linkedin, "Social pagado"), (df_tiktok, "Social pagado")]
        _spend, _sin_casar = {}, 0.0
        _cual_key = {k: len(_mask(_ml, k)) for k in _all_keys}

        def _sk_de(key):
            return key[0] if len(key) == 1 else " | ".join(key)

        _n_casadas = _n_ads_camps = 0
        _diag_map, _gasto_ads, _map_camp, _asignadas = [], {}, {}, set()
        if "campaña" in _gcols:
            # Gasto por campaña, separado por tipo de plataforma: las campañas de
            # búsqueda y de social comparten producto (hay un "Diploma de Pastelería"
            # en Google y otro en Meta), así que mezclarlas produciría empates.
            def _gasto_de(dfs):
                out = {}
                for _d in dfs:
                    if _d.empty or "campaña" not in _d.columns:
                        continue
                    for _c, _g in _d.groupby("campaña")["gasto"].sum().items():
                        if _c:
                            out[str(_c)] = out.get(str(_c), 0.0) + float(_g)
                return out

            # Google declara su utm_campaign en la plantilla de seguimiento, así
            # que ahí no hace falta adivinar por parecido: se casa exacto.
            _utm_decl = {}
            for _d in (df_google, df_meta, df_linkedin, df_tiktok):
                if _d.empty or "utm_declarada" not in _d.columns:
                    continue
                for _u, _c in zip(_d["utm_declarada"], _d["campaña"]):
                    _u = str(_u or "").strip().lower()
                    if _u:
                        _utm_decl.setdefault(_u, str(_c))

            # Un conjunto de candidatos por plataforma. Mezclarlas provoca empates:
            # "Webinar_Julio26_Vinos" existe en Meta y "Webinar_vinos_julio2026" en
            # TikTok, y ambas competían por la misma utm_campaign.
            _pools = {"Google Ads":   _gasto_de([df_google]),
                      "Meta Ads":     _gasto_de([df_meta]),
                      "LinkedIn Ads": _gasto_de([df_linkedin]),
                      "TikTok Ads":   _gasto_de([df_tiktok])}
            _gasto_ads = {}
            for _p, _d in _pools.items():
                for _c, _g in _d.items():
                    _gasto_ads[(_p, _c)] = _g
            _n_ads_camps = len(_gasto_ads)

            # Plataformas de cada campaña de HubSpot: la fuente distingue búsqueda de
            # social y, dentro del social, red_social dice si fue Facebook, LinkedIn
            # o TikTok. Una misma utm_campaign puede haberse usado en varias.
            _RED_PLAT = {"facebook": "Meta Ads", "instagram": "Meta Ads",
                         "meta": "Meta Ads", "fb": "Meta Ads",
                         "linkedin": "LinkedIn Ads", "tiktok": "TikTok Ads"}
            _SOCIALES = ["Meta Ads", "LinkedIn Ads", "TikTok Ads"]
            _plats_camp = {}
            if not _ml.empty and "campaña" in _ml.columns:
                for _c, _sub in _ml.groupby("campaña"):
                    _ps = set()
                    _reds = (_sub["red_social"] if "red_social" in _sub.columns
                             else pd.Series([""] * len(_sub)))
                    for _f, _r in zip(_sub["fuente"], _reds):
                        if _f == "Búsqueda pagada":
                            _ps.add("Google Ads")
                        elif _f == "Social pagado":
                            _pl = _RED_PLAT.get(str(_r).strip().lower())
                            _ps.update([_pl] if _pl else _SOCIALES)
                    _plats_camp[str(_c)] = _ps

            _idx = _gcols.index("campaña")
            _map_camp, _diag_map = {}, []      # (utm, plataforma) → campaña de Ads
            for _c in sorted({k[_idx] for k in _all_keys}):
                _ps = _plats_camp.get(_c) or list(_pools)
                _hechas = []
                # 1º alias confirmado · 2º utm declarada por la plataforma ·
                # 3º parecido de nombre
                _cl = str(_c).strip().lower()
                _exacta = _utm_decl.get(_cl)
                for _p in _ps:
                    _pool_p = _pools.get(_p, {})
                    _ali = _ALIAS_UTM.get((_p, _cl))
                    if _ali and _ali in _pool_p:
                        _m = _ali
                    elif _exacta in _pool_p:
                        _m = _exacta
                    else:
                        _m = emparejar_campana(_c, list(_pool_p))
                    if _m:
                        _map_camp[(_c, _p)] = _m
                        _hechas.append((_p, _m))
                _diag_map.append((_c, ", ".join(sorted(_ps)) or "—", _hechas))
            _casadas = set(_map_camp.items())
            _asignadas = {(_p, _m) for (_c, _p), _m in _map_camp.items()}
            _n_casadas = len(_asignadas)
            _sin_casar = sum(g for k, g in _gasto_ads.items() if k not in _asignadas)

            # Una campaña de Ads suele generar varias UTM (_video, _sinconv, _v2…) y
            # además cada UTM puede caer en varias filas si se agrupa por dos
            # dimensiones. Su gasto se reparte proporcionalmente a los leads de cada
            # fila entre todo lo que apunta a ella, para no contarlo dos veces.
            _leads_ads, _filas_ads = {}, {}
            for _key in _all_keys:
                for (_c, _p), _a in _map_camp.items():
                    if _c != _key[_idx]:
                        continue
                    _leads_ads[(_p, _a)] = _leads_ads.get((_p, _a), 0) + _cual_key[_key]
                    _filas_ads[(_p, _a)] = _filas_ads.get((_p, _a), 0) + 1
            for _key in _all_keys:
                _acum = 0.0
                for (_c, _p), _a in _map_camp.items():
                    if _c != _key[_idx]:
                        continue
                    _g = _gasto_ads.get((_p, _a), 0.0)
                    if not _g:
                        continue
                    _tl = _leads_ads.get((_p, _a), 0)
                    _acum += (_g * _cual_key[_key] / _tl) if _tl \
                             else (_g / max(_filas_ads.get((_p, _a), 1), 1))
                if _acum:
                    _spend[_sk_de(_key)] = _acum
        elif _gcols == ["fuente"]:
            for _d, _lbl in _srcs:
                if not _d.empty and "gasto" in _d.columns:
                    _spend[_lbl] = _spend.get(_lbl, 0.0) + float(_d["gasto"].sum())

        _rows, _data = [], []
        for _key in _all_keys:
            _sl, _sp = _mask(_ml, _key), _mask(_mp, _key)
            _sw = _mask(_mw, _key)          # ganados que entraron en el período
            _cual = len(_sl)
            _cnt = lambda k: (_sp[_sp["etapa"].isin(_ET[k])]["deal_id"].nunique()
                              if not _sp.empty else 0)
            _amt = lambda k: (float(_sp[_sp["etapa"].isin(_ET[k])]["amount"].sum())
                              if not _sp.empty else 0.0)
            _entr, _env       = _cnt("entrevista"), _cnt("envio")
            _il, _np_, _perd  = _cnt("iloc"), _cnt("nopres"), _cnt("perdido")
            _pend, _fperd     = _amt("envio"), _amt("perdido")
            # Ganados y facturado: visión de caja del período (cualquier cohorte)
            _gan  = _sw["deal_id"].nunique()   if not _sw.empty else 0
            _fact = float(_sw["amount"].sum()) if not _sw.empty else 0.0

            _sk = _key[0] if len(_key) == 1 else " | ".join(_key)
            _inv = st.session_state[_inv_key].get(_sk, _spend.get(_sk, 0.0))
            _cpl = (_inv / _cual) if (_cual and _inv) else None
            _roi = ((_fact - _inv) / _inv * 100) if _inv else None
            _p   = lambda n: (n / _cual * 100) if _cual else 0

            _d = {"key": _sk, "cual": _cual, "inv": _inv, "gan": _gan, "fact": _fact}
            _data.append(_d)

            # Campañas que traen leads pero no gastan en el período: suelen ser
            # pausadas, o cohortes antiguas que siguen cerrando.
            _etiq = list(_key)
            if "campaña" in _gcols and not _inv and (_cual or _gan):
                _i = _gcols.index("campaña")
                _etiq[_i] = (f"{_etiq[_i]} <span style='color:{_RF['muted']};"
                             f"font-size:11.5px'>(*pausada o sin inversión)</span>")
            _row = _etiq + [
                _fmt_eur(_inv), _fmt_eur(_cpl) if _cpl else "—", f"<b>{_fmt_int(_cual)}</b>",
                _fmt_int(_entr), _pill_conv(_p(_entr)),
                _fmt_int(_env), _pill_conv(_p(_env)), _fmt_eur(_pend),
                f"<b>{_fmt_int(_gan)}</b>", _pill_conv(_p(_gan)), _fmt_eur(_fact),
                _pill(_fmt_pct(_roi), "green" if _roi > 0 else "red") if _roi is not None else "—",
                _fmt_int(_il), _pill_neg(_p(_il)),
                _fmt_int(_np_), _pill_neg(_p(_np_)),
            ]
            for _m in _motivos:
                _n = (_sp[(_sp["etapa"] == "Cierre Perdido") &
                          (_sp["motivo_cierre"] == _m)]["deal_id"].nunique()
                      if not _sp.empty else 0)
                _row.append(_pill_neg(_p(_n)) if _n else
                            f"<span style='color:{_RF['muted']}'>—</span>")
            _row += [_fmt_int(_perd), _fmt_eur(_fperd)]
            _rows.append((_cual, _row))

        _rows.sort(key=lambda t: -t[0])
        _rows = [r for _, r in _rows]

        _cols = [(d, "left") for d in _dims_sel] + [
            ("Inversión", "right"), ("CPL", "right"), ("Leads cualif.", "right"),
            ("Entrevistas", "right"), ("% Entrev.", "center"),
            ("Envío insc.", "right"), ("% Envío", "center"), ("Factura por pagar", "right"),
            ("Cierre ganado", "right"), ("% Ganado", "center"), ("Facturado", "right"),
            ("ROI", "center"),
            ("Ilocalizados", "right"), ("% Iloc.", "center"),
            ("No se presenta", "right"), ("% No pres.", "center"),
        ] + [(f"❌ {m}", "center") for m in _motivos] + [
            ("Cierre perdido", "right"), ("Facturación perdida", "right"),
        ]

        _t_inv  = sum(d["inv"]  for d in _data)
        _t_cual = sum(d["cual"] for d in _data)
        _t_gan  = sum(d["gan"]  for d in _data)
        _t_fact = sum(d["fact"] for d in _data)
        _t_roi  = ((_t_fact - _t_inv) / _t_inv * 100) if _t_inv else None

        _aviso = ""
        if "campaña" in _gcols and _n_ads_camps:
            _ok = (f"<div style='font-size:12.5px;color:#1E7A4F;margin:0 0 10px'>"
                   f"✅ {_n_casadas} de {_n_ads_camps} campañas de Ads emparejadas "
                   f"automáticamente con su utm_campaign de HubSpot.</div>")
            _aviso = _ok
            if _sin_casar:
                _aviso += (f"<div style='font-size:12.5px;color:#B32B45;margin:0 0 10px'>"
                           f"⚠️ {_fmt_eur(_sin_casar)} en campañas que no se han podido "
                           f"emparejar sin ambigüedad. No se reparten en las filas —es "
                           f"preferible avisar que atribuirlas mal—: ajústalas con el "
                           f"editor de abajo o alinea el nombre del anuncio con la UTM."
                           f"</div>")
        elif _sin_casar:
            _aviso = (f"<div style='font-size:12.5px;color:#B32B45;margin:0 0 10px'>"
                      f"⚠️ {_fmt_eur(_sin_casar)} de inversión sin repartir.</div>")
        st.markdown(
            f"<div style='font-size:12.5px;color:{_RF['muted']};margin:0 0 10px'>"
            f"Las columnas de <b style='color:{_RF['ink_soft']}'>%</b> son conversión "
            f"<b style='color:{_RF['ink_soft']}'>sobre leads cualificados</b> de esa fila · "
            f"<b style='color:{_RF['ink_soft']}'>haz clic en cualquier cabecera para "
            f"ordenar</b> · desliza en horizontal para ver el embudo completo.</div>"
            + _aviso, unsafe_allow_html=True)
        _tabla_ordenable(_cols, _rows, nombre="Tabla maestra",
                         altura=min(660, 120 + 41 * max(len(_rows), 1)))

        _q = st.columns(6)
        kpi_card(_q[0], "Inversión total",    _fmt_eur0(_t_inv), BARCA["ink60"])
        kpi_card(_q[1], "Leads cualificados", _fmt_int(_t_cual), BARCA["blue"])
        kpi_card(_q[2], "CPL medio",
                 _fmt_eur(_t_inv / _t_cual) if (_t_cual and _t_inv) else "—",
                 BARCA["blue_deep"])
        kpi_card(_q[3], "Cierres ganados",    _fmt_int(_t_gan),  BARCA["gold"])
        kpi_card(_q[4], "Facturado",          _fmt_eur0(_t_fact), BARCA["garnet"])
        kpi_card(_q[5], "ROI global",
                 _fmt_pct(_t_roi) if _t_roi is not None else "—",
                 BARCA["gold"] if (_t_roi or 0) > 0 else BARCA["garnet_deep"])

        # Corrección manual de inversión + descarga
        _e = st.columns([2, 1, .7, .7])
        _opts_k = [d["key"] for d in _data]
        with _e[0]:
            _sel = st.selectbox("Corregir inversión de", _opts_k, key="roi_inv_sel")
        with _e[1]:
            _val = st.number_input("Inversión (€)", min_value=0.0, step=100.0,
                                   value=float(st.session_state[_inv_key].get(
                                       _sel, _spend.get(_sel, 0.0))), key="roi_inv_val")
        with _e[2]:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("💾 Guardar", key="roi_inv_save", use_container_width=True):
                st.session_state[_inv_key][_sel] = _val
                st.rerun()
        with _e[3]:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if _sel in st.session_state[_inv_key]:
                if st.button("↩️ Auto", key="roi_inv_reset", use_container_width=True,
                             help="Volver al valor automático de las APIs de Ads"):
                    del st.session_state[_inv_key][_sel]
                    st.rerun()

        # ── Panel auditable del emparejamiento ────────────────────────────────
        if _diag_map:
            _cas = [d for d in _diag_map if d[2]]
            _no  = [d for d in _diag_map if not d[2]]
            with st.expander(f"🔗 Emparejamiento de campañas · {len(_cas)} de "
                             f"{len(_diag_map)} con inversión asignada"):
                st.caption("Cómo se ha casado cada utm_campaign de HubSpot con la campaña "
                           "real del panel de anuncios. Se empareja plataforma por plataforma "
                           "y se exige que el mejor candidato gane al segundo por un margen: "
                           "ante empate se deja sin asignar en vez de atribuir el gasto a la "
                           "campaña equivocada.")
                if _cas:
                    _fil = []
                    for _c, _ps, _hechas in _cas:
                        for _p, _m in _hechas:
                            _fil.append([_c, _p, f"<b>{_m}</b>",
                                         _fmt_eur(_gasto_ads.get((_p, _m), 0.0))])
                    st.markdown(_table(
                        [("Campaña en HubSpot (utm)", "left"), ("Plataforma", "left"),
                         ("Campaña en el panel de Ads", "left"), ("Gasto", "right")],
                        _fil), unsafe_allow_html=True)
                _huerf = sorted(((g, p, c) for (p, c), g in _gasto_ads.items()
                                 if (p, c) not in _asignadas), reverse=True)
                if _huerf:
                    st.markdown("**Campañas de Ads sin pareja en HubSpot**")
                    st.markdown(_table(
                        [("Campaña en el panel de Ads", "left"), ("Plataforma", "left"),
                         ("Gasto sin repartir", "right")],
                        [[c, p, _fmt_eur(g)] for g, p, c in _huerf]), unsafe_allow_html=True)
                if _no:
                    st.markdown("**UTM de HubSpot sin campaña de Ads equivalente**")
                    st.caption("Suelen ser tráfico no pagado, campañas renombradas en el "
                               "panel o nombres ambiguos entre dos campañas.")
                    st.markdown(_table(
                        [("Campaña en HubSpot (utm)", "left"),
                         ("Plataformas consultadas", "left")],
                        [[c, _ps] for c, _ps, _ in _no]), unsafe_allow_html=True)

        # La descarga de esta tabla la sirve ya su propia barra (⬇️ CSV),
        # junto con el Excel completo del final de la página.

    def page_rst():
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

        df_lead_prog = df if not df.empty else pd.DataFrame(columns=df.columns)
        df_prog_sin = df_lead_prog[df_lead_prog["programa"] != "Sin programa"]

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
            df_lead_prog = df_lead_prog[df_lead_prog["modalidad"] == _modal_sel]
            df_prog_sin  = df_prog_sin[df_prog_sin["modalidad"] == _modal_sel]

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



    def page_campana():
        st.subheader("📍 Leads por Campaña, País y Programa")
        st.caption("Análisis de leads por fuente de tráfico, país y programa — datos en tiempo real de HubSpot CRM")

        if df.empty:
            st.info("No hay leads para el período y filtros seleccionados.")
        else:
            _PLATS_PAGO_RST = {"Social pagado", "Búsqueda pagada"}

            # ── Filtros inline ────────────────────────────────────────────────────
            _filt_col1, _filt_col2, _filt_col3, _filt_col4 = st.columns(4)
            with _filt_col1:
                _excl_eventos = st.checkbox("Excluir Webinar / Open Day", value=True, key="cpn_excl")
            
            df_cpn = df.copy()
            if _excl_eventos:
                _CATS_EXCL = {"Webinar", "Open Day", "Open Day Digital", "Sesión Informativa Online"}
                df_cpn = df_cpn[~df_cpn["categoria"].isin(_CATS_EXCL)]

            _plat_opts = sorted(df_cpn["fuente"].dropna().unique().tolist())
            _pais_opts = sorted(df_cpn["pais"].dropna().unique().tolist())
            _prog_opts = sorted(df_cpn["programa"].dropna().unique().tolist())

            with _filt_col2:
                _filtro_plat = st.multiselect("Fuente", _plat_opts, default=_plat_opts, key="cpn_plat")
            with _filt_col3:
                _filtro_pais = st.multiselect("País", _pais_opts, default=_pais_opts, key="cpn_pais")
            with _filt_col4:
                _filtro_prog = st.multiselect("Programa", _prog_opts, default=_prog_opts, key="cpn_prog")

            if _filtro_plat:
                df_cpn = df_cpn[df_cpn["fuente"].isin(_filtro_plat)]
            if _filtro_pais:
                df_cpn = df_cpn[df_cpn["pais"].isin(_filtro_pais)]
            if _filtro_prog:
                df_cpn = df_cpn[df_cpn["programa"].isin(_filtro_prog)]

            if df_cpn.empty:
                st.info("No hay leads con los filtros aplicados.")
            else:
                # ── KPIs ──────────────────────────────────────────────────────────────
                _total_cpn    = len(df_cpn)
                _leads_pagado = len(df_cpn[df_cpn["fuente"].isin(_PLATS_PAGO_RST)])
                _paises_n     = df_cpn["pais"].nunique()
                _pct_pago     = _leads_pagado / _total_cpn * 100 if _total_cpn > 0 else 0

                _k1, _k2, _k3, _k4 = st.columns(4)
                kpi_card(_k1, "Total Leads",    _total_cpn,                   BARCA["blue"])
                kpi_card(_k2, "Leads Pagados",  _leads_pagado,                BARCA["garnet"])
                kpi_card(_k3, "Países",         _paises_n,                    BARCA["blue_deep"])
                kpi_card(_k4, "% Pagados",      f"{_pct_pago:.0f}%",        BARCA["gold"])

                st.markdown("<br>", unsafe_allow_html=True)

                # Sub-chips por fuente
                _fuentes_presentes = df_cpn["fuente"].value_counts()
                _val_por_fuente = (
                    df_cpn.groupby(["fuente", "lead_valido"]).size()
                    .unstack(fill_value=0)
                )
                for _col in ["Válido", "No válido", "Sin datos"]:
                    if _col not in _val_por_fuente.columns:
                        _val_por_fuente[_col] = 0

                _MAX_C = 5
                _chunks = [_fuentes_presentes.index.tolist()[i:i+_MAX_C]
                           for i in range(0, len(_fuentes_presentes), _MAX_C)]
                for _chunk in _chunks:
                    _pcols = st.columns(len(_chunk))
                    for _ci, _fuente in enumerate(_chunk):
                        _n         = int(_fuentes_presentes[_fuente])
                        _validos   = int(_val_por_fuente.loc[_fuente, "Válido"])    if _fuente in _val_por_fuente.index else 0
                        _invalidos = int(_val_por_fuente.loc[_fuente, "No válido"]) if _fuente in _val_por_fuente.index else 0
                        _pct_val   = _validos / _n * 100 if _n > 0 else 0
                        with _pcols[_ci]:
                            with st.container(border=True):
                                st.markdown(f"**{_fuente}**")
                                st.markdown(
                                    f"<div style='font-size:13px;line-height:2'>"
                                    f"Leads totales: <b>{_n:,}</b><br>"
                                    f"Leads válidos: <b>{_validos:,}</b><br>"
                                    f"Leads inválidos: <b>{_invalidos:,}</b><br>"
                                    f"% Válidos: <b>{_pct_val:.0f}%</b>"
                                    f"</div>",
                                    unsafe_allow_html=True,
                                )

                st.divider()

                # ── Resumen general por fuente ────────────────────────────────────────
                st.markdown("### 📊 Leads por Fuente")
                _col_rs1, _col_rs2 = st.columns(2)

                with _col_rs1:
                    st.caption("Total leads por fuente")
                    _df_fnt = (
                        df_cpn.groupby("fuente").size()
                        .reset_index(name="Leads")
                        .sort_values("Leads", ascending=True)
                    )
                    _fig_fnt = px.bar(
                        _df_fnt, x="Leads", y="fuente", orientation="h",
                        text_auto=".0f",
                        color="Leads",
                        color_continuous_scale=[BARCA["bone"], BARCA["blue"], BARCA["blue_ink"]],
                        labels={"fuente": ""},
                    )
                    _fig_fnt.update_layout(coloraxis_showscale=False,
                                           yaxis=dict(categoryorder="total ascending"))
                    barca_layout(_fig_fnt, max(240, len(_df_fnt) * 36))
                    st.plotly_chart(_fig_fnt, use_container_width=True)

                with _col_rs2:
                    st.caption("Válidos e inválidos por fuente")
                    _df_val = (
                        df_cpn.groupby(["fuente", "lead_valido"])
                        .size().reset_index(name="Leads")
                    )
                    # orden fuentes por total desc
                    _fnt_order = (
                        _df_val.groupby("fuente")["Leads"].sum()
                        .sort_values(ascending=True).index.tolist()
                    )
                    _COLOR_VAL = {
                        "Válido":    BARCA["blue"],
                        "No válido": BARCA["garnet"],
                        "Sin datos": BARCA["ink20"],
                    }
                    _fig_val = px.bar(
                        _df_val, x="Leads", y="fuente", color="lead_valido",
                        orientation="h", barmode="stack",
                        color_discrete_map=_COLOR_VAL,
                        text_auto=".0f",
                        labels={"fuente": "", "lead_valido": ""},
                        category_orders={"fuente": _fnt_order},
                    )
                    barca_layout(_fig_val, max(240, len(_df_fnt) * 36))
                    st.plotly_chart(_fig_val, use_container_width=True)

                st.divider()

                # ── Válidos / Inválidos por campaña ──────────────────────────────────
                st.markdown("### ✅ Válidos e Inválidos por Campaña")

                _fuentes_val = ["Todas"] + sorted(df_cpn["fuente"].dropna().unique().tolist())
                _sel_fuente_val = st.selectbox(
                    "Filtrar por fuente de tráfico",
                    _fuentes_val,
                    key="val_camp_fuente",
                )

                _df_val_base = df_cpn if _sel_fuente_val == "Todas" else df_cpn[df_cpn["fuente"] == _sel_fuente_val]
                _df_val_base = _df_val_base.copy()

                def _camp_name_val(row):
                    # data_1/data_2 están invertidos según la fuente (ver
                    # resolve_campana): en Meta data_1 es la red social.
                    for k in ("campana_reciente", "campana"):
                        c = (row.get(k) or "").strip()
                        if c and c != "Sin campaña":
                            return c
                    return row.get("fuente") or "Sin campaña"

                _df_val_base["_campaña"] = _df_val_base.apply(_camp_name_val, axis=1)

                # Tabla pivotada: campaña × válido/inválido
                _df_val_grp = (
                    _df_val_base.groupby(["fuente", "_campaña", "lead_valido"])
                    .size().reset_index(name="n")
                )
                _df_val_pivot = (
                    _df_val_grp.pivot_table(
                        index=["fuente", "_campaña"],
                        columns="lead_valido",
                        values="n",
                        aggfunc="sum",
                        fill_value=0,
                    )
                    .reset_index()
                )
                _df_val_pivot.columns.name = None
                # Asegurar columnas aunque no haya datos en alguna categoría
                for _col in ["Válido", "No válido", "Sin datos"]:
                    if _col not in _df_val_pivot.columns:
                        _df_val_pivot[_col] = 0
                _df_val_pivot["Total"] = _df_val_pivot[["Válido", "No válido", "Sin datos"]].sum(axis=1)
                _df_val_pivot["% Válido"] = (
                    _df_val_pivot["Válido"] / _df_val_pivot["Total"] * 100
                ).round(1)
                _df_val_pivot = _df_val_pivot.sort_values("Total", ascending=False)
                _df_val_pivot = _df_val_pivot.rename(columns={
                    "fuente":   "Fuente",
                    "_campaña": "Campaña",
                })

                st.dataframe(
                    _df_val_pivot[["Fuente", "Campaña", "Total", "Válido", "No válido", "Sin datos", "% Válido"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Total":      st.column_config.NumberColumn(format="%d", width="small"),
                        "Válido":     st.column_config.NumberColumn(format="%d", width="small"),
                        "No válido":  st.column_config.NumberColumn(format="%d", width="small"),
                        "Sin datos":  st.column_config.NumberColumn(format="%d", width="small"),
                        "% Válido":   st.column_config.NumberColumn(format="%.1f%%", width="small"),
                        "Campaña":    st.column_config.TextColumn(width="large"),
                        "Fuente":     st.column_config.TextColumn(width="medium"),
                    },
                )

                st.divider()

                # ── Leads diarios por fuente ─────────────────────────────────────────
                st.markdown("### 📅 Leads Diarios por Fuente")
                _df_day = (
                    df_cpn.groupby(["fecha", "fuente"])
                    .size().reset_index(name="leads")
                    .sort_values("fecha")
                )
                _df_day["fecha_str"] = pd.to_datetime(_df_day["fecha"]).dt.strftime("%d/%m")
                _fig_day = px.bar(
                    _df_day, x="fecha_str", y="leads",
                    color="fuente", barmode="stack",
                    color_discrete_sequence=COLOR_FUENTES,
                    labels={"fecha_str": "", "leads": "Leads", "fuente": ""},
                    text_auto=".0f",
                )
                _fig_day.update_traces(textposition="inside", textfont_size=9)
                barca_layout(_fig_day, 340)
                st.plotly_chart(_fig_day, use_container_width=True)

                st.divider()

                # ── País y Programa ──────────────────────────────────────────────────
                _col_p1, _col_p2 = st.columns(2)

                with _col_p1:
                    st.markdown("### 🌍 Leads por País")
                    _df_pais = (
                        df_cpn.groupby("pais").size()
                        .reset_index(name="Leads")
                        .sort_values("Leads", ascending=True)
                        .tail(15)
                    )
                    _fig_p = px.bar(
                        _df_pais, x="Leads", y="pais", orientation="h",
                        text_auto=".0f",
                        color="Leads",
                        color_continuous_scale=[BARCA["line2"], BARCA["blue_deep"], BARCA["blue_ink"]],
                        labels={"pais": ""},
                    )
                    _fig_p.update_layout(coloraxis_showscale=False,
                                         yaxis=dict(categoryorder="total ascending"))
                    barca_layout(_fig_p, max(280, len(_df_pais) * 30))
                    st.plotly_chart(_fig_p, use_container_width=True)

                with _col_p2:
                    st.markdown("### 🎓 Leads por Programa")
                    _df_prog_c = (
                        df_cpn[df_cpn["programa"] != "Sin programa"]
                        .groupby("programa").size()
                        .reset_index(name="Leads")
                        .sort_values("Leads", ascending=True)
                        .tail(15)
                    )
                    if _df_prog_c.empty:
                        st.info("Sin datos de programa.")
                    else:
                        _fig_pg = px.bar(
                            _df_prog_c, x="Leads", y="programa", orientation="h",
                            text_auto=".0f",
                            color="Leads",
                            color_continuous_scale=[BARCA["bone"], BARCA["blue"], BARCA["blue_ink"]],
                            labels={"programa": ""},
                        )
                        _fig_pg.update_layout(coloraxis_showscale=False,
                                              yaxis=dict(categoryorder="total ascending"))
                        barca_layout(_fig_pg, max(280, len(_df_prog_c) * 30))
                        st.plotly_chart(_fig_pg, use_container_width=True)

                st.divider()

                # ── Resumen de leads por campaña ──────────────────────────────────────
                st.markdown("### 🏷️ Resumen por Campaña")

                _fuentes_camp = ["Todas"] + sorted(df_cpn["fuente"].dropna().unique().tolist())
                _sel_fuente = st.selectbox(
                    "Filtrar por fuente de tráfico",
                    _fuentes_camp,
                    key="resumen_camp_fuente",
                )

                _df_camp_base = df_cpn if _sel_fuente == "Todas" else df_cpn[df_cpn["fuente"] == _sel_fuente]

                # Campaña real: en Google está en data_1 y en Meta en data_2
                # (ver resolve_campana), por eso se usa la columna ya resuelta.
                def _camp_name(row):
                    for k in ("campana_reciente", "campana"):
                        c = (row.get(k) or "").strip()
                        if c and c != "Sin campaña":
                            return c
                    return row.get("fuente") or "Sin campaña"

                _df_camp_base = _df_camp_base.copy()
                _df_camp_base["_campaña"] = _df_camp_base.apply(_camp_name, axis=1)

                _df_resumen = (
                    _df_camp_base.groupby(["fuente", "_campaña", "tipo_medio"])
                    .size().reset_index(name="Leads")
                    .sort_values("Leads", ascending=False)
                    .rename(columns={
                        "fuente":              "Fuente",
                        "_campaña":            "Campaña",
                        "tipo_medio":          "Tipo / Medio",
                    })
                )

                st.dataframe(
                    _df_resumen[["Fuente", "Campaña", "Tipo / Medio", "Leads"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Leads":         st.column_config.NumberColumn(format="%d", width="small"),
                        "Fuente":        st.column_config.TextColumn(width="medium"),
                        "Tipo / Medio":  st.column_config.TextColumn(width="small"),
                        "Campaña":       st.column_config.TextColumn(width="large"),
                    },
                )

                st.divider()

                # ── Tabla principal: Fuente × País × Programa ────────────────────────
                st.markdown("### 📋 Detalle por Fuente, País y Programa")
                _det_f1, _det_f2 = st.columns(2)
                with _det_f1:
                    _det_pais_opts = ["Todos"] + sorted(df_cpn["pais"].dropna().unique().tolist())
                    _det_sel_pais  = st.selectbox("Filtrar por País", _det_pais_opts, key="det_pais")
                with _det_f2:
                    _det_prog_opts = ["Todos"] + sorted(df_cpn["programa"].dropna().unique().tolist())
                    _det_sel_prog  = st.selectbox("Filtrar por Programa", _det_prog_opts, key="det_prog")

                _df_tabla_src = df_cpn.copy()
                if _det_sel_pais != "Todos":
                    _df_tabla_src = _df_tabla_src[_df_tabla_src["pais"] == _det_sel_pais]
                if _det_sel_prog != "Todos":
                    _df_tabla_src = _df_tabla_src[_df_tabla_src["programa"] == _det_sel_prog]

                _df_tabla = (
                    _df_tabla_src.groupby(["fuente", "pais", "programa"])
                    .size().reset_index(name="Leads")
                    .sort_values("Leads", ascending=False)
                    .rename(columns={"fuente": "Fuente", "pais": "País", "programa": "Programa"})
                )
                st.dataframe(
                    _df_tabla,
                    use_container_width=True,
                    hide_index=True,
                    column_config={"Leads": st.column_config.NumberColumn(format="%d")},
                )

                st.divider()

                # ── Heatmap: País × Fuente ────────────────────────────────────────────
                st.markdown("### 🗺️ Leads: País × Fuente")
                _heat_pais_all = sorted(
                    df_cpn[df_cpn["pais"].notna() & (df_cpn["pais"] != "")]
                    ["pais"].value_counts().head(20).index.tolist()
                )
                _heat_sel_pais = st.multiselect(
                    "Filtrar países (por defecto Top 12)",
                    options=_heat_pais_all,
                    default=_heat_pais_all[:12],
                    key="heat_pais",
                )
                _heat_paises_use = _heat_sel_pais if _heat_sel_pais else _heat_pais_all[:12]
                _df_heat_src = df_cpn[
                    df_cpn["pais"].isin(_heat_paises_use) &
                    df_cpn["fuente"].notna() & (df_cpn["fuente"] != "")
                ]
                _df_heat = (
                    _df_heat_src
                    .groupby(["pais", "fuente"])
                    .size().reset_index(name="leads")
                )
                if not _df_heat.empty:
                    _pivot = _df_heat.pivot_table(
                        index="pais", columns="fuente", values="leads",
                        aggfunc="sum", fill_value=0,
                    )
                    _fig_h = px.imshow(
                        _pivot, aspect="auto",
                        color_continuous_scale=["#e6f3fb", "#0053B3", "#000a3f"],
                        labels={"x": "Fuente", "y": "País", "color": "Leads"},
                        text_auto=".0f",
                    )
                    barca_layout(_fig_h, max(300, len(_pivot) * 40))
                    _fig_h.update_layout(coloraxis_showscale=False)
                    _fig_h.update_xaxes(tickangle=-30)
                    _fig_h.update_traces(textfont_size=11)
                    st.plotly_chart(_fig_h, use_container_width=True)

                st.divider()

                # ── Detalle de campaña por lead ──────────────────────────────────────
                st.markdown("### 📡 Detalle de Campaña por Lead")
                st.caption(
                    "Nombre de campaña y fuente de tráfico exacta asociada a cada lead. "
                    "**Fuente más reciente** = última sesión antes del formulario. "
                    "**Fuente original** = primera sesión que trajo al contacto."
                )

                _camp_det_opts = ["Todas"] + sorted(
                    df_cpn["campana_reciente"]
                    .dropna()
                    .pipe(lambda s: s[(s != "") & (s != "Sin campaña")])
                    .unique()
                    .tolist()
                )
                _camp_det_sel = st.selectbox(
                    "Filtrar por Campaña",
                    _camp_det_opts,
                    key="camp_det_sel",
                )
                _df_cpn_det = (df_cpn if _camp_det_sel == "Todas"
                               else df_cpn[df_cpn["campana_reciente"] == _camp_det_sel])

                # Agrupar por campaña resuelta + país + programa
                _df_camp_det = (
                    _df_cpn_det.groupby([
                        "campana_reciente",
                        "fuente_reciente_d1",
                        "fuente_reciente",
                        "campana",
                        "fuente_original_d1",
                        "fuente_original",
                        "pais",
                        "programa",
                    ])
                    .size().reset_index(name="Leads")
                    .sort_values("Leads", ascending=False)
                    .rename(columns={
                        "campana_reciente":   "Campaña (más reciente)",
                        "fuente_reciente_d1": "An. Det. 1 (más reciente)",
                        "fuente_reciente":    "Fuente más reciente",
                        "campana":            "Campaña (original)",
                        "fuente_original_d1": "An. Det. 1 (original)",
                        "fuente_original":    "Fuente original",
                        "pais":               "País",
                        "programa":           "Programa",
                    })
                )
                # Reordenar columnas para que las más útiles vayan primero
                _col_order = [
                    "Leads",
                    "Campaña (más reciente)", "An. Det. 1 (más reciente)", "Fuente más reciente",
                    "País", "Programa",
                    "Campaña (original)", "An. Det. 1 (original)", "Fuente original",
                ]
                _df_camp_det = _df_camp_det[[c for c in _col_order if c in _df_camp_det.columns]]

                st.dataframe(
                    _df_camp_det,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Leads": st.column_config.NumberColumn(format="%d", width="small"),
                        "Campaña (más reciente)": st.column_config.TextColumn(width="large"),
                        "Campaña (original)":     st.column_config.TextColumn(width="large"),
                    },
                )

                st.divider()

                # ── Análisis de fuentes por programa ─────────────────────────────────
                st.markdown("### 🎓 Fuentes de tráfico por Programa")
                st.caption("Selecciona un programa para ver qué fuentes generan más leads y la calidad de cada una.")

                _progs_disponibles = (
                    df_cpn[df_cpn["programa"] != "Sin programa"]["programa"]
                    .value_counts()
                    .index.tolist()
                )
                if not _progs_disponibles:
                    st.info("Sin datos de programa disponibles.")
                else:
                    _sel_prog_fnt = st.selectbox(
                        "Selecciona un programa",
                        _progs_disponibles,
                        key="prog_fuente_sel",
                    )

                    _df_pf = df_cpn[df_cpn["programa"] == _sel_prog_fnt].copy()
                    _total_pf = len(_df_pf)

                    # KPIs rápidos del programa seleccionado
                    _val_pf     = (_df_pf["lead_valido"] == "Válido").sum()
                    _noval_pf   = (_df_pf["lead_valido"] == "No válido").sum()
                    _pct_val_pf = _val_pf / _total_pf * 100 if _total_pf > 0 else 0

                    _kpf1, _kpf2, _kpf3, _kpf4 = st.columns(4)
                    kpi_card(_kpf1, "Total leads",     _total_pf,                  BARCA["blue"])
                    kpi_card(_kpf2, "Leads válidos",   _val_pf,                    "#2E7D32")
                    kpi_card(_kpf3, "Leads inválidos", _noval_pf,                  BARCA["garnet"])
                    kpi_card(_kpf4, "% Válidos",       f"{_pct_val_pf:.0f}%",     BARCA["gold"])

                    st.markdown("<br>", unsafe_allow_html=True)

                    _col_pf1, _col_pf2 = st.columns(2)

                    with _col_pf1:
                        # Barras horizontales: total por fuente
                        _df_pf_fnt = (
                            _df_pf.groupby("fuente").size()
                            .reset_index(name="Leads")
                            .sort_values("Leads", ascending=True)
                        )
                        _fig_pf_tot = px.bar(
                            _df_pf_fnt, x="Leads", y="fuente", orientation="h",
                            text_auto=".0f",
                            color="Leads",
                            color_continuous_scale=[BARCA["bone"], BARCA["blue"], BARCA["blue_ink"]],
                            labels={"fuente": ""},
                            title="Leads por fuente",
                        )
                        _fig_pf_tot.update_layout(
                            coloraxis_showscale=False,
                            yaxis=dict(categoryorder="total ascending"),
                        )
                        barca_layout(_fig_pf_tot, max(260, len(_df_pf_fnt) * 44))
                        st.plotly_chart(_fig_pf_tot, use_container_width=True)

                    with _col_pf2:
                        # Barras apiladas: válido/inválido por fuente
                        _df_pf_val = (
                            _df_pf.groupby(["fuente", "lead_valido"])
                            .size().reset_index(name="Leads")
                        )
                        _fnt_order_pf = (
                            _df_pf_val.groupby("fuente")["Leads"].sum()
                            .sort_values(ascending=True).index.tolist()
                        )
                        _COLOR_VAL2 = {
                            "Válido":    BARCA["blue"],
                            "No válido": BARCA["garnet"],
                            "Sin datos": BARCA["ink20"],
                        }
                        _fig_pf_val = px.bar(
                            _df_pf_val, x="Leads", y="fuente", color="lead_valido",
                            orientation="h", barmode="stack",
                            color_discrete_map=_COLOR_VAL2,
                            text_auto=".0f",
                            labels={"fuente": "", "lead_valido": ""},
                            category_orders={"fuente": _fnt_order_pf},
                            title="Válidos e inválidos por fuente",
                        )
                        _fig_pf_val.update_layout(legend=dict(orientation="h", y=-0.15, title=""))
                        barca_layout(_fig_pf_val, max(260, len(_df_pf_fnt) * 44))
                        st.plotly_chart(_fig_pf_val, use_container_width=True)

                    # Tabla resumen: fuente × válido/inválido con totales
                    _df_pf_pivot = (
                        _df_pf.groupby(["fuente", "lead_valido"]).size()
                        .unstack(fill_value=0)
                        .reset_index()
                    )
                    _df_pf_pivot.columns.name = None
                    for _col in ["Válido", "No válido", "Sin datos"]:
                        if _col not in _df_pf_pivot.columns:
                            _df_pf_pivot[_col] = 0
                    _df_pf_pivot["Total"] = _df_pf_pivot[["Válido", "No válido", "Sin datos"]].sum(axis=1)
                    _df_pf_pivot["% Válido"] = (
                        _df_pf_pivot["Válido"] / _df_pf_pivot["Total"] * 100
                    ).round(1)
                    _df_pf_pivot = (
                        _df_pf_pivot
                        .sort_values("Total", ascending=False)
                        .rename(columns={"fuente": "Fuente"})
                    )
                    st.dataframe(
                        _df_pf_pivot[["Fuente", "Total", "Válido", "No válido", "Sin datos", "% Válido"]],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Fuente":     st.column_config.TextColumn(width="medium"),
                            "Total":      st.column_config.NumberColumn(format="%d", width="small"),
                            "Válido":     st.column_config.NumberColumn(format="%d", width="small"),
                            "No válido":  st.column_config.NumberColumn(format="%d", width="small"),
                            "Sin datos":  st.column_config.NumberColumn(format="%d", width="small"),
                            "% Válido":   st.column_config.NumberColumn(format="%.1f%%", width="small"),
                        },
                    )

                st.divider()

                # Vista alternativa: tabla plana con un lead por fila (expandible)
                with st.expander("📋 Ver tabla completa lead a lead"):
                    _cols_raw = [
                        "fecha", "pais", "programa", "fuente",
                        "fuente_reciente", "fuente_reciente_d1", "fuente_reciente_d2",
                        "fuente_original", "fuente_original_d1", "fuente_original_d2",
                        "categoria", "lead_status",
                    ]
                    _cols_raw = [c for c in _cols_raw if c in df_cpn.columns]
                    st.dataframe(
                        df_cpn[_cols_raw].rename(columns={
                            "fecha":               "Fecha",
                            "pais":                "País",
                            "programa":            "Programa",
                            "fuente":              "Fuente",
                            "fuente_reciente":     "Fuente más reciente",
                            "fuente_reciente_d1":  "An. Det. 1 reciente",
                            "fuente_reciente_d2":  "An. Det. 2 reciente (crudo)",
                            "fuente_original":     "Fuente original",
                            "fuente_original_d1":  "An. Det. 1 original",
                            "fuente_original_d2":  "An. Det. 2 original (crudo)",
                            "categoria":           "Tipo contacto",
                            "lead_status":         "Estado",
                        }).sort_values("Fecha", ascending=False),
                        use_container_width=True,
                        hide_index=True,
                    )


    # ── Router de páginas ───────────────────────────────────────────────────
    {
        "💰 Contactos, Conversión & ROI": page_roi,
        "📊 RST Dashboard":               page_rst,
        "📍 Leads por Campaña":           page_campana,
    }[_pagina]()

    # ── Footer ──────────────────────────────────────────────────────────────────
    st.markdown(
        f"<br><div style='text-align:center;color:{BARCA['ink40']};font-size:12px'>"
        f"{ACCOUNT_NAME} · Formularios HighTicket RST · Datos actualizados automáticamente cada 5 min</div>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
