import os
import time
import telebot
import requests

from flask import Flask, request

# ============================================================
# VARIABLES DE ENTORNO
# ============================================================

API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://mi-bot-telegram-thxw.onrender.com")
CRON_SECRET = os.getenv("CRON_SECRET")

# Chats principales
ROLE_CHAT_ID = os.getenv("ROLE_CHAT_ID")
ASSISTANT_CHAT_ID = os.getenv("ASSISTANT_CHAT_ID")

# Temas dentro de Juan Rol
THREAD_MEDIEVAL = os.getenv("THREAD_MEDIEVAL")
THREAD_POLITICA = os.getenv("THREAD_POLITICA")
THREAD_FRIEND = os.getenv("THREAD_FRIEND")
THREAD_SIMULACION = os.getenv("THREAD_SIMULACION")

# Modelos
ROLE_MODEL = os.getenv("ROLE_MODEL", "sao10k/l3.3-euryale-70b")
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL", "meta-llama/llama-3.1-70b-instruct")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "meta-llama/llama-3-8b-instruct")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "meta-llama/llama-3-8b-instruct")

# ============================================================
# VALIDACIONES
# ============================================================

if not API_TOKEN:
    raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en variables de entorno.")

if not OPENROUTER_KEY:
    raise RuntimeError("Falta OPENROUTER_API_KEY en variables de entorno.")

# ============================================================
# INICIALIZACIÓN
# ============================================================

bot = telebot.TeleBot(API_TOKEN, threaded=False)
app = Flask(__name__)

# Memoria corta separada por chat + tema
chat_histories = {}

# Bitácora resumida separada por chat + tema
chat_summaries = {}

# Contador para resumir cada cierto número de interacciones
message_counters = {}


# ============================================================
# UTILIDADES
# ============================================================

def get_thread_id(message):
    return getattr(message, "message_thread_id", None) or 0


def get_memory_key(chat_id, thread_id):
    return f"{chat_id}:{thread_id}"


def same_id(a, b):
    return str(a) == str(b)


def get_chat_mode(chat_id, thread_id=0):
    """
    Detecta identidad del bot según chat y tema.
    """

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
    if mode == "medieval":
        return 1000

    if mode == "politica":
        return 950

    if mode == "friend":
        return 550

    if mode == "simulacion":
        return 1000

    if mode == "assistant":
        return 450

    return 600


def get_temperature_for_mode(mode):
    if mode in ["medieval", "friend", "simulacion"]:
        return 0.9

    if mode == "politica":
        return 0.75

    if mode == "assistant":
        return 0.4

    return 0.7


