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
# `por_banco_com_zero` também carrega o que `separar_posicoes` deixou fora DAQUELE banco.
MOTIVOS_BANCO = (*MOTIVOS_PARTE, "outra_moeda", "conexao_pausada")
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
        # Opção e derivativo não são ação; o `pluggy_rv_kind` (outros consumidores) não os
        # separa. EQUITY/ETF segue em Ações (decisão do PR F).
        if st in ("OPTION", "DERIVATIVES"):
            return "outros"
        return "fii" if pluggy_rv_kind(t, st) == "fii" else "acoes"
    return {"MUTUAL_FUND": "fundos", "ETF": "etf"}.get(t, "outros")


def q_posicao(p) -> Decimal:
    """O valor da posição na soma, em centavos (saldo não finito soma 0). A regra do total de hoje e
    a do histórico do ritmo (`db/metas_ritmo.py`): uma só."""
    return p["balance"].quantize(CENTAVO) if finito(p["balance"]) else Decimal("0.00")


def _ordem(motivos: set, lista=MOTIVOS) -> list[str]:
    return [m for m in lista if m in motivos]


def _motivos_fora(fora: dict) -> set:
    """Motivos do que `separar_posicoes` deixou fora: a regra do topo e a de cada banco."""
    return {m for m, sim in (("outra_moeda", fora["moeda"]), ("conexao_pausada", fora["pausada"])) if sim}


def _partes(acc: dict, chave: str, rotulo, *, zeradas: bool = False, ordem=MOTIVOS_PARTE) -> list[dict]:
    # Soma 0 com alguma posição sem saldo → `null` (não se sabe); parte que fecha em 0 some
    # (salvo `zeradas`, para quem precisa distinguir "zerou" de "não está mais lá").
    out = []
    for k, (soma, motivos) in acc.items():
        valor = None if soma == 0 and "saldo_ausente" in motivos else soma
        if zeradas or valor is None or valor != 0:
            out.append({chave: k, **rotulo(k), "valor": valor, "motivos": _ordem(motivos, ordem)})
    return sorted(out, key=lambda p: (p["valor"] is None, -(p["valor"] or 0),
                                      p.get("rotulo", p.get("banco"))))


def _por_conexao(conexoes, vivas, velha, linhas, acc) -> dict:
    """`{connection_id: {valor, motivos, posicoes}}` de TODA conexão, pelo id: duas "Nubank" são duas
    (`por_banco` agrupa pelo nome). `valor` = Σ das posições dela (a regra de `_partes`); `None` = não se
    sabe: sem posição lida, só posição sem saldo, só posição em outra moeda ou conexão pausada; e em TODA
    conexão no ramo cedo de `calcular` (nenhuma viva sincronizada com posição: o critério é do conjunto). Resgatada de conexão viva é R$ 0
    conhecido, como em `por_banco_com_zero`. `posicoes` = as linhas do espelho dela, sem duplicata."""
    dela = {}
    for r in linhas:
        dela.setdefault(r["connection_id"], []).append(r)
    ids_vivas = {c["id"] for c in vivas}
    out = {}
    for c in conexoes:
        cid = c["id"]
        rs = dela.get(cid, [])
        fora = separar_posicoes(rs)[1]
        soma, ms = acc.get(cid, (Decimal("0.00"), set()))
        ms = ms | _motivos_fora(fora) | ({"banco_desatualizado"} if velha.get(cid) else set())
        if cid not in ids_vivas:
            ms.add("conexao_pausada")
        valor = soma if cid in ids_vivas and (cid in acc or fora["resgatada"]) else None
        if valor == 0 and "saldo_ausente" in ms:
            valor = None
        out[cid] = {"valor": valor, "motivos": _ordem(ms, MOTIVOS_BANCO), "posicoes": len(rs)}
    return out


