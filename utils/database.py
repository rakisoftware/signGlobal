import json
import random

import aiosqlite


class Database:
    def __init__(self, db_name="schemas.db"):
        self.db_name = db_name

    async def initialize_db(self):
        async with aiosqlite.connect(self.db_name) as db:
            for chain in ["bsc", "opbnb", "polygon"]:
                await db.execute(f"""
                    CREATE TABLE IF NOT EXISTS {chain}_schemas (
                        id TEXT PRIMARY KEY,
                        mode TEXT,
                        chainType TEXT,
                        chainId TEXT,
                        schemaId TEXT,
                        transactionHash TEXT,
                        name TEXT,
                        description TEXT,
                        dataLocation TEXT,
                        revocable BOOLEAN,
                        maxValidFor TEXT,
                        resolver TEXT,
                        registerTimestamp INTEGER,
                        registrant TEXT,
                        data TEXT,
                        originalData TEXT
                    )
                """)
            await db.commit()

    async def schema_exists(self, schema_id, chain):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute(f"SELECT 1 FROM {chain}_schemas WHERE id = ?", (schema_id,)) as cursor:
                return await cursor.fetchone() is not None

    async def insert_schema(self, schema, chain):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute(f"""
                INSERT INTO {chain}_schemas (
                    id, mode, chainType, chainId, schemaId, transactionHash, name, description,
                    dataLocation, revocable, maxValidFor, resolver, registerTimestamp, registrant, data, originalData
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                schema["id"], schema["mode"], schema["chainType"], schema["chainId"], schema["schemaId"],
                schema["transactionHash"], schema["name"], schema["description"], schema["dataLocation"],
                schema["revocable"], schema["maxValidFor"], schema["resolver"],
                schema["registerTimestamp"], schema["registrant"],
                json.dumps(schema["data"]),  # Сохраняем data как JSON
                schema["originalData"]
            ))
            await db.commit()

    async def get_random_schema_id(self, chain):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute(f"SELECT schemaId FROM {chain}_schemas") as cursor:
                rows = await cursor.fetchall()
                if not rows:
                    return None
                return random.choice(rows)[0]

    async def get_schema_data_by_id(self, schema_id, chain):
        async with aiosqlite.connect(self.db_name) as db:
            cursor = await db.execute(f"SELECT data FROM {chain}_schemas WHERE schemaId = ?", (schema_id,))
            row = await cursor.fetchone()
            await cursor.close()
            if row:
                data = row[0]
                return data
            else:
                return None