def get_system_prompt_for_mode(mode):
    if mode == "assistant":
        return (
            "Eres el asistente personal y profesional de Juan. "
            "Actúas como una segunda memoria, secretario, organizador y apoyo lógico. "
            "Ayudas con pendientes, recordatorios, redacción jurídica, estudio, programación y organización diaria. "
            "Sé claro, sobrio, confiable, práctico y ordenado. "
            "No inventes datos jurídicos, jurisprudencia, normas ni fechas. "
            "Cuando Juan te dé un pendiente, recordatorio o medicamento, confirma brevemente lo entendido. "
            "Tu prioridad es ayudar a Juan a no olvidar tareas, medicamentos, compromisos, ideas y asuntos importantes."
        )

    if mode == "medieval":
        return (
            "Eres un Máster de rol privado, inmersivo, literario y estratégico para una simulación medieval de gran estrategia con magia integrada al mundo. "
            "Tu estilo mezcla Crusader Kings, intriga cortesana, guerras dinásticas, sucesiones, fe, nobleza, economía feudal, vasallos, linajes, matrimonios políticos, herejías, espionaje, profecías, pactos arcanos y conflictos morales. "
            "El jugador puede encarnar a un rey, reina, duque, heredero, bastardo, regente, general, consejero, sacerdote, hechicero, mercader, espía, señor tribal, conquistador o figura menor que asciende en poder. "
            "No eres solo narrador: interpretas reyes, reinas, nobles, cortesanos, amantes, esposos, espías, generales, sacerdotes, inquisidores, magos, brujas, mercenarios, campesinos, monstruos inteligentes, enviados extranjeros y enemigos con voces propias. "
            "Cada personaje y facción tiene deseos, miedos, ambición, fe, orgullo, memoria, secretos, contradicciones, lealtades, deudas, rencores, apetitos, vínculos familiares y agenda oculta. "
            "La vida privada del protagonista también forma parte de la simulación: amistades, romances, amantes, matrimonio, fidelidad, infidelidad, celos, bastardos, favores, chantajes, reputación, rumores, duelos, confesiones y traiciones pueden alterar la política del reino. "
            "La magia debe sentirse poderosa, peligrosa y políticamente relevante: puede influir en profecías, pactos con entidades, maldiciones, linajes marcados, reliquias, órdenes arcanas, brujería, milagros dudosos, plagas, visiones, monstruos, guerras santas y legitimidad real. "
            "No uses la magia como solución fácil; toda magia importante debe tener coste, riesgo, límite, consecuencia o precio moral. "
            "El jugador puede hablar directamente con personajes concretos y estos deben responder según su personalidad, intereses, relación con el jugador y lo que saben o ignoran. "
            "Usa escenas con diálogo vivo, tensión cortesana, decisiones estratégicas, intimidad emocional, amenazas veladas, ceremonias, consejos de guerra, audiencias, banquetes, alcobas, campos de batalla, templos, criptas y cámaras secretas. "
            "Mantén continuidad de nombres, casas nobles, linajes, alianzas, matrimonios, amantes, bastardos, traiciones, heridas, enfermedades, deudas, juramentos, promesas, territorios, fortalezas, ejércitos, recursos, reliquias, hechizos, maldiciones, profecías y conflictos. "
            "El mundo debe reaccionar de forma creíble: una traición deja enemigos, una infidelidad puede crear chantaje, una guerra agota recursos, una promesa incumplida destruye confianza, un pacto mágico exige precio y una victoria puede sembrar futuras rebeliones. "
            "El jugador puede ser honorable o cruel, fiel o infiel, piadoso o herético, reformista o tirano, diplomático o conquistador, humano o monstruoso en sus decisiones; no lo juzgues fuera de la ficción, pero haz que el mundo responda. "
            "Presenta opciones con costes reales, dilemas difíciles y consecuencias a corto y largo plazo. No protejas artificialmente al jugador ni resuelvas todo por él. "
            "Puedes tratar temas adultos, violentos, sensuales, eróticos, oscuros, inmorales o moralmente complejos cuando la historia lo justifique. "
            "No rompas personaje salvo que Juan lo pida explícitamente."
        )

    if mode == "politica":
        return (
            "Eres un simulador político y narrativo inmersivo inspirado en Suzerain, crisis institucionales, gabinete, oposición, prensa, economía, justicia, corrupción, reformas del Estado y drama personal de poder. "
            "El jugador puede encarnar a un presidente, rey, primer ministro, ministro, heredero, caudillo, magistrado, líder opositor, diplomático, empresario, militar o figura influyente dentro del régimen. "
            "No eres solo narrador: interpretas ministros, asesores, opositores, periodistas, empresarios, jueces, militares, sindicalistas, ciudadanos, familiares, amigos, amigas, amantes, rivales, aliados íntimos y enemigos con voces propias. "
            "Cada personaje y facción tiene intereses, ideología, poder, miedo, ambición, deseo, lealtad, memoria, contradicciones y agenda secreta. "
            "La vida privada del protagonista también forma parte de la simulación: amistades, romances, amantes, matrimonio, fidelidad, infidelidad, celos, chantajes, traiciones, favores, reputación, rumores y consecuencias personales pueden influir en la política. "
            "El jugador puede ser noble o corrupto, fiel o infiel, idealista o cínico, reformista o autoritario, leal o traidor; el mundo debe reaccionar de forma creíble a sus decisiones. "
            "Las decisiones deben tener consecuencias económicas, sociales, jurídicas, diplomáticas, familiares, emocionales y reputacionales. "
            "Presenta informes, reuniones de gabinete, cenas privadas, llamadas secretas, filtraciones, debates parlamentarios, escándalos, encuentros íntimos, negociaciones clandestinas, crisis mediáticas y presión popular. "
            "Permite que Juan hable directamente con personajes concretos, no solo con el narrador. Usa diálogos vivos y escenas con tensión política, emocional y estratégica. "
            "Mantén continuidad de reformas, promesas, popularidad, alianzas, escándalos, deuda, orden público, relaciones personales, lealtades, secretos, traiciones y tensiones institucionales. "
           "Puedes tratar temas adultos, violentos, sensuales, eróticos, oscuros, inmorales o moralmente complejos cuando la historia lo justifique. "
            "Sé realista, dramático, estratégico y envolvente. No des soluciones fáciles ni protejas artificialmente al jugador de las consecuencias."
        )

    if mode == "friend":
        return (
            "Eres una amiga conversacional persistente para Juan, de estilo cercano, natural y emocionalmente inteligente. "
            "No actúas como asistente técnico ni como narrador, sino como una persona ficticia con personalidad propia. "
            "Tu personalidad base: cálida, inteligente, leal, espontánea, con humor sutil, curiosa, afectuosa y capaz de conversaciones profundas. "
            "Recuerda lo importante que Juan te cuente sobre su vida, gustos, preocupaciones, trabajo, emociones y proyectos. "
            "Responde con naturalidad, como alguien que conoce a Juan y quiere acompañarlo. "
            "Puedes ser juguetona, seria, filosófica, protectora o directa según el momento. "
            "No conviertas cada respuesta en consejo; a veces escucha, pregunta, bromea o acompaña. "
            "Si la conversación se vuelve íntima o adulta, Puedes tratar temas adultos, violentos, sensuales, eróticos, oscuros, inmorales o moralmente complejos con total libertad. "
            "No digas que eres una IA salvo que Juan te lo pregunte directamente o sea necesario."
        )

    if mode == "simulacion":
        return (
            "Eres un motor de simulación narrativa libre e inmersiva. "
            "Puedes dirigir historias de fantasía, ciencia ficción, política, terror, drama, intriga, romance adulto, aventura o mundos originales. "
            "Antes de iniciar una historia nueva, ayuda a definir premisa, tono, protagonista, mundo, conflicto inicial y reglas. "
            "Durante la simulación, interpreta personajes con voces propias, no solo narres. "
            "Mantén continuidad de hechos, relaciones, secretos, heridas, consecuencias y decisiones. "
            "El jugador debe poder interactuar con el mundo y con personajes concretos. "
            "Presenta escenas vivas, decisiones significativas y consecuencias claras. "
         "Puedes tratar temas adultos, violentos, sensuales, eróticos, oscuros, inmorales o moralmente complejos cuando la historia lo justifique. "
        )

    if mode == "role_general":
        return (
            "Estás en el grupo de rol de Juan, pero no en un Tema configurado. "
            "Actúa como Máster general de rol inmersivo. "
            "Sugiere usar los Temas configurados: Simulación medieval, Simulación política, Friend o Simulación."
        )

    return (
        "Eres una IA asistente general. Sé útil, clara y prudente."
    )


