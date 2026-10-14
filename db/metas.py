"""
db/metas.py — as metas do /painel (`/api/v2/metas`): organização sobre dado do banco.

Meta não guarda dinheiro: tem nome, alvo, data-alvo opcional e a FONTE que o usuário
escolheu (P5): `investido` (tudo investido), `banco` (investido num banco) ou `conta`
(saldo de uma conta). O progresso (`atual`) é o valor de HOJE da fonte: a mesma soma de
`/investido` e `/contas` (`db/investido.py` e `db/contas_hoje.py`); a meta anula quando
nada da fonte ficou dentro do recorte. A reserva de emergência é a meta do tipo `reserva`:
alvo = `meses` × o custo mensal das recorrências de saída do banco, cartão
incluído (`previsao_recorrencias.custo_mensal`). Sem custo, `alvo` é `None` (nunca 0).

Toda query filtra `user_id`. Só lê o que já existe; nada aqui escreve em outra tabela.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

import psycopg.errors

from . import contas_hoje, investido, metas_ritmo
from .bank_movements import _lock_user
from .connection import get_conn

MESES_RESERVA = 6
CENTAVO = Decimal("0.01")
MOTIVOS_CUSTO = ("sem_banco_conectado", "sem_recorrencias", "recorrencias_banco_nao_lidas",
                 "plano_sem_custo_mensal", "recorrencia_valor_invalido")
MOTIVOS = tuple(dict.fromkeys(
    (*investido.MOTIVOS, *contas_hoje.MOTIVOS_CONTA, "fonte_indisponivel", "custo_desconhecido",
     "plano_sem_investimentos", "plano_sem_custo_mensal")))
# O ritmo é lido antes de os motivos do alvo da reserva entrarem: esses dois ele nunca emite.
MOTIVOS_RITMO = (*(m for m in MOTIVOS if m not in ("custo_desconhecido", "plano_sem_custo_mensal")),
                 *metas_ritmo.MOTIVOS_RITMO)
# Dinheiro que ficou FORA da soma (`outra_moeda`, `conexao_pausada`): exclusão parcial não anula o
# número (é o R$ de `/investido`, com o motivo, que a tela mostra "a conferir"); só vira `None` se
# NADA da fonte ficou dentro (R$ 0 sem posição em reais não é R$ 0). `saldo_ausente` já chega como
# `valor`/`total` `None`; os demais motivos acompanham o número.
FORA = frozenset({"outra_moeda", "conexao_pausada"})
_COLUNAS_FONTE = ("fonte_tipo", "fonte_ref", "fonte_banco", "fonte_numero", "fonte_tipo_conta",
                  "fonte_rotulo")


class MetaNaoEncontrada(LookupError):
    pass


class ReservaJaExiste(Exception):
    pass


class Invalida(ValueError):
    """Pedido que o formato deixou passar e o domínio recusa (vira 422)."""

    def __init__(self, campo: str, msg: str):
        super().__init__(msg)
        self.campo, self.msg = campo, msg


def _snapshot(cur) -> None:
    cur.execute("set transaction isolation level repeatable read, read only")


def _hoje():
    from utils_date import day_tz, now_tz

    return day_tz(now_tz())


def _ler_fontes(cur, user_id: int, tipos: set, investimentos: bool = True):
    inv = None
    if investimentos and tipos & {"investido", "banco"}:
        inv = investido.calcular(cur, user_id, com_zero=True)
    contas = contas_hoje.listar(cur, user_id)["contas"] if "conta" in tipos else None
    return inv, contas


def _conta(l: dict, contas: list) -> dict | None:
    """Pela identidade do provedor guardada. Se ela está numa conexão pausada/apagada (o
    `conexao_pausada` de `contas_hoje`) e há UMA conta VIVA com a mesma instituição + número +
    tipo/subtipo (poupança ≠ corrente), a viva vence (reconexão com ids novos); sem a identidade, só
    esse casamento. Ambíguo ou sem candidato: fica a conta antiga (com o motivo dela) ou nenhuma."""
    c = next((c for c in contas if c["provider_account_id"] == l["fonte_ref"]), None)
    if c is not None and "conexao_pausada" not in c["motivos"]:
        return c
    if l["fonte_numero"] and l["fonte_banco"]:
        chave = (l["fonte_banco"], l["fonte_numero"], l["fonte_tipo_conta"])
        cand = [x for x in contas if (x["instituicao"], x["numero"], x["tipo_conta"]) == chave
                and "conexao_pausada" not in x["motivos"]]
        if len(cand) == 1:
            return cand[0]
    return c


def _fonte(l: dict, inv, contas) -> tuple[Decimal | None, list, int | None]:
    """`(atual, motivos, conta_id)`. `atual` é `None` quando a fonte não dá número (conta
    pausada, saldo ausente, outra moeda, banco ou conta que sumiu): nunca R$ 0 inventado."""
    t = l["fonte_tipo"]
    if t != "conta" and inv is None:  # o plano não tem `investments`: o mesmo corte de `/investido`
        return None, ["plano_sem_investimentos"], None
    if t == "investido":
        sem = bool(FORA & set(inv["motivos"])) and not inv.get("com_posicao")
        return (None if sem else inv["total"]), inv["motivos"], None
    if t == "banco":
        # `com_zero`: o banco que resgatou tudo segue lá, em R$ 0 (≠ banco que sumiu).
        p = next((p for p in inv["por_banco_com_zero"] if p["banco"] == l["fonte_ref"]), None)
        # Só o que é DESTE banco: o `outra_moeda` de outro banco não é motivo dele; sem banco
        # vivo nenhum, o `sem_banco_conectado` de `investido` é o que explica.
        topo = [m for m in inv["motivos"] if m == "sem_banco_conectado"]
        if p is None:  # sem nenhuma linha no espelho: sumiu (banco só pausado tem `p`, com `conexao_pausada`)
            return None, ["fonte_indisponivel", *topo], None
        sem = bool(FORA & set(p["motivos"])) and p["banco"] not in inv["bancos_com_posicao"]
        return (None if sem else p["valor"]), [*p["motivos"], *topo], None
    c = _conta(l, contas)
    if c is None:
        return None, ["fonte_indisponivel"], None
    return (c["saldo"] if c["no_total"] else None), c["motivos"], c["id"]


def _meses_ate(hoje, alvo) -> int:
    """Meses do calendário de hoje até `alvo` (mês parcial conta cheio), no mínimo 1."""
    n = (alvo.year - hoje.year) * 12 + alvo.month - hoje.month + (alvo.day > hoje.day)
    return max(n, 1)


def _meta(l: dict, inv, contas, custo: dict, hoje, ritmo) -> dict:
    atual, motivos, conta_id = _fonte(l, inv, contas)
    motivos = set(motivos)
    r_valor, r_dias, r_motivos = ritmo(l, inv, atual, motivos)  # antes dos motivos do alvo da reserva
    alvo = l["alvo"]
    if l["tipo"] == "reserva":
        alvo = custo["valor"] * l["meses"] if custo["valor"] else None  # custo 0 = não sabe
        if alvo is None:
            motivos.add("plano_sem_custo_mensal" if "plano_sem_custo_mensal" in custo["motivos"]
                        else "custo_desconhecido")
    sabe = alvo is not None and atual is not None
    # `atual` fica como a tela dele (`/contas` não quantiza); `falta` e `por_mes` saem em centavos.
    falta = max(alvo - atual, Decimal(0)).quantize(CENTAVO, ROUND_HALF_UP) if sabe else None
    passou = bool(l["data_alvo"] and l["data_alvo"] < hoje)
    por_mes = None
    if falta is not None and l["data_alvo"] and not passou:
        por_mes = (falta / _meses_ate(hoje, l["data_alvo"])).quantize(CENTAVO, ROUND_CEILING)
    return {
        "id": l["id"], "nome": l["nome"], "tipo": l["tipo"], "meses": l["meses"],
        "data_alvo": l["data_alvo"].isoformat() if l["data_alvo"] else None,
        "atual": atual, "alvo": alvo, "falta": falta,
        # Piso e teto em 100: "100" só quando o alvo foi batido. Sem o `//` quando já bateu: com `atual`
        # enorme e alvo ínfimo o quociente passa de 28 dígitos (`InvalidOperation`).
        "percentual": (0 if atual <= 0 else 100 if atual >= alvo else int(atual * 100 // alvo)) if sabe else None,
        "por_mes": por_mes, "prazo_passou": passou,
        # O ritmo ATUAL (crescimento da fonte, com rendimento) ao lado do NECESSÁRIO (`por_mes`).
        "ritmo": {"valor": r_valor, "dias_de_historico": r_dias,
                  "parcial": r_valor is not None and r_dias < metas_ritmo.RITMO_DIAS_JANELA,
                  "motivos": [m for m in MOTIVOS_RITMO if m in r_motivos]},
        "chega_em": metas_ritmo.chega_em(falta, r_valor, hoje),
        "motivos": [m for m in MOTIVOS if m in motivos],
        "fonte": {"tipo": l["fonte_tipo"], "rotulo": l["fonte_rotulo"], "conta_id": conta_id,
                  "banco": l["fonte_ref"] if l["fonte_tipo"] == "banco" else l["fonte_banco"]},
    }


def listar(user_id: int, investimentos: bool = True, recorrencias: bool = True) -> dict:
    """As metas e o custo mensal, num snapshot só (repeatable read, só leitura).
    `investimentos=False` (plano sem o recurso): as fontes `investido`/`banco` saem sem número.
    `recorrencias=False` (plano sem `subscriptions`): o custo nem é lido e a reserva sai sem alvo."""
    from core.services.previsao_recorrencias import custo_mensal

    hoje = _hoje()
    with get_conn() as conn, conn.cursor() as cur:
        _snapshot(cur)
        cur.execute("select * from metas where user_id = %s order by id", (user_id,))
        linhas = cur.fetchall()
        inv, contas = _ler_fontes(cur, user_id, {l["fonte_tipo"] for l in linhas}, investimentos)
        custo = (custo_mensal(cur, user_id, hoje) if recorrencias
                 else {"valor": None, "recorrencias": 0, "motivos": ["plano_sem_custo_mensal"]})
        ritmo = metas_ritmo.leitor(cur, user_id, hoje)  # lê as fotos no MESMO snapshot
        metas = [_meta(l, inv, contas, custo, hoje, ritmo) for l in linhas]
        conn.rollback()
    return {"metas": metas, "custo_mensal": custo}


def resolver_fonte(user_id: int, tipo: str, banco: str | None, conta: int | None) -> dict:
    """A fonte pedida, resolvida DENTRO do que é do usuário (`fonte_*` prontas para gravar).
    `conta` é o `id` público de `/contas`; `banco`, o texto de `por_banco` de `/investido`."""
    if (tipo == "banco") != (banco is not None) or (tipo == "conta") != (conta is not None):
        raise Invalida("fonte", "Fonte incompleta ou com campo a mais.")
    f = dict.fromkeys(_COLUNAS_FONTE)
    f["fonte_tipo"] = tipo
    if tipo == "investido":
        f["fonte_rotulo"] = "Tudo investido"
        return f
    with get_conn() as conn, conn.cursor() as cur:
        _snapshot(cur)
        inv, contas = _ler_fontes(cur, user_id, {tipo})
        conn.rollback()
    if tipo == "banco":
        if all(p["banco"] != banco for p in inv["por_banco_com_zero"]):
            raise Invalida("fonte", "Banco não encontrado.")
        f["fonte_ref"], f["fonte_rotulo"] = banco, f"Investido · {banco}"
        return f
    c = next((c for c in contas if c["id"] == conta), None)
    if c is None or c["moeda"] != "BRL":
        raise Invalida("fonte", "Conta não encontrada ou fora do real.")
    f.update(fonte_ref=c["provider_account_id"], fonte_banco=c["instituicao"], fonte_numero=c["numero"],
             fonte_tipo_conta=c["tipo_conta"], fonte_rotulo=" · ".join(x for x in (c["instituicao"], c["nome"]) if x) or "Conta")
    return f


def criar(user_id: int, d: dict) -> int:
    """`d`: nome, tipo, alvo, meses, data_alvo e as `fonte_*` de `resolver_fonte`."""
    tipo = d["tipo"]
    if tipo == "meta" and (d["alvo"] is None or d["meses"] is not None):
        raise Invalida("alvo" if d["alvo"] is None else "meses", "Meta pede alvo e não tem meses.")
    if tipo == "reserva" and d["alvo"] is not None:
        raise Invalida("alvo", "A reserva calcula o alvo.")
    if d["data_alvo"] and d["data_alvo"] < _hoje():
        raise Invalida("data_alvo", "A data-alvo não pode estar no passado.")
    meses = d["meses"] if d["meses"] is not None else (MESES_RESERVA if tipo == "reserva" else None)
    d = {**d, "user_id": user_id, "meses": meses}
    with get_conn() as conn, conn.cursor() as cur:
        try:  # a reserva única é o índice `uq_metas_reserva`: vale na corrida e no merge de contas
            cur.execute(
                "insert into metas (user_id, nome, tipo, alvo, meses, data_alvo, fonte_tipo, fonte_ref,"
                " fonte_banco, fonte_numero, fonte_tipo_conta, fonte_rotulo) values (%(user_id)s,"
                " %(nome)s, %(tipo)s, %(alvo)s, %(meses)s, %(data_alvo)s, %(fonte_tipo)s, %(fonte_ref)s,"
                " %(fonte_banco)s, %(fonte_numero)s, %(fonte_tipo_conta)s, %(fonte_rotulo)s)"
                " returning id", d)
        except psycopg.errors.UniqueViolation:
            raise ReservaJaExiste() from None
        meta_id = cur.fetchone()["id"]
        conn.commit()
    return meta_id


def editar(user_id: int, meta_id: int, m: dict) -> None:
    """`m` leva só o que muda (`data_alvo: None` limpa). O tipo não muda, salvo reserva →
    meta com `alvo` (os `meses` saem). Campo igual ao gravado é 200 sem escrever. O cliente manda
    só o que mudou: reenviar o objeto do GET dá 422 (`meses: null` numa meta, `alvo` numa reserva)."""
    m = dict(m)
    with get_conn() as conn, conn.cursor() as cur:
        _lock_user(cur, user_id)
        cur.execute("select * from metas where id = %s and user_id = %s for update", (meta_id, user_id))
        atual = cur.fetchone()
        if atual is None:
            raise MetaNaoEncontrada()
        nova = m.get("data_alvo")  # só a data NOVA tem de ser futura: meta vencida reenvia a velha
        if nova and nova != atual["data_alvo"] and nova < _hoje():
            raise Invalida("data_alvo", "A data-alvo não pode estar no passado.")
        n = {**atual}
        tipo = m.pop("tipo", atual["tipo"])
        if tipo != atual["tipo"]:
            if (atual["tipo"], tipo) != ("reserva", "meta"):
                raise Invalida("tipo", "Meta não vira reserva.")
            if "alvo" not in m or "meses" in m:
                raise Invalida("alvo", "Converter em meta pede o alvo, sem meses.")
            n["meses"] = None
        elif "meses" in m and tipo == "meta" or "alvo" in m and tipo == "reserva":
            raise Invalida("meses" if tipo == "meta" else "alvo", "Campo que este tipo não tem.")
        n.update(m, tipo=tipo)
        if n == atual:
            return
        cur.execute(
            "update metas set nome = %(nome)s, tipo = %(tipo)s, alvo = %(alvo)s, meses = %(meses)s,"
            " data_alvo = %(data_alvo)s, fonte_tipo = %(fonte_tipo)s, fonte_ref = %(fonte_ref)s,"
            " fonte_banco = %(fonte_banco)s, fonte_numero = %(fonte_numero)s,"
            " fonte_tipo_conta = %(fonte_tipo_conta)s, fonte_rotulo = %(fonte_rotulo)s,"
            " atualizada_em = now()"
            " where id = %(id)s and user_id = %(user_id)s", n)
        conn.commit()


def apagar(user_id: int, meta_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from metas where id = %s and user_id = %s returning id", (meta_id, user_id))
        if cur.fetchone() is None:
            raise MetaNaoEncontrada()
        conn.commit()
