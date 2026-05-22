"""
Dashboard principal — ejecuta este archivo para ver el panel completo.
"""
from rich.console import Console
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
import sys, os

sys.path.insert(0, os.path.dirname(__file__))

console = Console()


def header():
    console.print(Panel(
        "[bold white]SKILLIA — HubSpot CRM Dashboard[/bold white]\n"
        "[dim]Contactos · Deals · Emails · Analytics[/dim]",
        style="bold blue", expand=True
    ))


def menu():
    opciones = [
        ("1", "Ver contactos"),
        ("2", "Estadísticas de contactos"),
        ("3", "Ver deals / pipeline"),
        ("4", "Forecast mensual"),
        ("5", "Informe CRM completo"),
        ("6", "Ver listas de email"),
        ("7", "Ver emails de marketing"),
        ("8", "Performance de emails"),
        ("9", "Exportar contactos a CSV"),
        ("10", "Exportar deals a CSV"),
        ("0", "Salir"),
    ]
    console.print("\n[bold]¿Qué quieres ver?[/bold]")
    for num, desc in opciones:
        console.print(f"  [cyan]{num:>2}[/cyan]  {desc}")
    return input("\nOpción: ").strip()


def run():
    header()
    while True:
        opcion = menu()

        if opcion == "1":
            from contactos import mostrar_contactos
            n = input("¿Cuántos contactos mostrar? [20]: ").strip() or "20"
            mostrar_contactos(int(n))

        elif opcion == "2":
            from contactos import stats_contactos
            stats_contactos()

        elif opcion == "3":
            from deals import mostrar_deals, informe_pipeline
            mostrar_deals(30)
            informe_pipeline()

        elif opcion == "4":
            from deals import forecast_mensual
            forecast_mensual()

        elif opcion == "5":
            from analytics import informe_crm_completo
            informe_crm_completo()

        elif opcion == "6":
            from emails import listar_listas
            listar_listas()

        elif opcion == "7":
            from emails import listar_emails_marketing
            listar_emails_marketing()

        elif opcion == "8":
            from analytics import resumen_emails_performance
            resumen_emails_performance()

        elif opcion == "9":
            from contactos import exportar_contactos_csv
            exportar_contactos_csv()

        elif opcion == "10":
            from deals import exportar_deals_csv
            exportar_deals_csv()

        elif opcion == "0":
            console.print("[dim]Hasta luego.[/dim]")
            break
        else:
            console.print("[red]Opción no válida.[/red]")


if __name__ == "__main__":
    run()
