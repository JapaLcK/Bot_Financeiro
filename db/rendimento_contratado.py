"""O card `rendimento`: a taxa contratada de cada posição BRL (`itens`/`motivos`, como sempre) e, em
`rendimentos`, quanto os investimentos renderam no Mês e no Total (`db/rendimentos.py`, estimado pelo saldo).
O valor vem do espelho de hoje e da foto-base (`open_finance_investment_snapshots`), nunca de
lastMonthRate/annualRate. Só lê, pelo cursor recebido; toda query filtra o dono da conexão.
"""
import logging
from datetime import datetime, timedelta, timezone

from core.services.pluggy_catalogo import logo_proprio
from utils_date import day_tz, today_tz

from .investido import q_posicao, tipo_da_posicao
from .metas_ritmo import _DE
from .of_snapshots import _numero, _quantidade
from .open_finance_state import _TERMINAL
from .patrimonio import (BANCO_VELHO, POSICOES_BANCO_SQL, desatualizada, fora_do_sync,
                        ler_conexoes, sem_saldo, ultima_geracao)
from .rendimentos import JANELA_DIAS, calcular, fim_do_mes_anterior

log = logging.getLogger(__name__)

# A última foto confirmada de cada identidade na janela do fim do mês passado; `_DE` é o recorte de
# `metas_ritmo` (vencedora por identidade, dono pelo `c.user_id`).
_BASE_SQL = _DE.format(
    cols="distinct on (s.provider_investment_id) s.provider_investment_id, s.observed_on, s.balance,"
         " s.amount_original, s.quantity", extra="") + """
       and s.collection_confirmed and s.observed_on between %(ini)s and %(fim)s
     order by s.provider_investment_id, s.observed_on desc, c.id desc
"""

_SUBTIPO = {"CDB": "CDB", "LCI": "LCI", "LCA": "LCA", "LC": "LC", "CRI": "CRI", "CRA": "CRA",
            "TREASURY": "Tesouro Direto", "DEBENTURES": "Debêntures"}


def ler(cur, user_id: int, *, hoje=None) -> dict:
    # Mesmo recorte latest por identidade/conexão do patrimônio; os ids são
    # somente chaves de query e nunca saem na projeção pública.
    cur.execute(POSICOES_BANCO_SQL, (user_id,))
    posicoes = [p for p in cur.fetchall() if p["connection_status"] not in _TERMINAL
                and p["currency"] == "BRL" and p["status"] != "TOTAL_WITHDRAWAL"]
    conexoes, estados = ler_conexoes(cur, user_id)
    por_id = {c["id"]: c for c in conexoes}
    ultima = ultima_geracao(cur, user_id, "open_finance_investments")
    limite = datetime.now(timezone.utc) - BANCO_VELHO
    hoje = hoje or today_tz()
    fim = fim_do_mes_anterior(hoje)
    bases = {}
    if posicoes:
        try:  # savepoint: erro de SQL na base não aborta a transação (`itens` segue); `bases` None = sem `rendimentos`
            with cur.connection.transaction():
                cur.execute(_BASE_SQL, {"u": user_id, "f": list({p["connection_id"] for p in posicoes}),
                                        "ini": fim - timedelta(days=JANELA_DIAS), "fim": fim})
                rows = cur.fetchall()
            # NaN/Infinity/enorme na foto viram None (= ausente), como o A e o Q; o `_numero` lê texto
            n = lambda v, f=_numero: None if v is None else f(str(v))  # noqa: E731
            bases = {b["provider_investment_id"]: {"observed_on": b["observed_on"], "B0": n(b["balance"]),
                                                   "A0": n(b["amount_original"]), "Q0": n(b["quantity"], _quantidade)}
                     for b in rows}
        except Exception:
            log.exception("rendimento: leitura da foto-base falhou (user_id=%s)", user_id)
            bases = None
    itens, lidos = [], []
    for p in posicoes:
        cur.execute("""select i.name, i.type, i.subtype, c.institution_name, s.contract_rate,
                              s.contract_rate_type, s.observed_at,
                              i.raw->>'amountOriginal' as aplicado, i.raw->>'quantity' as quantidade
                         from open_finance_investments i
                         join open_finance_connections c on c.id=i.connection_id
                         left join lateral (
                           select s.contract_rate, s.contract_rate_type, s.observed_at
                             from open_finance_investment_snapshots s
                            where s.connection_id=i.connection_id
                              and s.provider_investment_id=i.provider_investment_id
                              and s.collection_confirmed
                            order by s.observed_at desc limit 1
                         ) s on true
                        where c.user_id=%s and i.connection_id=%s
                          and i.provider_investment_id=%s""",
                    (user_id, p["connection_id"], p["provider_investment_id"]))
        r = cur.fetchone()
        taxa = r["contract_rate"]
        tipo = r["contract_rate_type"]
        informada = taxa is not None and taxa.is_finite() and bool(tipo)
        tipo_pos = (r["type"] or "").upper()
        nome = r["name"]
        if not nome or nome.upper() == tipo_pos:
            nome = _SUBTIPO.get((r["subtype"] or "").upper()) or (
                "Renda fixa" if tipo_pos == "FIXED_INCOME" else nome)
        c = por_id[p["connection_id"]]
        lidos.append((p, r, nome, c))
        # Ação/FII/ETF sem taxa não tem "rendimento contratado" (fica em `rendimentos`); tipo nulo/desconhecido fica.
        if not informada and tipo_pos in ("EQUITY", "ETF"):
            continue
        motivos = [] if informada else ["taxa_contratada_ausente"]
        if desatualizada(c, estados[str(c["id"])], limite):
            motivos.append("banco_desatualizado")
        if fora_do_sync(p, ultima):
            motivos.append("posicao_fora_do_ultimo_sync")
        if (r["observed_at"] is not None and p["updated_at"] is not None
                and r["observed_at"] < p["updated_at"]):
            motivos.append("taxa_de_coleta_anterior")
        itens.append({"nome": nome, "instituicao": r["institution_name"],
                      "taxa": taxa if informada else None,
                      "tipo_taxa": tipo if informada else None,
                      "observado_em": r["observed_at"], "motivos": motivos})
    try:  # o bloco novo falha fechado: nunca derruba `itens`/`motivos`
        ativos = [{"tipo": tipo_da_posicao(r["type"], r["subtype"]), "nome": nome,
                   "instituicao": r["institution_name"],
                   "logo_proprio": logo_proprio(c["institution_id"]) if c["provider"] == "pluggy" else None,
                   "saldo_ausente": sem_saldo(p), "B": q_posicao(p), "A": _numero(r["aplicado"]),
                   "Q": _quantidade(r["quantidade"]), "base": bases.get(p["provider_investment_id"]),
                   "atualizado_em": p["updated_at"] and day_tz(p["updated_at"])} for p, r, nome, c in lidos]
        rend = calcular(ativos, hoje)
        syncs = [por_id[p["connection_id"]]["last_sync_at"] for p in posicoes]
        rend["sincronizado_em"] = min((t for t in syncs if t), default=None)
        if not any(c["last_sync_at"] and c["id"] in ultima for c in conexoes
                   if (c["status"] or "").upper() not in _TERMINAL):
            rend["saldo"] = None  # nenhuma conexão viva lida com posição: o total do `investido` também é `None`
        rend["motivos"] = ([] if ativos else ["sem_posicoes"]) + (
            ["banco_desatualizado"] if any(desatualizada(por_id[i], estados[str(i)], limite)
                                           for i in {p["connection_id"] for p in posicoes}) else [])
    except Exception:
        log.exception("rendimento: cálculo de rendimentos falhou (user_id=%s)", user_id)
        rend = None
    return {"tipo": "contratado", "itens": itens, "motivos": [] if itens else ["sem_posicoes_brl"],
            "rendimentos": rend}
