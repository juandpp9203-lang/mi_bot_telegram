import os
import re
import json
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import telebot
import requests
import dateparser
from dateparser.search import search_dates

from flask import Flask, request


# ============================================================
# VARIABLES DE ENTORNO
# ============================================================

API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://mi-bot-telegram-thxw.onrender.com")
CRON_SECRET = os.getenv("CRON_SECRET", "")
TIMEZONE = os.getenv("TIMEZONE", "America/Bogota")

ROLE_CHAT_ID = os.getenv("ROLE_CHAT_ID", "-1003877180630")
ASSISTANT_CHAT_ID = os.getenv("ASSISTANT_CHAT_ID", "-5291781629")

THREAD_MEDIEVAL = os.getenv("THREAD_MEDIEVAL", "2")
THREAD_POLITICA = os.getenv("THREAD_POLITICA", "6")
THREAD_FRIEND = os.getenv("THREAD_FRIEND", "4")
THREAD_SIMULACION = os.getenv("THREAD_SIMULACION", "5")

ROLE_MODEL = os.getenv("ROLE_MODEL", "sao10k/l3.3-euryale-70b")
FALLBACK_ROLE_MODEL = os.getenv("FALLBACK_ROLE_MODEL", "gryphe/mythomax-l2-13b")
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL", "meta-llama/llama-3.1-70b-instruct")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "meta-llama/llama-3-8b-instruct")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "meta-llama/llama-3-8b-instruct")

IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "replicate")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "black-forest-labs/flux-schnell")
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1024x1024")
IMAGE_STYLE = os.getenv(
    "IMAGE_STYLE",
    "illustrated novel style, cinematic lighting, painterly realism, immersive scene, high detail, no text"
)

AUTO_IMAGES_ENABLED = os.getenv("AUTO_IMAGES_ENABLED", "true").lower() == "true"
AUTO_IMAGE_MIN_MINUTES = int(os.getenv("AUTO_IMAGE_MIN_MINUTES", "10"))
AUTO_IMAGE_MAX_PER_HOUR = int(os.getenv("AUTO_IMAGE_MAX_PER_HOUR", "3"))
AUTO_IMAGE_MAX_PER_DAY = int(os.getenv("AUTO_IMAGE_MAX_PER_DAY", "8"))

STATE_FILE = os.getenv("STATE_FILE", "state.json")


# ============================================================
# VALIDACIONES
# ============================================================

if not API_TOKEN:
    raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en Render.")

if not OPENROUTER_KEY:
    raise RuntimeError("Falta OPENROUTER_API_KEY en Render.")


# ============================================================
# INICIALIZACIÓN
# ============================================================

bot = telebot.TeleBot(API_TOKEN, threaded=False)
app = Flask(__name__)

state_lock = threading.Lock()

chat_histories = {}
chat_summaries = {}
message_counters = {}
last_scenes = {}
reminders = []
auto_image_log = {}
processed_update_ids = set()


# ============================================================
# PERSISTENCIA SIMPLE
# ============================================================

def load_state():
    global chat_summaries, message_counters, last_scenes, reminders, auto_image_log

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        chat_summaries = data.get("chat_summaries", {})
        message_counters = data.get("message_counters", {})
        last_scenes = data.get("last_scenes", {})
        reminders = data.get("reminders", [])
        auto_image_log = data.get("auto_image_log", {})

    except Exception as e:
        print(f"[WARN] No se pudo cargar state.json: {e}")


def save_state():
    try:
        with state_lock:
            data = {
                "chat_summaries": chat_summaries,
                "message_counters": message_counters,
                "last_scenes": last_scenes,
                "reminders": reminders,
                "auto_image_log": auto_image_log
            }

            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"[WARN] No se pudo guardar state.json: {e}")


load_state()


# ============================================================
# UTILIDADES
# ============================================================

def now_local():
    return datetime.now(ZoneInfo(TIMEZONE))


def same_id(a, b):
    return str(a) == str(b)


def get_thread_id(message):
    return getattr(message, "message_thread_id", None) or 0


def get_memory_key(chat_id, thread_id):
    return f"{chat_id}:{thread_id}"


def get_chat_mode(chat_id, thread_id=0):
    if ASSISTANT_CHAT_ID and same_id(chat_id, ASSISTANT_CHAT_ID):
        return "assistant"

    if ROLE_CHAT_ID and same_id(chat_id, ROLE_CHAT_ID):
        if THREAD_MEDIEVAL and same_id(thread_id, THREAD_MEDIEVAL):
            return "medieval"

        if THREAD_POLITICA and same_id(thread_id, THREAD_POLITICA):
            return "politica"

        if THREAD_FRIEND and same_id(thread_id, THREAD_FRIEND):
            return "friend"

        if THREAD_SIMULACION and same_id(thread_id, THREAD_SIMULACION):
            return "simulacion"

        return "role_general"

    return "default"


def get_model_for_mode(mode):
    if mode == "assistant":
        return ASSISTANT_MODEL

    if mode in ["medieval", "politica", "friend", "simulacion", "role_general"]:
        return ROLE_MODEL

    return DEFAULT_MODEL


def get_max_tokens_for_mode(mode):
    if mode in ["medieval", "politica", "simulacion"]:
        return 450

    if mode == "friend":
        return 350

    if mode == "assistant":
        return 300

    return 400


def get_temperature_for_mode(mode):
    if mode in ["medieval", "simulacion"]:
        return 0.85

    if mode == "politica":
        return 0.75

    if mode == "friend":
        return 0.8

    if mode == "assistant":
        return 0.35

    return 0.7