def calcular(cur, user_id: int, *, agora: datetime | None = None, com_zero: bool = False,
             por_conexao: bool = False) -> dict:
    """`com_zero` (só `db/metas.py`) acrescenta `por_banco_com_zero`: `por_banco` mais o banco lido
    que resgatou tudo, em R$ 0 (e `com_posicao`, `bancos_com_posicao`, `conexoes_por_banco`).
    `por_conexao` (só `db/bancos.py`) acrescenta `por_conexao`, a soma por conexão. Sem eles o retorno
    é o de sempre."""
    conexoes, estados = ler_conexoes(cur, user_id)
    por_id = {c["id"]: c for c in conexoes}
    vivas = [c for c in conexoes if (c["status"] or "").upper() not in _TERMINAL]
    limite = (agora if agora is not None else datetime.now(timezone.utc)) - BANCO_VELHO
    velha = {c["id"]: desatualizada(c, estados[str(c["id"])], limite) for c in vivas}
    cur.execute(POSICOES_BANCO_SQL, (user_id,))
    linhas = cur.fetchall()
    posicoes, fora = separar_posicoes(linhas)
    ultima = ultima_geracao(cur, user_id, "open_finance_investments")

    topo = _motivos_fora(fora) | ({"banco_desatualizado"} if any(velha.values()) else set())
    total, por_tipo, por_banco, por_cx = Decimal("0.00"), {}, {}, {}
    for p in posicoes:
        c = por_id[p["connection_id"]]
        ausente = sem_saldo(p)
        motivos = {m for m, sim in (("banco_desatualizado", velha[c["id"]]), ("saldo_ausente", ausente),
                                    ("moeda_presumida", not p["currency_code"]),
                                    ("conta_fora_do_ultimo_sync", fora_do_sync(p, ultima))) if sim}
        topo |= motivos
        q = q_posicao(p)
        total += q
        for acc, k in ((por_tipo, tipo_da_posicao(p["type"], p["subtype"])),
                       (por_banco, c["institution_name"] or SEM_NOME), (por_cx, c["id"])):
            soma, ms = acc.get(k, (Decimal("0.00"), set()))
            acc[k] = (soma + q, ms | motivos)

    nome = lambda cid: por_id[cid]["institution_name"] or SEM_NOME  # noqa: E731
    fora_b = {}  # banco -> o que `separar_posicoes` deixou fora DAQUELE banco (suas linhas do espelho)
    for r in linhas:
        fora_b.setdefault(nome(r["connection_id"]), []).append(r)
    fora_b = {k: separar_posicoes(rs)[1] for k, rs in fora_b.items()}

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
        vazio = {"total": None, "por_tipo": [], "por_banco": [], "motivos": _ordem(topo)}
        if por_conexao:  # o `total` é `None` aqui: o `investido` de cada conexão também (Σ == total)
            vazio["por_conexao"] = {k: {**v, "valor": None}
                                    for k, v in _por_conexao(conexoes, vivas, velha, linhas, por_cx).items()}
        if not com_zero:
            return vazio
        # Banco só de conexão pausada existe (≠ sumiu): `null` com o motivo DELE, nunca R$ 0.
        pausados = {k: (Decimal("0.00"), _motivos_fora(f)) for k, f in fora_b.items() if _motivos_fora(f)}
        return {**vazio, "por_banco_com_zero": _partes(pausados, "banco", lambda k: {}, zeradas=True,
                                                        ordem=MOTIVOS_BANCO),
                "com_posicao": False, "bancos_com_posicao": set(),
                "conexoes_por_banco": {}}
    if total == 0 and "saldo_ausente" in topo:
        total = None  # soma 0 com um saldo desconhecido não é R$ 0: o critério da parte `null`
    dados = {
        "total": total,
        "por_tipo": _partes(por_tipo, "tipo", lambda k: {"rotulo": TIPOS[k]}),
        "por_banco": _partes(por_banco, "banco", lambda k: {}),
        "motivos": _ordem(topo),
    }
    if por_conexao:
        dados["por_conexao"] = _por_conexao(conexoes, vivas, velha, linhas, por_cx)
    if com_zero:  # banco lido que resgatou tudo (posições em 0 ou `TOTAL_WITHDRAWAL`) segue lá, em R$ 0
        acc = dict(por_banco)
        for c in vivas:  # todo item lido do banco conta (a ordem dos ids não decide o motivo)
            if c["last_sync_at"] and c["id"] in ultima:
                soma, ms = acc.get(nome(c["id"]), (Decimal("0.00"), set()))
                acc[nome(c["id"])] = (soma, ms | ({"banco_desatualizado"} if velha[c["id"]] else set()))
        resgatados = set()
        for k, f in fora_b.items():  # o dinheiro DAQUELE banco que ficou fora da soma
            lido, fora_k = k in acc, _motivos_fora(f)
            if lido or fora_k:  # banco só de conexão pausada também existe, com o motivo dele
                soma, ms = acc.get(k, (Decimal("0.00"), set()))
                acc[k] = (soma, ms | fora_k)
            if lido and f["resgatada"]:
                resgatados.add(k)
        dados["por_banco_com_zero"] = _partes(acc, "banco", lambda k: {}, zeradas=True, ordem=MOTIVOS_BANCO)
        # Quem tem posição DENTRO da soma (≠ só o que ficou fora): `db/metas.py` anula o número sem ela.
        # A resgatada de conexão viva é R$ 0 conhecido, não dinheiro que sumiu: conta como dentro.
        dados["com_posicao"] = bool(posicoes) or bool(fora["resgatada"])
        dados["bancos_com_posicao"] = set(por_banco) | resgatados
        # As conexões VIVAS com alguma posição VENCEDORA (`linhas`), por banco: quem compõe a fonte hoje,
        # e o que o ritmo (`db/metas_ritmo.py`) exige com foto no dia-base. Conexão que só perde
        # identidades para outra mais nova não entra; as fotos dela valem pela identidade, não por ela.
        dados["conexoes_por_banco"] = {}
        for r in linhas:
            if r["connection_status"] not in _TERMINAL:
                dados["conexoes_por_banco"].setdefault(nome(r["connection_id"]), set()).add(r["connection_id"])
    return dados


def ler(user_id: int) -> dict:
    """`calcular` num snapshot só (repeatable read, só leitura), como `GET /api/v2/contas`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        dados = calcular(cur, user_id)
        conn.rollback()
    return dados
