"""
firebase_config.py

Initializes Firebase Admin SDK and exposes a Firestore client plus a
helper to pull relevant knowledge-base context for a user query.

The developer must replace 'firebase-key.json' with their own Firebase
service-account JSON before running the app. This module does not
hardcode any collection names or domain-specific data -- it discovers
collections/documents dynamically from Firestore at request time.
"""

import os
import firebase_admin
from firebase_admin import credentials, firestore

KEY_FILE = os.path.join(os.path.dirname(__file__), "firebase-key.json")

_db = None


def init_firebase():
    """Initialize the Firebase app exactly once and return a Firestore client."""
    global _db
    if _db is not None:
        return _db

    if not firebase_admin._apps:
        if not os.path.exists(KEY_FILE):
            raise FileNotFoundError(
                f"Firebase service account file not found at '{KEY_FILE}'. "
                "Replace the placeholder firebase-key.json with your real "
                "Firebase service-account JSON."
            )
        cred = credentials.Certificate(KEY_FILE)
        firebase_admin.initialize_app(cred)

    _db = firestore.client()
    return _db


def get_db():
    """Return the initialized Firestore client (initializing if needed)."""
    if _db is None:
        return init_firebase()
    return _db


def fetch_knowledge_context(query_text, max_collections=5, max_docs_per_collection=5):
    """
    Dynamically pull knowledge-base content from Firestore that may be
    relevant to the user's query.

    This is a lightweight, generic retrieval approach: it lists whatever
    collections exist in the database (the developer creates and
    maintains these manually, e.g. "common_errors", "code_examples",
    "concepts", "best_practices") and pulls a bounded number of
    documents from each so Gemini has real, grounded context to reason
    over. Gemini itself is responsible for judging relevance and for
    interpreting abbreviated/short field names.

    Returns a plain-text block suitable for inclusion in the prompt, or
    an empty string if no data / Firebase is unavailable.
    """
    try:
        db = get_db()
    except Exception:
        # Firebase not configured yet -- fail gracefully so the chatbot
        # can still answer using Gemini's own reasoning.
        return ""

    context_parts = []

    try:
        collections = list(db.collections())
    except Exception:
        return ""

    for coll_ref in collections[:max_collections]:
        coll_name = coll_ref.id
        try:
            docs = list(coll_ref.limit(max_docs_per_collection).stream())
        except Exception:
            continue

        if not docs:
            continue

        context_parts.append(f"\n--- Collection: {coll_name} ---")
        for doc in docs:
            data = doc.to_dict() or {}
            if not data:
                continue
            fields_str = ", ".join(f"{k}: {v}" for k, v in data.items())
            context_parts.append(f"[doc:{doc.id}] {fields_str}")

    if not context_parts:
        return ""

    return "\n".join(context_parts)