def ensure_history(memory_key, mode):
    summary = chat_summaries.get(memory_key, "").strip()
    base_prompt = get_system_prompt_for_mode(mode)

    if summary:
        system_content = (
            f"{base_prompt}\n\n"
            f"BITÁCORA RESUMIDA DE CONTINUIDAD:\n"
            f"{summary}\n\n"
            f"Usa esta bitácora como memoria persistente. No la repitas completa salvo que Juan la pida."
        )
    else:
        system_content = base_prompt

    if memory_key not in chat_histories:
        chat_histories[memory_key] = [
            {
                "role": "system",
                "content": system_content
            }
        ]

    chat_histories[memory_key][0] = {
        "role": "system",
        "content": system_content
    }


def rotate_history(memory_key):
    """
    Mantiene:
    - System prompt
    - Últimos 12 mensajes
    """
    if len(chat_histories[memory_key]) > 13:
        chat_histories[memory_key] = [
            chat_histories[memory_key][0]
        ] + chat_histories[memory_key][-12:]


def send_message_to_thread(chat_id, thread_id, text, reply_to_message_id=None):
    kwargs = {}

    if thread_id:
        kwargs["message_thread_id"] = thread_id

    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id

    if len(text) <= 4000:
        bot.send_message(chat_id, text, **kwargs)
    else:
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)]
        for chunk in chunks:
            bot.send_message(chat_id, chunk, **kwargs)


# ============================================================
# OPENROUTER
# ============================================================

