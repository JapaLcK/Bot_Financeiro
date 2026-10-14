"""Status de todas as caixinhas: as com meta (target_amount) ganham progresso e ritmo;
as sem meta saem com is_goal False e indicator "no_target".

Extraído de frontend/routes/pockets.py::goals_status_route sem mudar a conta:
a rota chama esta função, e o show_card do chat da IA (PR B1) vai chamar a mesma.
"""

from datetime import date

from core.services.plan_service import require_min_tier
from db.connection import get_conn
from db.pockets import list_pockets


def status_metas(user_id: int) -> list[dict]:
    pockets = list_pockets(user_id)
    goals = []
    today = date.today()
    # OF ao vivo é feature paga (Essencial+): no Grátis a caixinha do banco congela
    # e o front mostra "reative seu banco" em vez de "Sincronizada".
    of_plan_active = require_min_tier(user_id, "essencial")
    for p in pockets:
        ta = p.get("target_amount")
        saved = float(p.get("balance") or 0)
        is_goal = ta is not None
        if not is_goal:
            # Caixinha sem meta: retorna info mínima
            goals.append({
                "id": p["id"],
                "name": p["name"],
                "balance": saved,
                "target_amount": None,
                "target_date": None,
                "emoji": p.get("emoji"),
                "color": p.get("color"),
                "status": p.get("status") or "active",
                "description": p.get("description"),
                "interest_enabled": bool(p.get("interest_enabled")),
                "interest_rate": float(p.get("interest_rate") or 1),
                "interest_period": p.get("interest_period") or "cdi",
                "source": p.get("source"),
                "of_investment_id": p.get("of_investment_id"),
                "of_plan_active": of_plan_active,
                "is_goal": False,
                "pct_complete": None,
                "remaining": None,
                "days_left": None,
                "monthly_pace_needed": None,
                "monthly_pace_current": None,
                "projected_months": None,
                "indicator": "no_target",
            })
            continue
        tgt = float(ta)
        pct = (saved / tgt * 100.0) if tgt > 0 else 0.0
        td = p.get("target_date")
        days_left = (td - today).days if td else None
        remaining = max(0.0, tgt - saved)
        monthly_pace_needed = None
        if td and days_left and days_left > 0 and remaining > 0:
            months_left = max(1, days_left / 30.0)
            monthly_pace_needed = remaining / months_left

        # Ritmo atual: médio dos últimos 90 dias (depositos - saques)
        monthly_pace_current = None
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select
                      coalesce(sum(case when tipo = 'deposito_caixinha' then valor else 0 end), 0) -
                      coalesce(sum(case when tipo = 'saque_caixinha' then valor else 0 end), 0)
                      as net
                    from launches
                    where user_id=%s and alvo=%s
                      and criado_em >= now() - interval '90 days'
                    """,
                    (user_id, p["name"]),
                )
                r = cur.fetchone()
                net90 = float(r["net"] or 0)
                monthly_pace_current = net90 / 3.0  # média mensal

        # Status indicator
        indicator = "active"
        if pct >= 100:
            indicator = "achieved"
        elif days_left is not None and days_left < 0:
            indicator = "behind"
        elif monthly_pace_needed and monthly_pace_current is not None:
            if monthly_pace_current >= monthly_pace_needed * 1.1:
                indicator = "ahead"
            elif monthly_pace_current >= monthly_pace_needed * 0.9:
                indicator = "on_track"
            elif monthly_pace_current >= monthly_pace_needed * 0.5:
                indicator = "tight"
            else:
                indicator = "behind"

        # Projeção: a esse ritmo, quanto tempo até atingir?
        projected_months = None
        if monthly_pace_current and monthly_pace_current > 0 and remaining > 0:
            projected_months = remaining / monthly_pace_current

        goals.append({
            "id": p["id"],
            "name": p["name"],
            "balance": saved,
            "target_amount": tgt,
            "target_date": td.isoformat() if td else None,
            "emoji": p.get("emoji"),
            "color": p.get("color"),
            "status": p.get("status") or "active",
            "description": p.get("description"),
            "interest_enabled": bool(p.get("interest_enabled")),
            "interest_rate": float(p.get("interest_rate") or 1),
            "interest_period": p.get("interest_period") or "cdi",
            "source": p.get("source"),
            "of_investment_id": p.get("of_investment_id"),
            "of_plan_active": of_plan_active,
            "is_goal": True,
            "pct_complete": round(pct, 1),
            "remaining": round(remaining, 2),
            "days_left": days_left,
            "monthly_pace_needed": round(monthly_pace_needed, 2) if monthly_pace_needed else None,
            "monthly_pace_current": round(monthly_pace_current, 2) if monthly_pace_current is not None else None,
            "projected_months": round(projected_months, 1) if projected_months else None,
            "indicator": indicator,
        })
    return goals
