# Dashboard CRM — Hofmann

Contexto para trabajar en este proyecto. Léelo antes de tocar código.

## Qué es
Dashboard **Streamlit** sobre el CRM de HubSpot de **Hofmann** (contactos, pipeline
de negocios, email marketing, analítica).

- **Repo:** `WeRise-ESP/hofmann-crm-dashboard` (rama `main`)
- **App:** https://dahsboardhofmann.streamlit.app  ⚠️ el subdominio lleva "**dahs**"
  (errata original) — NO la corrijas, rompería los enlaces ya compartidos.
- **Entry point / main file:** `dashboard_rst.py` (~2700 líneas)
- **Actualizar = `git push` a `main`** → Streamlit Cloud redespliega solo.

## Arrancar en local
```bash
source venv/bin/activate
streamlit run dashboard_rst.py
```
Necesitas `.streamlit/secrets.toml` con el token de HubSpot (NO está en git —
pídelo por el gestor de contraseñas del equipo).

## ⚠️ MUY IMPORTANTE — esta carpeta NO es solo el repo
La carpeta local contiene además **documentos de trabajo del cliente** (planes,
forecasts, informes, Excel de leads con datos personales) que **NO** forman parte del
repositorio. El `.gitignore` los excluye explícitamente. **Nunca hagas `git add -A`
a lo loco ni subas `*.xlsx`/`*.docx`/`*.csv`** — hay datos personales de leads.

## ⚠️ Comparte código con BiHub
Es el **mismo dashboard RST** que `bihub-rst-dashboard`, adaptado a otro cliente. Un
bug arreglado aquí probablemente haya que replicarlo allá, y viceversa.

- Tema (`.streamlit/config.toml`): `primaryColor = #004D98` (azul). **Sospecha:** ese
  azul es el del FC Barcelona, probablemente heredado por copia del BiHub. Verifica si
  es el color correcto para Hofmann antes de darlo por bueno.
- **Batches de HubSpot: máximo 100 inputs** por llamada.
- **Matrículas = deals ganados**, no contactos por lifecyclestage.