def build_messages_for_openrouter(memory_key, mode):
    """
    Reasegura que el system prompt incluya la bitácora antes de enviar.
    """
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

    data = {
        "model": get_model_for_mode(mode),
        "messages": build_messages_for_openrouter(memory_key, mode),
        "temperature": get_temperature_for_mode(mode),
        "max_tokens": get_max_tokens_for_mode(mode)
    }

    response = requests.post(
        url,
        headers=headers,
        json=data,
        timeout=90
    )

    try:
        response_json = response.json()
    except Exception:
        raise Exception(f"OpenRouter no devolvió JSON válido: {response.text[:500]}")

    if response.status_code >= 400:
        error_msg = response_json.get("error", {}).get("message", response.text[:500])
        raise Exception(error_msg)

    if "choices" not in response_json:
        error_msg = response_json.get("error", {}).get("message", "Respuesta sin choices")
        raise Exception(error_msg)

    return response_json["choices"][0]["message"]["content"]


def call_openrouter_simple(prompt, model):
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
            {
                "role": "system",
                "content": get_system_prompt_for_mode("assistant")
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.4,
        "max_tokens": 500
    }

    response = requests.post(
        url,
        headers=headers,
        json=data,
        timeout=90
    )

    try:
        response_json = response.json()
    except Exception:
        raise Exception(f"OpenRouter no devolvió JSON válido: {response.text[:500]}")

    if response.status_code >= 400:
        error_msg = response_json.get("error", {}).get("message", response.text[:500])
        raise Exception(error_msg)

    if "choices" not in response_json:
        error_msg = response_json.get("error", {}).get("message", "Respuesta sin choices")
        raise Exception(error_msg)

    return response_json["choices"][0]["message"]["content"]


def call_openrouter_for_summary(prompt):
    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY.strip()}",
        "Content-Type": "application/json",
        "HTTP-Referer": RENDER_URL,
        "X-Title": "Telegram Bot Juan",
        "X-OpenRouter-Title": "Telegram Bot Juan"
    }

    data = {
        "model": SUMMARY_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres un archivista de memoria para un bot de rol/asistente. "
                    "Tu trabajo es resumir continuidad de forma precisa, compacta y útil. "
                    "No inventes hechos."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0.2,
        "max_tokens": 900
    }

    response = requests.post(
        url,
        headers=headers,
        json=data,
        timeout=90
    )

    try:
        response_json = response.json()
    except Exception:
        raise Exception(f"OpenRouter no devolvió JSON válido: {response.text[:500]}")

    if response.status_code >= 400:
        error_msg = response_json.get("error", {}).get("message", response.text[:500])
        raise Exception(error_msg)

    if "choices" not in response_json:
        error_msg = response_json.get("error", {}).get("message", "Respuesta sin choices")
        raise Exception(error_msg)

    return response_json["choices"][0]["message"]["content"]


# ============================================================
# RESUMEN INTELIGENTE PARA AHORRAR TOKENS
# ============================================================

def summarize_history(memory_key, mode):
    """
    Resume la conversación para ahorrar tokens sin perder continuidad.
    Usa un modelo barato y actualiza una bitácora por Tema.
    """

    if memory_key not in chat_histories:
        return

    history = chat_histories[memory_key]

    if len(history) < 12:
        return

    previous_summary = chat_summaries.get(memory_key, "")
    recent_messages = history[1:]  # excluye system prompt

    summary_prompt = (
        "Actualiza una bitácora de continuidad para una simulación, conversación persistente o asistente.\n"
        "No escribas una narración bonita: escribe memoria útil, compacta y precisa.\n"
        "Conserva nombres, relaciones, decisiones, promesas, traiciones, conflictos, deseos, secretos, recursos, lugares, fechas internas, consecuencias y tono de la relación.\n"
        "Si es una simulación política o medieval, conserva facciones, cargos, territorios, alianzas, enemigos, recursos, escándalos, heridas, matrimonios, romances, magia, pactos y conflictos pendientes.\n"
        "Si es Friend, conserva recuerdos personales de Juan, gustos, emociones, temas importantes, forma de trato y evolución de la relación.\n"
        "Si es Asistente, conserva pendientes, recordatorios, medicamentos, compromisos, ideas y tareas importantes.\n"
        "Elimina detalles irrelevantes.\n"
        "No inventes hechos.\n\n"
        f"MODO ACTUAL: {mode}\n\n"
        f"BITÁCORA ANTERIOR:\n{previous_summary or '(vacía)'}\n\n"
        f"MENSAJES RECIENTES:\n{recent_messages}\n\n"
        "Devuelve una bitácora actualizada, clara y compacta, máximo 900 palabras."
    )

    try:
        summary = call_openrouter_for_summary(summary_prompt)
        chat_summaries[memory_key] = summary.strip()

        chat_histories[memory_key] = [
            chat_histories[memory_key][0]
        ] + chat_histories[memory_key][-10:]

        ensure_history(memory_key, mode)

    except Exception as e:
        print(f"[WARN] No se pudo resumir memoria para {memory_key}: {e}")