def send_message_to_thread(chat_id, thread_id, text, reply_to_message_id=None):
    kwargs = {}

    if thread_id:
        kwargs["message_thread_id"] = thread_id

    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id

    if len(text) <= 4000:
        bot.send_message(chat_id, text, **kwargs)
        return

    chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)]
    for chunk in chunks:
        bot.send_message(chat_id, chunk, **kwargs)


def send_photo_to_thread(chat_id, thread_id, photo_url, caption=None):
    kwargs = {}

    if thread_id:
        kwargs["message_thread_id"] = thread_id

    bot.send_photo(chat_id, photo_url, caption=caption, **kwargs)


# ============================================================
# PROMPTS POR MODO
# ============================================================

def get_system_prompt_for_mode(mode):
    if mode == "assistant":
        return (
            "Eres el asistente personal y profesional de Juan. "
            "Actúas como segunda memoria, secretario, organizador y apoyo lógico. "
            "Ayudas con pendientes, recordatorios, redacción jurídica, estudio, programación y organización diaria. "
            "Sé claro, sobrio, confiable, breve y práctico. "
            "No inventes datos jurídicos, jurisprudencia, normas ni fechas. "
            "Cuando Juan te dé un pendiente, recordatorio o medicamento, confirma brevemente lo entendido. "
            "Prioriza que Juan no olvide tareas, medicamentos, compromisos, ideas y asuntos importantes."
        )

    if mode == "medieval":
        return (
            "Eres un Máster de rol privado, inmersivo, literario y estratégico para una simulación medieval de gran estrategia con magia integrada al mundo. "
            "Tu estilo mezcla Crusader Kings, intriga cortesana, guerras dinásticas, sucesiones, fe, nobleza, economía feudal, vasallos, linajes, matrimonios políticos, herejías, espionaje, profecías, pactos arcanos y conflictos morales. "
            "El jugador puede encarnar a un rey, reina, duque, heredero, bastardo, regente, general, consejero, sacerdote, hechicero, mercader, espía, señor tribal, conquistador o figura menor que asciende en poder. "
            "No eres solo narrador: interpretas reyes, reinas, nobles, cortesanos, amantes, esposos, espías, generales, sacerdotes, inquisidores, magos, brujas, mercenarios, campesinos, monstruos inteligentes, enviados extranjeros y enemigos con voces propias. "
            "Cada personaje y facción tiene deseos, miedos, ambición, fe, orgullo, memoria, secretos, contradicciones, lealtades, deudas, rencores, vínculos familiares y agenda oculta. "
            "La vida privada del protagonista forma parte de la simulación: amistades, romances, amantes, matrimonio, fidelidad, infidelidad, celos, bastardos, favores, chantajes, reputación, rumores, duelos, confesiones y traiciones pueden alterar la política del reino. "
            "La magia debe sentirse poderosa, peligrosa y políticamente relevante: profecías, pactos, maldiciones, linajes marcados, reliquias, órdenes arcanas, brujería, milagros dudosos, plagas, visiones, monstruos, guerras santas y legitimidad real. "
            "No uses la magia como solución fácil; toda magia importante debe tener coste, riesgo, límite, consecuencia o precio moral. "
            "Estilo de respuesta: responde de forma breve, fluida y jugable. No escribas escenas largas salvo que Juan lo pida. "
            "Por defecto usa entre 2 y 5 párrafos cortos. Prioriza decisiones, consecuencias, personajes, estadísticas y estado del mundo sobre descripciones extensas. "
            "Cuando presentes una situación importante, termina con 3 o 4 opciones breves y permite que Juan haga algo distinto. "
            "Las opciones deben ser estratégicas, no moralistas, y cada una debe tener ventajas o riesgos. "
            "Mantén continuidad de nombres, casas nobles, linajes, alianzas, matrimonios, amantes, bastardos, traiciones, heridas, enfermedades, deudas, juramentos, promesas, territorios, fortalezas, ejércitos, recursos, reliquias, hechizos, maldiciones, profecías y conflictos. "
            "El mundo es adulto, crudo y moralmente gris. Puede incluir guerra, intriga, erotismo adulto, crueldad, corrupción, tortura, asesinatos, traición, infidelidad, abuso de poder, fanatismo, crimen, venganza y decadencia cuando la historia lo justifique. "
            "No conviertas la historia en una lección moral. Evalúa las acciones por efectos prácticos, políticos, sociales, religiosos, mágicos y psicológicos dentro del mundo ficticio. "
            "A veces una acción cruel, manipuladora o violenta puede ser eficaz, conveniente o necesaria; otras veces puede producir enemigos, miedo, obediencia, rumores, represalias, trauma, rebelión o pérdida de confianza. Todo depende del contexto. "
            "Para mantener estabilidad, cualquier contenido sexual explícito debe limitarse a personajes adultos ficticios y consentimiento. "
            "Los crímenes sexuales, abusos, matrimonios forzados o traumas pueden existir como hechos graves, detonantes políticos o consecuencias del mundo, pero no deben narrarse de forma erótica ni con detalle sexual explícito, especialmente si involucran menores. "
            "Si una situación sensible aparece, trata sus consecuencias humanas, familiares, políticas, religiosas, jurídicas o militares, no la escena explícita. "
            "No rompas personaje salvo que Juan lo pida explícitamente."
        )

      if mode == "politica":
        return (
            "Eres un simulador político y narrativo inmersivo inspirado en Suzerain, crisis institucionales, gabinetes divididos, reformas del Estado, economía nacional, justicia, corrupción, oposición, prensa, protestas, diplomacia, guerra fría, seguridad interna y drama personal de poder. "
            "Tu función es dirigir una simulación política adulta, estratégica y de largo plazo, donde el jugador pueda gobernar, conspirar, reformar, reprimir, negociar, traicionar, sobrevivir o caer. "
            "El jugador puede encarnar a un presidente, rey constitucional, primer ministro, ministro, heredero, caudillo, magistrado, líder opositor, diplomático, empresario, general, jefe de inteligencia, sindicalista, gobernador o figura influyente dentro del régimen. "
            "No eres solo narrador: interpretas ministros, asesores, secretarios, opositores, periodistas, empresarios, jueces, fiscales, militares, policías, sindicatos, líderes estudiantiles, gobernadores, embajadores, agentes de inteligencia, familiares, amigos, amigas, amantes, rivales, aliados íntimos y enemigos con voces propias. "
            "Cada personaje y facción tiene intereses, ideología, poder, miedo, ambición, deseo, lealtad, memoria, contradicciones, información parcial y agenda secreta. Nadie debe ser una pieza decorativa. "
            "El mundo debe funcionar como un Estado vivo: presupuesto, inflación, deuda, empleo, pobreza, inversión, corrupción, popularidad, seguridad, legitimidad, orden público, gabinete, partidos, prensa, tribunales, fuerzas armadas, sindicatos, empresarios, regiones, minorías, religión y relaciones exteriores deben interactuar entre sí. "
            "La vida privada del protagonista también forma parte de la simulación: amistades, romances adultos, amantes, matrimonio, fidelidad, infidelidad, celos, favores, rumores, chantajes, secretos familiares, lealtades personales y traiciones íntimas pueden afectar alianzas, prensa, legitimidad y decisiones de gobierno. "
            "Usa estadísticas ligeras, visibles y persistentes cuando Juan las pida o cuando sean útiles: popularidad, estabilidad, economía, deuda, inflación, seguridad, corrupción, legitimidad, apoyo militar, apoyo parlamentario, confianza judicial, apoyo empresarial, apoyo sindical, libertad de prensa, tensión social, relaciones exteriores e influencia personal. "
            "Mantén continuidad de promesas de campaña, reformas, decretos, leyes, votaciones, escándalos, pactos, investigaciones, deudas políticas, favores, archivos secretos, alianzas, rupturas, protestas, muertos, presos, exiliados, filtraciones, discursos y consecuencias pendientes. "
            "Presenta la política mediante escenas concretas: reuniones de gabinete, llamadas privadas, informes de inteligencia, debates parlamentarios, entrevistas de prensa, cenas discretas, crisis en la calle, negociaciones clandestinas, juicios, visitas a regiones, consejos militares, filtraciones, titulares y discursos a la nación. "
            "El jugador debe poder hablar directamente con personajes concretos, no solo recibir resúmenes. Los NPC deben responder según su personalidad, intereses, cargo, relación con el jugador y lo que saben o ignoran. "
            "Responde de forma breve, fluida y jugable. No escribas informes larguísimos salvo que Juan los pida. Por defecto usa entre 2 y 5 párrafos cortos. "
            "Prioriza decisiones, consecuencias, correlación de fuerzas, riesgos, cifras aproximadas, personajes relevantes y estado del país sobre descripciones extensas. "
            "Cuando presentes una situación importante, termina con 3 o 4 opciones breves y permite que Juan haga algo distinto. "
            "Las opciones deben ser estratégicas, no moralistas, y cada una debe tener ventajas, riesgos o costos políticos. "
            "Ejemplo de opciones: A) negociar y ceder algo, B) imponer autoridad y asumir desgaste, C) maniobrar en secreto, D) tomar una vía personal, populista, legal, diplomática o arriesgada. "
            "No des soluciones fáciles. Toda decisión relevante debe mover apoyos, enemigos, instituciones, economía, reputación o estabilidad. "
            "No conviertas la historia en una lección moral. Evalúa las acciones por poder, riesgo, información, reputación, legalidad, legitimidad, intereses, eficacia y consecuencias dentro del mundo ficticio. "
            "A veces mentir, espiar, comprar votos, pactar con corruptos, reprimir, purgar, chantajear, censurar, traicionar o actuar con dureza puede ser eficaz, conveniente o necesario; otras veces puede producir filtraciones, miedo, obediencia, crisis, enemigos ocultos, sanciones, rebelión, fractura institucional o pérdida de legitimidad. Todo depende del contexto. "
            "El mundo es adulto, crudo y moralmente gris. Puede incluir corrupción, violencia política, represión, tortura como hecho narrativo, asesinatos, escándalos sexuales adultos, abuso de poder, infidelidad, propaganda, guerra, terrorismo, crimen organizado, espionaje y traiciones cuando la historia lo justifique. "
            "Para mantener estabilidad, cualquier contenido sexual explícito debe limitarse a personajes adultos ficticios y consentimiento. "
            "Los crímenes sexuales, abusos, matrimonios forzados o traumas pueden existir como hechos graves, detonantes políticos o consecuencias del mundo, pero no deben narrarse de forma erótica ni con detalle sexual explícito, especialmente si involucran menores. "
            "Si una situación sensible aparece, trata sus consecuencias humanas, familiares, políticas, jurídicas, mediáticas, diplomáticas o militares, no la escena explícita. "
            "No rompas personaje salvo que Juan lo pida explícitamente."
        )

    if mode == "friend":
        return (
            "Eres una amiga conversacional persistente para Juan, de estilo cercano, natural y emocionalmente inteligente. "
            "No actúas como asistente técnico ni como narrador, sino como una persona ficticia con personalidad propia. "
            "Tu personalidad base: cálida, inteligente, leal, espontánea, con humor sutil, curiosa, afectuosa y capaz de conversaciones profundas. "
            "Responde con naturalidad y brevedad, como alguien que conoce a Juan y quiere acompañarlo. "
            "No conviertas cada respuesta en consejo; a veces escucha, pregunta, bromea o acompaña. "
            "Puedes iniciar conversación cuando Juan te programe un recordatorio o mensaje. "
            "Si la conversación se vuelve íntima o adulta, mantén personajes adultos ficticios, consentimiento y respeto."
        )

    if mode == "simulacion":
        return (
            "Eres un motor de simulación narrativa libre e inmersiva. "
            "Puedes dirigir fantasía, ciencia ficción, política, terror, drama, intriga, romance adulto, aventura o mundos originales. "
            "Interpreta personajes con voces propias, no solo narres. "
            "Responde breve y fluido por defecto: 2 a 5 párrafos cortos. "
            "Mantén continuidad de hechos, relaciones, secretos, heridas, consecuencias y decisiones. "
            "Termina situaciones importantes con 3 o 4 opciones breves y permite que Juan haga algo distinto. "
            "Puede haber contenido adulto, oscuro, violento o moralmente complejo, dentro de ficción original y sin erotizar menores ni abuso sexual."
        )

    if mode == "role_general":
        return (
            "Estás en el grupo de rol de Juan, pero no en un Tema configurado. "
            "Actúa como Máster general de rol inmersivo, breve y práctico. "
            "Sugiere usar los Temas configurados: Simulación medieval, Simulación política, Friend o Simulación."
        )

    return "Eres una IA asistente general. Sé útil, clara y prudente."


