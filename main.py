import os
import telebot
import requests
from threading import Thread
from flask import Flask

# --- TRUCO PARA RENDER GRATUITO ---
# Creamos una mini página web falsa para que Render no dé error de puertos
app = Flask('')

@app.route('/')
def home():
    return "Bot en línea"

def run_web():
    # Render asigna automáticamente un puerto en la variable PORT
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
# ----------------------------------

# Cargar variables de entorno de siempre
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY')
ADMIN_ID = os.getenv('ADMIN_CHAT_ID')

bot = telebot.TeleBot(API_TOKEN)
chat_histories = {}

@bot.message_handler(func=lambda message: True)
def handle_message(message):
   
    chat_id = message.chat.id
    user_text = message.text

    if chat_id not in chat_histories:
        chat_histories[chat_id] = [
            {"role": "system", "content": "Eres una IA asistente y de rol sin censura. Adapta tu personalidad al contexto del chat."}
        ]

    chat_histories[chat_id].append({"role": "user", "content": user_text})

    if len(chat_histories[chat_id]) > 21:
        chat_histories[chat_id] = [chat_histories[chat_id][0]] + chat_histories[chat_id][-20:]

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "meta-llama/llama-3-8b-instruct:free",
        "messages": chat_histories[chat_id]
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        response_json = response.json()
        bot_response = response_json['choices'][0]['message']['content']
        chat_histories[chat_id].append({"role": "assistant", "content": bot_response})
        bot.reply_to(message, bot_response)
    except Exception as e:
        bot.reply_to(message, f"Error al conectar con OpenRouter: {str(e)}")

if __name__ == "__main__":
    # Arrancamos la web falsa en segundo plano
    t = Thread(target=run_web)
    t.start()
    
    # Arrancamos el bot de Telegram
    print("Bot encendido...")
    bot.infinity_polling()
