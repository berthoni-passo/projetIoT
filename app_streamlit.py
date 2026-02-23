import streamlit as st
import base64
from io import BytesIO
from PIL import Image
from agents_logic import app as agent_app
import mcp_server as mcp_tools

st.set_page_config(page_title="Système Multi-Agents IoT", page_icon="🏠", layout="wide")
st.title("🏠 Système Multi-Agents IoT")
st.markdown("**Contrôlez votre maison intelligente par la voix ou le texte**")

# Sidebar : État des appareils
with st.sidebar:
    st.header("📊 État des Appareils")

    st.subheader("🌡️ Thermostat")
    try:
        temp = mcp_tools.get_temperature()
        st.metric("Température Actuelle", f"{temp}°C (Simulé)")
    except:
        st.metric("Température Actuelle", "21.5°C (Simulé)")

    st.subheader("🔌 Prise Connectée (Tapo P100)")
    try:
        plug_status = mcp_tools.get_plug_status()
        st.write(f"État : **{plug_status}**")
    except:
        st.write("État : Simulé")

    st.subheader("📷 Caméra (Tapo C225)")
    try:
        cam_status = mcp_tools.get_camera_status()
        st.write(f"Statut : **{cam_status}**")
    except:
        st.write("Statut : Prête")

    if st.button("📷 Voir la caméra"):
        with st.spinner("Connexion à la caméra..."):
            result = mcp_tools.take_snapshot()
            if isinstance(result, dict) and "image_b64" in result:
                img_data = base64.b64decode(result["image_b64"])
                img = Image.open(BytesIO(img_data))
                st.image(img, caption="📷 Live — Tapo C225", width='stretch')
            else:
                st.warning(str(result))

# Zone principale : Chat
st.header("💬 Parlez à vos Agents")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if "image_b64" in message:
            img_data = base64.b64decode(message["image_b64"])
            img = Image.open(BytesIO(img_data))
            st.image(img, caption="📷 Snapshot — Tapo C225", use_container_width=True)
        if "snapshots" in message:
            cols = st.columns(len(message["snapshots"]))
            for col, snap in zip(cols, message["snapshots"]):
                img_data = base64.b64decode(snap["image_b64"])
                img = Image.open(BytesIO(img_data))
                st.image(img, caption=f"📷 {snap['position']}", width='stretch')

if prompt := st.chat_input("Que voulez-vous faire ? (ex: allume la prise, prends une photo, température ?)"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
            with st.spinner("Les agents réfléchissent..."):
                inputs = {"user_query": prompt, "messages": []}
                all_messages = []
                snapshot_b64 = None
                snapshots_list = []
                try:
                    for chunk in agent_app.stream(inputs):
                        for node_name, node_output in chunk.items():
                            if isinstance(node_output, dict):
                                if "messages" in node_output:
                                    msgs = node_output["messages"]
                                    if isinstance(msgs, list):
                                        all_messages.extend(msgs)
                                    elif isinstance(msgs, str):
                                        all_messages.append(msgs)
                                if "image_b64" in node_output:
                                    snapshot_b64 = node_output["image_b64"]
                                if "snapshots" in node_output:
                                    snapshots_list = node_output["snapshots"]
                except Exception as e:
                    all_messages.append(f"❌ Erreur Agent : {e}")

                response = "\n".join(all_messages) if all_messages else "Je suis prêt à vous aider !"
                st.markdown(response)

                # Affichage panorama (plusieurs photos)
                if snapshots_list:
                    st.markdown(f"**🔄 Tour 360° — {len(snapshots_list)} photos**")
                    cols = st.columns(len(snapshots_list))
                    for col, snap in zip(cols, snapshots_list):
                        img_data = base64.b64decode(snap["image_b64"])
                        img = Image.open(BytesIO(img_data))
                        st.image(img, caption=f"📷 {snap['position']}", width='stretch')
                    saved = {"role": "assistant", "content": response, "snapshots": snapshots_list}
                # Affichage snapshot simple
                elif snapshot_b64:
                    img_data = base64.b64decode(snapshot_b64)
                    img = Image.open(BytesIO(img_data))
                    st.image(img, caption="📷 Snapshot — Tapo C225", width='stretch')
                    saved = {"role": "assistant", "content": response, "image_b64": snapshot_b64}
                else:
                    saved = {"role": "assistant", "content": response}

                st.session_state.messages.append(saved)
                st.rerun()

# Logs
st.header("📈 Logs et Historique")
col1, col2 = st.columns(2)
with col1:
    st.subheader("Dernières Actions")
    st.info("Logs des capteurs enregistrés dans Oracle")
with col2:
    st.subheader("Alertes de Sécurité")
    st.warning("Aucune intrusion détectée")
