import os
import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
from hubspot import HubSpot
from dotenv import load_dotenv

load_dotenv()

def get_client() -> HubSpot:
    token = os.getenv("HUBSPOT_TOKEN")
    if not token:
        raise ValueError("Falta el HUBSPOT_TOKEN en el archivo .env")
    return HubSpot(access_token=token)

def get_headers() -> dict:
    token = os.getenv("HUBSPOT_TOKEN")
    if not token:
        raise ValueError("Falta el HUBSPOT_TOKEN en el archivo .env")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
