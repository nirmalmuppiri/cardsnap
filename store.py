import os
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId

_client = None

def _db():
    global _client
    if _client is None:
        _client = MongoClient(os.environ["MONGODB_URI"])
    return _client["cardsnap"]


def init_db():
    pass  # MongoDB creates collections automatically


def save_contact(event_name: str, data: dict) -> str:
    result = _db().contacts.insert_one({
        "event_name": event_name,
        "data": data,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return str(result.inserted_id)


def get_contacts(event_name: str | None = None) -> list[dict]:
    query = {"event_name": event_name} if event_name else {}
    rows = _db().contacts.find(query).sort("created_at", 1)
    return [_serialize(r) for r in rows]


def update_contact(contact_id: str, data: dict):
    _db().contacts.update_one({"_id": ObjectId(contact_id)}, {"$set": {"data": data}})


def delete_contact(contact_id: str):
    _db().contacts.delete_one({"_id": ObjectId(contact_id)})


def list_events() -> list[str]:
    return _db().contacts.distinct("event_name")


def _serialize(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc
