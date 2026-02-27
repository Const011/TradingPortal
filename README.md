# Trading Portal

Spot-first trading portal skeleton with:

- `backend/`: FastAPI service with Bybit REST + WebSocket market integration; **computes all indicators and runs trade strategy logic**.
- `frontend/`: Next.js portal with ticker switcher and Lightweight Charts view; **visualization only** (displays pre-calculated data).
- `docs/`: BRD (`brd-architecture.md`) and architecture decisions (`adr/001-v1-architecture-decisions.md`).

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

