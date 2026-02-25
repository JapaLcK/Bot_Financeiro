# core/services/cc_service.py
from __future__ import annotations

from typing import Optional

import db
from utils_text import fmt_brl


def saldo_text(user_id: int) -> str:
    bal = db.get_balance(user_id)
    return f"🏦 **Conta Corrente:** {fmt_brl(bal)}"


def listar_launches_text(user_id: int, limit: int = 10) -> str:
    rows = db.list_launches(user_id, limit=limit)
    if not rows:
        return "🧾 Nenhum lançamento ainda."

    lines: list[str] = ["🧾 **Últimos lançamentos:**"]
    for r in rows:
        dt = r.get("criado_em")
        dt_s = dt.strftime("%d/%m") if hasattr(dt, "strftime") else str(dt)

        tipo = (r.get("tipo") or "").lower()
        sinal = "-" if tipo == "despesa" else "+"

        nota = (r.get("nota") or "").strip() or "(sem nota)"
        nota = nota.replace("\n", " ")
        if len(nota) > 42:
            nota = nota[:39] + "..."

        valor = r.get("valor", 0)

        lines.append(f"`{r['id']:>3}` • {dt_s} • {nota} • {sinal}{fmt_brl(valor)}")

    lines.append("\nPara apagar: `apagar <id>` • Para desfazer último: `desfazer`")
    return "\n".join(lines)


def preview_launch(user_id: int, launch_id: int) -> Optional[str]:
    # Pra mostrar preview sem criar função nova no db.py:
    # pega alguns recentes e tenta achar o ID.
    rows = db.list_launches(user_id, limit=50)
    for r in rows:
        if int(r["id"]) == int(launch_id):
            dt = r.get("criado_em")
            dt_s = dt.strftime("%d/%m/%Y") if hasattr(dt, "strftime") else str(dt)
            tipo = (r.get("tipo") or "").lower()
            sinal = "-" if tipo == "despesa" else "+"
            nota = (r.get("nota") or "").strip() or "(sem nota)"
            valor = r.get("valor", 0)
            return f"`{r['id']}` • {dt_s} • {nota} • {sinal}{fmt_brl(valor)}"
    return None