from typing import TypedDict, List, Annotated
import operator
import json
import os
import boto3
import oracledb
from langgraph.graph import StateGraph, END
from langchain_aws import ChatBedrock

# Import direct des outils MCP (le serveur tourne dans le même processus)
import mcp_server as mcp_tools

# --- LLM ---
llm = ChatBedrock(
    model_id="anthropic.claude-3-haiku-20240307-v1:0",
    model_kwargs={"max_tokens": 1000},
    region_name="eu-west-3"
)

# --- Client Bedrock pour les embeddings RAG ---
bedrock_runtime = boto3.client("bedrock-runtime", region_name="eu-west-3")

# --- Configuration Oracle ---
ORACLE_USER     = os.getenv("ORACLE_USER", "system")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")   # Requis dans .env
ORACLE_DSN      = os.getenv("ORACLE_DSN", "localhost:1521/FREEPDB1")

class AgentState(TypedDict):
    messages: Annotated[List[str], operator.add]
    next_agent: str
    user_query: str
    image_b64: str  # Pour transmettre le snapshot à Streamlit

# ============================================================
# FONCTION RAG : Recherche dans Oracle 23ai
# ============================================================
def get_rag_context(query: str, top_k: int = 2) -> str:
    """Génère un embedding et cherche les documents similaires dans Oracle 23ai."""
    try:
        body = json.dumps({"inputText": query})
        response = bedrock_runtime.invoke_model(
            modelId="amazon.titan-embed-text-v2:0",
            body=body,
            contentType="application/json",
            accept="application/json"
        )
        query_embedding = json.loads(response["body"].read())["embedding"]
        embedding_str = str(query_embedding)

        conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT content, source
               FROM iot_knowledge_rag
               ORDER BY VECTOR_DISTANCE(vector_data, :1, COSINE)
               FETCH FIRST :2 ROWS ONLY""",
            [embedding_str, top_k]
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            return ""
        return "\n".join([f"[{row[1]}] {row[0]}" for row in rows])

    except Exception as e:
        print(f"RAG non disponible : {e}")
        return ""

# ============================================================
# AGENTS LANGGRAPH
# ============================================================
def concierge_node(state: AgentState):
    """Routeur : classification par mots-clés PUIS LLM si nécessaire."""
    q = state['user_query'].lower()

    thermostat_keywords = ["température", "thermostat", "chauffage", "chaud", "froid", "degrés", "celsius", "chaleur", "climatisation"]
    prise_keywords = ["prise", "lumière", "lampe", "éclairage", "allume", "éteins", "éteindre", "allumer", "branché", "courant"]
    camera_keywords = ["caméra", "camera", "photo", "image", "surveillance", "intrusion", "enregistre", "snapshot", "film", "vidéo", "live", "tourne", "gauche", "droite", "haut", "bas", "panorama", "patrol", "pivote", "déplace"]

    # ⚠️ Caméra en PREMIER : "allume la caméra" ne doit pas aller vers l'agent prise
    if any(kw in q for kw in camera_keywords):
        return {"next_agent": "camera"}
    if any(kw in q for kw in thermostat_keywords):
        return {"next_agent": "thermostat"}
    if any(kw in q for kw in prise_keywords):
        return {"next_agent": "prise"}

    prompt = (
        "Tu es un routeur strict. Réponds UNIQUEMENT par un seul mot parmi : THERMOSTAT, PRISE, CAMERA, DIRECT.\n"
        "THERMOSTAT : questions sur la température ou le chauffage.\n"
        "PRISE : questions sur l'éclairage ou la prise électrique.\n"
        "CAMERA : questions sur la caméra ou la surveillance.\n"
        "DIRECT : salutations, remerciements, ou questions sans rapport avec les appareils.\n\n"
        f"Demande : '{state['user_query']}'"
    )
    decision = llm.invoke(prompt).content.upper().strip()

    if "THERMOSTAT" in decision: return {"next_agent": "thermostat"}
    if "PRISE" in decision: return {"next_agent": "prise"}
    if "CAMERA" in decision: return {"next_agent": "camera"}

    res = llm.invoke(
        f"Tu es le concierge d'une maison intelligente avec 3 agents : Thermostat, Prise connectée, Caméra. "
        f"Réponds poliment à : {state['user_query']}"
    )
    return {"messages": [res.content], "next_agent": END}


def thermostat_node(state: AgentState):
    """Agent Thermostat : utilise l'outil MCP get_temperature + RAG."""
    temp = mcp_tools.get_temperature()
    rag_context = get_rag_context(state['user_query'])
    context_str = f"\n\nContexte des manuels :\n{rag_context}" if rag_context else ""

    res = llm.invoke(
        f"Tu es l'agent Thermostat d'une maison intelligente. "
        f"Tu viens de lire la température via le système MCP (protocole de contrôle IoT). "
        f"Résultat MCP : température actuelle = {temp}°C.{context_str}\n"
        f"Réponds à la demande de l'utilisateur : '{state['user_query']}'. "
        f"Ne dis JAMAIS que tu ne peux pas lire la température - tu viens de le faire."
    )
    return {"messages": [f"🌡️ {res.content}"]}


