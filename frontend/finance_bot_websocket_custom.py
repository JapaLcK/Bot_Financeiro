"""
Finance Bot WebSocket Dashboard Server
Real-time financial data via WebSocket + FastAPI

Endpoints:
  GET  /                        → serves dashboard.html
  GET  /manifest.json           → PWA manifest
  GET  /service-worker.js       → PWA service worker
  GET  /health
  GET  /users
  GET  /data/{user_id}          → snapshot (query: year, month)
  GET  /history/{user_id}       → last N months summary (query: months)
  GET  /budgets/{user_id}       → list budgets
  POST /budgets/{user_id}       → set budget {categoria, budget}
  DEL  /budgets/{user_id}/{cat} → delete budget
  GET  /export/{user_id}        → CSV download (query: year, month)
  WS   /ws/{user_id}            → real-time updates
"""

import asyncio
import csv
import io
import json
import os
import pathlib
import sys
import time as _startup_time
import urllib.parse
from decimal import Decimal
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse, HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn
from pydantic import BaseModel, EmailStr
import jwt as pyjwt
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from config.env import load_app_env
from token_utils import decode_dashboard_token, make_dashboard_token
from utils_phone import normalize_phone_e164
from core.admin_dashboard import (
    ensure_admin_tables,
    log_auth_login_event,
    log_system_event,
    log_admin_startup_warnings,
    admin_error_logging_middleware,
    register_admin_routes,
)
from db import (
    accrue_all_investments,
    create_investment_db,
    delete_investment,
    get_dashboard_market_rates,
    investment_deposit_from_account,
    investment_withdraw_to_account,
)

load_app_env()

DATABASE_URL      = os.getenv("DATABASE_URL")
DASHBOARD_USER_ID = os.getenv("DASHBOARD_USER_ID")
TZ                = os.getenv("TZ", "America/Sao_Paulo")
JWT_SECRET              = (os.getenv("JWT_SECRET") or "").strip()
DASHBOARD_URL           = os.getenv("DASHBOARD_URL", "http://localhost:8000").strip()
# Sanitiza caso a var de ambiente tenha sido definida como "DASHBOARD_URL=https://..."
if DASHBOARD_URL.startswith("DASHBOARD_URL="):
    DASHBOARD_URL = DASHBOARD_URL[len("DASHBOARD_URL="):]
DASHBOARD_URL = DASHBOARD_URL.rstrip("/")
WHATSAPP_NUMBER         = os.getenv("WHATSAPP_NUMBER", "")
STRIPE_SECRET_KEY       = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET   = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID_PRO     = os.getenv("STRIPE_PRICE_ID_PRO", "")   # price_xxx do plano Pro
DASHBOARD_MAGIC_LINK_MINUTES = int(os.getenv("DASHBOARD_MAGIC_LINK_MINUTES", "5"))
DASHBOARD_SESSION_HOURS = float(os.getenv("DASHBOARD_SESSION_HOURS", "12"))
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
STARTUP_STEP_TIMEOUT = int(os.getenv("STARTUP_STEP_TIMEOUT", "12"))
RUN_BACKGROUND_TASKS = os.getenv("RUN_BACKGROUND_TASKS", "1") != "0"

HERE = pathlib.Path(__file__).parent  # directory of this file

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set. Check your .env file.", file=sys.stderr)
    sys.exit(1)

if not JWT_SECRET:
    print("ERROR: JWT_SECRET not set. Refusing to start with insecure default.", file=sys.stderr)
    sys.exit(1)

# ─── JSON serializer ─────────────────────────────────────────────────────────

class FinanceEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):  return float(obj)
        if isinstance(obj, datetime): return obj.isoformat()
        if isinstance(obj, date):     return obj.isoformat()
        return super().default(obj)

def jdump(data: dict) -> str:
    return json.dumps(data, cls=FinanceEncoder, ensure_ascii=False)

# ─── DB helpers ──────────────────────────────────────────────────────────────

async def db_connect():
    return await psycopg.AsyncConnection.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=DB_CONNECT_TIMEOUT,
    )

async def list_users() -> list:
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM users ORDER BY created_at")
            return await cur.fetchall()

