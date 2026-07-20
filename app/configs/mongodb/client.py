import certifi
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import get_settings
from app.constants import MESSAGES_COLLECTION

_client: MongoClient | None = None


def get_mongo_client() -> MongoClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = MongoClient(
            settings.mongodb_uri,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000,
        )
    return _client


def get_mongo_db() -> Database:
    settings = get_settings()
    return get_mongo_client()[settings.mongodb_db_name]


def get_messages_collection() -> Collection:
    return get_mongo_db()[MESSAGES_COLLECTION]


def ensure_mongo_indexes() -> None:
    collection = get_messages_collection()
    collection.create_index([("conversation_id", 1), ("created_at", 1)])
    collection.create_index([("conversation_id", 1), ("created_at", -1)])
    collection.create_index([("user_id", 1), ("created_at", -1)])
