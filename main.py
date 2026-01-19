import json
import websocket
import uuid
import time
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional
import requests
from telegram import Bot
from telegram.error import TelegramError
import threading
import queue
import ssl

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = "An_Sx6HQ9HDiiCOYOz20uvcivy0TjwT_beExY0acUyn7ec532X4GoucUKgHMFtGSh4pd9LVMJuAG1K3fvGQexZwvLSJioe9uek06S8yT3_vCcSs79iTCO8lslkvhxr0DnqFXgCihQiOt6P7HPIl--5iS6SdOW50l4kA0wmRgKjrEL13sSUKdD_aNXjbNWDSqZ5xmxuNJaLlI_7awpWapub9NaU5l1j_9ao45yO0TQlBnPFUPvgpa6yfYFGxRw6tTllfGa6XfcXf1DsHT4MNYUlF_EkVMTkRw0vA-vtje8E-Q_0pbcCC8ClMY85AkNpySSkb3EXdq_qcjKtg14fiuV_NyhkLrD8n2nIEn_MLw11oD-bEHtEOPZsneedpGAKxU1SMV2ZC7uYhGopmDeeJiJ10rkg-Hgc6Oq4wSjdY40HL_e3JIiD0B17PrsxD4gwktEEfoOaMgSg3Afwmd_ZvwBEACbxgB8HDTo9oPF51cXGGCaLZAf9q7ojRQbx8OMp_X33rkprYouja40xDjzYzwm-9bvOgCz6cuMPQhZc66KJWQP9LKwGoIu3C5ED1wDi3fw283yMeKIlodhLy8e6PfnB6NzxNG83K7gSqa3BvRgBJxvnzXLKww_DCryuuopce3TxticlSRqwfDmC1bGncS-oii-RG2BjK0Zm3sIoHp09RvuqZTDNctGlPQhaoBpkDdcE8WrS8"
WS_URL = "wss://ws-api.oneme.ru/websocket"
DEVICE_ID = str(uuid.uuid4())

# Telegram настройки
TELEGRAM_BOT_TOKEN = "8496661954:AAErKw7SkqzVnmlmcZ0ik_aqlsbcfjDt5jo"  # Замените на ваш токен
TELEGRAM_CHAT_ID = -1003499201614      # Замените на ID чата

# ID чата MAX для мониторинга
MAX_CHAT_ID = -69258601204457

# Глобальные переменные для отслеживания состояния
last_message_ids = set()
processed_messages = set()