async def ensure_budget_table():
    """Create category_budgets table if it doesn't exist yet."""
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS category_budgets (
                    id        BIGSERIAL PRIMARY KEY,
                    user_id   BIGINT  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    categoria TEXT    NOT NULL,
                    budget    NUMERIC NOT NULL CHECK (budget > 0),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (user_id, categoria)
                )
            """)
        await conn.commit()


async def ensure_investment_metadata_columns():
    """Migrations leves usadas pela aba de investimentos do dashboard."""
    statements = [
        "ALTER TABLE investments ADD COLUMN IF NOT EXISTS asset_type TEXT NOT NULL DEFAULT 'CDB'",
        "ALTER TABLE investments ADD COLUMN IF NOT EXISTS indexer TEXT",
        "ALTER TABLE investments ADD COLUMN IF NOT EXISTS issuer TEXT",
        "ALTER TABLE investments ADD COLUMN IF NOT EXISTS purchase_date DATE",
        "ALTER TABLE investments ADD COLUMN IF NOT EXISTS maturity_date DATE",
        "ALTER TABLE investments ADD COLUMN IF NOT EXISTS interest_payment_frequency TEXT NOT NULL DEFAULT 'maturity'",
        "ALTER TABLE investments ADD COLUMN IF NOT EXISTS tax_profile TEXT NOT NULL DEFAULT 'regressive_ir_iof'",
    ]
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            for stmt in statements:
                await cur.execute(stmt)
        await conn.commit()



def _month_range(year: int, month: int):
    """Returns (start_date, exclusive_end_date) for the given month."""
    start = date(year, month, 1)
    end   = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end

# ─── Core data fetcher ───────────────────────────────────────────────────────

async def get_financial_data(user_id: int, year: int = None, month: int = None, page: int = 1, limit: int = 25) -> dict:
    """
    Fetch full financial snapshot for user_id.
    If year/month are given, income/expenses/categories/launches
    are scoped to that month. Balance, pockets and investments
    always reflect the current state.
    """
    now = datetime.now(timezone.utc)
    y   = year  or now.year
    m   = month or now.month
    month_start, month_end = _month_range(y, m)
    is_current = (y == now.year and m == now.month)
    page = max(int(page or 1), 1)
    limit = max(min(int(limit or 25), 100), 1)
    offset = (page - 1) * limit
    current_investments = await asyncio.to_thread(accrue_all_investments, user_id)
    market_rates = await asyncio.to_thread(get_dashboard_market_rates)

    async with await db_connect() as conn:
        async with conn.cursor() as cur:

            # Account balance (always current)
            await cur.execute(
                "SELECT balance FROM accounts WHERE user_id = %s", (user_id,)
            )
            account = await cur.fetchone()

            # Savings pockets (always current)
            await cur.execute(
                "SELECT name, balance FROM pockets WHERE user_id = %s ORDER BY name",
                (user_id,),
            )
            pockets = await cur.fetchall()

            # Investments (always current)
            investments = current_investments

                        # Total launches for the requested month (excluindo criação/remoção de bolsos)
            await cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM launches
                WHERE user_id = %s
                  AND criado_em >= %s AND criado_em < %s
                  AND tipo NOT IN ('criar_caixinha', 'delete_pocket', 'create_investment', 'delete_investment')
                """,
                (user_id, month_start, month_end),
            )
            launches_total_row = await cur.fetchone()
            launches_total = int(launches_total_row["total"] or 0)

            # Launches for the requested month (paginated) — exclui apenas criação/remoção de bolsos
            await cur.execute(
                """
                SELECT tipo, valor, alvo, nota, categoria, criado_em, is_internal_movement
                FROM launches
                WHERE user_id = %s
                  AND criado_em >= %s AND criado_em < %s
                  AND tipo NOT IN ('criar_caixinha', 'delete_pocket', 'create_investment', 'delete_investment')
                ORDER BY criado_em DESC
                LIMIT %s OFFSET %s
                """,
                (user_id, month_start, month_end, limit, offset),
            )
            launches = await cur.fetchall()

            # Monthly income / expense totals — EXCLUINDO movimentações internas
            await cur.execute(
                """
                SELECT tipo, SUM(valor) AS total
                FROM launches
                WHERE user_id = %s
                  AND criado_em >= %s AND criado_em < %s
                  AND is_internal_movement = false
                GROUP BY tipo
                """,
                (user_id, month_start, month_end),
            )
            monthly = await cur.fetchall()

            # Expense categories for the month — EXCLUINDO movimentações internas
            await cur.execute(
                """
                SELECT COALESCE(categoria, 'sem categoria') AS categoria,
                       SUM(valor) AS total,
                       COUNT(*)   AS count
                FROM launches
                WHERE user_id = %s
                  AND tipo     = 'despesa'
                  AND is_internal_movement = false
                  AND criado_em >= %s AND criado_em < %s
                GROUP BY COALESCE(categoria, 'sem categoria')
                ORDER BY total DESC
                LIMIT 10
                """,
                (user_id, month_start, month_end),
            )
            categories = await cur.fetchall()

            # Credit cards — sempre listar TODOS os cartões do usuário,
            # mesmo sem nenhuma compra/fatura no mês selecionado.
            # Quando não houver fatura, o cartão deve aparecer com total/due/pago = 0.
            await cur.execute(
                """
                SELECT id, name, closing_day, due_day
                FROM credit_cards
                WHERE user_id = %s
                ORDER BY name
                """,
                (user_id,),
            )
            base_cards = await cur.fetchall()

            cards = []
            for cc in base_cards:
                await cur.execute(
                    """
                    SELECT
                        status,
                        total,
                        COALESCE(paid_amount, 0)                          AS paid_amount,
                        GREATEST(0, total - COALESCE(paid_amount, 0))     AS due_amount,
                        period_start,
                        period_end
                    FROM credit_bills
                    WHERE card_id = %s
                      AND period_start >= %s
                      AND period_start < %s
                    ORDER BY period_start DESC
                    LIMIT 1
                    """,
                    (cc["id"], month_start, month_end),
                )
                bill = await cur.fetchone()

                card_row = {
                    "id": cc["id"],
                    "name": cc["name"],
                    "closing_day": cc["closing_day"],
                    "due_day": cc["due_day"],
                    "status": bill["status"] if bill else "open",
                    "total": float(bill["total"]) if bill and bill["total"] is not None else 0.0,
                    "paid_amount": float(bill["paid_amount"]) if bill and bill["paid_amount"] is not None else 0.0,
                    "due_amount": float(bill["due_amount"]) if bill and bill["due_amount"] is not None else 0.0,
                    "period_start": bill["period_start"] if bill else None,
                    "period_end": bill["period_end"] if bill else None,
                }
                cards.append(card_row)

            # Daily expenses for the month (for bar chart) — EXCLUINDO movimentações internas
            await cur.execute(
                f"""
                SELECT EXTRACT(DAY FROM criado_em AT TIME ZONE '{TZ}')::int AS dia,
                       SUM(valor) AS total
                FROM launches
                WHERE user_id = %s
                  AND tipo IN ('despesa', 'saida')
                  AND is_internal_movement = false
                  AND criado_em >= %s AND criado_em < %s
                GROUP BY dia
                ORDER BY dia
                """,
                (user_id, month_start, month_end),
            )
            daily_rows = await cur.fetchall()

            # Budgets per category
            await cur.execute(
                "SELECT categoria, budget FROM category_budgets WHERE user_id = %s",
                (user_id,),
            )
            budget_rows = await cur.fetchall()

    # Build maps
    monthly_map = {row["tipo"]: float(row["total"]) for row in monthly}
    budget_map  = {r["categoria"]: float(r["budget"]) for r in budget_rows}

    # Merge budgets into categories + detect alerts
    cat_list = []
    alerts   = []
    for c in categories:
        cat      = dict(c)
        cat_name = cat["categoria"]
        spent    = float(cat["total"])
        cat["total"] = spent

        if cat_name in budget_map:
            bgt = budget_map[cat_name]
            pct = round(spent / bgt * 100, 1)
            cat["budget"]     = bgt
            cat["budget_pct"] = pct

            if spent > bgt:
                alerts.append({
                    "type":      "budget_exceeded",
                    "categoria": cat_name,
                    "spent":     spent,
                    "budget":    bgt,
                    "pct":       pct,
                })
            elif pct >= 85:
                alerts.append({
                    "type":      "budget_warning",
                    "categoria": cat_name,
                    "spent":     spent,
                    "budget":    bgt,
                    "pct":       pct,
                })

        cat_list.append(cat)

    inc = monthly_map.get("receita", 0.0)
    exp = monthly_map.get("despesa", 0.0)

    return {
        "user_id":            user_id,
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "year":               y,
        "month":              m,
        "is_current_month":   is_current,
        "balance":            float(account["balance"]) if account else 0.0,
        "pockets":            [dict(r) for r in pockets],
        "investments":        [dict(r) for r in investments],
        "market_rates":       market_rates,
        "recent_launches":    [dict(r) for r in launches],
        "launches_pagination": {
            "page": page,
            "limit": limit,
            "total": launches_total,
            "total_pages": max((launches_total + limit - 1) // limit, 1),
        },
        "monthly_income":     inc,
        "monthly_expense":    exp,
        "expense_categories": cat_list,
        "credit_cards":       [dict(r) for r in cards],
        "budgets":            budget_map,
        "alerts":             alerts,
        "daily_expenses":     [{"day": int(r["dia"]), "total": float(r["total"])} for r in daily_rows],
    }

# ─── Monthly history ─────────────────────────────────────────────────────────

async def get_monthly_history(user_id: int, n_months: int = 6) -> list:
    """Returns last n_months of income/expense totals, oldest first."""
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT TO_CHAR(DATE_TRUNC('month', criado_em), 'YYYY-MM') AS mes,
                       tipo, SUM(valor) AS total
                FROM launches
                WHERE user_id = %s
                  AND criado_em >= NOW() - INTERVAL '{int(n_months)} months'
                  AND tipo IN ('receita', 'despesa')
                  AND is_internal_movement = false
                GROUP BY mes, tipo
                ORDER BY mes
                """,
                (user_id,),
            )
            rows = await cur.fetchall()

    history: dict = {}
    for row in rows:
        k = row["mes"]
        if k not in history:
            history[k] = {"month": k, "income": 0.0, "expense": 0.0}
        if row["tipo"] == "receita":
            history[k]["income"] = float(row["total"])
        elif row["tipo"] == "despesa":
            history[k]["expense"] = float(row["total"])

    return list(history.values())

# ─── CSV export ──────────────────────────────────────────────────────────────

async def build_csv(user_id: int, year: int, month: int) -> str:
    month_start, month_end = _month_range(year, month)
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT tipo, valor, alvo, nota, categoria, criado_em
                FROM launches
                WHERE user_id = %s
                  AND criado_em >= %s AND criado_em < %s
                ORDER BY criado_em DESC
                """,
                (user_id, month_start, month_end),
            )
            rows = await cur.fetchall()

    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["data", "tipo", "valor", "alvo", "nota", "categoria"])
    for r in rows:
        w.writerow([
            r["criado_em"].strftime("%Y-%m-%d %H:%M") if r["criado_em"] else "",
            r["tipo"],
            f"{float(r['valor']):.2f}",
            r["alvo"]      or "",
            r["nota"]      or "",
            r["categoria"] or "",
        ])
    return buf.getvalue()