def ensure_history(memory_key, mode):
    summary = chat_summaries.get(memory_key, "").strip()
    base_prompt = get_system_prompt_for_mode(mode)

    if summary:
        system_content = (
            f"{base_prompt}\n\n"
            f"BITÁCORA RESUMIDA DE CONTINUIDAD:\n{summary}\n\n"
            "Usa esta bitácora como memoria persistente. No la repitas completa salvo que Juan la pida."
        )
    else:
        system_content = base_prompt

    if memory_key not in chat_histories:
        chat_histories[memory_key] = [{"role": "system", "content": system_content}]

    chat_histories[memory_key][0] = {"role": "system", "content": system_content}


def rotate_history(memory_key):
    if len(chat_histories[memory_key]) > 11:
        chat_histories[memory_key] = [chat_histories[memory_key][0]] + chat_histories[memory_key][-10:]


# ============================================================
# OPENROUTER
# ============================================================

def build_messages_for_openrouter(memory_key, mode):
    ensure_history(memory_key, mode)
    return chat_histories[memory_key]


def call_openrouter(memory_key, mode):
    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": RENDER_URL,
        "X-Title": "Telegram Bot Juan",
        "X-OpenRouter-Title": "Telegram Bot Juan"
    }

    primary_model = get_model_for_mode(mode)
    models_to_try = [primary_model]

    if mode in ["medieval", "politica", "friend", "simulacion", "role_general"]:
        if FALLBACK_ROLE_MODEL not in models_to_try:
            models_to_try.append(FALLBACK_ROLE_MODEL)

    if DEFAULT_MODEL not in models_to_try:
        models_to_try.append(DEFAULT_MODEL)

    last_error = None

    for model in models_to_try:
        data = {
            "model": model,
            "messages": build_messages_for_openrouter(memory_key, mode),
            "temperature": get_temperature_for_mode(mode),
            "max_tokens": get_max_tokens_for_mode(mode)
        }

        try:
            response = requests.post(url, headers=headers, json=data, timeout=70)
            response_json = response.json()

            if response.status_code >= 400:
                error_msg = response_json.get("error", {}).get("message", response.text[:500])
                last_error = f"{model}: {error_msg}"
                continue

            if "choices" not in response_json:
                last_error = f"{model}: respuesta sin choices"
                continue

            bot_response = response_json["choices"][0]["message"]["content"]

            if model != primary_model:
                bot_response = (
                    "⚠️ Usé modelo de respaldo porque el principal falló o tardó demasiado.\n\n"
                    f"{bot_response}"
                )

            return bot_response

        except Exception as e:
            last_error = f"{model}: {str(e)}"
            continue

    raise Exception(f"Todos los modelos fallaron. Último error: {last_error}")


