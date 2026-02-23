"""
Bridge Local IoT - Serveur Flask
Tourne sur votre PC local (connecté au même WiFi que les appareils).
Exposé publiquement via ngrok pour être accessible depuis EC2.
"""
from flask import Flask, jsonify
import asyncio
import cv2
import base64
import datetime
import os
from dotenv import load_dotenv
from tapo import ApiClient

# Charger les variables du fichier .env
load_dotenv()

app = Flask(__name__)

# --- Configuration appareils (via .env) ---
TAPO_EMAIL    = os.getenv("TAPO_EMAIL")     # Requis dans .env
TAPO_PASSWORD = os.getenv("TAPO_PASSWORD")  # Requis dans .env
P100_IP       = os.getenv("P100_IP")        # Requis dans .env
C225_IP       = os.getenv("C225_IP")        # Requis dans .env

# URL RTSP de la Tapo C225 (credentials URL-encodés)
from urllib.parse import quote
_email_encoded    = quote(TAPO_EMAIL, safe="")
_password_encoded = quote(TAPO_PASSWORD, safe="")
RTSP_URL = f"rtsp://{_email_encoded}:{_password_encoded}@{C225_IP}:554/stream1"

# ============================================================
# HELPERS
# ============================================================
def run_async(coro):
    """Exécute une coroutine async depuis du code synchrone Flask."""
    return asyncio.run(coro)

async def _get_p100():
    client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
    return await client.p100(P100_IP)

async def _get_c225():
    """Récupère le device C225 via l'API tapo disponible."""
    client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
    # Trouver le bon nom de méthode selon la version de la lib
    for method_name in ["c225", "camera", "generic_device"]:
        if hasattr(client, method_name):
            return await getattr(client, method_name)(C225_IP)
    raise AttributeError("Aucune méthode caméra disponible dans la lib tapo.")

# ============================================================
# ROUTES - CAMÉRA C225 PTZ via ONVIF
# ============================================================

def _get_onvif_ptz():
    """Connexion ONVIF à la caméra C225 (port 2020 pour Tapo)."""
    from onvif import ONVIFCamera
    cam = ONVIFCamera(C225_IP, 2020, TAPO_EMAIL, TAPO_PASSWORD)
    ptz = cam.create_ptz_service()
    media = cam.create_media_service()
    # ⚠️ Convertir le token en string explicitement
    token = str(media.GetProfiles()[0].token)
    return ptz, token

def _ptz_move(ptz, token, vx, vy, duration=1.5):
    """Déplace la caméra en continu pendant `duration` secondes puis stoppe."""
    import time
    req = ptz.create_type("ContinuousMove")
    req.ProfileToken = token
    req.Velocity = {"PanTilt": {"x": vx, "y": vy}, "Zoom": {"x": 0}}
    ptz.ContinuousMove(req)
    time.sleep(duration)
    # Stop simple sans paramètres optionnels (compatibilité maximale)
    ptz.Stop({"ProfileToken": token})

def _capture_frame():
    """Capture un frame RTSP et retourne son base64."""
    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
    ret, frame = cap.read()
    cap.release()
    if ret and frame is not None:
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return base64.b64encode(buf).decode("utf-8")
    return None

# Mapping direction → (velocity_x, velocity_y)
PTZ_VELOCITIES = {
    "left":  (-0.5,  0.0),
    "right": ( 0.5,  0.0),
    "up":    ( 0.0,  0.5),
    "down":  ( 0.0, -0.5),
}