# ─── Connection manager ───────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        # active[user_id][ws] = {"year": int, "month": int}
        self.active: Dict[int, Dict[WebSocket, dict]] = {}

    async def connect(self, ws: WebSocket, user_id: int, year: int, month: int):
        await ws.accept()
        self.active.setdefault(user_id, {})[ws] = {"year": year, "month": month}

    def disconnect(self, ws: WebSocket, user_id: int):
        if user_id in self.active:
            self.active[user_id].pop(ws, None)
            if not self.active[user_id]:
                self.active.pop(user_id, None)

    def set_month(self, ws: WebSocket, user_id: int, year: int, month: int):
        if user_id in self.active and ws in self.active[user_id]:
            self.active[user_id][ws]["year"] = year
            self.active[user_id][ws]["month"] = month

    def get_month(self, ws: WebSocket, user_id: int):
        info = self.active.get(user_id, {}).get(ws)
        if info:
            return info["year"], info["month"]

        now = datetime.now(timezone.utc)
        return now.year, now.month

    async def send_to(self, ws: WebSocket, payload: str):
        await ws.send_text(payload)

manager = ConnectionManager()

# ─── App startup ──────────────────────────────────────────────────────────────

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    _t0 = _startup_time.monotonic()

    async def _startup_required(label: str, coro):
        try:
            return await asyncio.wait_for(coro, timeout=STARTUP_STEP_TIMEOUT)
        except asyncio.TimeoutError as exc:
            print(
                f"ERROR: Startup travou em '{label}' por mais de {STARTUP_STEP_TIMEOUT}s.",
                file=sys.stderr,
                flush=True,
            )
            raise RuntimeError(f"Startup timeout: {label}") from exc

    async def _startup_optional(label: str, coro, default=None):
        try:
            return await asyncio.wait_for(coro, timeout=STARTUP_STEP_TIMEOUT)
        except asyncio.TimeoutError:
            print(
                f"WARNING: Startup ignorou '{label}' após {STARTUP_STEP_TIMEOUT}s.",
                file=sys.stderr,
                flush=True,
            )
            return default
        except Exception as exc:
            print(f"WARNING: Startup ignorou '{label}': {exc}", file=sys.stderr, flush=True)
            return default

    # ── 1. DB health check ────────────────────────────────────────────────────
    try:
        async def _db_health_check():
            async with await db_connect() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")

        await _startup_required("database health check", _db_health_check())
        print("OK: Database connected", flush=True)
    except Exception as exc:
        print(f"ERROR: Database connection failed: {exc}", file=sys.stderr, flush=True)
        raise

    # ── 2. Setup de tabelas em paralelo ───────────────────────────────────────
    await _startup_required(
        "setup de tabelas",
        asyncio.gather(
            ensure_budget_table(),
            ensure_investment_metadata_columns(),
            ensure_admin_tables(),
        ),
    )
    print("OK: Budget table ready", flush=True)
    print("OK: Investment metadata ready", flush=True)
    print("OK: Admin observability tables ready", flush=True)

    # ── 3. Warnings + detecção de usuário em paralelo ─────────────────────────
    async def _resolve_uid() -> int | None:
        if DASHBOARD_USER_ID:
            return int(DASHBOARD_USER_ID)
        rows = await list_users()
        if not rows:
            print("WARNING: No users found in database.", flush=True)
            return None
        if len(rows) == 1:
            uid = rows[0]["id"]
            print(f"INFO: Auto-detected user ID: {uid}", flush=True)
        else:
            ids = [r["id"] for r in rows]
            uid = ids[0]
            print(f"INFO: Multiple users {ids}. Using first: {uid}", flush=True)
        return uid

    uid, _ = await asyncio.gather(
        _startup_optional("detecção de usuário padrão", _resolve_uid()),
        _startup_optional("avisos administrativos de startup", log_admin_startup_warnings()),
    )

    app.state.default_user_id = uid
    _port = int(os.environ.get("PORT", "8000"))
    if uid:
        print(f"Dashboard: http://localhost:{_port}/", flush=True)
        print(f"WebSocket: ws://localhost:{_port}/ws/{uid}", flush=True)

    # ── 4. Background tasks com imports lazy ──────────────────────────────────
    # Os imports pesados ficam dentro dos wrappers: só são carregados quando
    # a coroutine executa (após o yield), fora da janela de startup.
    async def _wa_worker():
        try:
            await asyncio.sleep(1)
            from adapters.whatsapp.wa_app import _worker_loop  # noqa: PLC0415
            await _worker_loop()
        except Exception as exc:
            print(f"[wa_worker] erro: {exc}", file=sys.stderr)

    async def _wa_daily():
        try:
            await asyncio.sleep(1)
            from adapters.whatsapp.wa_app import _daily_report_loop  # noqa: PLC0415
            await _daily_report_loop()
        except Exception as exc:
            print(f"[wa_daily] erro: {exc}", file=sys.stderr)

    async def _engagement():
        try:
            await asyncio.sleep(1)
            from core.services.engagement_scheduler import run_engagement_loop  # noqa: PLC0415
            await run_engagement_loop()
        except Exception as exc:
            print(f"[engagement] erro: {exc}", file=sys.stderr)

    _elapsed = _startup_time.monotonic() - _t0
    print(f"[app] Startup interno concluído em {_elapsed:.1f}s.", flush=True)

    tasks = []
    if RUN_BACKGROUND_TASKS:
        tasks.extend(
            [
                asyncio.create_task(_wa_worker(), name="wa_worker"),
                asyncio.create_task(_wa_daily(), name="wa_daily"),
                asyncio.create_task(_engagement(), name="engagement"),
            ]
        )
    else:
        print("[app] Background tasks desativadas neste processo.", flush=True)

    yield

    # Shutdown: cancela tasks e aguarda com timeout para não travar
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=5)
    for t in tasks:
        if not t.done():
            print(f"[lifespan] task '{t.get_name()}' não encerrou a tempo.", file=sys.stderr)

