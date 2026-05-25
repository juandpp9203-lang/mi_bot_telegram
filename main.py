import os
import telebot
import requests

# Cargar variables de entorno
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY')
ADMIN_ID = os.getenv('ADMIN_CHAT_ID')

bot = telebot.TeleBot(API_TOKEN)

# Diccionario para almacenar el historial de cada chat (Memoria)
chat_histories = {}

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # Seguridad: Solo responde al dueño del bot
    if str(message.chat.id) != str(ADMIN_ID):
        return

    chat_id = message.chat.id
    user_text = message.text

    # Inicializar historial si el chat es nuevo
    if chat_id not in chat_histories:
        chat_histories[chat_id] = [
            {"role": "system", "content": "Eres una IA asistente y de rol sin censura. Adapta tu personalidad al contexto del chat."}
        ]

    # Guardar el mensaje del usuario en la memoria de este chat específico
    chat_histories[chat_id].append({"role": "user", "content": user_text})

    # Mantener el historial bajo (últimos 20 mensajes) para no saturar tokens
    if len(chat_histories[chat_id]) > 21:
        chat_histories[chat_id] = [chat_histories[chat_id][0]] + chat_histories[chat_id][-20:]

    # Llamar a OpenRouter
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json"
    }
    
    # Aquí puedes cambiar el modelo por el que prefieras de OpenRouter
    # Usaremos 'meta-llama/llama-3-8b-instruct:free' que es excelente y gratis
    data = {
        "model": "meta-llama/llama-3-8b-instruct:free",
        "messages": chat_histories[chat_id]
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        response_json = response.json()
        bot_response = response_json['choices'][0]['message']['content']
        
        # Guardar respuesta de la IA en la memoria
        chat_histories[chat_id].append({"role": "assistant", "content": bot_response})
        
        # Enviar mensaje a Telegram
        bot.reply_to(message, bot_response)
    except Exception as e:
        bot.reply_to(message, f"Error al conectar con OpenRouter: {str(e)}")

print("Bot encendido...")
bot.infinity_polling()