def prise_node(state: AgentState):
    """Agent Prise (Tapo P100) : utilise les outils MCP set/get_plug_status + RAG."""
    intent_prompt = (
        f"L'utilisateur dit : '{state['user_query']}'.\n"
        "Réponds UNIQUEMENT par : ALLUMER, ETEINDRE, ou CONSULTER."
    )
    intent = llm.invoke(intent_prompt).content.upper().strip()

    if "ALLUMER" in intent:
        mcp_tools.set_plug_status(True)
    elif "ETEINDRE" in intent:
        mcp_tools.set_plug_status(False)

    etat_actuel = mcp_tools.get_plug_status()
    rag_context = get_rag_context(state['user_query'])
    context_str = f"\n\nContexte des manuels :\n{rag_context}" if rag_context else ""

    res = llm.invoke(
        f"Tu es l'agent Prise Connectée Tapo P100 d'une maison intelligente. "
        f"Tu viens d'exécuter une action via le système MCP (protocole de contrôle IoT). "
        f"Résultat MCP : la prise est maintenant {etat_actuel}.{context_str}\n"
        f"Confirme à l'utilisateur l'état actuel de la prise et réponds à : '{state['user_query']}'. "
        f"Ne dis JAMAIS que tu ne peux pas contrôler la prise - l'action est déjà effectuée via MCP."
    )
    return {"messages": [f"🔌 {res.content}"]}


def camera_node(state: AgentState):
    """Agent Caméra (Tapo C225) : snapshot, PTZ, panorama + RAG."""
    q = state['user_query'].lower()
    rag_context = get_rag_context(state['user_query'])
    context_str = f"\n\nContexte des manuels :\n{rag_context}" if rag_context else ""
    image_b64 = None
    snapshots = []

    # --- Détecter l'intention PTZ ---
    is_patrol = any(kw in q for kw in ["panorama", "patrol", "tout l'espace", "faís un tour", "fais un tour", "découverte"])
    is_left   = any(kw in q for kw in ["gauche", "left"])
    is_right  = any(kw in q for kw in ["droite", "right"])
    is_up     = any(kw in q for kw in ["haut", "up", "monte"])
    is_down   = any(kw in q for kw in ["bas", "down", "descend"])
    is_home   = any(kw in q for kw in ["home", "initial", "position"])
    is_on     = any(kw in q for kw in ["allume", "active", "démarre", "ouvre"])
    is_off    = any(kw in q for kw in ["éteins", "éteindre", "désactive", "coupe", "ferme"])

    ptz_result = None
    if is_patrol:
        result = mcp_tools.patrol_camera()
        if isinstance(result, dict) and result.get("status") == "success":
            snapshots = result.get("snapshots", [])
            ptz_result = result.get("message", "Panorama effectué.")
        else:
            ptz_result = result.get("message", "Erreur panorama.") if isinstance(result, dict) else str(result)
    elif is_on:
        ptz_result = mcp_tools.set_camera_power(True)
    elif is_off:
        ptz_result = mcp_tools.set_camera_power(False)
    elif is_left:
        ptz_result = mcp_tools.move_camera("left")
    elif is_right:
        ptz_result = mcp_tools.move_camera("right")
    elif is_up:
        ptz_result = mcp_tools.move_camera("up")
    elif is_down:
        ptz_result = mcp_tools.move_camera("down")
    elif is_home:
        ptz_result = mcp_tools.move_camera("home")

    # --- Snapshot si pas de panorama ---
    if not is_patrol:
        snapshot_result = mcp_tools.take_snapshot()
        if isinstance(snapshot_result, dict):
            image_b64 = snapshot_result.get("image_b64")
            snapshot_text = snapshot_result.get("message", "Snapshot effectué.")
        else:
            snapshot_text = str(snapshot_result)
    else:
        snapshot_text = ptz_result or "Panorama terminé."

    camera_status = mcp_tools.get_camera_status()
    ptz_info = f" Action PTZ : {ptz_result}." if ptz_result else ""

    res = llm.invoke(
        f"Tu es l'agent Caméra de surveillance d'une maison intelligente. "
        f"Tu viens d'exécuter une action via le système MCP.{ptz_info} "
        f"Résultat snapshot : '{snapshot_text}'. Statut : '{camera_status}'.{context_str}\n"
        f"Confirme à l'utilisateur que l'action a été effectuée : '{state['user_query']}'. "
        f"Ne dis JAMAIS que tu ne peux pas prendre de photos ou pivoter la caméra."
    )

    out = {"messages": [f"📷 {res.content}"]}
    if image_b64:
        out["image_b64"] = image_b64
    if snapshots:
        out["snapshots"] = snapshots
    return out


# ============================================================
# GRAPHE LANGGRAPH
# ============================================================
workflow = StateGraph(AgentState)
workflow.add_node("concierge", concierge_node)
workflow.add_node("thermostat", thermostat_node)
workflow.add_node("prise", prise_node)
workflow.add_node("camera", camera_node)

workflow.set_entry_point("concierge")
workflow.add_conditional_edges(
    "concierge",
    lambda x: x["next_agent"],
    {"thermostat": "thermostat", "prise": "prise", "camera": "camera", END: END}
)
workflow.add_edge("thermostat", END)
workflow.add_edge("prise", END)
workflow.add_edge("camera", END)

app = workflow.compile()