app = FastAPI(title="Finance Dashboard", lifespan=lifespan)

# Middleware de log de erros HTTP (definido em core/admin_dashboard.py)
app.middleware("http")(admin_error_logging_middleware)

# ─── WhatsApp webhook routes (lazy import) ───────────────────────────────────
# Importar wa_app no nível de módulo puxava toda a cadeia de lógica do bot
# (wa_client, wa_runtime, handle_incoming, db, bcrypt, requests…) adicionando
# ~1s ao startup. Com wrappers lazy, o import só acontece na 1ª requisição.

async def _wa_verify(request: Request):
    from adapters.whatsapp.wa_app import wa_verify  # noqa: PLC0415
    return await wa_verify(request)

async def _wa_webhook(request: Request):
    from adapters.whatsapp.wa_app import wa_webhook  # noqa: PLC0415
    return await wa_webhook(request)

async def _wa_simulate(request: Request):
    from adapters.whatsapp.wa_app import wa_simulate  # noqa: PLC0415
    return await wa_simulate(request)

# Mantem compatibilidade com a rota antiga `/webhook`, usada em configs legadas.
app.add_api_route("/wa/webhook",     _wa_verify,   methods=["GET"])
app.add_api_route("/wa/webhook",     _wa_webhook,  methods=["POST"])
app.add_api_route("/webhook",        _wa_verify,   methods=["GET"])
app.add_api_route("/webhook",        _wa_webhook,  methods=["POST"])
app.add_api_route("/wa/dev/simulate", _wa_simulate, methods=["POST"])

# ─── Rate limiting ────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Admin dashboard routes (delegado para core/admin_dashboard.py) ───────────
register_admin_routes(app, HERE, JWT_SECRET, limiter)

# ─── Auth helpers ────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)

