"""
db/investido.py — o total investido nos bancos conectados, com a divisão por tipo e
por banco (`GET /api/v2/investido`, a tool `get_investment_summary` da IA e o "meus
investimentos" do WhatsApp). Uma regra só para os três.

Só posições do Open Finance, com o recorte da foto do patrimônio (`db/patrimonio.py`:
`POSICOES_BANCO_SQL` + `separar_posicoes`). Investimento e caixinha manuais não são
lidos; a caixinha espelhada entra pela posição. Só lê, pelo cursor recebido.

Centavos: cada posição entra quantizada (`q`); parte = Σ q das posições dela; total =
Σ q de todas. Assim Σ por_tipo == total == Σ por_banco, exato. Custo declarado: o total
pode diferir do `investimentos_banco` da foto (que não quantiza) em até 0,005 × nº de
posições — só com saldo de 3+ casas.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .connection import get_conn
from .open_finance_state import _TERMINAL
from .patrimonio import (BANCO_VELHO, POSICOES_BANCO_SQL, desatualizada, finito, fora_do_sync,
                         ler_conexoes, sem_saldo, separar_posicoes, ultima_geracao)
from .rv import pluggy_rv_kind

MOTIVOS = ("sem_banco_conectado", "banco_desatualizado", "saldo_ausente", "moeda_presumida",
           "conta_fora_do_ultimo_sync", "outra_moeda", "conexao_pausada", "nenhum_investimento")
MOTIVOS_PARTE = ("banco_desatualizado", "saldo_ausente", "moeda_presumida", "conta_fora_do_ultimo_sync")
# A ÚNICA tabela de rótulos de tipo: a API manda o rótulo pronto, o TS não tem cópia.
TIPOS = {
    "renda_fixa": "Renda fixa", "tesouro": "Tesouro Direto", "acoes": "Ações",
    "fii": "Fundos imobiliários", "fundos": "Fundos de investimento", "etf": "ETFs", "outros": "Outros",
}
CENTAVO = Decimal("0.01")
SEM_NOME = "Banco sem nome"  # `institution_name` nulo na conexão


def tipo_da_posicao(inv_type: str | None, subtype: str | None) -> str:
    """`type`/`subtype` do Pluggy → chave de `TIPOS`. Tipo desconhecido vai para "outros"
    (nunca some do total). ponytail: COE/previdência ganham chave quando aparecerem."""
    t, st = (inv_type or "").upper(), (subtype or "").upper()
    if t == "FIXED_INCOME":
        return "tesouro" if st == "TREASURY" else "renda_fixa"
    if t == "EQUITY":
        return "fii" if pluggy_rv_kind(t, st) == "fii" else "acoes"
    return {"MUTUAL_FUND": "fundos", "ETF": "etf"}.get(t, "outros")


def _ordem(motivos: set, lista=MOTIVOS) -> list[str]:
    return [m for m in lista if m in motivos]


def _partes(acc: dict, chave: str, rotulo) -> list[dict]:
    # Parte só com posições sem saldo (e soma 0) → `null`; parte que fecha em 0 some.
    out = []
    for k, (soma, todas_sem_saldo, motivos) in acc.items():
        valor = None if todas_sem_saldo and soma == 0 else soma
        if valor is None or valor != 0:
            out.append({chave: k, **rotulo(k), "valor": valor, "motivos": _ordem(motivos, MOTIVOS_PARTE)})
    return sorted(out, key=lambda p: (p["valor"] is None, -(p["valor"] or 0),
                                      p.get("rotulo", p.get("banco"))))


def calcular(cur, user_id: int, *, agora: datetime | None = None) -> dict:
    conexoes, estados = ler_conexoes(cur, user_id)
    por_id = {c["id"]: c for c in conexoes}
    vivas = [c for c in conexoes if (c["status"] or "").upper() not in _TERMINAL]
    limite = (agora if agora is not None else datetime.now(timezone.utc)) - BANCO_VELHO
    velha = {c["id"]: desatualizada(c, estados[str(c["id"])], limite) for c in vivas}
    cur.execute(POSICOES_BANCO_SQL, (user_id,))
    posicoes, fora = separar_posicoes(cur.fetchall())
    ultima = ultima_geracao(cur, user_id, "open_finance_investments")

    topo = {m for m, sim in (("outra_moeda", fora["moeda"]), ("conexao_pausada", fora["pausada"]),
                             ("banco_desatualizado", any(velha.values()))) if sim}
    total, por_tipo, por_banco = Decimal("0.00"), {}, {}
    for p in posicoes:
        c = por_id[p["connection_id"]]
        ausente = sem_saldo(p)
        motivos = {m for m, sim in (("banco_desatualizado", velha[c["id"]]), ("saldo_ausente", ausente),
                                    ("moeda_presumida", not p["currency_code"]),
                                    ("conta_fora_do_ultimo_sync", fora_do_sync(p, ultima))) if sim}
        topo |= motivos
        q = p["balance"].quantize(CENTAVO) if finito(p["balance"]) else Decimal("0.00")
        total += q
        for acc, k in ((por_tipo, tipo_da_posicao(p["type"], p["subtype"])),
                       (por_banco, c["institution_name"] or SEM_NOME)):
            soma, todas, ms = acc.get(k, (Decimal("0.00"), True, set()))
            acc[k] = (soma + q, todas and ausente, ms | motivos)

    # Conexão sem nenhuma linha no espelho (`ultima`) não prova carteira vazia: o sync não
    # grava "li e veio vazio", e a falha nem sempre vira `status_reason` (item em
    # NEEDS_USER grava ""). Sem linha não há R$ 0.
    if not any(c["last_sync_at"] and c["id"] in ultima for c in vivas):
        # Sem banco vivo, ou nenhum sincronizado com posição no espelho: não se sabe (≠ zero).
        if not vivas:
            topo.add("sem_banco_conectado")
        elif not any(velha.values()) and not fora["pausada"]:
            # Só com TODA conexão viva saudável (o contrário do `banco_desatualizado`: tela
            # "Atualizado", sync em 48 h, sem tentativa depois). Qualquer dúvida fica com ele.
            # Posição de conexão pausada existe, só está fora: "não encontrei" seria falso.
            # Moeda/resgatada não chegam aqui: a linha põe a conexão viva em `ultima`.
            topo.add("nenhum_investimento")
        return {"total": None, "por_tipo": [], "por_banco": [], "motivos": _ordem(topo)}
    if posicoes and total == 0 and all(sem_saldo(p) for p in posicoes):
        total = None  # nenhum saldo veio do banco: o mesmo critério da parte `null`
    return {
        "total": total,
        "por_tipo": _partes(por_tipo, "tipo", lambda k: {"rotulo": TIPOS[k]}),
        "por_banco": _partes(por_banco, "banco", lambda k: {}),
        "motivos": _ordem(topo),
    }


def ler(user_id: int) -> dict:
    """`calcular` num snapshot só (repeatable read, só leitura), como `GET /api/v2/contas`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        dados = calcular(cur, user_id)
        conn.rollback()
    return dados
