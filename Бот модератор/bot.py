import os
import re
import asyncio
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, Set, List

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

load_dotenv()
logger.info("Загружены переменные окружения")

# Инициализация бота и диспетчера
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()
logger.info("Инициализирован бот и диспетчер")

# Хранилище для отслеживания сообщений и банов
message_history: Dict[int, List[datetime]] = defaultdict(list)  # user_id -> list of message timestamps
user_bans: Dict[int, datetime] = {}  # user_id -> ban end time
message_texts: Dict[str, Set[int]] = defaultdict(set)  # message_text -> set of user_ids

# Список исключений - ID чатов/сообществ и админов, чьи сообщения не удаляются
EXCLUDED_SENDERS = {
    2385254556,
    2439700122,
    2250868984,
    2299912906,
    2406705223,
    8192306358  # Админ
}

# Стандартные разрешения для ограничения
restricted_permissions = types.ChatPermissions(
    can_send_messages=False,
    can_send_media_messages=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_send_polls=False,
    can_invite_users=False,
    can_pin_messages=False,
    can_change_info=False
)

async def delete_user_messages(chat_id: int, user_id: int, message_id: int = None) -> None:
    """Удаляет все сообщения пользователя из чата"""
    logger.info(f"Начало удаления сообщений пользователя {user_id} из чата {chat_id}")
    deleted_count = 0
    
    try:
        # Если передан message_id, начинаем с него, иначе пробуем получить последнее сообщение
        if message_id:
            start_id = message_id
        else:
            start_id = 1
            
        # Пробуем удалить сообщения в диапазоне ±100 от текущего
        for msg_id in range(start_id + 100, max(0, start_id - 100), -1):
            try:
                msg = await bot.delete_message(chat_id, msg_id)
                deleted_count += 1
                logger.debug(f"Удалено сообщение {msg_id}")
            except Exception as e:
                # Пропускаем ошибки для чужих сообщений и несуществующих
                continue
        
        logger.info(f"Удалено сообщений: {deleted_count}")
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщений: {e}")

async def check_numeric_sequence(message: types.Message) -> bool:
    """Проверка на числовые последовательности"""
    logger.debug(f"Проверка сообщения {message.message_id} на числовые последовательности")
    
    if re.search(r'\d{3,}', message.text):
        logger.info(f"Обнаружена числовая последовательность в сообщении {message.message_id} от пользователя {message.from_user.id}")
        restrict_until = datetime.now() + timedelta(days=1)
        user_bans[message.from_user.id] = restrict_until
        
        # Удаляем все сообщения пользователя
        await delete_user_messages(message.chat.id, message.from_user.id, message.message_id)
        
        # Ограничиваем права
        try:
            await message.chat.restrict(
                user_id=message.from_user.id,
                until_date=restrict_until,
                permissions=restricted_permissions
            )
            logger.info(f"Пользователь {message.from_user.id} ограничен до {restrict_until}")
        except Exception as e:
            logger.error(f"Ошибка при ограничении пользователя {message.from_user.id}: {e}")
        
        logger.info(f"Пользователь {message.from_user.id} ограничен за использование числовых последовательностей")
        
        return True
    return False

async def check_amount_mention(message: types.Message) -> bool:
    """Проверка на упоминание сумм (число + к/k, с пробелом или без)"""
    logger.debug(f"Проверка сообщения {message.message_id} на упоминание сумм")
    
    # Паттерн для поиска: число (1+ цифр) + опциональный пробел + к/K (рус/англ)
    if re.search(r'\d+\s*[кКkK]', message.text):
        logger.info(f"Обнаружено упоминание суммы в сообщении {message.message_id} от пользователя {message.from_user.id}")
        restrict_until = datetime.now() + timedelta(days=7)
        user_bans[message.from_user.id] = restrict_until
        
        # Удаляем все сообщения пользователя
        await delete_user_messages(message.chat.id, message.from_user.id, message.message_id)
        
        # Ограничиваем права
        try:
            await message.chat.restrict(
                user_id=message.from_user.id,
                until_date=restrict_until,
                permissions=restricted_permissions
            )
            logger.info(f"Пользователь {message.from_user.id} ограничен до {restrict_until} за упоминание суммы")
        except Exception as e:
            logger.error(f"Ошибка при ограничении пользователя {message.from_user.id}: {e}")
        
        return True
    return False

