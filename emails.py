"""
Email marketing: listar templates, crear emails, enviar a listas/contactos.
Usa la API de Marketing Emails v3 y Transactional Emails.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import requests
from rich.console import Console
from rich.table import Table
from client import get_headers

console = Console()

BASE = "https://api.hubapi.com"
HEADERS = get_headers()


def _get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def _post(path, data):
    r = requests.post(f"{BASE}{path}", headers=HEADERS, json=data)
    r.raise_for_status()
    return r.json()


# ── Listas de contactos ────────────────────────────────────────────────────────

def listar_listas():
    """Lista todas las listas de contactos."""
    data = _get("/contacts/v1/lists", {"count": 250})
    listas = []
    for l in data.get("lists", []):
        listas.append({
            "ID": l["listId"],
            "Nombre": l["name"],
            "Tipo": l["listType"],
            "Tamaño": l.get("metaData", {}).get("size", 0),
            "Dinámica": "Sí" if l["dynamic"] else "No",
        })
    table = Table(title=f"Listas de Contactos ({len(listas)})", show_lines=True)
    for col in ["ID", "Nombre", "Tipo", "Tamaño", "Dinámica"]:
        table.add_column(col)
    for l in listas:
        table.add_row(str(l["ID"]), l["Nombre"], l["Tipo"], str(l["Tamaño"]), l["Dinámica"])
    console.print(table)
    return listas


def contactos_en_lista(lista_id):
    """Devuelve contactos de una lista específica."""
    data = _get(f"/contacts/v1/lists/{lista_id}/contacts/all", {"count": 100})
    contactos = []
    for c in data.get("contacts", []):
        props = c.get("properties", {})
        contactos.append({
            "ID": c["vid"],
            "Email": props.get("email", {}).get("value", ""),
            "Nombre": props.get("firstname", {}).get("value", ""),
            "Apellido": props.get("lastname", {}).get("value", ""),
        })
    return contactos


# ── Marketing Emails ───────────────────────────────────────────────────────────

def listar_emails_marketing(limite=50):
    """Lista los emails de marketing existentes."""
    data = _get("/marketing/v3/emails", {"limit": limite})
    emails = []
    for e in data.get("results", []):
        emails.append({
            "ID": e["id"],
            "Nombre": e.get("name", ""),
            "Asunto": e.get("subject", ""),
            "Estado": e.get("state", ""),
            "Tipo": e.get("emailType", ""),
            "Creado": e.get("createdAt", "")[:10],
        })
    table = Table(title=f"Emails Marketing ({len(emails)})", show_lines=True)
    for col in ["ID", "Nombre", "Asunto", "Estado", "Tipo", "Creado"]:
        table.add_column(col, style="cyan" if col == "Nombre" else "white")
    for e in emails:
        table.add_row(e["ID"], e["Nombre"], e["Asunto"], e["Estado"], e["Tipo"], e["Creado"])
    console.print(table)
    return emails


def crear_email_marketing(nombre, asunto, html_body, from_name="Skillia", from_email=None):
    """
    Crea un nuevo email de marketing en HubSpot.
    Requiere que from_email sea un email verificado en tu cuenta.
    """
    payload = {
        "name": nombre,
        "subject": asunto,
        "content": {
            "body": html_body,
        },
        "emailType": "BATCH_EMAIL",
        "from": {
            "name": from_name,
        },
    }
    if from_email:
        payload["from"]["email"] = from_email

    result = _post("/marketing/v3/emails", payload)
    console.print(f"[green]Email creado: {nombre} — ID: {result['id']}[/green]")
    return result


def enviar_email_a_lista(email_id, lista_id):
    """
    Envía un email de marketing a una lista de contactos.
    El email debe estar en estado DRAFT y la lista debe existir.
    """
    payload = {
        "emailId": email_id,
        "contactListIds": [lista_id],
    }
    result = _post(f"/marketing/v3/emails/{email_id}/send", {})
    console.print(f"[green]Email {email_id} enviado a lista {lista_id}[/green]")
    return result


# ── Email transaccional (1 a 1) ────────────────────────────────────────────────

def enviar_email_transaccional(template_id, to_email, to_name, propiedades_personalizadas=None):
    """
    Envía un email transaccional a un contacto individual.
    Requiere tener el add-on de Transactional Email activado en HubSpot.
    template_id: ID del template en HubSpot
    """
    payload = {
        "emailId": template_id,
        "message": {
            "to": to_email,
        },
        "contactProperties": {
            "firstname": to_name,
        },
        "customProperties": propiedades_personalizadas or {},
    }
    result = _post("/marketing/v3/transactional/single-email/send", payload)
    console.print(f"[green]Email transaccional enviado a {to_email}[/green]")
    return result


# ── Estadísticas de emails ─────────────────────────────────────────────────────

def stats_email(email_id):
    """Obtiene métricas de un email enviado."""
    data = _get(f"/marketing/v3/emails/{email_id}/statistics/histogram", {
        "startTimestamp": "2024-01-01T00:00:00Z",
        "interval": "DAY",
    })
    return data


def stats_email_resumen(email_id):
    """Métricas de performance de un email."""
    data = _get(f"/marketing/v3/emails/{email_id}/statistics/total")
    stats = data.get("counters", {})
    console.print(f"\n[bold]Estadísticas email {email_id}:[/bold]")
    for key, val in stats.items():
        console.print(f"  {key}: [cyan]{val}[/cyan]")
    return stats


if __name__ == "__main__":
    listar_listas()
    listar_emails_marketing()
