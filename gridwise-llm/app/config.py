import os
from dotenv import load_dotenv

# CRITICAL: Force load the .env file into the environment
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

PRIMARY_TIMEOUT = float(os.getenv("PRIMARY_TIMEOUT", "10.0"))
PRIMARY_RETRY_TIMEOUT = float(os.getenv("PRIMARY_RETRY_TIMEOUT", "2.0"))
FALLBACK_TIMEOUT = float(os.getenv("FALLBACK_TIMEOUT", "10.0"))
TOTAL_LLM_BUDGET = float(os.getenv("TOTAL_LLM_BUDGET", "22.0"))