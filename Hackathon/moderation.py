"""
Moderation logic using Google Gemini API.
Handles content classification with fail-open behavior.
"""

import os
import json
import logging
import requests


def check_message(message_content: str) -> dict:
    """
    Checks a message for toxic content using the Gemini API.
    Returns a dictionary with is_allowed, category, confidence, and reason.
    Uses a fail-open pattern: defaults to allowed if API fails.
    
    CONFIGURE: Set GEMINI_API_KEY in local.settings.json
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    default_response = {
        "is_allowed": True,
        "category": "clean",
        "confidence": 1.0,
        "reason": "Fail-open due to API unavailability or error"
    }

    if not api_key:
        logging.warning("GEMINI_API_KEY not found in environment variables. Falling back to fail-open.")
        return default_response

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

    prompt = (
        "You are a content moderator for a live streaming chat platform. "
        "Analyze the following message and classify it.\n\n"
        f"Message: \"{message_content}\"\n\n"
        "Respond with ONLY a JSON object in this format:\n"
        "{\n"
        "  \"is_allowed\": true/false,\n"
        "  \"category\": \"clean\" | \"toxic\" | \"spam\" | \"harassment\",\n"
        "  \"confidence\": 0.0-1.0,\n"
        "  \"reason\": \"brief explanation\"\n"
        "}"
    )

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }

    try:
        response = requests.post(
            endpoint,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        
        # Extract content from the Gemini response structure
        reply_text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        
        if not reply_text:
            raise ValueError("Empty or invalid response from Gemini API.")

        # Strip markdown code blocks if the model outputs them
        reply_text = reply_text.strip()
        if reply_text.startswith("```json"):
            reply_text = reply_text.removeprefix("```json").strip()
            if reply_text.endswith("```"):
                reply_text = reply_text.removesuffix("```").strip()
        elif reply_text.startswith("```"):
            reply_text = reply_text.removeprefix("```").strip()
            if reply_text.endswith("```"):
                reply_text = reply_text.removesuffix("```").strip()

        result = json.loads(reply_text)
        
        # Ensure returned dictionary has expected keys
        return {
            "is_allowed": bool(result.get("is_allowed", True)),
            "category": str(result.get("category", "clean")),
            "confidence": float(result.get("confidence", 1.0)),
            "reason": str(result.get("reason", ""))
        }
    except Exception as e:
        logging.error(f"Error during Gemini API moderation check: {e}")
        return default_response
