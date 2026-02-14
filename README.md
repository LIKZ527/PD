# PD FastAPI Starter

Minimal FastAPI skeleton using the requested tech stack.

## Setup

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Create a .env from the example:

   ```bash
   copy .env.example .env
   ```

3. Run the app:

   ```bash
   uvicorn app.main:app --reload
   ```

## Endpoints

- GET /healthz
- POST /api/v1/auth/login
