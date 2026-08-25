"""Static configuration: env-driven paths, timing bounds, prompt payloads."""
from __future__ import annotations

import os
from pathlib import Path

AUTH_JSON = Path(
    os.environ.get("PI_AUTH_JSON", str(Path.home() / ".pi" / "agent" / "auth.json"))
)
CURATED_DIR = Path(
    os.environ.get(
        "PI_CURATED_DIR",
        str(Path.home() / ".pi" / "agent" / "extensions" / "live" / "curated"),
    )
)
STORE_JSON = Path(
    os.environ.get(
        "PI_STORE_JSON", str(Path.home() / ".pi" / "agent" / "models-store.json")
    )
)

PROBE_TIMEOUT = 15  # seconds per model (fast-only: only responsive models survive)
PROBE_PACE = 1.5  # seconds between probes (NIM worker saturation / key RPM pacing)
HTTP_TIMEOUT = 15  # seconds per HTTP request
FETCH_RETRIES = 3  # catalog/auth GET attempts before giving up
RETRY_BACKOFF = (1, 2)  # seconds between retries

# Model ids that are categorically not for coding/agentic chat (embedding,
# moderation, translation, retrieval, etc.) — skipped before probing.
NON_CHAT_KEYWORDS = frozenset({
    "embed", "safety", "guard", "translate", "parse",
    "retriever", "clip", "diffusion", "video", "detector",
    "reward", "deplot",
    "imagen", "veo", "lyria", "tts", "audio",
    "deep-research", "robotics",
})


def is_non_chat(model_id: str) -> bool:
    """True if the model id matches a NON_CHAT_KEYWORDS token (not usable for agentic chat)."""
    return any(kw in model_id.lower() for kw in NON_CHAT_KEYWORDS)


# Prompt sent to every probed model — coding-flavored, short bounded output
# (latency here is total stream-drain time; wordy prompts would inflate it).
PROBE_PROMPT = "Write a one-line Python function that adds two numbers. Reply with code only."

# Minimal tool schema mirroring Pi's agent tools — proves the model accepts
# tool-calling request format (a model that breaks on tools is unusable in Pi).
PI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Bash command"}
                },
                "required": ["command"],
            },
        },
    }
]

# Same bash tool in Google's native functionDeclarations shape — the gemini
# probe declares tools on the native :generateContent path Pi's extension uses.
GEMINI_TOOLS = [
    {
        "functionDeclarations": [
            {
                "name": "bash",
                "description": "Execute a bash command",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Bash command"}
                    },
                    "required": ["command"],
                },
            }
        ]
    }
]