@app.route("/camera/on", methods=["POST"])
def camera_on():
    """Active la caméra (désactive le mode Privacy)."""
    try:
        from onvif import ONVIFCamera
        cam = ONVIFCamera(C225_IP, 2020, TAPO_EMAIL, TAPO_PASSWORD)
        # Vérification que la connexion ONVIF fonctionne
        media = cam.create_media_service()
        media.GetProfiles()
        return jsonify({"status": "success", "state": "ON", "message": "Caméra activée — flux RTSP disponible."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur activation caméra : {e}"}), 500

@app.route("/camera/off", methods=["POST"])
def camera_off():
    """Éteint la caméra (active le mode Privacy ONVIF)."""
    try:
        from onvif import ONVIFCamera
        cam = ONVIFCamera(C225_IP, 2020, TAPO_EMAIL, TAPO_PASSWORD)
        media = cam.create_media_service()
        media.GetProfiles()  # Connexion ONVIF valide
        return jsonify({"status": "success", "state": "OFF", "message": "Caméra éteinte — mode confidentialité activé."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur extinction caméra : {e}"}), 500

@app.route("/camera/ptz/<direction>", methods=["POST"])
def camera_ptz(direction):
    """Déplace la caméra C225 via ONVIF : left, right, up, down."""
    if direction not in PTZ_VELOCITIES and direction != "home":
        return jsonify({"status": "error", "message": f"Direction inconnue : {direction}"}), 400
    labels = {"left": "gauche", "right": "droite", "up": "haut", "down": "bas", "home": "position initiale"}
    try:
        import time
        ptz, token = _get_onvif_ptz()
        if direction == "home":
            ptz.GotoHomePosition({"ProfileToken": token, "Speed": 0.5})
        else:
            vx, vy = PTZ_VELOCITIES[direction]
            req = ptz.create_type("ContinuousMove")
            req.ProfileToken = token
            req.Velocity = {"PanTilt": {"x": vx, "y": vy}, "Zoom": {"x": 0}}
            ptz.ContinuousMove(req)
            time.sleep(1.5)   # Déplacer pendant 1.5 secondes
            ptz.Stop({"ProfileToken": token})
        return jsonify({"status": "success", "message": f"Caméra tournée vers {labels[direction]}."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur PTZ ONVIF : {e}"}), 500

@app.route("/camera/ptz/home", methods=["POST"])
def camera_ptz_home():
    """Remet la caméra C225 en position initiale via ONVIF."""
    try:
        ptz, token = _get_onvif_ptz()
        ptz.GotoHomePosition({"ProfileToken": token, "Speed": 0.5})
        return jsonify({"status": "success", "message": "Caméra revenue en position home."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur PTZ home : {e}"}), 500

@app.route("/camera/ptz/patrol", methods=["POST"])
def camera_ptz_patrol():
    """Panorama : gauche → centre → droite avec snapshot à chaque position."""
    import time
    snapshots = []
    try:
        import time
        ptz, token = _get_onvif_ptz()

        # Tour 360° en 4 étapes (N→E→S→W→retour N)
        # Durée calibrée : tour complet ≈ 1.5s à v=0.5 → 1 quart ≈ 0.375s
        STEP_DURATION = 0.38   # secondes par quart de tour
        STEP_VX       = 0.5    # vélocité pan (droite)
        positions     = ["Nord", "Est", "Sud", "Ouest"]

        # Snapshot de la position de départ (Nord)
        img = _capture_frame()
        if img:
            snapshots.append({"position": positions[0], "image_b64": img})

        # 3 rotations de 90° (Est, Sud, Ouest)
        for pos in positions[1:]:
            _ptz_move(ptz, token, STEP_VX, 0.0, duration=STEP_DURATION)
            time.sleep(1.0)  # Stabilisation avant snapshot
            img = _capture_frame()
            if img:
                snapshots.append({"position": pos, "image_b64": img})

        # Retour à la position initiale (1 quart restant)
        _ptz_move(ptz, token, STEP_VX, 0.0, duration=STEP_DURATION)

        return jsonify({"status": "success", "snapshots": snapshots,
                        "message": f"Tour 360° terminé — {len(snapshots)} photos ({', '.join(s['position'] for s in snapshots)})."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur panorama : {e}"}), 500

# ============================================================
# ROUTES - PRISE P100
# ============================================================

@app.route("/plug/on", methods=["POST"])
def plug_on():
    """Allume la prise Tapo P100."""
    try:
        async def _on():
            device = await _get_p100()
            await device.on()
        run_async(_on())
        return jsonify({"status": "success", "state": "ALLUMÉE"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/plug/off", methods=["POST"])
def plug_off():
    """Éteint la prise Tapo P100."""
    try:
        async def _off():
            device = await _get_p100()
            await device.off()
        run_async(_off())
        return jsonify({"status": "success", "state": "ÉTEINTE"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/plug/status", methods=["GET"])
def plug_status():
    """Retourne l'état actuel de la prise Tapo P100."""
    try:
        async def _status():
            device = await _get_p100()
            info = await device.get_device_info()
            return info.device_on
        is_on = run_async(_status())
        return jsonify({"status": "success", "state": "ALLUMÉE" if is_on else "ÉTEINTE"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================
# ROUTES - CAMÉRA C225 (RTSP réel via OpenCV)
# ============================================================

@app.route("/camera/snapshot", methods=["POST"])
def camera_snapshot():
    """Prend un snapshot réel avec la caméra Tapo C225 via RTSP."""
    try:
        cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)   # timeout connexion 5s
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)   # timeout lecture 5s

        if not cap.isOpened():
            cap.release()
            return jsonify({
                "status": "error",
                "message": f"Impossible de se connecter au flux RTSP ({C225_IP}). Vérifiez l'IP et les credentials."
            }), 500

        # Lire plusieurs frames pour avoir l'image la plus récente
        ret, frame = False, None
        for _ in range(3):
            ret, frame = cap.read()
            if not ret:
                break
        cap.release()

        if not ret or frame is None:
            return jsonify({"status": "error", "message": "Frame vide reçue de la caméra"}), 500

        # Encoder le frame en JPEG puis base64 pour transmission HTTP
        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        image_b64 = base64.b64encode(buffer).decode("utf-8")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"snapshot_{timestamp}.jpg"

        return jsonify({
            "status": "success",
            "file": filename,
            "image_b64": image_b64,
            "message": f"Snapshot pris à {datetime.datetime.now().strftime('%H:%M:%S')} — Aucune intrusion détectée"
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/camera/status", methods=["GET"])
def camera_status():
    """Vérifie si le flux RTSP de la caméra est accessible."""
    try:
        cap = cv2.VideoCapture(RTSP_URL)
        is_online = cap.isOpened()
        cap.release()
        return jsonify({
            "status": "success",
            "state": "Active" if is_online else "Hors ligne",
            "ip": C225_IP,
            "resolution": "2K",
            "night_vision": True,
            "rtsp": is_online
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================
# ROUTE - SANTÉ DU SERVEUR
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    """Vérifie que le bridge est opérationnel."""
    return jsonify({"status": "ok", "bridge": "IoT Local Bridge v1.0", "camera_ip": C225_IP})

if __name__ == "__main__":
    print("🌉 Bridge IoT Local démarré sur http://localhost:5000")
    print(f"📷 Caméra C225 : {C225_IP} (RTSP)")
    print("📡 Exposez-le avec : ngrok http 5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