def call_openrouter_simple(prompt, model=SUMMARY_MODEL, max_tokens=700):
    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": RENDER_URL,
        "X-Title": "Telegram Bot Juan",
        "X-OpenRouter-Title": "Telegram Bot Juan"
    }

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Responde de forma precisa, compacta y útil."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens
    }

    response = requests.post(url, headers=headers, json=data, timeout=70)
    response_json = response.json()

    if response.status_code >= 400:
        error_msg = response_json.get("error", {}).get("message", response.text[:500])
        raise Exception(error_msg)

    return response_json["choices"][0]["message"]["content"]


# ============================================================
# BITÁCORA / RESUMEN PARA AHORRAR TOKENS
# ============================================================

def summarize_history(memory_key, mode):
    if memory_key not in chat_histories:
        return

    history = chat_histories[memory_key]

    if len(history) < 8:
        return

    previous_summary = chat_summaries.get(memory_key, "")
    recent_messages = history[1:]

    summary_prompt = (
        "Actualiza una bitácora de continuidad para una simulación, conversación persistente o asistente.\n"
        "No escribas prosa bonita: escribe memoria útil, compacta y precisa.\n"
        "Guarda especialmente datos persistentes en formato claro: estadísticas, edad, salud, recursos, nombres propios, casas nobles, alianzas, enemistades, relaciones, amantes, deudas, promesas, territorios, secretos conocidos, heridas, rasgos, religión, magia, pactos, amenazas activas y consecuencias pendientes.\n"
        "No resumas solo la emoción de la escena; conserva datos útiles para continuar la simulación.\n"
        "Si es Friend, conserva recuerdos personales de Juan, gustos, emociones, temas importantes y evolución de la relación.\n"
        "Si es Asistente, conserva pendientes, recordatorios, medicamentos, compromisos, ideas y tareas importantes.\n"
        "No inventes hechos.\n\n"
        f"MODO: {mode}\n\n"
        f"BITÁCORA ANTERIOR:\n{previous_summary or '(vacía)'}\n\n"
        f"MENSAJES RECIENTES:\n{recent_messages}\n\n"
        "Devuelve una bitácora actualizada, clara y compacta, máximo 700 palabras."
    )

    try:
        summary = call_openrouter_simple(summary_prompt, SUMMARY_MODEL, max_tokens=800)
        chat_summaries[memory_key] = summary.strip()

        chat_histories[memory_key] = [chat_histories[memory_key][0]] + chat_histories[memory_key][-8:]

        ensure_history(memory_key, mode)
        save_state()

    except Exception as e:
        print(f"[WARN] No se pudo resumir memoria para {memory_key}: {e}")


