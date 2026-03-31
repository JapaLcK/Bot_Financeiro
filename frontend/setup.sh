#!/usr/bin/env bash
# setup.sh — Finance Bot WebSocket Dashboard Setup
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
VENV_DIR="$SCRIPT_DIR/.venv"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Finance Bot WebSocket Dashboard — Setup"
echo "═══════════════════════════════════════════════════"
echo ""

# ── 1. DATABASE_URL ──────────────────────────────────────────────────────────
if grep -q "^DATABASE_URL=" "$ENV_FILE" 2>/dev/null; then
    EXISTING_DB=$(grep "^DATABASE_URL=" "$ENV_FILE" | cut -d'=' -f2-)
    echo "✅ DATABASE_URL already set in .env"
    echo "   ${EXISTING_DB:0:50}..."
else
    echo "Enter your PostgreSQL DATABASE_URL:"
    echo "  (e.g. postgresql://user:pass@host:port/db)"
    read -rp "DATABASE_URL: " INPUT_DB
    if [[ -z "$INPUT_DB" ]]; then
        echo "❌ DATABASE_URL cannot be empty." && exit 1
    fi
    echo "DATABASE_URL=$INPUT_DB" >> "$ENV_FILE"
    echo "✅ DATABASE_URL saved to .env"
fi

# ── 2. DASHBOARD_USER_ID ─────────────────────────────────────────────────────
if grep -q "^DASHBOARD_USER_ID=" "$ENV_FILE" 2>/dev/null; then
    EXISTING_UID=$(grep "^DASHBOARD_USER_ID=" "$ENV_FILE" | cut -d'=' -f2-)
    echo "✅ DASHBOARD_USER_ID already set: $EXISTING_UID"
else
    echo ""
    echo "Enter your Discord User ID (leave blank to auto-detect from DB):"
    echo "  (Right-click your name in Discord → Copy User ID)"
    read -rp "DASHBOARD_USER_ID: " INPUT_UID
    if [[ -n "$INPUT_UID" ]]; then
        echo "DASHBOARD_USER_ID=$INPUT_UID" >> "$ENV_FILE"
        echo "✅ DASHBOARD_USER_ID saved to .env"
    else
        echo "ℹ️  Will auto-detect user ID at server startup."
    fi
fi

# ── 3. Virtual environment ───────────────────────────────────────────────────
echo ""
if [[ -d "$VENV_DIR" ]]; then
    echo "✅ Virtual environment already exists at .venv"
else
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "✅ Virtual environment created"
fi

source "$VENV_DIR/bin/activate"

# ── 4. Install / upgrade dependencies ────────────────────────────────────────
echo ""
echo "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "psycopg[binary]>=3.1" \
    "python-dotenv>=1.0" \
    "websockets>=12.0"

echo "✅ Dependencies installed"

# ── 5. Verify DB connection ───────────────────────────────────────────────────
echo ""
echo "Testing database connection..."
python3 - <<'PYEOF'
import os, sys
from dotenv import load_dotenv
load_dotenv()
db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("❌ DATABASE_URL not found after setup."); sys.exit(1)
try:
    import psycopg
    conn = psycopg.connect(db_url)
    conn.close()
    print("✅ Database connection successful")
except Exception as e:
    print(f"❌ Database connection failed: {e}"); sys.exit(1)
PYEOF

# ── 6. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════"
echo "  Setup complete! Start the server with:"
echo ""
echo "    source .venv/bin/activate"
echo "    python3 finance_bot_websocket_custom.py"
echo ""
echo "  Then check: curl http://localhost:8000/health"
echo "═══════════════════════════════════════════════════"
echo ""
