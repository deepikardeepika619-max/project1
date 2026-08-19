"""
app.py

Flask backend for DebugMate AI - a domain-specific debugging assistant
chatbot. Uses the Google GenAI SDK (Gemini) for reasoning, and Firebase
Firestore as a Firebase-first knowledge base.
"""

import os
import uuid
import traceback

from flask import Flask, request, jsonify, render_template, session

from google import genai
from google.genai import types

from chatbot_config import SYSTEM_PROMPT, CHATBOT_TITLE
from firebase_config import fetch_knowledge_context

# ---------------------------------------------------------------------------
# APP SETUP
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")or 
if not GEMINI_API_KEY:
    # Do not crash on import so `flask run` can still show a helpful error
    # on the first request instead of failing silently at startup.
    print("WARNING: GEMINI_API_KEY environment variable is not set.")

# Latest generally available Gemini model.
GEMINI_MODEL ="gemini-3.6-flash"

_client = None


def get_client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set in the environment.")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# PER-SESSION CONVERSATION MEMORY (in-memory store keyed by session id)
# ---------------------------------------------------------------------------
# For a production deployment this could be swapped for Firestore-backed
# storage, but an in-memory dict keeps the template simple and dependency
# free while still giving each browser session its own isolated history.

_conversations = {}  # { session_id: [ {"role": "user"/"model", "text": "..."} ] }
MAX_HISTORY_MESSAGES = 20


def get_session_id():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return session["session_id"]


def get_history(session_id):
    return _conversations.setdefault(session_id, [])


def append_history(session_id, role, text):
    history = get_history(session_id)
    history.append({"role": role, "text": text})
    if len(history) > MAX_HISTORY_MESSAGES:
        del history[: len(history) - MAX_HISTORY_MESSAGES]


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", chatbot_title=CHATBOT_TITLE)


@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = (data.get("message") or "").strip()

        if not user_message:
            return jsonify({"error": "Message is required."}), 400

        if len(user_message) > 4000:
            return jsonify({"error": "Message is too long."}), 400

        session_id = get_session_id()
        history = get_history(session_id)

        # 1. Retrieve relevant knowledge from Firebase (Firebase-first).
        knowledge_context = fetch_knowledge_context(user_message)

        # 2. Build the conversation contents for Gemini: prior turns +
        #    the new user turn, with knowledge-base context attached to
        #    the latest user message only.
        contents = []
        for turn in history:
            contents.append(
                types.Content(
                    role=turn["role"],
                    parts=[types.Part.from_text(text=turn["text"])],
                )
            )

        if knowledge_context:
            user_turn_text = (
                f"{user_message}\n\n"
                f"[KNOWLEDGE BASE CONTEXT - use as primary factual source "
                f"where relevant, ignore if not relevant]\n{knowledge_context}"
            )
        else:
            user_turn_text = user_message

        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_turn_text)],
            )
        )

        # 3. Call Gemini.
        client = get_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.4,
            ),
        )

        reply_text = (response.text or "").strip()
        if not reply_text:
            reply_text = (
                "Sorry, I couldn't generate a response for that. Could you "
                "rephrase your question or share the exact error message?"
            )

        # 4. Update memory (store the clean user message, not the injected
        #    knowledge-base context, to keep history readable/compact).
        append_history(session_id, "user", user_message)
        append_history(session_id, "model", reply_text)

        return jsonify({"reply": reply_text})

    except RuntimeError as e:
        # Configuration errors (e.g. missing API key) -- safe message only.
        app.logger.error("Configuration error: %s", e)
        return jsonify({"error": "The assistant is not configured correctly. Please contact the administrator."}), 500

    except Exception:
        app.logger.error("Unhandled error in /api/chat:\n%s", traceback.format_exc())
        return jsonify({"error": "Something went wrong while processing your request. Please try again."}), 500


@app.route("/api/reset", methods=["POST"])
def reset():
    """Clear the current session's conversation memory."""
    session_id = get_session_id()
    _conversations[session_id] = []
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
