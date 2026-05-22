"""
Analytics y métricas: tráfico web, conversiones, email performance, actividad CRM.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import requests
import pandas as pd
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table
from client import get_headers

console = Console()

BASE = "https://api.hubapi.com"
HEADERS = get_headers()


def _get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params or {})
    r.raise_for_status()
    return r.json()


def _post(path, data):
    r = requests.post(f"{BASE}{path}", headers=HEADERS, json=data)
    r.raise_for_status()
    return r.json()


# ── Actividad de contactos ─────────────────────────────────────────────────────

def actividad_reciente_contacto(contact_id):
    """Historial de actividad de un contacto."""
    data = _get(f"/engagements/v1/engagements/associated/contact/{contact_id}/paged",
                {"limit": 50})
    actividades = []
    for e in data.get("results", []):
        eng = e.get("engagement", {})
        actividades.append({
            "Tipo": eng.get("type", ""),
            "Fecha": datetime.fromtimestamp(eng.get("createdAt", 0) / 1000).strftime("%Y-%m-%d"),
            "Asunto": e.get("metadata", {}).get("subject", ""),
        })
    return actividades


# ── Actividades / Engagements ──────────────────────────────────────────────────

def listar_actividades(limite=200):
    """Lista actividades recientes (calls, emails, meetings, notes)."""
    data = _get("/engagements/v1/engagements/paged", {"limit": 100, "count": 100})
    actividades = []
    for e in data.get("results", []):
        eng = e.get("engagement", {})
        actividades.append({
            "ID": eng.get("id"),
            "Tipo": eng.get("type", ""),
            "Fecha": datetime.fromtimestamp(eng.get("createdAt", 0) / 1000).strftime("%Y-%m-%d"),
            "Propietario": eng.get("ownerId", ""),
        })
    table = Table(title="Actividades recientes", show_lines=True)
    for col in ["ID", "Tipo", "Fecha", "Propietario"]:
        table.add_column(col)
    for a in actividades[:50]:
        table.add_row(str(a["ID"]), a["Tipo"], a["Fecha"], str(a["Propietario"]))
    console.print(table)
    return actividades


# ── Métricas de emails marketing ───────────────────────────────────────────────

def resumen_emails_performance():
    """Resumen de performance de todos los emails enviados."""
    data = _get("/marketing/v3/emails", {"limit": 50})
    resultados = []
    for email in data.get("results", []):
        eid = email["id"]
        try:
            stats = _get(f"/marketing/v3/emails/{eid}/statistics/total")
            counters = stats.get("counters", {})
            resultados.append({
                "Nombre": email.get("name", ""),
                "Asunto": email.get("subject", "")[:50],
                "Enviados": counters.get("SENT", 0),
                "Abiertos": counters.get("OPEN", 0),
                "Clics": counters.get("CLICK", 0),
                "Rebotes": counters.get("BOUNCE", 0),
                "Bajas": counters.get("UNSUBSCRIBED", 0),
            })
        except Exception:
            pass

    if resultados:
        df = pd.DataFrame(resultados)
        df["Tasa apertura %"] = (df["Abiertos"] / df["Enviados"].replace(0, 1) * 100).round(1)
        df["Tasa clic %"] = (df["Clics"] / df["Enviados"].replace(0, 1) * 100).round(1)
        console.print("\n[bold underline]PERFORMANCE EMAILS MARKETING[/bold underline]")
        console.print(df.to_string(index=False))
        return df
    console.print("[yellow]No hay emails con estadísticas disponibles.[/yellow]")
    return pd.DataFrame()


# ── Informes CRM ───────────────────────────────────────────────────────────────

def informe_crm_completo():
    """Panel resumen del CRM: contactos, deals, actividad."""
    from contactos import listar_contactos
    from deals import listar_deals

    contactos = listar_contactos(5000)
    deals = listar_deals(5000)

    df_c = pd.DataFrame(contactos)
    df_d = pd.DataFrame(deals)

    console.print("\n" + "="*60)
    console.print("[bold underline]INFORME CRM COMPLETO — SKILLIA[/bold underline]")
    console.print("="*60)

    # Contactos
    console.print(f"\n[bold]CONTACTOS[/bold]")
    console.print(f"Total: [cyan]{len(df_c)}[/cyan]")
    if not df_c.empty and "Etapa" in df_c.columns:
        console.print("\nPor etapa de ciclo de vida:")
        for etapa, cnt in df_c["Etapa"].value_counts().items():
            console.print(f"  {etapa or 'Sin etapa'}: {cnt}")

    # Deals
    console.print(f"\n[bold]DEALS / NEGOCIOS[/bold]")
    console.print(f"Total deals: [cyan]{len(df_d)}[/cyan]")
    if not df_d.empty:
        ganados = df_d[df_d["Cerrado ganado"] == "true"]
        abiertos = df_d[df_d["Cerrado ganado"].isin(["", None, "false"])]
        console.print(f"Ganados: [green]{len(ganados)}[/green] — Valor: [green]€{ganados['Importe'].sum():,.0f}[/green]")
        console.print(f"Pipeline abierto: [yellow]{len(abiertos)}[/yellow] — Valor: [yellow]€{abiertos['Importe'].sum():,.0f}[/yellow]")

        console.print("\nPor etapa:")
        for etapa, grupo in df_d.groupby("Etapa"):
            console.print(f"  {etapa}: {len(grupo)} deals — €{grupo['Importe'].sum():,.0f}")

    console.print("\n" + "="*60)


# ── Tráfico web (Analytics API v2) ────────────────────────────────────────────

def trafico_web(dias=30, breakdown="totals"):
    """
    Métricas de tráfico web.
    breakdown: 'totals', 'sources', 'geolocation'
    """
    end = datetime.now()
    start = end - timedelta(days=dias)
    try:
        data = _get(f"/analytics/v2/reports/{breakdown}/daily", {
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
        })
        console.print(f"\n[bold]Tráfico web — últimos {dias} días:[/bold]")
        for item in data.get("data", [])[:10]:
            console.print(f"  {item}")
        return data
    except Exception as e:
        console.print(f"[yellow]Analytics web requiere acceso adicional: {e}[/yellow]")


if __name__ == "__main__":
    informe_crm_completo()
    resumen_emails_performance()
