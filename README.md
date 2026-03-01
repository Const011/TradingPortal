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
uvicorn app.main:app --reload --port 9000
```

## Frontend

```bash
cd frontend
npm install
npm run dev
```

**Note:** Use the run scripts from the project root (`./run-dev-trading.sh` or `./run-dev-simulation.sh`) so the frontend gets the correct backend URL. For manual runs, copy `frontend/.env.example` to `frontend/.env` and set `NEXT_PUBLIC_API_URL` (trading: `http://localhost:9000`, simulation: `http://localhost:9001`).

Then open `http://localhost:4000` (or the port from your run script).

## Run Modes (Simulation vs Trading)

Use the convenience scripts from the project root:

- **Trading** (default): Frontend 4000, backend 9000. Strategy runs; trade events are logged to `logs/trades/`. Chart displays logged trades.
  ```bash
  ./run-dev-trading.sh
  # or: ./run-dev.sh
  ```

- **Simulation**: Frontend 4001, backend 9001. Strategy runs on live stream; emits simulated trade events. No order execution.
  ```bash
  ./run-dev-simulation.sh
  ```

Ports are configurable via env: `FRONTEND_PORT`, `BACKEND_PORT`. Example: `FRONTEND_PORT=4010 BACKEND_PORT=9010 ./run-dev-trading.sh`.

See `docs/multi-gateway-trading-simulation-plan.md`.

