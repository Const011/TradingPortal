# Trading Portal

Spot-first trading portal skeleton with:

- `backend/`: FastAPI service with Bybit REST + WebSocket market integration.
- `frontend/`: Next.js portal with ticker switcher and Lightweight Charts view.
- `docs/`: BRD and architecture/ADR documents.

## Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

## Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Then open `http://localhost:3000`.

