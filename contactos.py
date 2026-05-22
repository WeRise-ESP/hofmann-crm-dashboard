"""
Gestión de contactos: listar, buscar, crear, actualizar, exportar.
"""
import pandas as pd
from rich.console import Console
from rich.table import Table
from hubspot.crm.contacts import SimplePublicObjectInputForCreate, ApiException
from client import get_client

console = Console()
client = get_client()


def listar_contactos(limite=100):
    """Lista contactos con sus propiedades principales."""
    props = ["firstname", "lastname", "email", "phone", "company",
             "jobtitle", "lifecyclestage", "hs_lead_status", "createdate"]
    contactos = []
    after = None

    while True:
        resp = client.crm.contacts.basic_api.get_page(
            limit=min(limite - len(contactos), 100),
            properties=props,
            after=after,
        )
        for c in resp.results:
            p = c.properties
            contactos.append({
                "ID": c.id,
                "Nombre": f"{p.get('firstname','')} {p.get('lastname','')}".strip(),
                "Email": p.get("email", ""),
                "Teléfono": p.get("phone", ""),
                "Empresa": p.get("company", ""),
                "Cargo": p.get("jobtitle", ""),
                "Etapa": p.get("lifecyclestage", ""),
                "Estado lead": p.get("hs_lead_status", ""),
                "Creado": p.get("createdate", "")[:10] if p.get("createdate") else "",
            })
        if not resp.paging or len(contactos) >= limite:
            break
        after = resp.paging.next.after

    return contactos


def mostrar_contactos(limite=50):
    """Muestra contactos en tabla visual."""
    contactos = listar_contactos(limite)
    table = Table(title=f"Contactos HubSpot ({len(contactos)})", show_lines=True)
    for col in ["ID", "Nombre", "Email", "Empresa", "Cargo", "Etapa"]:
        table.add_column(col, style="cyan" if col == "Nombre" else "white")
    for c in contactos:
        table.add_row(c["ID"], c["Nombre"], c["Email"], c["Empresa"], c["Cargo"], c["Etapa"])
    console.print(table)


def buscar_contacto(email=None, nombre=None):
    """Busca un contacto por email o nombre."""
    from hubspot.crm.contacts.models import Filter, FilterGroup, PublicObjectSearchRequest
    filtros = []
    if email:
        filtros.append(Filter(property_name="email", operator="EQ", value=email))
    if nombre:
        filtros.append(Filter(property_name="firstname", operator="CONTAINS_TOKEN", value=nombre))

    req = PublicObjectSearchRequest(
        filter_groups=[FilterGroup(filters=filtros)],
        properties=["firstname", "lastname", "email", "phone", "company", "lifecyclestage"],
    )
    resp = client.crm.contacts.search_api.do_search(public_object_search_request=req)
    return resp.results


def crear_contacto(email, nombre, apellido="", empresa="", telefono="", cargo=""):
    """Crea un nuevo contacto."""
    props = {"email": email, "firstname": nombre, "lastname": apellido,
              "company": empresa, "phone": telefono, "jobtitle": cargo}
    props = {k: v for k, v in props.items() if v}
    obj = SimplePublicObjectInputForCreate(properties=props)
    try:
        result = client.crm.contacts.basic_api.create(
            simple_public_object_input_for_create=obj
        )
        console.print(f"[green]Contacto creado: {nombre} {apellido} ({email}) — ID: {result.id}[/green]")
        return result
    except ApiException as e:
        console.print(f"[red]Error creando contacto: {e.body}[/red]")


def actualizar_contacto(contact_id, **props):
    """Actualiza propiedades de un contacto por ID."""
    from hubspot.crm.contacts import SimplePublicObjectInput
    obj = SimplePublicObjectInput(properties=props)
    result = client.crm.contacts.basic_api.update(contact_id=contact_id,
                                                   simple_public_object_input=obj)
    console.print(f"[green]Contacto {contact_id} actualizado.[/green]")
    return result


def exportar_contactos_csv(ruta="exports/contactos.csv", limite=5000):
    """Exporta todos los contactos a CSV."""
    import os; os.makedirs("exports", exist_ok=True)
    contactos = listar_contactos(limite)
    df = pd.DataFrame(contactos)
    df.to_csv(ruta, index=False, encoding="utf-8-sig")
    console.print(f"[green]Exportado: {ruta} ({len(contactos)} contactos)[/green]")
    return df


def stats_contactos():
    """Estadísticas rápidas de contactos."""
    df = pd.DataFrame(listar_contactos(5000))
    console.print("\n[bold]Estadísticas de Contactos[/bold]")
    console.print(f"Total: [cyan]{len(df)}[/cyan]")
    if "Etapa" in df.columns:
        console.print("\nPor etapa de ciclo de vida:")
        console.print(df["Etapa"].value_counts().to_string())
    if "Estado lead" in df.columns:
        console.print("\nPor estado de lead:")
        console.print(df["Estado lead"].value_counts().to_string())


if __name__ == "__main__":
    mostrar_contactos(20)