async def check_flood(message: types.Message) -> bool:
    """Проверка на одинаковые сообщения от разных пользователей"""
    logger.debug(f"Проверка сообщения {message.message_id} на флуд")
    
    text = message.text
    user_id = message.from_user.id
    
    if text in message_texts:
        if message_texts[text]:  # Если уже есть другие пользователи с таким сообщением
            logger.info(f"Обнаружен флуд в сообщении {message.message_id}. Текст: {text}")
            restrict_until = datetime.now() + timedelta(days=3)
            affected_users = message_texts[text] | {user_id}
            
            logger.info(f"Затронутые пользователи: {affected_users}")
            
            # Ограничиваем всех пользователей и удаляем их сообщения
            for uid in affected_users:
                try:
                    user_bans[uid] = restrict_until
                    await message.chat.restrict(
                        user_id=uid,
                        until_date=restrict_until,
                        permissions=restricted_permissions
                    )
                    logger.info(f"Пользователь {uid} ограничен до {restrict_until}")
                    await delete_user_messages(message.chat.id, uid, message.message_id)
                except Exception as e:
                    logger.error(f"Ошибка при ограничении пользователя {uid}: {e}")
            
            logger.info(f"Пользователи {affected_users} ограничены за флуд")
            
            message_texts[text].clear()  # Очищаем список пользователей
            return True
            
    message_texts[text].add(user_id)
    logger.debug(f"Сообщение {message.message_id} добавлено в отслеживание флуда")
    return False

async def check_spam(message: types.Message) -> bool:
    """Проверка на частый постинг"""
    logger.debug(f"Проверка сообщения {message.message_id} на спам")
    
    user_id = message.from_user.id
    current_time = datetime.now()
    
    # Очистка старых сообщений
    old_count = len(message_history[user_id])
    message_history[user_id] = [
        time for time in message_history[user_id]
        if current_time - time <= timedelta(minutes=2)
    ]
    new_count = len(message_history[user_id])
    
    if old_count != new_count:
        logger.debug(f"Удалено {old_count - new_count} устаревших записей для пользователя {user_id}")
    
    message_history[user_id].append(current_time)
    logger.debug(f"Добавлено новое сообщение. Всего сообщений за 2 минуты: {len(message_history[user_id])}")
    
    if len(message_history[user_id]) >= 3:
        logger.info(f"Обнаружен спам от пользователя {user_id} в сообщении {message.message_id}")
        restrict_until = datetime.now() + timedelta(days=20)
        user_bans[user_id] = restrict_until
        
        try:
            # Удаляем все сообщения пользователя
            await delete_user_messages(message.chat.id, user_id, message.message_id)
            
            # Ограничиваем права
            await message.chat.restrict(
                user_id=user_id,
                until_date=restrict_until,
                permissions=restricted_permissions
            )
            logger.info(f"Пользователь {user_id} ограничен до {restrict_until}")
            
            logger.info(f"Пользователь {message.from_user.id} ограничен за спам")
            
        except Exception as e:
            logger.error(f"Ошибка при обработке спама от пользователя {user_id}: {e}")
        
        message_history[user_id].clear()
        return True
    return False

@dp.message()
async def handle_message(message: types.Message) -> None:
    """Обработчик всех сообщений"""
    logger.debug(f"Получено новое сообщение {message.message_id} от пользователя {message.from_user.id}")
    
    if not message.text:
        logger.debug("Сообщение не содержит текст, пропускаем")
        return
    
    # Проверка на исключения - пропускаем сообщения от чатов/сообществ и админов
    sender_id = message.from_user.id if message.from_user else None
    sender_chat_id = message.sender_chat.id if message.sender_chat else None
    
    if sender_id in EXCLUDED_SENDERS or sender_chat_id in EXCLUDED_SENDERS:
        logger.debug(f"Сообщение от исключенного отправителя (user: {sender_id}, chat: {sender_chat_id}), пропускаем проверки")
        return

    # Проверка на пересланные сообщения
    if message.forward_date:
        logger.info(f"Обнаружено пересланное сообщение {message.message_id} от пользователя {message.from_user.id}")
        try:
            await message.delete()
            logger.info("Пересланное сообщение удалено")
        except Exception as e:
            logger.error(f"Ошибка при обработке пересланного сообщения: {e}")
        return

    # Проверка на бан
    user_id = message.from_user.id
    if user_id in user_bans:
        if datetime.now() >= user_bans[user_id]:
            logger.info(f"Снято ограничение с пользователя {user_id}")
            del user_bans[user_id]
        else:
            logger.debug(f"Удалено сообщение от ограниченного пользователя {user_id}")
            await message.delete()
            return

    # Применяем все проверки
    try:
        if await check_amount_mention(message):
            logger.debug("Сработала проверка на упоминание суммы")
            return
        if await check_numeric_sequence(message):
            logger.debug("Сработала проверка на числовые последовательности")
            return
        if await check_flood(message):
            logger.debug("Сработала проверка на флуд")
            return
        if await check_spam(message):
            logger.debug("Сработала проверка на спам")
            return
    except Exception as e:
        logger.error(f"Ошибка при проверке сообщения {message.message_id}: {e}")

async def main() -> None:
    """Запуск бота"""
    logger.info("Запуск бота...")
    try:
        logger.info("Начало поллинга...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
    finally:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    try:
        logger.info("Инициализация бота...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        logger.info("Программа завершена")
