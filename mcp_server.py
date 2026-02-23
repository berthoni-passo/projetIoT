"""
Serveur MCP (Model Context Protocol) pour le système IoT - Version EC2
EC2 appelle le bridge local (local_bridge.py) via ngrok pour contrôler les appareils.
Pas de dépendance à 'tapo' - uniquement des appels HTTP vers le bridge.
"""
from mcp.server.fastmcp import FastMCP
import requests
import os

# Initialisation du serveur MCP
mcp = FastMCP("IoT Smart Home Server")

# URL du bridge local exposé via ngrok
BRIDGE_URL = os.getenv("BRIDGE_URL")   # Requis dans .env — ex: https://xxxx.ngrok-free.dev

# Header obligatoire pour bypasser la page d'avertissement ngrok
NGROK_HEADERS = {"ngrok-skip-browser-warning": "true"}

# État thermostat simulé
_temperature = 21.5

# ============================================================
# OUTILS MCP - PRISE CONNECTÉE (Tapo P100 via Bridge ngrok)
# ============================================================

@mcp.tool()
def set_plug_status(status: bool) -> str:
    """Allume ou éteint la prise connectée Tapo P100.

    Args:
        status: True pour allumer, False pour éteindre
    """
    try:
        endpoint = f"{BRIDGE_URL}/plug/{'on' if status else 'off'}"
        response = requests.post(endpoint, headers=NGROK_HEADERS, timeout=10)
        data = response.json()
        if data["status"] == "success":
            return f"Prise Tapo P100 : {data['state']} (commande physique envoyée)."
        return f"Erreur bridge : {data.get('message', 'Inconnue')}"
    except Exception as e:
        return f"Erreur connexion bridge : {e}"

@mcp.tool()
def get_plug_status() -> str:
    """Retourne l'état actuel de la prise connectée Tapo P100."""
    try:
        response = requests.get(f"{BRIDGE_URL}/plug/status", headers=NGROK_HEADERS, timeout=10)
        data = response.json()
        if data["status"] == "success":
            return data["state"]
        return "INCONNU"
    except Exception as e:
        return f"Erreur connexion bridge : {e}"

# ============================================================
# OUTILS MCP - THERMOSTAT (Simulé)
# ============================================================

@mcp.tool()
def get_temperature() -> float:
    """Lit la température actuelle du salon (thermostat simulé)."""
    return _temperature

@mcp.tool()
def set_temperature(target: float) -> str:
    """Règle la température de consigne du thermostat.

    Args:
        target: Température cible en degrés Celsius
    """
    global _temperature
    _temperature = target
    return f"Thermostat réglé sur {target}°C."

# ============================================================
# OUTILS MCP - CAMÉRA (Tapo C225 via Bridge - RTSP réel)
# ============================================================

@mcp.tool()
def take_snapshot() -> dict:
    """Prend une photo réelle avec la caméra de sécurité Tapo C225."""
    try:
        response = requests.post(
            f"{BRIDGE_URL}/camera/snapshot",
            headers=NGROK_HEADERS,
            timeout=15
        )
        data = response.json()
        if data.get("status") == "success":
            return data
        return {"status": "error", "message": data.get("message", "Erreur inconnue")}
    except Exception as e:
        return {"status": "error", "message": f"Erreur caméra : {e}"}

@mcp.tool()
def set_camera_power(on: bool) -> str:
    """Allume ou éteint la caméra Tapo C225.

    Args:
        on: True pour allumer, False pour éteindre
    """
    try:
        endpoint = f"{BRIDGE_URL}/camera/{'on' if on else 'off'}"
        response = requests.post(endpoint, headers=NGROK_HEADERS, timeout=10)
        data = response.json()
        return data.get("message", "Commande envoyée.")
    except Exception as e:
        return f"Erreur caméra on/off : {e}"

@mcp.tool()
def get_camera_status() -> str:
    """Retourne le statut de la caméra Tapo C225."""
    try:
        response = requests.get(
            f"{BRIDGE_URL}/camera/status",
            headers=NGROK_HEADERS,
            timeout=10
        )
        data = response.json()
        state = data.get("state", "Inconnue")
        rtsp = "✅ RTSP actif" if data.get("rtsp") else "⚠️ RTSP inactif"
        return f"Caméra Tapo C225 : {state}, résolution 2K. {rtsp}"
    except Exception as e:
        return f"Erreur caméra : {e}"

@mcp.tool()
def move_camera(direction: str) -> str:
    """Déplace la caméra Tapo C225 dans une direction.

    Args:
        direction: 'left', 'right', 'up', 'down' ou 'home'
    """
    try:
        endpoint = f"{BRIDGE_URL}/camera/ptz/{direction}"
        response = requests.post(endpoint, headers=NGROK_HEADERS, timeout=15)
        data = response.json()
        return data.get("message", f"Caméra déplacée : {direction}")
    except Exception as e:
        return f"Erreur PTZ : {e}"

@mcp.tool()
def patrol_camera() -> dict:
    """Lance un panorama de la pièce : la caméra tourne et prend des photos à gauche, centre et droite."""
    try:
        response = requests.post(
            f"{BRIDGE_URL}/camera/ptz/patrol",
            headers=NGROK_HEADERS,
            timeout=60  # Panorama prend ~30s
        )
        data = response.json()
        return data
    except Exception as e:
        return {"status": "error", "message": f"Erreur panorama : {e}"}

# ============================================================
# POINT D'ENTRÉE
# ============================================================
if __name__ == "__main__":
    mcp.run()
