""""Foi em dinheiro" (ADR 0006, decisão 6 de 2026-10-13): a declaração de despesa/receita que ficou "a conferir"
vira um fato da Carteira, por ação explícita do /app. Dois caminhos:

- selecionar um lançamento C que o usuário já tem na Carteira (sem novo débito): C ganha `efeitos.foi_em_dinheiro`
  com o contexto da declaração (alvo, nota, categoria, data, número), que então é apagada; C mantém os próprios
  campos (DP-3, DP-5);
- converter ("nenhum destes"): a própria declaração vira lançamento da Carteira, uma vez, com o valor e a data
  declarados; o saldo pode ficar negativo (o gasto já aconteceu).

Candidato = valor igual em CENTAVOS (sem a tolerância do banco), mesmo tipo, a até `RECON_DATE_WINDOW` dias, na
Carteira e sem outro vínculo. Nunca é escolhido sozinho. O marcador é a exclusividade (I10): um C resolve uma
declaração só, também o convertido. Mutex do usuário primeiro, como nas irmãs de `bank_conferencia`."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from psycopg.types.json import Json

from utils_date import _tz, launch_day

from .accounts import _LIGADO_SQL, ORIGEM_CARTEIRA
from .bank_declaracoes import CARTEIRA_SQL, _dia, e_fluxo, tem_evidencia
from .connection import TIPO_CANON_SQL, get_conn
from .open_finance_cash import VINCULADO_SQL

MARCADOR = "foi_em_dinheiro"
MAX_CANDIDATOS = 5


def candidatos_carteira(cur, user_id, declarations):
    """`{launch_id: [até 5 {id, name, category, amount, date}]}` das declarações de fluxo SEM par. UMA consulta
    para todas (o pré-filtro por valor e dia mantém barato quando as declarações se acumulam)."""
    from .bank_movements import _money
    from .open_finance import RECON_DATE_WINDOW
    pendentes = [d for d in declarations if e_fluxo(d) and not d["matched_transaction_id"]]
    if not pendentes:
        return {}
    dias, w = [_dia(d) for d in pendentes], timedelta(days=RECON_DATE_WINDOW + 1)
    cur.execute(
        f"""select id, alvo, categoria, {TIPO_CANON_SQL} as tipo, valor, criado_em from launches
             where user_id = %s and {TIPO_CANON_SQL} in ('despesa', 'receita') and {CARTEIRA_SQL}
               and not is_internal_movement and not (efeitos ? '{MARCADOR}')
               and not {_LIGADO_SQL} and not {VINCULADO_SQL}
               and round(valor::numeric, 2) = any(%s) and criado_em >= %s and criado_em < %s""",
        (user_id, [abs(_money(d["amount"])) for d in pendentes],
         datetime.combine(min(dias) - w, time.min, tzinfo=_tz()),
         datetime.combine(max(dias) + w + timedelta(days=1), time.min, tzinfo=_tz())))
    por_dia = {}  # uma vez por requisição: cada declaração olha só (seus centavos, os 7 dias da janela), não a lista toda
    for r in cur.fetchall():
        c = dict(r, dia=launch_day(r["criado_em"]))
        por_dia.setdefault((_money(c["valor"]), c["dia"]), []).append(c)
    saida = {}
    for d in pendentes:  # a autoridade é esta: centavos, tipo e dia (o SQL só estreita)
        centavos, base = abs(_money(d["amount"])), _dia(d)
        certos = [c for k in range(-RECON_DATE_WINDOW, RECON_DATE_WINDOW + 1)
                  for c in por_dia.get((centavos, base + timedelta(days=k)), ()) if c["tipo"] == d["tipo"]]
        certos.sort(key=lambda c: (not tem_evidencia(d, {"description": c["alvo"]}), abs((c["dia"] - base).days), c["id"]))
        saida[d["launch_id"]] = [{"id": c["id"], "name": c["alvo"], "category": c["categoria"],
                                  "amount": float(c["valor"]), "date": c["dia"].isoformat()}
                                 for c in certos[:MAX_CANDIDATOS]]
    return saida


def foi_em_dinheiro(user_id, launch_id, carteira_launch_id=None):
    """`carteira_launch_id` presente = selecionar; ausente = converter. 404 (`LookupError`) se a declaração não
    existe mais; 409 (`ValueError`) para o resto. Uma transação; não propõe conciliação (DP-2)."""
    from .bank_movements import _declarations, _lock_user, _money
    with get_conn() as conn, conn.cursor() as cur:
        _lock_user(cur, user_id)
        d = next((d for d in _declarations(cur, user_id) if d["launch_id"] == launch_id), None)
        if not d:
            raise LookupError("MOVEMENT_NOT_FOUND")
        if not e_fluxo(d):
            raise ValueError("MOVEMENT_ACTION_UNSUPPORTED")  # caixinha/aporte: ADR 0003
        if d["matched_transaction_id"]:
            raise ValueError("MOVEMENT_ALREADY_MATCHED")  # decisão 7: desfazer antes
        cur.execute("select efeitos, categoria, user_seq from launches where id=%s and user_id=%s for update",
                    (launch_id, user_id))
        row = cur.fetchone()
        if not row:  # apagada por quem não toma o mutex, enquanto o `for update` esperava
            raise LookupError("MOVEMENT_NOT_FOUND")
        efeitos = row["efeitos"] or {}
        # O delete (selecionar) e o `delta_conta` (converter) ignorariam um efeito a reverter (ex.: `bill_id`).
        if set(efeitos) - {"delta_conta", "funding_source"} or efeitos.get("delta_conta"):
            raise ValueError("MOVEMENT_ACTION_UNSUPPORTED")
        agora = datetime.now(_tz()).isoformat()
        if carteira_launch_id is None:
            cur.execute("""delete from bank_movement_declarations where user_id=%s and launch_id=%s
                              and matched_transaction_id is null returning amount, declared_at""",
                        (user_id, launch_id))
            gone = cur.fetchone()
            if not gone:
                raise ValueError("MOVEMENT_STATE_CHANGED")  # outra corrida venceu: quem perde não mexe no saldo
            # `amount` já é assinado (despesa -V, receita +V). O declarado, não o projetado (`launch_valor`).
            cur.execute("""update launches set valor=%s, criado_em=%s, origem=%s,
                                  efeitos=(efeitos - 'funding_source') || %s::jsonb where id=%s and user_id=%s""",
                        (abs(gone["amount"]), gone["declared_at"], ORIGEM_CARTEIRA,
                         Json({"delta_conta": float(gone["amount"]),
                               MARCADOR: {"convertida": True, "associada_em": agora}}), launch_id, user_id))
            cur.execute("update accounts set balance = balance + %s where user_id=%s returning balance",
                        (gone["amount"], user_id))  # sem checar saldo: negativo permitido (decisão 6)
            saldo = cur.fetchone()
            if not saldo:
                raise ValueError("MOVEMENT_STATE_CHANGED")
            conn.commit()
            return {"ok": True, "mode": "convertida", "launch_id": launch_id, "balance": float(saldo["balance"])}
        cur.execute("select id from launches where id=%s and user_id=%s for update", (carteira_launch_id, user_id))
        if not cur.fetchone():
            raise ValueError("CASH_NOT_ELIGIBLE")
        # Outro statement, depois do lock: snapshot novo, então a edição que C esperou já vale aqui.
        if carteira_launch_id not in {c["id"] for c in candidatos_carteira(cur, user_id, [d])[launch_id]}:
            raise ValueError("CASH_NOT_ELIGIBLE")
        contexto = {"tipo": d["tipo"], "valor": f"{abs(_money(d['amount'])):.2f}", "alvo": d["alvo"], "nota": d["nota"],
                    "categoria": row["categoria"], "declarada_em": d["declared_at"].astimezone(_tz()).isoformat(),
                    "numero": row["user_seq"]}  # da declaração (`amount`), não do `launch_valor` projetado
        cur.execute(f"""update launches set efeitos = efeitos || %s::jsonb where id=%s and user_id=%s
                           and not (efeitos ? '{MARCADOR}') returning id""",
                    (Json({MARCADOR: {"declaracao": contexto, "associada_em": agora}}), carteira_launch_id, user_id))
        if not cur.fetchone():
            raise ValueError("CASH_NOT_ELIGIBLE")
        cur.execute("delete from launches where id=%s and user_id=%s returning id", (launch_id, user_id))
        if not cur.fetchone():
            raise ValueError("MOVEMENT_STATE_CHANGED")
        conn.commit()
        return {"ok": True, "mode": "carteira", "launch_id": carteira_launch_id}