class MaxMessageFetcher:
    def __init__(self, token: str, max_chat_id: int):
        self.token = token
        self.max_chat_id = max_chat_id
        self.ws = None
        self.seq_counter = 0
        
    def create_websocket(self):
        """Создание нового WebSocket соединения"""
        try:
            # Создаем новый WebSocket
            ws = websocket.WebSocket()
            
            # Устанавливаем соединение с таймаутом
            ws.connect(
                WS_URL,
                origin="https://web.max.ru",
                header=[
                    "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                ],
                timeout=10
            )
            
            logger.debug("WebSocket connection created")
            return ws
            
        except Exception as e:
            logger.error(f"Failed to create WebSocket: {e}")
            return None
    
    def send_message(self, ws, obj: Dict):
        """Отправка сообщения через WebSocket"""
        try:
            # Увеличиваем счетчик последовательности
            self.seq_counter += 1
            obj["seq"] = self.seq_counter
            
            ws.send(json.dumps(obj))
            logger.debug(f"→ Sent opcode: {obj['opcode']}, seq: {obj['seq']}")
            
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            raise
    
    def receive_message(self, ws, timeout=5):
        """Получение сообщения через WebSocket с таймаутом"""
        try:
            # Устанавливаем таймаут
            ws.settimeout(timeout)
            
            # Получаем сообщение
            response = ws.recv()
            
            # Сбрасываем таймаут
            ws.settimeout(None)
            
            return response
            
        except websocket.WebSocketTimeoutException:
            logger.warning(f"Timeout receiving message after {timeout} seconds")
            return None
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            return None
    
    def authenticate_session(self, ws):
        """Аутентификация в новой сессии"""
        try:
            device_id = str(uuid.uuid4())
            
            # 1️⃣ HELLO
            self.send_message(ws, {
                "ver": 11,
                "cmd": 0,
                "seq": 0,  # Будет перезаписано в send_message
                "opcode": 6,
                "payload": {
                    "userAgent": {
                        "deviceType": "WEB",
                        "locale": "ru",
                        "deviceLocale": "ru",
                        "osVersion": "macOS",
                        "deviceName": "Chrome",
                        "headerUserAgent": "Mozilla/5.0",
                        "appVersion": "25.12.14",
                        "screen": "982x1512 2.0x",
                        "timezone": "Europe/Moscow"
                    },
                    "deviceId": device_id
                }
            })
            
            # Получаем ответ (может быть несколько технических ответов)
            hello_response = self.receive_message(ws)
            if hello_response:
                logger.debug(f"← HELLO response received")
            
            # 2️⃣ AUTH
            self.send_message(ws, {
                "ver": 11,
                "cmd": 0,
                "seq": 0,  # Будет перезаписано
                "opcode": 19,
                "payload": {
                    "interactive": False,
                    "token": self.token
                }
            })
            
            # Получаем ответ
            auth_response = self.receive_message(ws)
            if auth_response:
                logger.debug(f"← AUTH response received")
            
            logger.info("Successfully authenticated new session")
            return True
            
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return False
    
    def fetch_messages_from_session(self):
        """Запрос сообщений из новой сессии"""
        ws = None
        try:
            # Создаем новую сессию
            ws = self.create_websocket()
            if not ws:
                return []
            
            # Аутентифицируемся
            if not self.authenticate_session(ws):
                ws.close()
                return []
            
            # 3️⃣ GET MESSAGES
            self.send_message(ws, {
                "ver": 11,
                "cmd": 0,
                "seq": 0,  # Будет перезаписано
                "opcode": 49,
                "payload": {
                    "chatId": self.max_chat_id,
                    "from": int(time.time() * 1000),
                    "forward": 30,
                    "backward": 15,
                    "getMessages": True
                }
            })
            
            # Первый ответ - технический
            first_response = self.receive_message(ws, timeout=3)
            logger.debug(f"← First (technical) response received")
            
            # Второй ответ - с сообщениями (ждем дольше)
            second_response = self.receive_message(ws, timeout=10)
            if not second_response:
                logger.warning("No second response received (timeout)")
                ws.close()
                return []
            
            logger.debug(f"← Second (messages) response received")
            
            # Закрываем соединение
            ws.close()
            
            # Парсим сообщения
            messages = self.parse_messages_response(second_response)
            return messages
            
        except Exception as e:
            logger.error(f"Error in fetch_messages_from_session: {e}")
            if ws:
                try:
                    ws.close()
                except:
                    pass
            return []
    
    def parse_messages_response(self, response: str) -> List[Dict]:
        """Парсинг ответа с сообщениями"""
        try:
            data = json.loads(response)
            
            # Проверяем структуру ответа
            if "payload" not in data or "messages" not in data["payload"]:
                logger.warning("No messages in response")
                return []
            
            messages = data["payload"]["messages"]
            
            # Фильтруем только USER сообщения с текстом
            user_messages = []
            for msg in messages:
                if msg.get("type") == "USER" and msg.get("text"):
                    user_messages.append(msg)
            
            logger.info(f"Parsed {len(user_messages)} user messages from response")
            return user_messages
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing messages: {e}")
            return []
    
    def extract_message_info(self, message: Dict) -> Dict:
        """Извлечение основной информации из сообщения"""
        msg_id = message.get("id", "")
        sender = message.get("sender", "")
        text = message.get("text", "")
        timestamp = message.get("time", 0)
        
        # Извлекаем ссылки из элементов
        links = []
        if "elements" in message:
            for elem in message["elements"]:
                if elem.get("type") == "LINK" and "attributes" in elem:
                    links.append(elem["attributes"].get("url", ""))
        
        # Извлекаем вложения
        attachments = []
        if "attaches" in message:
            for attach in message["attaches"]:
                attach_type = attach.get("_type", "")
                if attach_type == "SHARE":
                    attachments.append({
                        "type": "share",
                        "title": attach.get("title", ""),
                        "url": attach.get("url", ""),
                        "description": attach.get("description", "")
                    })
                elif attach_type == "FILE":
                    attachments.append({
                        "type": "file",
                        "name": attach.get("name", ""),
                        "size": attach.get("size", 0)
                    })
        
        return {
            "id": msg_id,
            "sender": sender,
            "text": text,
            "timestamp": timestamp,
            "links": links,
            "attachments": attachments,
            "raw": message
        }


class TelegramBot:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot = Bot(token=bot_token)
        self.chat_id = chat_id
        
    async def send_message_async(self, text: str):
        """Асинхронная отправка сообщения в Telegram"""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode='HTML',
                disable_web_page_preview=False
            )
            logger.info(f"Message sent to Telegram chat {self.chat_id}")
            return True
        except TelegramError as e:
            logger.error(f"Failed to send message to Telegram: {e}")
            return False
    
    def send_message_sync(self, text: str):
        """Синхронная отправка сообщения в Telegram"""
        try:
            asyncio.run(self.send_message_async(text))
            return True
        except Exception as e:
            logger.error(f"Error in sync send: {e}")
            return False
    
    def format_message_for_telegram(self, message_info: Dict) -> str:
        """Форматирование сообщения для отправки в Telegram"""
        text = message_info["text"]
        
        # Добавляем заголовок
        formatted_text = f"📨 <b>Новое сообщение из MAX</b>\n\n"
        
        # Добавляем текст сообщения
        if text:
            formatted_text += f"{text}\n\n"
        
        # Добавляем ссылки в конец сообщения
        links = message_info.get("links", [])
        if links:
            formatted_text += "🔗 <b>Ссылки:</b>\n"
            for i, link in enumerate(links, 1):
                formatted_text += f"{i}. {link}\n"
        
        # Добавляем информацию о вложениях
        attachments = message_info.get("attachments", [])
        if attachments:
            formatted_text += "\n📎 <b>Вложения:</b>\n"
            for attach in attachments:
                if attach["type"] == "share":
                    title = attach.get('title', 'Ссылка')
                    url = attach.get('url', '')
                    if url:
                        formatted_text += f"• 📄 {title}: {url}\n"
                    else:
                        formatted_text += f"• 📄 {title}\n"
                elif attach["type"] == "file":
                    formatted_text += f"• 📎 Файл: {attach.get('name', 'Без названия')}\n"
        
        # Обрезаем если слишком длинное (Telegram ограничение 4096 символов)
        if len(formatted_text) > 4000:
            formatted_text = formatted_text[:3997] + "..."
        
        return formatted_text