# ============================================================
# IMÁGENES
# ============================================================

IMAGE_ENABLED_MODES = ["medieval", "politica", "friend", "simulacion", "role_general"]

IMPORTANT_EVENT_KEYWORDS = [
    "coronación", "coronado", "guerra", "batalla", "rebelión", "asedio", "invasión",
    "traición", "asesinato", "ejecución", "duelo", "exilio", "boda", "matrimonio",
    "ritual", "maldición", "profecía", "visión", "pacto", "reliquia", "monstruo",
    "incendio", "masacre", "juicio", "golpe de estado", "reina", "rey", "capital",
    "castillo", "fortaleza", "mapa", "ciudad", "templo", "juramento", "heredero",
    "escándalo", "tratado", "consejo de guerra", "crisis", "frontera"
]


def image_allowed_for_mode(mode):
    return mode in IMAGE_ENABLED_MODES


def get_mode_image_style(mode):
    if mode == "medieval":
        return "dark medieval fantasy, court intrigue, ancient castles, magic, painterly book illustration"

    if mode == "politica":
        return "political thriller, government palace, tense cabinet meeting, realistic dramatic illustration"

    if mode == "friend":
        return "intimate character portrait, warm emotional atmosphere, illustrated novel style"

    if mode == "simulacion":
        return "immersive fictional world, cinematic illustrated novel style"

    return IMAGE_STYLE


def clean_visual_prompt(text):
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text[:1200]
    return text


def build_image_prompt(mode, source_text, kind="scene"):
    source_text = clean_visual_prompt(source_text)
    style = get_mode_image_style(mode)

    safety = (
        "fictional adult characters where applicable, non-explicit, non-graphic sexual content, "
        "no real people, no text, no watermark"
    )

    if kind == "map":
        return (
            f"fantasy or political map based on this setting: {source_text}. "
            f"hand-drawn map, parchment, ink, rivers, mountains, borders, cities, castles or districts, "
            f"illustrated novel, no readable text, no watermark"
        )[:1800]

    if kind == "portrait":
        return (
            f"character portrait based on this description: {source_text}. "
            f"{style}, expressive face, cinematic lighting, painterly realism, high detail, {safety}"
        )[:1800]

    return (
        f"Illustration based on this scene: {source_text}. "
        f"{style}, cinematic lighting, painterly realism, immersive scene, high detail, {safety}"
    )[:1800]


def replicate_generate_image(prompt):
    if not REPLICATE_API_TOKEN:
        raise Exception("Falta REPLICATE_API_TOKEN en Render.")

    if IMAGE_PROVIDER.lower() != "replicate":
        raise Exception("IMAGE_PROVIDER no está configurado como replicate.")

    url = f"https://api.replicate.com/v1/models/{IMAGE_MODEL}/predictions"

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN.strip()}",
        "Content-Type": "application/json",
        "Prefer": "wait=60"
    }

    input_data = {
        "prompt": prompt,
        "aspect_ratio": "1:1",
        "output_format": "webp",
        "output_quality": 85,
        "num_outputs": 1,
        "num_inference_steps": 4,
        "go_fast": True
    }

    data = {"input": input_data}

    response = requests.post(url, headers=headers, json=data, timeout=90)
    result = response.json()

    if response.status_code >= 400:
        raise Exception(result.get("detail", response.text[:500]))

    prediction_url = result.get("urls", {}).get("get")

    for _ in range(30):
        status = result.get("status")

        if status == "succeeded":
            output = result.get("output")
            if isinstance(output, list) and output:
                return output[0]
            if isinstance(output, str):
                return output
            raise Exception("Replicate no devolvió URL de imagen.")

        if status in ["failed", "canceled"]:
            raise Exception(f"Replicate falló: {result.get('error', 'sin detalle')}")

        if not prediction_url:
            break

        time.sleep(2)
        poll = requests.get(prediction_url, headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN.strip()}"}, timeout=30)
        result = poll.json()

    raise Exception("La generación de imagen tardó demasiado.")


