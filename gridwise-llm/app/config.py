import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
PORT = int(os.getenv("PORT", "8000"))

PRIMARY_TIMEOUT = 10.0
PRIMARY_RETRY_TIMEOUT = 2.0
FALLBACK_TIMEOUT = 10.0
TOTAL_LLM_BUDGET = 22.0
