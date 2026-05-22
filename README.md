# HubSpot CRM — Skillia

## Setup

1. Copia tu nuevo token en `.env`:
```
HUBSPOT_TOKEN=pat-eu1-TU_NUEVO_TOKEN
```

2. Activa el entorno virtual:
```bash
source venv/bin/activate
```

3. Lanza el dashboard:
```bash
python dashboard.py
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `client.py` | Conexión autenticada con HubSpot |
| `contactos.py` | Listar, buscar, crear, exportar contactos |
| `deals.py` | Pipeline, forecast, informes de negocios |
| `emails.py` | Listas, campañas, envío de emails |
| `analytics.py` | Métricas, performance, informe CRM |
| `dashboard.py` | Panel interactivo en terminal |

## Uso directo

```python
from contactos import listar_contactos, crear_contacto, exportar_contactos_csv
from deals import listar_deals, informe_pipeline, forecast_mensual
from emails import listar_listas, listar_emails_marketing
from analytics import informe_crm_completo, resumen_emails_performance
```
