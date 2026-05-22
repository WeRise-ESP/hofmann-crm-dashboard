"""
Gestión de Deals (Negocios): listar, buscar, crear, pipeline, informes.
"""
import pandas as pd
from rich.console import Console
from rich.table import Table
from hubspot.crm.deals import SimplePublicObjectInputForCreate, SimplePublicObjectInput
from client import get_client

console = Console()
client = get_client()

PROPS = [
    "dealname", "amount", "dealstage", "pipeline", "closedate",
    "hubspot_owner_id", "hs_deal_stage_probability", "createdate",
    "hs_is_closed_won", "hs_is_closed"
]


def listar_deals(limite=500):
    deals = []
    after = None
    while True:
        resp = client.crm.deals.basic_api.get_page(
            limit=min(limite - len(deals), 100),
            properties=PROPS,
            after=after,
        )
        for d in resp.results:
            p = d.properties
            deals.append({
                "ID": d.id,
                "Nombre": p.get("dealname", ""),
                "Importe": float(p.get("amount") or 0),
                "Etapa": p.get("dealstage", ""),
                "Pipeline": p.get("pipeline", ""),
                "Cierre": (p.get("closedate") or "")[:10],
                "Probabilidad %": float(p.get("hs_deal_stage_probability") or 0) * 100,
                "Cerrado ganado": p.get("hs_is_closed_won", ""),
                "Creado": (p.get("createdate") or "")[:10],
            })
        if not resp.paging or len(deals) >= limite:
            break
        after = resp.paging.next.after
    return deals


def mostrar_deals(limite=30):
    deals = listar_deals(limite)
    table = Table(title=f"Deals HubSpot ({len(deals)})", show_lines=True)
    cols = ["ID", "Nombre", "Importe", "Etapa", "Cierre", "Probabilidad %"]
    for col in cols:
        table.add_column(col, style="cyan" if col == "Nombre" else "white")
    for d in deals:
        table.add_row(
            d["ID"], d["Nombre"],
            f"€{d['Importe']:,.0f}", d["Etapa"],
            d["Cierre"], f"{d['Probabilidad %']:.0f}%"
        )
    console.print(table)


def crear_deal(nombre, importe, etapa, pipeline="default", fecha_cierre=""):
    props = {
        "dealname": nombre,
        "amount": str(importe),
        "dealstage": etapa,
        "pipeline": pipeline,
    }
    if fecha_cierre:
        props["closedate"] = fecha_cierre
    obj = SimplePublicObjectInputForCreate(properties=props)
    result = client.crm.deals.basic_api.create(simple_public_object_input_for_create=obj)
    console.print(f"[green]Deal creado: {nombre} — ID: {result.id}[/green]")
    return result


def actualizar_deal(deal_id, **props):
    obj = SimplePublicObjectInput(properties={k: str(v) for k, v in props.items()})
    result = client.crm.deals.basic_api.update(deal_id=deal_id, simple_public_object_input=obj)
    console.print(f"[green]Deal {deal_id} actualizado.[/green]")
    return result


def informe_pipeline():
    """Informe completo del pipeline de ventas."""
    deals = listar_deals(5000)
    df = pd.DataFrame(deals)

    console.print("\n[bold underline]INFORME PIPELINE DE VENTAS[/bold underline]\n")
    console.print(f"Total deals: [cyan]{len(df)}[/cyan]")
    console.print(f"Valor total pipeline: [green]€{df['Importe'].sum():,.0f}[/green]")

    console.print("\n[bold]Por etapa:[/bold]")
    resumen = df.groupby("Etapa").agg(
        Cantidad=("ID", "count"),
        Valor_Total=("Importe", "sum"),
        Valor_Medio=("Importe", "mean"),
    ).sort_values("Valor_Total", ascending=False)
    console.print(resumen.to_string())

    console.print("\n[bold]Deals ganados vs perdidos vs abiertos:[/bold]")
    console.print(df["Cerrado ganado"].value_counts().to_string())

    return df


def exportar_deals_csv(ruta="exports/deals.csv", limite=5000):
    import os; os.makedirs("exports", exist_ok=True)
    deals = listar_deals(limite)
    df = pd.DataFrame(deals)
    df.to_csv(ruta, index=False, encoding="utf-8-sig")
    console.print(f"[green]Exportado: {ruta} ({len(deals)} deals)[/green]")
    return df


def forecast_mensual():
    """Forecast de ingresos por mes de cierre."""
    df = pd.DataFrame(listar_deals(5000))
    df["Cierre"] = pd.to_datetime(df["Cierre"], errors="coerce")
    df["Mes"] = df["Cierre"].dt.to_period("M")
    df["Importe_ponderado"] = df["Importe"] * df["Probabilidad %"] / 100

    forecast = df.groupby("Mes").agg(
        Deals=("ID", "count"),
        Valor_Bruto=("Importe", "sum"),
        Valor_Ponderado=("Importe_ponderado", "sum"),
    ).dropna()

    console.print("\n[bold underline]FORECAST MENSUAL[/bold underline]")
    console.print(forecast.to_string())
    return forecast


if __name__ == "__main__":
    mostrar_deals(20)
    informe_pipeline()