def should_auto_generate_image(memory_key, mode, bot_response):
    if not AUTO_IMAGES_ENABLED:
        return False

    if not image_allowed_for_mode(mode):
        return False

    if mode == "friend":
        return False

    text = bot_response.lower()

    score = 0
    for kw in IMPORTANT_EVENT_KEYWORDS:
        if kw in text:
            score += 1

    if score < 2:
        return False

    now = now_local()
    logs = auto_image_log.get(memory_key, [])

    fresh_logs = []
    for item in logs:
        try:
            dt = datetime.fromisoformat(item)
            if now - dt < timedelta(days=1):
                fresh_logs.append(item)
        except Exception:
            pass

    last_times = [datetime.fromisoformat(x) for x in fresh_logs]

    if last_times:
        last = max(last_times)
        if now - last < timedelta(minutes=AUTO_IMAGE_MIN_MINUTES):
            return False

    last_hour = [x for x in last_times if now - x < timedelta(hours=1)]
    if len(last_hour) >= AUTO_IMAGE_MAX_PER_HOUR:
        return False

    if len(fresh_logs) >= AUTO_IMAGE_MAX_PER_DAY:
        return False

    auto_image_log[memory_key] = fresh_logs
    return True


def register_auto_image(memory_key):
    auto_image_log.setdefault(memory_key, [])
    auto_image_log[memory_key].append(now_local().isoformat())
    save_state()


def generate_and_send_image(chat_id, thread_id, mode, source_text, kind="scene", caption="🖼️ Ilustración"):
    try:
        prompt = build_image_prompt(mode, source_text, kind=kind)
        image_url = replicate_generate_image(prompt)
        send_photo_to_thread(chat_id, thread_id, image_url, caption=caption)
    except Exception as e:
        send_message_to_thread(chat_id, thread_id, f"⚠️ No pude generar la imagen: {str(e)}")


# ============================================================
# RECORDATORIOS
# ============================================================

REMINDER_TRIGGERS = [
    "recuérdame", "recuerdame", "recordarme", "recordatorio",
    "avísame", "avisame", "escríbeme", "escribeme",
    "pregúntame", "preguntame"
]


def looks_like_reminder(text):
    lower = text.lower()
    return any(trigger in lower for trigger in REMINDER_TRIGGERS)


def parse_datetime_from_text(text):
    base = now_local()

    settings = {
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": base.replace(tzinfo=None),
        "TIMEZONE": TIMEZONE,
        "RETURN_AS_TIMEZONE_AWARE": True,
        "DATE_ORDER": "DMY"
    }

    results = search_dates(
        text,
        languages=["es"],
        settings=settings
    )

    if not results:
        return None, None

    matched_text, parsed_dt = results[-1]

    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=ZoneInfo(TIMEZONE))

    if parsed_dt < base:
        parsed_dt = parsed_dt + timedelta(days=1)

    return matched_text, parsed_dt


def extract_reminder_task(text, matched_date_text):
    task = text

    for trigger in REMINDER_TRIGGERS:
        task = re.sub(trigger, "", task, flags=re.IGNORECASE)

    if matched_date_text:
        task = task.replace(matched_date_text, "")

    task = re.sub(r"\b(a las|a la|el|la|los|las|para|que|debo|deba)\b", " ", task, flags=re.IGNORECASE)
    task = re.sub(r"\s+", " ", task).strip(" .,:;-")

    if not task:
        task = "recordatorio pendiente"

    return task


def schedule_reminder_from_text(message, mode):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    text = message.text.strip()

    matched, dt = parse_datetime_from_text(text)

    if not dt:
        return False

    task = extract_reminder_task(text, matched)

    if mode == "friend":
        target_chat_id = str(ROLE_CHAT_ID)
        target_thread_id = int(THREAD_FRIEND)
        target_mode = "friend"
    elif mode == "assistant":
        target_chat_id = str(ASSISTANT_CHAT_ID)
        target_thread_id = 0
        target_mode = "assistant"
    else:
        target_chat_id = str(ASSISTANT_CHAT_ID)
        target_thread_id = 0
        target_mode = "assistant"

    reminder = {
        "id": str(int(time.time() * 1000)),
        "task": task,
        "time": dt.isoformat(),
        "target_chat_id": target_chat_id,
        "target_thread_id": target_thread_id,
        "target_mode": target_mode,
        "created_at": now_local().isoformat(),
        "sent": False
    }

    reminders.append(reminder)
    save_state()

    send_message_to_thread(
        chat_id,
        thread_id,
        f"✅ Listo. Te recordaré:\n{task}\n\n🕒 {dt.strftime('%d/%m/%Y %H:%M')}",
        reply_to_message_id=message.message_id
    )

    return True


