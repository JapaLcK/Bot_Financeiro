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
import urllib.parse
from decimal import Decimal
from datetime import datetime, date, timezone
from typing import Dict

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

load_app_env()

DATABASE_URL      = os.getenv("DATABASE_URL")
DASHBOARD_USER_ID = os.getenv("DASHBOARD_USER_ID")
POLL_INTERVAL     = int(os.getenv("POLL_INTERVAL", "30"))
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
    return await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)

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
            await cur.execute(
                "SELECT name, balance, rate, period, last_date FROM investments "
                "WHERE user_id = %s ORDER BY name",
                (user_id,),
            )
            investments = await cur.fetchall()

                        # Total launches for the requested month (todos, incluindo movimentações internas)
            await cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM launches
                WHERE user_id = %s
                  AND criado_em >= %s AND criado_em < %s
                """,
                (user_id, month_start, month_end),
            )
            launches_total_row = await cur.fetchone()
            launches_total = int(launches_total_row["total"] or 0)

            # Launches for the requested month (paginated) — inclui is_internal_movement para tag visual
            await cur.execute(
                """
                SELECT tipo, valor, alvo, nota, categoria, criado_em, is_internal_movement
                FROM launches
                WHERE user_id = %s
                  AND criado_em >= %s AND criado_em < %s
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

    async def broadcast_current_view(self, user_id: int):
        conns = self.active.get(user_id, {})
        dead = []

        for ws, info in list(conns.items()):
            try:
                data = await get_financial_data(user_id, info["year"], info["month"])
                await ws.send_text(jdump({"type": "update", "data": data}))
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws, user_id)

manager = ConnectionManager()

# ─── Background push loop ─────────────────────────────────────────────────────

async def push_loop():
    """Every POLL_INTERVAL seconds push fresh data respecting each connection's selected month."""
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        for user_id, conns in list(manager.active.items()):
            if not conns:
                continue
            try:
                await manager.broadcast_current_view(user_id)
            except Exception as exc:
                print(f"[push_loop] error for user {user_id}: {exc}")

# ─── App startup ──────────────────────────────────────────────────────────────

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        print("OK: Database connected")
    except Exception as exc:
        print(f"ERROR: Database connection failed: {exc}", file=sys.stderr)
        raise

    await ensure_budget_table()
    print("OK: Budget table ready")

    uid = int(DASHBOARD_USER_ID) if DASHBOARD_USER_ID else None
    if uid is None:
        rows = await list_users()
        if not rows:
            print("WARNING: No users found in database.")
        elif len(rows) == 1:
            uid = rows[0]["id"]
            print(f"INFO: Auto-detected user ID: {uid}")
        else:
            ids = [r["id"] for r in rows]
            uid = ids[0]
            print(f"INFO: Multiple users {ids}. Using first: {uid}")

    app.state.default_user_id = uid
    if uid:
        print(f"Dashboard: http://localhost:8000/")
        print(f"WebSocket: ws://localhost:8000/ws/{uid}")

    from adapters.whatsapp.wa_app import _worker_loop as _wa_worker, _daily_report_loop as _wa_daily  # noqa: E402
    task = asyncio.create_task(push_loop())
    wa_worker = asyncio.create_task(_wa_worker())
    wa_daily = asyncio.create_task(_wa_daily())
    yield
    for t in (task, wa_worker, wa_daily):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

app = FastAPI(title="Finance Dashboard", lifespan=lifespan)

# ─── WhatsApp webhook routes ──────────────────────────────────────────────────
from adapters.whatsapp.wa_app import (  # noqa: E402
    wa_verify,
    wa_webhook,
    wa_simulate,
)
# Mantem compatibilidade com a rota antiga `/webhook`, usada em configs legadas.
app.add_api_route("/wa/webhook", wa_verify, methods=["GET"])
app.add_api_route("/wa/webhook", wa_webhook, methods=["POST"])
app.add_api_route("/webhook", wa_verify, methods=["GET"])
app.add_api_route("/webhook", wa_webhook, methods=["POST"])
app.add_api_route("/wa/dev/simulate", wa_simulate, methods=["POST"])

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
    text = urllib.parse.quote("Olá")
    return f"https://wa.me/{WHATSAPP_NUMBER}?text={text}"

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
    dash_token = make_dashboard_token(user_id, hours=2)

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
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")

    user_id    = result["user_id"]
    link_code  = create_link_code(user_id, minutes_valid=15)
    token      = _make_jwt(user_id, result["email"])
    dash_token = make_dashboard_token(user_id, hours=2)

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
    dash_token = make_dashboard_token(user_id, hours=2)
    return {
        "token": dash_token,
        "dashboard_url": f"{DASHBOARD_URL}/app?token={dash_token}",
        "expires_in": 7200,
    }


@app.post("/auth/dashboard-link")
async def auth_dashboard_link(body: DashboardLinkBody, user_id: int = Depends(_get_current_user)):
    """
    Consome um link curto do dashboard e libera acesso apenas
    se o usuário logado for o dono desse link.
    """
    from db import consume_dashboard_session
    from db import auto_link_auth_user, get_auth_user

    target_user_id = consume_dashboard_session(body.code.strip())
    if not target_user_id:
        raise HTTPException(status_code=401, detail="Link de dashboard inválido ou expirado.")
    final_user_id = int(target_user_id)
    if int(target_user_id) != int(user_id):
        current_user = get_auth_user(user_id)
        if not current_user:
            raise HTTPException(status_code=403, detail="Este link pertence a outra conta.")
        final_user_id = auto_link_auth_user(target_user_id, user_id)

    final_auth_user = get_auth_user(final_user_id)
    auth_token = None
    if final_auth_user:
        auth_token = _make_jwt(final_user_id, final_auth_user["email"])

    dash_token = make_dashboard_token(final_user_id, hours=2)
    return {
        "auth_token": auth_token,
        "token": dash_token,
        "dashboard_url": f"{DASHBOARD_URL}/app?token={dash_token}",
        "expires_in": 7200,
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
        raise HTTPException(status_code=400, detail="Assinatura inválida.")

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
            pass

    elif event["type"] in ("invoice.paid", "invoice.payment_succeeded"):
        invoice  = event["data"]["object"]
        user_id  = _resolve_user(invoice)
        sub_id   = invoice.get("subscription")
        if user_id and sub_id:
            sub = stripe.Subscription.retrieve(sub_id)
            expires_dt = datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc)
            update_user_plan(user_id, "pro", expires_dt)
            print(f"[billing] user {user_id} → pro até {expires_dt.date()}")

    elif event["type"] in ("customer.subscription.deleted", "invoice.payment_failed"):
        obj     = event["data"]["object"]
        user_id = _resolve_user(obj)
        if user_id:
            update_user_plan(user_id, "free", None)
            print(f"[billing] user {user_id} → free (cancelamento/falha)")

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
async def dashboard_short_link(code: str):
    """
    Resolve um short link gerado pelo bot.
    Redireciona para a landing, onde o usuário faz login
    e então o link é validado contra a conta autenticada.
    """
    from db import get_dashboard_session

    user_id = get_dashboard_session(code)
    if not user_id:
        return HTMLResponse(content="""<!DOCTYPE html>
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
Solicite um novo link digitando <strong style="color:rgba(255,255,255,.8)">dashboard</strong> no bot.</p>
<a href="/">← Página inicial</a>
</div></body></html>""", status_code=401)

    return RedirectResponse(url=f"/dashboard-login?dashboard_code={code}", status_code=302)


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