def _make_jwt(user_id: int, email: str) -> str:
    from datetime import timedelta
    payload = {
        "sub": str(user_id),
        "email": email,
        "type": "auth",
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")

def _decode_jwt(token: str) -> dict | None:
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None


def _build_whatsapp_onboarding_link(user_id: int, minutes_valid: int = 15) -> str:
    if not WHATSAPP_NUMBER:
        return ""
    safe_number = "".join(ch for ch in WHATSAPP_NUMBER if ch.isdigit())
    if not safe_number:
        return ""
    text = urllib.parse.quote("Olá")
    return f"https://api.whatsapp.com/send?phone={safe_number}&text={text}"

async def _get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> int:
    if not creds:
        raise HTTPException(status_code=401, detail="Token não fornecido.")
    payload = _decode_jwt(creds.credentials)
    if not payload or payload.get("type") != "auth":
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")
    return int(payload["sub"])


def _extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "").strip()
    if not auth:
        return None
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _resolve_dashboard_user_id(request: Request) -> int:
    token = request.query_params.get("token") or _extract_bearer_token(request)
    user_id = decode_dashboard_token(token or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token de dashboard inválido ou expirado.")
    return int(user_id)


def _authorize_dashboard_access(request: Request, user_id: int) -> int:
    current_user_id = _resolve_dashboard_user_id(request)
    if current_user_id != int(user_id):
        raise HTTPException(status_code=403, detail="Acesso negado para este usuário.")
    return current_user_id

# ─── Auth models ─────────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    email: str
    password: str
    phone: str

class LoginBody(BaseModel):
    email: str
    password: str

class EmailBody(BaseModel):
    email: str

class VerifyEmailBody(BaseModel):
    email: str
    code: str

class ResetPasswordBody(BaseModel):
    token: str
    new_password: str


class DashboardLinkBody(BaseModel):
    code: str


# ─── Auth endpoints ──────────────────────────────────────────────────────────

@app.get("/auth/validate")
async def auth_validate(token: str):
    """
    Valida um dashboard token gerado pelo bot.
    Retorna user_id se válido, 401 caso contrário.
    """
    user_id = decode_dashboard_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token inválido")
    return {"user_id": user_id}


@app.post("/auth/register")
@limiter.limit("5/minute")
async def auth_register(request: Request, body: RegisterBody):
    """
    Inicia o cadastro: valida os dados, gera código de 6 dígitos e envia por e-mail.
    A conta só é criada após confirmação via /auth/verify-email.
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import create_email_verification
    from core.services.email_service import send_verification_email

    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter pelo menos 6 caracteres.")

    try:
        normalize_phone_e164(body.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        code = create_email_verification(body.email, body.password, body.phone)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    sent = send_verification_email(body.email.strip().lower(), code)
    if not sent:
        raise HTTPException(status_code=500, detail="Não foi possível enviar o e-mail de verificação. Tente novamente.")

    return {"status": "verification_sent", "email": body.email.strip().lower()}


@app.post("/auth/verify-email")
@limiter.limit("10/minute")
async def auth_verify_email(request: Request, body: VerifyEmailBody):
    """
    Confirma o código de verificação e cria a conta.
    Retorna JWT + link_code igual ao registro anterior.
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import confirm_email_verification

    try:
        result = confirm_email_verification(body.email, body.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user_id    = result["user_id"]
    link_code  = result["link_code"]
    token      = _make_jwt(user_id, body.email.strip().lower())
    dash_token = make_dashboard_token(user_id, hours=DASHBOARD_SESSION_HOURS)

    wa_link = _build_whatsapp_onboarding_link(user_id)

    return {
        "token": token,
        "user_id": user_id,
        "link_code": link_code,
        "whatsapp_link": wa_link,
        "dashboard_url": f"{DASHBOARD_URL}/app?token={dash_token}",
        "expires_in": 86400,
    }


@app.post("/auth/login")
@limiter.limit("10/minute")
async def auth_login(request: Request, body: LoginBody):
    """Login via email+senha. Retorna JWT + link_code novo para vincular o bot."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import login_auth_user, create_link_code
    

    result = login_auth_user(body.email, body.password)
    if not result:
        await log_auth_login_event(
            body.email,
            False,
            ip_address=get_remote_address(request),
            user_agent=request.headers.get("user-agent"),
            failure_reason="invalid_credentials",
        )
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")

    user_id    = result["user_id"]
    link_code  = create_link_code(user_id, minutes_valid=15)
    token      = _make_jwt(user_id, result["email"])
    dash_token = make_dashboard_token(user_id, hours=DASHBOARD_SESSION_HOURS)

    await log_auth_login_event(
        result["email"],
        True,
        user_id=user_id,
        ip_address=get_remote_address(request),
        user_agent=request.headers.get("user-agent"),
    )

    wa_link = _build_whatsapp_onboarding_link(user_id)

    return {
        "token": token,
        "user_id": user_id,
        "plan": result["plan"],
        "link_code": link_code,
        "whatsapp_link": wa_link,
        "dashboard_url": f"{DASHBOARD_URL}/app?token={dash_token}",
        "expires_in": 86400,
    }


@app.post("/auth/forgot-password")
@limiter.limit("3/minute")
async def auth_forgot_password(request: Request, body: EmailBody):
    """
    Solicita recuperação de senha. Envia e-mail com link se o e-mail existir.
    Sempre retorna 200 para não revelar se o e-mail está cadastrado.
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import create_password_reset_token
    from core.services.email_service import send_password_reset_email

    token = create_password_reset_token(body.email)
    if token:
        reset_url = f"{DASHBOARD_URL}/reset-password?token={token}"
        send_password_reset_email(body.email.strip().lower(), reset_url)

    # sempre retorna 200 — não revela se o e-mail existe ou não
    return {"message": "Se este e-mail estiver cadastrado, você receberá as instruções em breve."}


@app.post("/auth/reset-password")
@limiter.limit("5/minute")
async def auth_reset_password(request: Request, body: ResetPasswordBody):
    """
    Redefine a senha usando o token recebido por e-mail.
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import consume_password_reset_token

    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter pelo menos 6 caracteres.")

    ok = consume_password_reset_token(body.token, body.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail="Link inválido ou expirado. Solicite um novo.")

    return {"message": "Senha redefinida com sucesso! Faça login com sua nova senha."}


@app.post("/auth/link-code")
async def auth_new_link_code(user_id: int = Depends(_get_current_user)):
    """Gera um novo link_code para o usuário autenticado vincular uma nova plataforma."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import create_link_code

    link_code = create_link_code(user_id, minutes_valid=15)
    wa_link = _build_whatsapp_onboarding_link(user_id)

    return {
        "link_code": link_code,
        "whatsapp_link": wa_link,
        "expires_in_minutes": 15,
    }


@app.get("/auth/me")
async def auth_me(user_id: int = Depends(_get_current_user)):
    """Retorna dados do usuário autenticado."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import get_auth_user

    user = get_auth_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    return {"user_id": user_id, **dict(user)}


@app.post("/auth/dashboard-token")
async def auth_dashboard_token(user_id: int = Depends(_get_current_user)):
    """Troca o token de login por um token curto de acesso ao dashboard."""
    dash_token = make_dashboard_token(user_id, hours=DASHBOARD_SESSION_HOURS)
    return {
        "token": dash_token,
        "dashboard_url": f"{DASHBOARD_URL}/app?token={dash_token}",
        "expires_in": int(DASHBOARD_SESSION_HOURS * 3600),
    }


@app.post("/auth/dashboard-link")
async def auth_dashboard_link(body: DashboardLinkBody, user_id: int = Depends(_get_current_user)):
    """
    Consome um magic link do dashboard e libera acesso apenas
    se o usuário logado for o dono desse link.
    """
    from db import consume_dashboard_session

    target_user_id = consume_dashboard_session(body.code.strip())
    if not target_user_id:
        raise HTTPException(status_code=401, detail="Link de dashboard inválido ou expirado.")
    if int(target_user_id) != int(user_id):
        raise HTTPException(status_code=403, detail="Este link pertence a outra conta.")

    dash_token = make_dashboard_token(int(user_id), hours=DASHBOARD_SESSION_HOURS)
    return {
        "token": dash_token,
        "dashboard_url": f"{DASHBOARD_URL}/app?token={dash_token}",
        "expires_in": int(DASHBOARD_SESSION_HOURS * 3600),
    }


# ─── Billing (Stripe) ────────────────────────────────────────────────────────

@app.post("/billing/create-checkout")
async def billing_create_checkout(user_id: int = Depends(_get_current_user)):
    """
    Cria uma sessão de checkout no Stripe para upgrade para o plano Pro.
    Requer: STRIPE_SECRET_KEY e STRIPE_PRICE_ID_PRO configurados.
    """
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID_PRO:
        raise HTTPException(status_code=503, detail="Pagamentos ainda não configurados.")

    import stripe
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import get_auth_user, set_stripe_customer

    stripe.api_key = STRIPE_SECRET_KEY

    user = get_auth_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    # Recupera ou cria o customer no Stripe
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            email=user["email"],
            metadata={"finbot_user_id": str(user_id)},
        )
        customer_id = customer.id
        set_stripe_customer(user_id, customer_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": STRIPE_PRICE_ID_PRO, "quantity": 1}],
        mode="subscription",
        success_url=f"{DASHBOARD_URL}/app?upgrade=success",
        cancel_url=f"{DASHBOARD_URL}/app?upgrade=cancelled",
        metadata={"finbot_user_id": str(user_id)},
    )

    return {"checkout_url": session.url}


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    """
    Recebe eventos do Stripe (checkout.session.completed, customer.subscription.*).
    Atualiza o plano do usuário no banco.
    """
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe não configurado.")

    import stripe
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import update_user_plan, get_user_by_stripe_customer

    stripe.api_key = STRIPE_SECRET_KEY
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        await log_system_event(
            "error",
            "billing_signature_invalid",
            "Webhook do Stripe rejeitado por assinatura invalida.",
            source="billing",
        )
        raise HTTPException(status_code=400, detail="Assinatura inválida.")

    await log_system_event(
        "info",
        "billing_webhook_received",
        f"Webhook do Stripe recebido: {event['type']}",
        source="billing",
        details={"event_type": event["type"]},
    )

    def _resolve_user(obj) -> int | None:
        uid = obj.get("metadata", {}).get("finbot_user_id")
        if uid:
            return int(uid)
        cid = obj.get("customer")
        if cid:
            return get_user_by_stripe_customer(cid)
        return None

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = _resolve_user(session)
        if user_id:
            # Subscription ainda pode estar incompleta; aguarda invoice.paid
            await log_system_event(
                "info",
                "billing_checkout_completed",
                "Checkout do Stripe concluido.",
                source="billing",
                user_id=user_id,
            )

    elif event["type"] in ("invoice.paid", "invoice.payment_succeeded"):
        invoice  = event["data"]["object"]
        user_id  = _resolve_user(invoice)
        sub_id   = invoice.get("subscription")
        if user_id and sub_id:
            sub = stripe.Subscription.retrieve(sub_id)
            expires_dt = datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc)
            update_user_plan(user_id, "pro", expires_dt)
            print(f"[billing] user {user_id} → pro até {expires_dt.date()}")
            await log_system_event(
                "info",
                "billing_plan_updated",
                "Plano do usuario atualizado para pro.",
                source="billing",
                user_id=user_id,
                details={"plan": "pro", "expires_at": expires_dt.isoformat()},
            )

    elif event["type"] in ("customer.subscription.deleted", "invoice.payment_failed"):
        obj     = event["data"]["object"]
        user_id = _resolve_user(obj)
        if user_id:
            update_user_plan(user_id, "free", None)
            print(f"[billing] user {user_id} → free (cancelamento/falha)")
            await log_system_event(
                "warning",
                "billing_payment_failed",
                "Assinatura movida para free por falha de pagamento ou cancelamento.",
                source="billing",
                user_id=user_id,
            )

    return {"received": True}


@app.post("/billing/portal")
async def billing_portal(user_id: int = Depends(_get_current_user)):
    """
    Cria uma sessão no Stripe Customer Portal para o usuário gerenciar
    a assinatura (cancelar, trocar cartão, ver faturas).
    """
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Pagamentos ainda não configurados.")

    import stripe
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import get_auth_user

    stripe.api_key = STRIPE_SECRET_KEY
    user = get_auth_user(user_id)
    if not user or not user.get("stripe_customer_id"):
        raise HTTPException(status_code=404, detail="Sem assinatura ativa.")

    portal = stripe.billing_portal.Session.create(
        customer=user["stripe_customer_id"],
        return_url=f"{DASHBOARD_URL}/app",
    )
    return {"portal_url": portal.url}


# ─── Static file routes ──────────────────────────────────────────────────────

@app.get("/d/{code}")
async def dashboard_short_link(code: str, view: str | None = None):
    """
    Resolve um magic link gerado pelo bot.
    O link é de uso único e cria uma sessão curta no navegador.
    """
    from db import consume_dashboard_session

    user_id = consume_dashboard_session(code)
    if not user_id:
        expired_html = """<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link expirado</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#070b14;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;color:rgba(255,255,255,.85)}
.box{text-align:center;max-width:400px;padding:48px 32px}
.icon{font-size:3.5rem;margin-bottom:20px}
h2{font-size:1.4rem;font-weight:600;margin-bottom:10px}
p{color:rgba(255,255,255,.5);line-height:1.7;margin-bottom:28px}
a{display:inline-block;padding:11px 28px;background:rgba(124,58,237,.45);
border:1px solid rgba(124,58,237,.55);border-radius:14px;color:white;
text-decoration:none;font-size:.9rem;transition:background .2s}
a:hover{background:rgba(124,58,237,.65)}
</style></head>
<body><div class="box">
<div class="icon">🔒</div>
<h2>Link expirado ou inválido</h2>
<p>Este link de acesso ao dashboard expirou ou já foi usado.<br>
Solicite um novo link digitando <strong style="color:rgba(255,255,255,.8)">dashboard</strong> no bot.<br>
Os links expiram em __MAGIC_LINK_MINUTES__ minutos e funcionam uma única vez.</p>
<a href="/">← Página inicial</a>
</div></body></html>""".replace("__MAGIC_LINK_MINUTES__", str(DASHBOARD_MAGIC_LINK_MINUTES))
        return HTMLResponse(content=expired_html, status_code=401)

    dash_token = make_dashboard_token(int(user_id), hours=DASHBOARD_SESSION_HOURS)
    target_view = view if view in {"overview", "investments"} else None
    suffix = f"&view={target_view}" if target_view else ""
    return RedirectResponse(url=f"/app?token={dash_token}{suffix}", status_code=302)


@app.get("/")
async def serve_landing():
    return FileResponse(HERE / "index.html")

@app.get("/app")
async def serve_dashboard():
    return FileResponse(HERE / "dashboard.html")


@app.get("/reset-password")
async def serve_reset_password():
    return FileResponse(HERE / "reset-password.html")


@app.get("/dashboard-login")
async def serve_dashboard_login():
    return FileResponse(HERE / "dashboard-login.html")

@app.get("/privacy")
async def serve_privacy():
    return FileResponse(HERE / "privacy.html", media_type="text/html")

@app.get("/changelog")
async def serve_changelog():
    return FileResponse(HERE / "changelog.html", media_type="text/html")

@app.get("/favicon.png")
async def serve_favicon():
    return FileResponse(HERE / "favicon.png", media_type="image/png")

@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse(HERE / "manifest.json", media_type="application/manifest+json")

@app.get("/service-worker.js")
async def serve_sw():
    resp = FileResponse(HERE / "service-worker.js", media_type="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"]          = "no-cache"
    return resp

# ─── Unsubscribe ─────────────────────────────────────────────────────────────

import hashlib as _hashlib
import hmac as _hmac
import base64 as _base64

from core.services.email_service import make_unsub_url  # noqa: E402


def _verify_unsub_token(user_id: int, email: str, token: str) -> bool:
    """Verifica token de unsubscribe usando a mesma lógica do email_service."""
    secret   = (JWT_SECRET or "pigbank-unsub").encode()
    payload  = f"{user_id}:{email}".encode()
    sig      = _hmac.new(secret, payload, _hashlib.sha256).digest()
    expected = _base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return _hmac.compare_digest(expected, token)


@app.get("/unsubscribe")
async def unsubscribe(uid: int, token: str):
    # busca o email pelo user_id
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT email FROM auth_accounts WHERE user_id = %s", (uid,)
            )
            row = await cur.fetchone()

    if not row:
        return HTMLResponse("<h2>Link inválido.</h2>", status_code=400)

    email = row["email"]
    if not _verify_unsub_token(uid, email, token):
        return HTMLResponse("<h2>Link inválido ou expirado.</h2>", status_code=400)

    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE auth_accounts SET engagement_opt_out = true WHERE user_id = %s",
                (uid,),
            )
        await conn.commit()

    return HTMLResponse("""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>Descadastro — PigBank AI</title>
  <style>
    body{margin:0;padding:0;background:#0a0d18;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh}
    .card{background:#0f1320;border:1px solid rgba(255,255,255,.1);border-radius:20px;
          padding:48px 40px;text-align:center;max-width:440px}
    .icon{font-size:56px;margin-bottom:16px}
    h1{margin:0 0 12px;font-size:22px;color:#fff}
    p{color:rgba(255,255,255,.6);line-height:1.7;margin:0 0 24px}
    a{color:#7c3aed;text-decoration:none}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">🐷</div>
    <h1>Descadastro confirmado</h1>
    <p>Você não vai mais receber os emails de dicas e insights do Piggy.<br/>
       Seus emails de segurança (código de verificação, redefinição de senha) continuam normais.</p>
    <p style="font-size:13px">Arrependeu? Mande "reativar emails" pro bot!
  </div>
</body>
</html>
""")


# ─── HTTP API routes ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        return {"status": "healthy", "db": "connected"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")

@app.get("/users")
async def list_all_users(request: Request):
    user_id = _resolve_dashboard_user_id(request)
    return {"users": [user_id]}

@app.get("/data/{user_id}")
async def get_data(
    request: Request,
    user_id: int,
    year: int = None,
    month: int = None,
    page: int = 1,
    limit: int = 25,
):
    _authorize_dashboard_access(request, user_id)
    return await get_financial_data(user_id, year, month, page, limit)

@app.get("/history/{user_id}")
async def monthly_history(request: Request, user_id: int, months: int = 6):
    _authorize_dashboard_access(request, user_id)
    if not 1 <= months <= 24:
        raise HTTPException(status_code=400, detail="months must be 1-24")
    data = await get_monthly_history(user_id, months)
    return {"data": data}

@app.get("/export/{user_id}")
async def export_csv(request: Request, user_id: int, year: int = None, month: int = None):
    _authorize_dashboard_access(request, user_id)
    now = datetime.now(timezone.utc)
    y = year  or now.year
    m = month or now.month
    content  = await build_csv(user_id, y, m)
    filename = f"financas_{y:04d}_{m:02d}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# ─── Budget routes ────────────────────────────────────────────────────────────

@app.get("/budgets/{user_id}")
async def get_budgets(request: Request, user_id: int):
    _authorize_dashboard_access(request, user_id)
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT categoria, budget FROM category_budgets WHERE user_id = %s ORDER BY categoria",
                (user_id,),
            )
            rows = await cur.fetchall()
    return {"budgets": [dict(r) for r in rows]}

class BudgetPayload(BaseModel):
    categoria: str
    budget: float

@app.post("/budgets/{user_id}")
async def set_budget(request: Request, user_id: int, payload: BudgetPayload):
    _authorize_dashboard_access(request, user_id)
    if payload.budget <= 0:
        raise HTTPException(status_code=400, detail="budget must be > 0")
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO category_budgets (user_id, categoria, budget)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, categoria)
                DO UPDATE SET budget = EXCLUDED.budget
                """,
                (user_id, payload.categoria, payload.budget),
            )
        await conn.commit()
    return {"ok": True, "categoria": payload.categoria, "budget": payload.budget}

@app.delete("/budgets/{user_id}/{categoria}")
async def delete_budget(request: Request, user_id: int, categoria: str):
    _authorize_dashboard_access(request, user_id)
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM category_budgets WHERE user_id = %s AND categoria = %s",
                (user_id, categoria),
            )
        await conn.commit()
    return {"ok": True}


# ─── Investment routes ───────────────────────────────────────────────────────

class InvestmentCreatePayload(BaseModel):
    name: str
    rate: float
    period: str
    initial_amount: float | None = None
    asset_type: str | None = None
    indexer: str | None = None
    issuer: str | None = None
    purchase_date: date | None = None
    maturity_date: date | None = None
    interest_payment_frequency: str | None = None
    tax_profile: str | None = None
    note: str | None = None


class InvestmentMovementPayload(BaseModel):
    name: str
    amount: float
    note: str | None = None


def _investment_action_note(action: str, name: str, issuer: str | None = None, note: str | None = None) -> str:
    clean_name = (name or "").strip() or "investimento"
    clean_issuer = (issuer or "").strip()
    clean_note = (note or "").strip()
    suffix = f" ({clean_issuer})" if clean_issuer and clean_issuer.lower() not in clean_name.lower() else ""
    description = f"{action} {clean_name}{suffix}"
    if clean_note:
        description += f" - {clean_note}"
    return description


@app.get("/investments/{user_id}/rates")
async def investment_rates(request: Request, user_id: int):
    _authorize_dashboard_access(request, user_id)
    rates = await asyncio.to_thread(get_dashboard_market_rates)
    return {"market_rates": rates}


@app.post("/investments/{user_id}")
async def create_investment_route(request: Request, user_id: int, payload: InvestmentCreatePayload):
    _authorize_dashboard_access(request, user_id)

    name = payload.name.strip()
    period = payload.period.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Nome do investimento é obrigatório.")
    if period not in {"daily", "monthly", "yearly", "cdi", "cdi_spread", "ipca_spread", "selic_spread"}:
        raise HTTPException(status_code=400, detail="Indexador inválido.")
    if payload.rate <= 0 and period != "selic_spread":
        raise HTTPException(status_code=400, detail="Taxa deve ser maior que zero.")

    try:
        create_note = _investment_action_note("Criou investimento", name, payload.issuer, payload.note)
        initial_note = _investment_action_note("Aporte inicial em", name)
        launch_id, inv_id, canon = await asyncio.to_thread(
            create_investment_db,
            user_id,
            name,
            payload.rate,
            period,
            create_note,
            asset_type=payload.asset_type,
            indexer=payload.indexer,
            issuer=payload.issuer,
            purchase_date=payload.purchase_date,
            maturity_date=payload.maturity_date,
            interest_payment_frequency=payload.interest_payment_frequency,
            tax_profile=payload.tax_profile,
            initial_amount=payload.initial_amount,
            initial_note=initial_note,
        )
    except Exception as exc:
        message = "Saldo insuficiente na conta para o aporte inicial." if str(exc) == "INSUFFICIENT_ACCOUNT" else str(exc)
        raise HTTPException(status_code=400, detail=message) from exc

    return {
        "ok": True,
        "created": launch_id is not None,
        "investment": {"id": inv_id, "name": canon, "rate": payload.rate, "period": period},
    }


@app.post("/investments/{user_id}/deposit")
async def deposit_investment_route(request: Request, user_id: int, payload: InvestmentMovementPayload):
    _authorize_dashboard_access(request, user_id)
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Valor deve ser maior que zero.")
    try:
        launch_id, new_acc, new_inv, canon = await asyncio.to_thread(
            investment_deposit_from_account,
            user_id,
            payload.name.strip(),
            payload.amount,
            payload.note or _investment_action_note("Aporte em", payload.name),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Investimento não encontrado.") from exc
    except ValueError as exc:
        message = "Saldo insuficiente na conta." if str(exc) == "INSUFFICIENT_ACCOUNT" else str(exc)
        raise HTTPException(status_code=400, detail=message) from exc

    return {"ok": True, "launch_id": launch_id, "account_balance": new_acc, "investment_balance": new_inv, "name": canon}


@app.post("/investments/{user_id}/withdraw")
async def withdraw_investment_route(request: Request, user_id: int, payload: InvestmentMovementPayload):
    _authorize_dashboard_access(request, user_id)
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Valor deve ser maior que zero.")
    try:
        launch_id, new_acc, new_inv, canon, tax_summary = await asyncio.to_thread(
            investment_withdraw_to_account,
            user_id,
            payload.name.strip(),
            payload.amount,
            payload.note or _investment_action_note("Resgate de", payload.name),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Investimento não encontrado.") from exc
    except ValueError as exc:
        message = "Saldo insuficiente no investimento." if str(exc) == "INSUFFICIENT_INVEST" else str(exc)
        raise HTTPException(status_code=400, detail=message) from exc

    return {
        "ok": True,
        "launch_id": launch_id,
        "account_balance": new_acc,
        "investment_balance": new_inv,
        "name": canon,
        "tax_summary": tax_summary,
    }


@app.delete("/investments/{user_id}/{name:path}")
async def delete_investment_route(request: Request, user_id: int, name: str):
    _authorize_dashboard_access(request, user_id)
    investment_name = urllib.parse.unquote(name).strip()
    try:
        launch_id, canon = await asyncio.to_thread(
            delete_investment,
            user_id,
            investment_name,
            "dashboard:delete",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Investimento não encontrado.") from exc
    except ValueError as exc:
        message = "Zere o saldo antes de remover o investimento." if str(exc) == "INV_NOT_ZERO" else str(exc)
        raise HTTPException(status_code=400, detail=message) from exc

    return {"ok": True, "launch_id": launch_id, "name": canon}


# ─── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(ws: WebSocket, user_id: int):
    token = ws.query_params.get("token", "")
    current_user_id = decode_dashboard_token(token)
    if not current_user_id or int(current_user_id) != int(user_id):
        await ws.close(code=1008)
        return

    now = datetime.now(timezone.utc)
    await manager.connect(ws, user_id, now.year, now.month)
    print(f"Connected: user={user_id} total={len(manager.active.get(user_id, {}))}")
    try:
        # Send initial snapshot with current month
        data = await get_financial_data(user_id, now.year, now.month)
        await ws.send_text(jdump({"type": "snapshot", "data": data}))

        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw) if raw.strip().startswith("{") else {"type": raw}
                t       = payload.get("type")

                if t == "refresh":
                    y, m = manager.get_month(ws, user_id)
                    page  = int(payload.get("page", 1))
                    limit = int(payload.get("limit", 25))
                    data = await get_financial_data(user_id, y, m, page, limit)
                    await ws.send_text(jdump({"type": "update", "data": data}))

                elif t == "get_month":
                    # Data for a specific month (month selector navigation)
                    now = datetime.now(timezone.utc)
                    y   = int(payload.get("year", now.year))
                    m   = int(payload.get("month", now.month))
                    page  = int(payload.get("page", 1))
                    limit = int(payload.get("limit", 25))

                    manager.set_month(ws, user_id, y, m)

                    data = await get_financial_data(user_id, y, m, page, limit)
                    await ws.send_text(jdump({"type": "month_data", "data": data}))

                elif t == "get_history":
                    n       = min(max(int(payload.get("months", 6)), 1), 24)
                    history = await get_monthly_history(user_id, n)
                    await ws.send_text(jdump({"type": "history_data", "data": history}))

                elif t == "ping":
                    await ws.send_text(jdump({"type": "pong"}))

            except Exception as exc:
                print(f"[ws] error handling message type={payload.get('type') if 'payload' in dir() else '?'}: {exc}")

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws, user_id)
        print(f"Disconnected: user={user_id}")

# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "finance_bot_websocket_custom:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