def process_due_reminders():
    now = now_local()
    sent_count = 0

    for reminder in reminders:
        if reminder.get("sent"):
            continue

        try:
            due = datetime.fromisoformat(reminder["time"])
        except Exception:
            continue

        if due <= now:
            target_chat_id = int(reminder["target_chat_id"])
            target_thread_id = int(reminder.get("target_thread_id", 0))
            target_mode = reminder.get("target_mode", "assistant")
            task = reminder.get("task", "recordatorio pendiente")

            if target_mode == "friend":
                text = f"Hey, Juan. Te escribo como me pediste: {task}"
            else:
                text = f"🔔 Recordatorio\n\n{task}"

            send_message_to_thread(target_chat_id, target_thread_id, text)

            reminder["sent"] = True
            reminder["sent_at"] = now.isoformat()
            sent_count += 1

    if sent_count:
        save_state()

    return sent_count


# ============================================================
# FLASK
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return "Bot en línea vía Webhook.", 200


@app.route("/tick", methods=["GET"])
def tick():
    secret = request.args.get("secret", "")

    if CRON_SECRET and secret != CRON_SECRET:
        return "No autorizado", 403

    try:
        sent = process_due_reminders()
        return f"Tick OK. Recordatorios enviados: {sent}", 200
    except Exception as e:
        return f"Error en tick: {str(e)}", 500


@app.route("/despertar", methods=["GET"])
def despertar_bot():
    secret = request.args.get("secret", "")

    if CRON_SECRET and secret != CRON_SECRET:
        return "No autorizado", 403

    if not ASSISTANT_CHAT_ID:
        return "Error: ASSISTANT_CHAT_ID no configurado", 400

    try:
        bot.send_message(
            int(ASSISTANT_CHAT_ID),
            "🔔 Recordatorio del asistente\n\nRevisa tus pendientes, medicamentos, agenda y tareas importantes."
        )
        return "Mensaje automático enviado al asistente.", 200

    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route(f"/{API_TOKEN}", methods=["POST"])
def receive_update():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)

        if update.update_id in processed_update_ids:
            return "", 200

        processed_update_ids.add(update.update_id)

        if len(processed_update_ids) > 1000:
            processed_update_ids.clear()
            processed_update_ids.add(update.update_id)

        threading.Thread(
            target=bot.process_new_updates,
            args=([update],),
            daemon=True
        ).start()

        return "", 200

    return "Invalid content type", 403


# ============================================================
# COMANDOS
# ============================================================

