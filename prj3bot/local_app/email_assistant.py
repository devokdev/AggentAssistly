"""Gemini-powered email drafting utilities."""

from __future__ import annotations

import json
import re
from typing import Any

from json_repair import repair_json
from litellm import acompletion

from prj3bot.config.schema import Config


PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")


def _sanitize_email_text(text: str) -> str:
    cleaned = (text or "").replace("*", "")
    cleaned = PLACEHOLDER_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return cleaned.strip()


def _parse_model_json(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            repaired = repair_json(text)
            return json.loads(repaired)
        except Exception:
            return {}


class EmailAssistant:
    """Generate professional email drafts with Gemini."""

    def __init__(self, config: Config):
        self.config = config

    async def draft_email(
        self,
        user_input: str,
        recipients: list[str] | None = None,
        thread_context: str | None = None,
    ) -> dict[str, Any]:
        recips = recipients or []
        recips_text = ", ".join(recips) if recips else ""
        thread_text = (thread_context or "").strip()
        model = self._pick_gemini_model()
        gemini_key = self.config.providers.gemini.api_key.strip()
        if not gemini_key:
            raise RuntimeError("Gemini API key is missing. Add it in Settings.")

        system_prompt = (
            "You are an email drafting assistant. Return only JSON.\n"
            "Rules:\n"
            "1) No asterisk characters anywhere.\n"
            "2) No placeholders like [Your Name].\n"
            "3) Subject must be concise and professional.\n"
            "4) Body must be clean, formal, and ready to send.\n"
            "5) Output schema: {\"to\": [\"a@b.com\"], \"subject\": \"...\", \"body\": \"...\"}."
        )
        user_prompt = (
            f"User request:\n{user_input}\n\n"
            f"Known recipients:\n{recips_text or '(none)'}\n\n"
            f"Thread context:\n{thread_text or '(none)'}"
        )

        response = await acompletion(
            model=model,
            api_key=gemini_key,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        content = (
            response.choices[0].message.content
            if response and response.choices
            else ""
        )
        parsed = _parse_model_json(content)
        to_values = parsed.get("to", recips)
        if isinstance(to_values, str):
            to_values = [item.strip() for item in to_values.split(",") if item.strip()]
        if not isinstance(to_values, list):
            to_values = list(recips)

        subject = _sanitize_email_text(str(parsed.get("subject", ""))) or "Follow-up"
        body = _sanitize_email_text(str(parsed.get("body", "")))
        if not body:
            body = _sanitize_email_text(user_input)

        return {
            "to": [str(v).strip() for v in to_values if str(v).strip()],
            "subject": subject,
            "body": body,
        }

    async def generate_reply(self, thread_text: str, user_instruction: str) -> str:
        """Generate context-aware reply body from full thread context."""
        model = self._pick_gemini_model()
        gemini_key = self.config.providers.gemini.api_key.strip()
        if not gemini_key:
            raise RuntimeError("Gemini API key is missing. Add it in Settings.")

        system_prompt = (
            "You write professional email replies.\n"
            "Rules:\n"
            "1) Use thread context carefully.\n"
            "2) No asterisk characters.\n"
            "3) No placeholders like [Your Name].\n"
            "4) Return only the email body text."
        )
        user_prompt = (
            f"Thread context:\n{thread_text or '(none)'}\n\n"
            f"Instruction:\n{user_instruction or 'Write a helpful reply.'}"
        )
        response = await acompletion(
            model=model,
            api_key=gemini_key,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        content = (
            response.choices[0].message.content
            if response and response.choices
            else ""
        )
        clean = _sanitize_email_text(content)
        if not clean:
            clean = "Thank you for your email. I will get back to you shortly."
        return clean

    def _pick_gemini_model(self) -> str:
        selected = (self.config.agents.defaults.model or "").strip()
        if selected.startswith("gemini/"):
            return selected
        return "gemini/gemini-2.5-flash-lite"