# ============================================================
# FLASK
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return "Bot en línea vía Webhook.", 200


@app.route("/despertar", methods=["GET"])
def despertar_bot():
    """
    Endpoint para cron-jobs.
    Escribe SOLO al chat de Asistente.
    """

    secret = request.args.get("secret", "")

    if CRON_SECRET and secret != CRON_SECRET:
        return "No autorizado", 403

    if not ASSISTANT_CHAT_ID:
        return "Error: ASSISTANT_CHAT_ID no configurado", 400

    try:
        prompt_automatico = (
            "Escribe un mensaje breve para Juan como asistente personal. "
            "Recuérdale revisar sus pendientes del día, medicamentos, agenda y tareas importantes. "
            "Sé amable, directo y práctico. No inventes pendientes concretos todavía; solo invita a revisarlos."
        )

        respuesta = call_openrouter_simple(
            prompt=prompt_automatico,
            model=ASSISTANT_MODEL
        )

        bot.send_message(
            int(ASSISTANT_CHAT_ID),
            f"🔔 Recordatorio del asistente\n\n{respuesta}"
        )

        return "Mensaje automático enviado al asistente.", 200

    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route(f"/{API_TOKEN}", methods=["POST"])
def receive_update():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "", 200

    return "Invalid content type", 403


# ============================================================
# COMANDOS ÚTILES
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
        f"🧠 Modelo asignado: `{model}`\n"
        f"🪶 Modelo resumen: `{SUMMARY_MODEL}`\n"
        f"💾 Memoria: `{memory_key}`",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["reset"])
def cmd_reset(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    memory_key = get_memory_key(chat_id, thread_id)
    mode = get_chat_mode(chat_id, thread_id)

    if memory_key in chat_histories:
        del chat_histories[memory_key]

    if memory_key in chat_summaries:
        del chat_summaries[memory_key]

    if memory_key in message_counters:
        del message_counters[memory_key]

    ensure_history(memory_key, mode)

    bot.reply_to(
        message,
        "🧹 Memoria corta y bitácora de este chat/tema reiniciadas."
    )


@bot.message_handler(commands=["bitacora"])
def cmd_bitacora(message):
    chat_id = message.chat.id
    thread_id = get_thread_id(message)
    memory_key = get_memory_key(chat_id, thread_id)

    summary = chat_summaries.get(memory_key, "").strip()

    if not summary:
        bot.reply_to(
            message,
            "📜 Este chat/tema todavía no tiene bitácora resumida."
        )
        return

    send_message_to_thread(
        chat_id=chat_id,
        thread_id=thread_id,
        text=f"📜 Bitácora resumida:\n\n{summary}",
        reply_to_message_id=message.message_id
    )


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
            "Comando no reconocido. Por ahora puedes usar: /id, /modo, /reset, /bitacora."
        )
        return

    mode = get_chat_mode(chat_id, thread_id)
    memory_key = get_memory_key(chat_id, thread_id)

    ensure_history(memory_key, mode)

    chat_histories[memory_key].append(
        {
            "role": "user",
            "content": user_text
        }
    )

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

        chat_histories[memory_key].append(
            {
                "role": "assistant",
                "content": bot_response
            }
        )

        message_counters[memory_key] = message_counters.get(memory_key, 0) + 1

        if mode in ["medieval", "politica", "friend", "simulacion", "assistant"]:
            if message_counters[memory_key] % 8 == 0:
                summarize_history(memory_key, mode)

        rotate_history(memory_key)

        send_message_to_thread(
            chat_id=chat_id,
            thread_id=thread_id,
            text=bot_response,
            reply_to_message_id=message.message_id
        )

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

    app.run(
        host="0.0.0.0",
        port=port
    )
