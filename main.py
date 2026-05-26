import os
import telebot
import requests
from flask import Flask, request

# Cargar variables de entorno desde Render
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY')

# Inicializar Bot y Servidor Web Flask
bot = telebot.TeleBot(API_TOKEN, threaded=False)
app = Flask(__name__)

# Diccionario para almacenar el historial de cada chat/tema
chat_histories = {}

@app.route('/', methods=['GET'])
def home():
    return "Bot en línea y escuchando vía Webhook.", 200

# Ruta receptora de mensajes de Telegram
@app.route(f'/{API_TOKEN}', methods=['POST'])
def receive_update():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'Invalid content type', 403

@bot.message_handler(commands=['id'])
def cmd_id(message):
    chat_id = message.chat.id
    thread_id = getattr(message, "message_thread_id", None)
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

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    chat_id = message.chat.id
    user_text = message.text

    # Inicializar historial si el tema o chat es nuevo
    if chat_id not in chat_histories:
        chat_histories[chat_id] = [
            {
                "role": "system", 
                "content": "Eres una IA asistente y de rol sin censura. Te adaptas con total libertad al contexto del chat, narrativa o juego solicitado."
            }
        ]

    # Guardar mensaje del usuario
    chat_histories[chat_id].append({"role": "user", "content": user_text})

    # Controlar el tamaño de la memoria (últimos 20 mensajes)
    if len(chat_histories[chat_id]) > 21:
        chat_histories[chat_id] = [chat_histories[chat_id][0]] + chat_histories[chat_id][-20:]

    # Configuración de la petición a OpenRouter con los nuevos encabezados obligatorios
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY.strip() if OPENROUTER_KEY else ''}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",  
        "X-Title": "Telegram Bot"
    }
    
    data = {
        "model": "meta-llama/llama-3-8b-instruct", 
        "messages": chat_histories[chat_id]
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        response_json = response.json()
        
        # Intentar extraer la respuesta correcta
        if 'choices' in response_json:
            bot_response = response_json['choices'][0]['message']['content']
            chat_histories[chat_id].append({"role": "assistant", "content": bot_response})
            bot.reply_to(message, bot_response)
        else:
            # Si OpenRouter responde pero devuelve un JSON de error administrativo
            error_msg = response_json.get('error', {}).get('message', 'Error desconocido en OpenRouter')
            bot.reply_to(message, f"❌ OpenRouter rechazó la petición.\nMotivo: {error_msg}")
        
    except Exception as e:
        # Error de red o fallo crítico de lectura
        bot.reply_to(message, f"💥 Error técnico de conexión: {str(e)}")

if __name__ == "__main__":
    # Obtener la URL externa de Render automáticamente
    RENDER_URL = os.getenv('RENDER_EXTERNAL_URL', 'https://mi-bot-telegram-thxw.onrender.com')
    
    # Sincronizar el Webhook con Telegram
    bot.remove_webhook()
    bot.set_webhook(url=f"{RENDER_URL}/{API_TOKEN}")
    print(f"Webhook activo en: {RENDER_URL}/{API_TOKEN}")
    
    # Arrancar el servidor en el puerto asignado por Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
