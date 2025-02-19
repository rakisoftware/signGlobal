# Глобальный счётчик ключей и ошибок
import asyncio
import os
import random

import time
from asyncio import WindowsSelectorEventLoopPolicy

from rich.console import Console
from rich.prompt import Prompt

import config
from utils.database import Database
from utils.logger import logger
from utils.sign import Sign

console = Console()
global_key_index = 0
error_count = 0  # Глобальный счётчик ошибок
last_error_time = None  # Время последней ошибки
key_lock = asyncio.Lock()  # Блокировка для синхронизации доступа к счётчику
error_lock = asyncio.Lock()  # Блокировка для синхронизации ошибок
heroes_ranks = {}
heroes_ranks_ready = asyncio.Event()

# Максимальное время между ошибками для их учета как последовательных (например, 60 секунд)
MAX_TIME_BETWEEN_ERRORS = 30

async def read_private_keys(filepath):
    with open(filepath, 'r') as file:
        keys = file.readlines()
    return [key.strip() for key in keys]


async def count_keys(filepath):
    with open(filepath, 'r') as file:
        return len(file.readlines())


async def retry_function(func, thread, key, *args, **kwargs):
    global error_count, last_error_time  # Используем глобальные переменные
    for _ in range(7):
        try:
            result = await func(*args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Поток {thread} | Ошибка выполнения функции {func.__name__}: {e}")

            async with error_lock:
                current_time = time.time()  # Получаем текущее время

                # Если это первая ошибка или предыдущая ошибка была слишком давно, сбрасываем счётчик
                if last_error_time is None or current_time - last_error_time > MAX_TIME_BETWEEN_ERRORS:
                    error_count = 1  # Сброс счётчика и начало новой серии ошибок
                else:
                    error_count += 1  # Увеличиваем счётчик, так как ошибка произошла в пределах допустимого интервала

                last_error_time = current_time  # Обновляем время последней ошибки

                # Если достигнуто 7 ошибок подряд, пауза для потока
                if error_count >= 7:
                    logger.warning(f"Поток {thread} | 7 ошибок подряд. Пауза на 5 минут.")
                    # Пауза на 10 минут для потока
                    await asyncio.sleep(300)  # Пауза на 5 минут (300 секунд)
                    # Сброс счётчика ошибок после паузы
                    error_count = 0

            await asyncio.sleep(5)  # Ожидание перед повтором

    try:
        filepath = os.path.join(os.path.dirname(__file__), 'reports/failed_keys.txt')
        with open(filepath, "a") as f:
            f.write(f"{key}\n")
        logger.info(f"Поток {thread} | Ключ {key} записан в файл.")
    except Exception as file_error:
        logger.error(f"Не удалось записать ключ в файл: {file_error}")

async def start(thread, keys, semaphore, key_count, mode, network, db):
    logger.info(f"Поток {thread} | Начал работу")

    try:
        async with semaphore:
            for key in keys:
                sign = Sign(key=key, thread=thread, db=db, chain=network)
                # Синхронизируем доступ к глобальному счётчику ключей
                async with key_lock:
                    global global_key_index
                    global_key_index += 1
                    current_key_index = global_key_index

                logger.info(
                    f"Поток {thread} работает с ключем ...{key[29:]} | {sign.address} | {current_key_index} of {key_count}")

                await retry_function(sign.login, thread, key)

                if mode == "schemas":
                    created_schemas = 0
                    for _ in range(random.randint(config.SCHEMAS_TO_CREATE[0], config.SCHEMAS_TO_CREATE[1])):
                        if await sign.create_schema():
                            await asyncio.sleep(random.randint(config.PAUSE_BETWEEN_CREATIONS[0], config.PAUSE_BETWEEN_CREATIONS[1]))
                            created_schemas += 1

                    if created_schemas > 0:
                        logger.info(f"Поток {thread} | Создано {created_schemas} новых схем, запишу их в базу")
                        await retry_function(sign.fetch_user_schemas, thread, key, network)

                if mode == "attestations":
                    created_attestations = 0
                    for _ in range(random.randint(config.ATTESTATIONS_TO_CREATE[0], config.ATTESTATIONS_TO_CREATE[1])):
                        schema_id = await db.get_random_schema_id(chain=network)
                        if await sign.create_attestation(schema_id):
                            created_attestations += 1
                            await asyncio.sleep(random.randint(config.PAUSE_BETWEEN_CREATIONS[0], config.PAUSE_BETWEEN_CREATIONS[1]))

                    logger.info(f"Поток {thread} | Создано {created_attestations} аттестаций")

                await sign.logout()

    except StopIteration:
        logger.info(f"Поток {thread} | Закончил работу")


async def main():
    db = Database()
    await db.initialize_db()

    console.print("[yellow]Выберите режим работы:[/yellow] \n 1: Создание схем \n 2: Создание аттестаций")
    mode = Prompt.ask("Введите 1 или 2", choices=["1", "2"])
    mode = "schemas" if mode == "1" else "attestations"

    console.print("[yellow]Выберите сеть: \n 1: BSC \n 2: opBNB \n 3: Polygon [/yellow]")
    network_options = {
        "1": "BSC",
        "2": "opBNB",
        "3": "Polygon"
    }
    network_choice = Prompt.ask("Введите 1, 2 или 3", choices=["1", "2", "3"])
    network = {"1": "bsc", "2": "opbnb", "3": "polygon"}[network_choice]

    console.print(f"\n✅ Выбран режим: [bold]{mode}[/bold], Сеть: [bold]{network_options[network_choice]}[/bold]\n")

    thread_count = config.THREADS

    filepath = os.path.join(os.path.dirname(__file__), 'data/private_keys.txt')

    keys = await read_private_keys(filepath)
    key_count = await count_keys(filepath)

    tasks = []
    keys_iterator = iter(keys)

    semaphore = asyncio.Semaphore(thread_count)

    for thread in range(1, thread_count + 1):
        tasks.append(asyncio.create_task(start(thread, keys_iterator, semaphore, key_count, mode, network, db)))

    await asyncio.gather(*tasks)

    console.print("[bold green]Прогон окончен[/bold green]")

if __name__ == "__main__":
    asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())
    asyncio.run(main())