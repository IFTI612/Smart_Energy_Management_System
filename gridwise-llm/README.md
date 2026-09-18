# GridWise: LLM-Assisted Campus Energy Optimization

GridWise is a production-grade, zero-placeholder API that merges deterministic Mixed-Integer Linear Programming (PuLP/CBC) with Large Language Model (LLM) interpretation to resolve complex, natural language operational constraints into rigorous energy schedules.

## Architecture

- **FastAPI Core**: A high-performance async entrypoint delegating requests effectively to threaded workflows.
- **LLM Interpreter**: Queries Groq (Qwen3-27B) primarily via strict JSON schema prompting. Fallbacks to Gemini API on timeout or failure. Outputs are deterministically mapped. Handles Qwen3 thinking tags transparently.
- **Guardrails Engine**: Pure functions isolating keys, mapping out-of-bounds metrics (e.g. `factor` restricted to `[0.0, 1.0]`), enforcing index alignments and resolving unknown/malformed JSON into `no_op`.
- **PuLP Optimizer (CBC)**: Builds the linear schedule, guaranteeing hourly energy balances, capacity maximums, rate limits, and end-of-day neutrality `energy_after[23] == initial`. Runs asynchronously on a separate thread block to prevent event loop stuttering.

## Project Structure
```text
gridwise-llm/
├── app/
│   ├── __init__.py
│   ├── config.py             # Envs & timeouts
│   ├── guardrails.py         # JSON parsing and physics clamping
│   ├── llm_interpreter.py    # LLM queries & SHA-256 caching
│   ├── main.py               # FastAPI routers & thread management
│   ├── optimizer.py          # PuLP/CBC execution
│   └── schemas.py            # Pydantic v2 schemas
├── data/
│   └── public_sample_cases.json # 10 canonical test payloads
├── scripts/
│   ├── keep_warm.py          # Ping tool to prevent cold starts
│   └── run_public_samples.py # E2E semantics and physics validator
├── tests/
│   ├── test_api.py           # Integration endpoints
│   ├── test_guardrails.py    # Deterministic mapping bounds
│   └── test_optimizer.py     # Pure PuLP math tests
├── .env.example
├── .gitignore
├── Dockerfile
├── requirements.txt
└── README.md
```

## Setup & Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your keys
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GROQ_API_KEY` | (Required) | Primary LLM Key |
| `GEMINI_API_KEY`| (Required) | Fallback LLM Key |
| `GROQ_MODEL` | `qwen/qwen3.8-27b` | Primary model identifier |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Fallback model identifier |
| `PORT` | `8000` | Application bind port |

*Note: Timeouts are hardcoded natively in `app.config` to prevent configuration desync.*

## Examples

### GET /health
```bash
curl http://localhost:8000/health
```

### POST /optimize-energy
```bash
curl -X POST http://localhost:8000/optimize-energy \
-H "Content-Type: application/json" \
-d '{
  "scenario_id": "test_1",
  "operator_notes": ["Reduce solar to 20% from 10 AM to 1 PM"],
  "battery": {
    "capacity_kwh": 100,
    "initial_energy_kwh": 20,
    "minimum_energy_kwh": 10,
    "max_charge_kwh_per_hour": 30,
    "max_discharge_kwh_per_hour": 30
  },
  "hours": [
     ... 24 hour objects ...
  ]
}'
```

## Testing & Validation

**Unit Tests**:
```bash
pytest tests/
```

**E2E Semantic & Physics Validator**:
```bash
python scripts/run_public_samples.py --url http://localhost:8000
```

## Docker

Build and execute via Docker containing the `CBC` binaries:
```bash
docker build -t gridwise-llm .
docker run -p 8000:8000 --env-file .env gridwise-llm
```

## Operational Limits
- **Caching**: Notes are matched via SHA-256 against their lowercased text appended with battery capacity for in-memory deduplication across identical operator payloads.
- **Concurrency**: Uvicorn spins exactly 1 worker. PuLP operates off the async event-loop.
- **Timeout Chain**: Groq attempts at 10s, falls back into a 2s retry. A full miss triggers the Gemini fallback capped at 10s. The total budget resolves within a strict `22.0` second execution threshold.