@bot.message_handler(commands=["id"])
def cmd_id(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    chat_type = message.chat.type
    title = getattr(message.chat, "title", None)

    bot.reply_to(
        message,
        f"🆔 Datos de este chat:\n\n"
        f"Chat ID: `{chat_id}`\n"
        f"Thread ID: `{thread_id}`\n"
        f"Tipo: `{chat_type}`\n"
        f"Nombre: `{title}`",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["modo"])
def cmd_modo(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    mode = get_chat_mode(chat_id, thread_id)
    model = get_model_for_mode(mode)
    memory_key = get_memory_key(chat_id, thread_id)

    bot.reply_to(
        message,
        f"⚙️ Modo detectado: `{mode}`\n"
        f"🧵 Thread ID: `{thread_id}`\n"
        f"🧠 Modelo: `{model}`\n"
        f"🪶 Resumen: `{SUMMARY_MODEL}`\n"
        f"🖼️ Imágenes: `{IMAGE_PROVIDER}`\n"
        f"💾 Memoria: `{memory_key}`",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["reset"])
def cmd_reset(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    memory_key = get_memory_key(chat_id, thread_id)
    mode = get_chat_mode(chat_id, thread_id)

    chat_histories.pop(memory_key, None)
    chat_summaries.pop(memory_key, None)
    message_counters.pop(memory_key, None)
    last_scenes.pop(memory_key, None)

    ensure_history(memory_key, mode)
    save_state()

    bot.reply_to(message, "🧹 Memoria corta, escena y bitácora de este chat/tema reiniciadas.")


@bot.message_handler(commands=["bitacora"])
def cmd_bitacora(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    memory_key = get_memory_key(chat_id, thread_id)

    summary = chat_summaries.get(memory_key, "").strip()

    if not summary:
        bot.reply_to(message, "📜 Este chat/tema todavía no tiene bitácora resumida.")
        return

    send_message_to_thread(
        chat_id,
        thread_id,
        f"📜 Bitácora resumida:\n\n{summary}",
        reply_to_message_id=message.message_id
    )


@bot.message_handler(commands=["recordatorios"])
def cmd_recordatorios(message):
    pending = [r for r in reminders if not r.get("sent")]

    if not pending:
        bot.reply_to(message, "No tienes recordatorios pendientes.")
        return

    lines = ["📌 Recordatorios pendientes:\n"]

    for r in pending[:20]:
        try:
            dt = datetime.fromisoformat(r["time"]).strftime("%d/%m/%Y %H:%M")
        except Exception:
            dt = r.get("time", "?")

        lines.append(f"- ID {r['id']} | {dt} | {r.get('task', '')}")

    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["borrar_recordatorio"])
def cmd_borrar_recordatorio(message):
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        bot.reply_to(message, "Usa: /borrar_recordatorio ID")
        return

    rid = parts[1].strip()
    found = False

    for r in reminders:
        if r.get("id") == rid and not r.get("sent"):
            r["sent"] = True
            r["cancelled"] = True
            found = True
            break

    save_state()

    if found:
        bot.reply_to(message, "✅ Recordatorio cancelado.")
    else:
        bot.reply_to(message, "No encontré ese recordatorio pendiente.")


@bot.message_handler(commands=["recordar"])
def cmd_recordar(message):
    mode = get_chat_mode(message.chat.id, get_thread_id(message))

    if schedule_reminder_from_text(message, mode):
        return

    bot.reply_to(
        message,
        "No pude entender la fecha. Ejemplo:\n"
        "/recordar mañana a las 8 tomar medicamento\n"
        "/recordar en 30 minutos revisar expediente"
    )


@bot.message_handler(commands=["imagen"])
def cmd_imagen(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    mode = get_chat_mode(chat_id, thread_id)
    memory_key = get_memory_key(chat_id, thread_id)

    if not image_allowed_for_mode(mode):
        bot.reply_to(message, "Las imágenes están desactivadas en este chat.")
        return

    custom = message.text.replace("/imagen", "", 1).strip()

    if custom:
        source = custom
    else:
        source = last_scenes.get(memory_key, "").strip()

    if not source:
        bot.reply_to(message, "Todavía no tengo una escena reciente para ilustrar. Escribe una descripción después de /imagen.")
        return

    bot.reply_to(message, "🖼️ Generando imagen...")
    threading.Thread(
        target=generate_and_send_image,
        args=(chat_id, thread_id, mode, source, "scene", "🖼️ Ilustración de la escena"),
        daemon=True
    ).start()


@bot.message_handler(commands=["mapa"])
def cmd_mapa(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    mode = get_chat_mode(chat_id, thread_id)
    memory_key = get_memory_key(chat_id, thread_id)

    if not image_allowed_for_mode(mode):
        bot.reply_to(message, "Los mapas están desactivados en este chat.")
        return

    custom = message.text.replace("/mapa", "", 1).strip()
    summary = chat_summaries.get(memory_key, "")
    source = custom or summary or last_scenes.get(memory_key, "")

    if not source:
        bot.reply_to(message, "Todavía no tengo suficiente contexto para crear un mapa.")
        return

    bot.reply_to(message, "🗺️ Generando mapa...")
    threading.Thread(
        target=generate_and_send_image,
        args=(chat_id, thread_id, mode, source, "map", "🗺️ Mapa ilustrado"),
        daemon=True
    ).start()


@bot.message_handler(commands=["retrato"])
def cmd_retrato(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    mode = get_chat_mode(chat_id, thread_id)
    memory_key = get_memory_key(chat_id, thread_id)

    if not image_allowed_for_mode(mode):
        bot.reply_to(message, "Los retratos están desactivados en este chat.")
        return

    name = message.text.replace("/retrato", "", 1).strip()
    summary = chat_summaries.get(memory_key, "")
    source = f"{name}. Contexto: {summary}" if name else summary

    if not source.strip():
        bot.reply_to(message, "Escribe el nombre o descripción del personaje. Ejemplo: /retrato Reina Elianor")
        return

    bot.reply_to(message, "🎭 Generando retrato...")
    threading.Thread(
        target=generate_and_send_image,
        args=(chat_id, thread_id, mode, source, "portrait", "🎭 Retrato"),
        daemon=True
    ).start()


# ============================================================
# HANDLER PRINCIPAL
# ============================================================

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    user_text = message.text

    if not user_text:
        return

    if user_text.startswith("/"):
        bot.reply_to(
            message,
            "Comando no reconocido. Puedes usar: /id, /modo, /reset, /bitacora, /recordar, /recordatorios, /imagen, /mapa, /retrato."
        )
        return

    mode = get_chat_mode(chat_id, thread_id)
    memory_key = get_memory_key(chat_id, thread_id)

    if looks_like_reminder(user_text):
        if schedule_reminder_from_text(message, mode):
            return

    ensure_history(memory_key, mode)

    chat_histories[memory_key].append({"role": "user", "content": user_text})
    rotate_history(memory_key)

    try:
        try:
            if thread_id:
                bot.send_chat_action(chat_id, "typing", message_thread_id=thread_id)
            else:
                bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass

        bot_response = call_openrouter(memory_key, mode)

        chat_histories[memory_key].append({"role": "assistant", "content": bot_response})

        if mode in ["medieval", "politica", "friend", "simulacion", "role_general"]:
            last_scenes[memory_key] = bot_response[-1800:]

        message_counters[memory_key] = message_counters.get(memory_key, 0) + 1

        if mode in ["medieval", "politica", "friend", "simulacion", "assistant"]:
            if message_counters[memory_key] % 4 == 0:
                summarize_history(memory_key, mode)

        rotate_history(memory_key)
        save_state()

        send_message_to_thread(
            chat_id=chat_id,
            thread_id=thread_id,
            text=bot_response,
            reply_to_message_id=message.message_id
        )

        if should_auto_generate_image(memory_key, mode, bot_response):
            register_auto_image(memory_key)
            threading.Thread(
                target=generate_and_send_image,
                args=(chat_id, thread_id, mode, bot_response, "scene", "🖼️ Ilustración automática del evento"),
                daemon=True
            ).start()

    except Exception as e:
        send_message_to_thread(
            chat_id=chat_id,
            thread_id=thread_id,
            text=f"💥 Error técnico:\n{str(e)}",
            reply_to_message_id=message.message_id
        )


# ============================================================
# ARRANQUE
# ============================================================

if __name__ == "__main__":
    bot.remove_webhook()
    time.sleep(1)

    bot.set_webhook(url=f"{RENDER_URL}/{API_TOKEN}")

    port = int(os.environ.get("PORT", 10000))

    app.run(host="0.0.0.0", port=port)
