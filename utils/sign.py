import asyncio
import datetime
import json
import os
import random
import string
from eth_abi import encode
from eth_account import Account
from web3.middleware import geth_poa_middleware
from constants.constants import rpc, contract_addresses, chain_ids, explorers
import config
from utils.logger import logger
from curl_cffi.requests import AsyncSession
from faker import Faker
from fake_useragent import UserAgent
from utils.web3_utils import Web3Utils


class Sign:
    def __init__(self, key: str, thread: int, db, chain):
        self.chain = chain
        self.w3 = Web3Utils(key=key, http_provider=rpc.get(chain))
        self.key = key
        if config.USE_PROXY:
            proxy = {
                'http': config.PROXY,
                'https': config.PROXY,
            }
            self.proxy = proxy
        self.db = db
        self.thread = thread
        self.fake = Faker()
        self.acct = self.w3.acct
        self.address = self.acct.address
        self.abi = self.read_abi(os.path.join(os.path.dirname(os.path.dirname(__file__)), "abis", "abi.json"))
        self.contract = self.w3.w3.eth.contract(address=contract_addresses.get(self.chain), abi=self.abi)
        self.w3.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        user_agent = UserAgent(os="Windows")
        ua_string = user_agent.chrome
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": "https://app.sign.global",
            "priority": "u=1, i",
            "referer": "https://app.sign.global/profile",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": ua_string
        }

        self.session = AsyncSession(
            headers=headers,
            proxies=self.proxy if config.USE_PROXY else None,  # Если USE_PROXY=False, передаём None
            impersonate="chrome110",
            verify=False,
            trust_env=True
        )

    async def login(self):
        def generate_nonce(length=12):
            """Генерация случайного nonce"""
            return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

        def get_iso_timestamp():
            """Получение текущего времени в формате ISO 8601"""
            return datetime.datetime.utcnow().isoformat() + "Z"

        nonce = generate_nonce()
        issued_at = get_iso_timestamp()

        message =  f"app.sign.global wants you to sign in with your Ethereum account:\n{self.address}\n\nSign Protocol\n\nURI: https://app.sign.global\nVersion: 1\nChain ID: 1\nNonce: {nonce}\nIssued At: {issued_at}"
        signature = self.w3.get_signed_code(message)

        data = {
            "message": message,
            "signature": signature,
            "chainType": "evm",
            "client": "MetaMask",
            "key": f"{self.address}"
        }
        response = await self.session.post(url='https://app.sign.global/api/signin', json=data)
        if response.status_code == 201:
            success = response.json().get("success")
            if success:
                logger.info(f"Поток {self.thread} | Залогинился")
            else:
                logger.error(f"Поток {self.thread} | Проблема с логином: {response.json()}")
                return False
        else:
            logger.error(f"Поток {self.thread} | Проблема с логином: {response.json()}")
            return False

    async def fetch_user_schemas(self, chain_name):
        chain_id = chain_ids.get(chain_name)
        if not chain_id:
            logger.error(f"Поток {self.thread} | Unknown chain name: {chain_name}")
            return

        url = f'https://mainnet-rpc.sign.global/api/scan/addresses/{self.address}/schemas?id={self.address}&page=1&size=100'

        response = await self.session.get(url)

        if response.status_code != 200:
            logger.error(f"Error fetching data: {response.status}")
            return

        data = response.json()
        if not data.get("success"):
            logger.error(f"Поток {self.thread} | Error in response data")
            return

        schemas = data.get("data", {}).get("rows", [])
        filtered_schemas = [schema for schema in schemas if schema.get("chainId") == str(chain_id)]

        for schema in filtered_schemas:
            if not await self.db.schema_exists(schema["id"], chain=self.chain):
                await self.db.insert_schema(schema, chain=self.chain)
        logger.success(f"Поток {self.thread} | Записал схемы в базу")

    def read_abi(self, path) -> dict:
        with open(path, "r") as f:
            return json.load(f)

    async def create_schema(self):
        def generate_data() -> str:
            field_types = ["string", "bool", "bytes", "uint256"]

            data = {
                "name": self.fake.word(),
                "description": self.fake.sentence(nb_words=random.randint(5, 15)),
                "data": [
                    {
                        "name": self.fake.word(),
                        "type": random.choice(field_types)
                    }
                    for _ in range(random.randint(1, 5))
                ]
            }
            return json.dumps(data, ensure_ascii=False)

        for _ in range(5):
            try:
                data = generate_data()
                schema_data = json.dumps(data)

                schema = (
                    self.address,  # registrant
                    True,  # revocable
                    0,  # dataLocation (пример, зависит от контракта)
                    0,  # maxValidFor (в секундах)
                    "0x0000000000000000000000000000000000000000",  # hook (если нет хука)
                    0,  # timestamp (текущее время)
                    data  # data
                )

                delegate_signature = b''

                # Строим необработанную транзакцию (без газа)

                transaction = self.contract.functions.register(schema=schema, delegateSignature=delegate_signature).build_transaction({
                    'from': self.address,
                    'nonce': self.w3.w3.eth.get_transaction_count(self.address),
                    'chainId': chain_ids.get(self.chain),
                    'gasPrice': self.w3.w3.to_wei(1, 'gwei') if self.chain=="bsc" else self.w3.w3.eth.gas_price
                })

                # Оцениваем газ
                estimated_gas = self.w3.w3.eth.estimate_gas(transaction)

                # Строим окончательную транзакцию с рассчитанным газом
                transaction['gas'] = int(estimated_gas * 1.05)
                signed_transaction = self.w3.w3.eth.account.sign_transaction(transaction, private_key=self.key)
                tx_hash = self.w3.w3.eth.send_raw_transaction(signed_transaction.rawTransaction)
                receipt = self.w3.w3.eth.wait_for_transaction_receipt(tx_hash)

                if receipt.status == 1:
                    explorer_url = explorers.get(self.chain)
                    tx_link = f"{explorer_url}{tx_hash.hex()}"

                    logger.success(f"Поток {self.thread} | Schema created successfully: {tx_link}")
                    return True
                else:
                    raise Exception(f"Поток {self.thread} | Transaction failed with hash {tx_hash.hex()}")

            except ValueError as value_error:
                logger.warning(f"Поток {self.thread} | Тут нет денег на газ")
                return False

            except Exception as err:
                if err.args == ('execution reverted', 'no data'):
                    logger.warning(f"Поток {self.thread} | Тут нет денег на газ")
                    return False
                else:
                    logger.error(f"Поток {self.thread} | Failed schema creation: {err}")
                    await asyncio.sleep(10)

        return False

    def encode_string_to_bytes(self, string):
        encoded_bytes = encode(["string"], [string])

        # Переводим в hex-формат
        hex_result = "0x" + encoded_bytes.hex()
        return hex_result

    def encode_tuple_to_bytes(self, data_tuple):
        """
        Кодирует кортеж (tuple) в байтовый формат (hex).
        """
        if not isinstance(data_tuple, tuple):
            raise TypeError("Аргумент должен быть кортежем (tuple)")

        # Определяем типы данных в кортеже
        types = []
        values = []

        for item in data_tuple:
            if isinstance(item, str):  # Строка → string
                types.append("string")
            elif isinstance(item, bool):  # Булево → bool
                types.append("bool")
            elif isinstance(item, int):  # Целые числа → uint256
                types.append("uint256")
            elif isinstance(item, bytes):  # Байтовые данные → bytes
                types.append("bytes")
            else:
                raise ValueError(f"Неизвестный тип данных: {type(item).__name__}")

            values.append(item)

        # Кодируем в байты
        encoded_bytes = encode(types, values)

        # Переводим в hex-строку
        return "0x" + encoded_bytes.hex()

    async def get_random_address(self):
        file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data/private_keys.txt")
        # Читаем приватные ключи из файла
        with open(file_path, "r") as f:
            private_keys = [line.strip() for line in f if line.strip()]

        if not private_keys:
            raise ValueError("Файл пуст или содержит только пустые строки")

        # Выбираем случайный приватный ключ
        private_key = random.choice(private_keys)

        # Создаём аккаунт из приватного ключа
        account = Account.from_key(private_key)

        # Возвращаем приватный ключ и его адрес
        return account.address

    async def create_attestation(self, schema_id):
        def generate_data(fields_json):
            fields = json.loads(fields_json)  # Преобразуем строку JSON в список Python
            result = []

            for field in fields:
                field_type = field["type"].lower()
                if field_type == "string":
                    result.append(self.fake.word())
                elif field_type == "bool":
                    result.append(random.choice([True, False]))
                elif field_type == "bytes":
                    result.append("0x")
                elif field_type == "uint256":
                    result.append(0)
                else:
                    raise ValueError(f"Неизвестный тип данных: {field_type}")

            return tuple(result)

        for _ in range(5):
            try:
                fields = await self.db.get_schema_data_by_id(schema_id=schema_id, chain=self.chain)
                data = generate_data(fields)

                random_recipient = await self.get_random_address()

                attestation = (
                    int(schema_id, 16),  # schemaId
                    0,  # linkedAttestationId
                    0,  # attestTimestamp
                    0,  # revokeTimestamp
                    self.address,  # attester
                    0,  # validUntil
                    0,  # dataLocation
                    False, # revoked
                    [self.encode_string_to_bytes(random_recipient)], # recipients
                    self.encode_tuple_to_bytes(data) #data
                )

                indexingKey = self.address
                delegateSignature = '0x'
                extraData = '0x'

                # Строим необработанную транзакцию (без газа)
                transaction = self.contract.functions.attest(attestation=attestation, indexingKey=indexingKey, delegateSignature=delegateSignature, extraData=extraData).build_transaction({
                    'from': self.address,
                    'nonce': self.w3.w3.eth.get_transaction_count(self.address),
                    'chainId': chain_ids.get(self.chain),
                    'gasPrice': self.w3.w3.to_wei(1, 'gwei') if self.chain=="bsc" else self.w3.w3.eth.gas_price
                })

                # Оцениваем газ
                estimated_gas = self.w3.w3.eth.estimate_gas(transaction)

                # Строим окончательную транзакцию с рассчитанным газом
                transaction['gas'] = int(estimated_gas * 1.05)
                signed_transaction = self.w3.w3.eth.account.sign_transaction(transaction, private_key=self.key)
                tx_hash = self.w3.w3.eth.send_raw_transaction(signed_transaction.rawTransaction)
                receipt = self.w3.w3.eth.wait_for_transaction_receipt(tx_hash)

                if receipt.status == 1:
                    explorer_url = explorers.get(self.chain)
                    tx_link = f"{explorer_url}{tx_hash.hex()}"

                    logger.success(f"Поток {self.thread} | Attestation created successfully: {tx_link} | Recipient: {random_recipient}")
                    return True
                else:
                    raise Exception(f"Поток {self.thread} | Transaction failed with hash {tx_hash.hex()}")

            except ValueError:
                logger.warning(f"Поток {self.thread} | Тут нет денег на газ")
                return False

            except Exception as err:
                if err.args == ('execution reverted', 'no data'):
                    logger.warning(f"Поток {self.thread} | Тут нет денег на газ")
                    return False
                else:
                    logger.error(f"Поток {self.thread} | Failed attestation creation: {err}")
                    await asyncio.sleep(15)

        return False

    async def logout(self):
        await self.session.close()