class MaxTelegramBridge:
    def __init__(self, max_token: str, telegram_token: str, telegram_chat_id: str, max_chat_id: int):
        self.max_fetcher = MaxMessageFetcher(max_token, max_chat_id)
        self.telegram_bot = TelegramBot(telegram_token, telegram_chat_id)
        self.running = False
        
        # Глобальные переменные
        global last_message_ids, processed_messages
        self.last_message_ids = last_message_ids
        self.processed_messages = processed_messages
        
    def start(self):
        """Запуск моста"""
        self.running = True
        logger.info("MAX-Telegram bridge started")
        
        # Запускаем основной цикл
        self._run_monitoring_loop()
        
        return True
    
    def _run_monitoring_loop(self):
        """Основной цикл мониторинга с созданием новой сессии каждую минуту"""
        while self.running:
            try:
                logger.info("Starting new monitoring cycle...")
                
                # Получаем сообщения через новую сессию
                messages = self.max_fetcher.fetch_messages_from_session()
                
                if messages:
                    logger.info(f"Retrieved {len(messages)} messages from MAX")
                    
                    # Обрабатываем сообщения в обратном порядке (от старых к новым)
                    for msg in messages:
                        msg_id = msg.get("id", "")
                        
                        # Проверяем, обрабатывали ли мы это сообщение
                        if msg_id and msg_id in self.processed_messages:
                            continue
                        
                        # Извлекаем информацию о сообщении
                        message_info = self.max_fetcher.extract_message_info(msg)
                        
                        # Форматируем для Telegram
                        telegram_text = self.telegram_bot.format_message_for_telegram(message_info)
                        
                        # Отправляем в Telegram
                        success = self.telegram_bot.send_message_sync(telegram_text)
                        
                        if success and msg_id:
                            # Добавляем в обработанные
                            self.processed_messages.add(msg_id)
                            logger.info(f"Successfully processed message: {msg_id}")
                
                # Ждем 60 секунд перед следующей проверкой
                logger.info("Waiting 60 seconds before next check...")
                for i in range(60):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
                self.stop()
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                
                # Ждем перед повторной попыткой
                logger.info("Waiting 30 seconds before retry...")
                for i in range(30):
                    if not self.running:
                        break
                    time.sleep(1)
    
    def stop(self):
        """Остановка моста"""
        self.running = False
        logger.info("MAX-Telegram bridge stopped")


def main():
    """Основная функция"""
    # Проверяем наличие необходимых токенов
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("Please set your Telegram bot token in TELEGRAM_BOT_TOKEN variable")
        return
    
    if TELEGRAM_CHAT_ID == "YOUR_TELEGRAM_CHAT_ID":
        logger.error("Please set your Telegram chat ID in TELEGRAM_CHAT_ID variable")
        return
    
    # Создаем и запускаем мост
    bridge = MaxTelegramBridge(
        max_token=TOKEN,
        telegram_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
        max_chat_id=MAX_CHAT_ID
    )
    
    try:
        bridge.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        bridge.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        bridge.stop()


if __name__ == "__main__":
    main()
