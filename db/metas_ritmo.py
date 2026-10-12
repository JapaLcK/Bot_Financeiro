"""
db/metas_ritmo.py — o ritmo de uma meta (`/api/v2/metas`): quanto a fonte cresce POR MÊS, medido nas
fotos por posição do Open Finance (`open_finance_investment_snapshots`, `db/of_snapshots.py`), não nas
fotos do patrimônio (que só têm o total, sem a divisão por banco).

Ritmo = (valor de hoje − valor em D0) ÷ dias × 30. O valor de um dia sai da MESMA regra de
`db/investido.calcular` (recorte `separar_posicoes`, posição quantizada por `q_posicao`). Só lê, pelo
cursor recebido (o snapshot de `db/metas.listar`); toda query filtra o dono da conexão.

D0 = o dia mais antigo de [hoje − 90, hoje) em que TODAS as conexões que compõem a fonte HOJE (as que têm
posição vencedora) têm foto confirmada. As fotos do passado cobrem as MESMAS posições de hoje, por
identidade do provedor: a posição que mudou de conexão (a → c) vale pela foto da antiga. Conexão que entrou depois empurra D0 para a
primeira foto dela (a série recomeça); conexão apagada leva as fotos em cascata e a série recomeça
sozinha; conexão pausada fica fora nas duas pontas. Posição que entra ou sai dentro da MESMA conexão
não quebra a série: é crescimento (ou dinheiro que saiu).

Limites declarados: NÃO separa aporte de rendimento (o ritmo é o crescimento do saldo e inclui
rendimento e variação); portabilidade de outro banco conta como crescimento; a fonte `conta` não tem
histórico de saldo (reconstruir pelas transações é proibido), então o ritmo dela é `null`.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

from .investido import CENTAVO, q_posicao
from .patrimonio import finito, separar_posicoes

RITMO_DIAS_MINIMOS = 7  # com menos histórico que isto o ritmo é `null` (`historico_curto`)
RITMO_DIAS_JANELA = 90  # a janela; com menos dias o número sai `parcial`
# `historico_curto`: o tempo resolve (sem dia comum a todas as conexões da fonte na janela, ou < RITMO_DIAS_MINIMOS).
# `historico_incompleto`: existe dia da janela com foto de TODAS as conexões da fonte e nenhum serve de base,
# então falta confirmação (conexão que nunca confirma a coleta): esperar não resolve. Conexões que sincronizam em
# dias alternados nunca têm dia comum e ficam `historico_curto` (hipótese, não provada com dado real).
MOTIVOS_RITMO = ("historico_curto", "historico_incompleto", "sem_historico_da_fonte")

# O recorte de `POSICOES_BANCO_SQL` (uma por identidade do provedor, a conexão mais nova) sobre as fotos de
# um dia, UM só para as duas SQL abaixo. `ganha` = a vencedora de HOJE por identidade, entre TODAS as
# conexões do dono (o mesmo `c.id desc` do espelho). A foto PERTENCE à fonte (`f` = as conexões que a
# compõem hoje) quando a vencedora da identidade está nela: a foto pode vir de qualquer conexão do dono
# (a identidade que mudou de conexão tinha foto na antiga), mas a de uma identidade cuja vencedora fica fora
# (pausada, outro banco) não conta no passado, como não conta hoje. Identidade fora do espelho (a posição
# já saiu) não tem vencedora: vale a foto de conexão da fonte.
_DE = """
    with ganha as (
        select distinct on (i.provider_investment_id) i.provider_investment_id, i.connection_id
          from open_finance_investments i
          join open_finance_connections c on c.id = i.connection_id
         where c.user_id = %(u)s
         order by i.provider_investment_id, c.id desc
    )
    select {cols}
      from open_finance_investment_snapshots s
      join open_finance_connections c on c.id = s.connection_id
      left join ganha g on g.provider_investment_id = s.provider_investment_id
      {extra}
     where c.user_id = %(u)s
       and (g.connection_id = any(%(f)s) or (g.connection_id is null and c.id = any(%(f)s)))
"""

# A foto não tem moeda: vem do espelho da conexão da própria foto; sem linha (a posição já saiu) é BRL
# presumido (`currency_code` nulo vira `moeda_presumida`).
_FOTOS_SQL = _DE.format(cols="""distinct on (s.provider_investment_id)
        s.balance, upper(coalesce(s.status, '')) as status,
        upper(coalesce(i.currency, 'BRL')) as currency,
        i.raw->>'currencyCode' as currency_code,
        upper(coalesce(c.status, '')) as connection_status""", extra="""left join open_finance_investments i
        on i.connection_id = s.connection_id and i.provider_investment_id = s.provider_investment_id""") + """
       and s.observed_on = %(d)s and s.collection_confirmed
     order by s.provider_investment_id, c.id desc
"""

_JANELA_SQL = " and s.observed_on >= %(ini)s and s.observed_on < %(hoje)s"

# Dia comum = TODA conexão da fonte tem foto (de qualquer confirmação). D0 só serve se, além disso, TODA foto que pertence à fonte é confirmada: dia
# misto (uma posição confirmada, outra não) subestimaria o valor de D0 e inflaria o ritmo. Foto de quem não
# pertence à fonte (pausada, outro banco) não decide o dia.
_DIA_COMUM_SQL = _DE.format(cols="s.observed_on", extra="") + _JANELA_SQL + """
     group by s.observed_on
    having count(distinct c.id) filter (where c.id = any(%(f)s)) = %(n)s"""
_DIA_BASE_SQL = _DIA_COMUM_SQL + " and bool_and(s.collection_confirmed) order by s.observed_on limit 1"


def _q(p) -> Decimal:
    """`q_posicao`; saldo que o `quantize` não alcança (≥ 1e26, precisão 28) é foto ilegível, como o não
    finito: vira `saldo_ausente` em vez de derrubar as metas do usuário."""
    try:
        return q_posicao(p)
    except InvalidOperation:
        p["balance"] = None
        return Decimal("0.00")


def valor_em(cur, user_id: int, conexoes, dia) -> tuple[Decimal | None, set]:
    """O que as `conexoes` valiam em `dia` (a regra de `investido.calcular`) e os motivos da dúvida.
    Soma 0 com algum saldo não finito é `None` (não se sabe), como no total de hoje."""
    cur.execute(_FOTOS_SQL, {"u": user_id, "f": list(conexoes), "d": dia})
    posicoes, _ = separar_posicoes(cur.fetchall())
    total = sum((_q(p) for p in posicoes), Decimal("0.00"))
    motivos = {m for m, sim in (("saldo_ausente", any(not finito(p["balance"]) for p in posicoes)),
                                ("moeda_presumida", any(not p["currency_code"] for p in posicoes))) if sim}
    return (None if total == 0 and "saldo_ausente" in motivos else total), motivos


def _base(cur, user_id: int, conexoes: frozenset, hoje):
    """`(D0, valor em D0, motivos)`; `D0` é `None` quando nenhum dia da janela serve de base, com
    `historico_incompleto` só se existe dia com foto de todas as conexões (de qualquer confirmação)."""
    q = {"u": user_id, "f": list(conexoes), "n": len(conexoes), "hoje": hoje,
         "ini": hoje - timedelta(days=RITMO_DIAS_JANELA)}
    cur.execute(_DIA_BASE_SQL, q)
    r = cur.fetchone()
    if r is None:
        cur.execute(_DIA_COMUM_SQL + " limit 1", q)
        return None, None, ({"historico_incompleto"} if cur.fetchone() else set())
    return (r["observed_on"], *valor_em(cur, user_id, conexoes, r["observed_on"]))


def leitor(cur, user_id: int, hoje):
    """`ritmo(meta, inv, atual, motivos) -> (valor, dias_de_historico, motivos)`. Metas da mesma
    composição de conexões dividem a leitura (`cache`)."""
    cache = {}

    def ritmo(l: dict, inv, atual, motivos: set):
        if l["fonte_tipo"] == "conta":
            return None, None, motivos | {"sem_historico_da_fonte"}
        if atual is None:  # a fonte nem dá o número de hoje: os motivos dela explicam
            return None, None, motivos
        por_banco = inv["conexoes_por_banco"]
        conexoes = frozenset(por_banco.get(l["fonte_ref"], ()) if l["fonte_tipo"] == "banco"
                             else set().union(*por_banco.values()))
        if not conexoes:  # o banco perdeu todas as identidades para outro: esperar não traz base nenhuma
            return None, None, motivos | {"historico_incompleto"}
        # ponytail: uma leitura por composição distinta; um lote só se o nº de metas distintas crescer.
        if conexoes not in cache:
            cache[conexoes] = _base(cur, user_id, conexoes, hoje)
        d0, v0, m0 = cache[conexoes]
        if d0 is None:
            return None, None, motivos | (m0 or {"historico_curto"})
        dias, motivos = (hoje - d0).days, motivos | m0
        if dias < RITMO_DIAS_MINIMOS:
            return None, dias, motivos | {"historico_curto"}
        if v0 is None:
            return None, dias, motivos
        try:
            return ((atual - v0) / dias * 30).quantize(CENTAVO, ROUND_HALF_UP) + 0, dias, motivos  # + 0: sem "-0.00"
        except InvalidOperation:  # ≥ 1e26 por mês: saldo fora de qualquer realidade, ilegível como o de `_q`
            return None, dias, motivos | {"saldo_ausente"}

    return ritmo


def chega_em(falta, ritmo, hoje) -> str | None:
    """`AAAA-MM` em que a falta fecha, no ritmo de hoje (mês de 30 dias, como o ritmo). `null` sem
    falta (alvo batido ou desconhecido), sem ritmo ou com ritmo que não cresce."""
    if not falta or ritmo is None or ritmo <= 0:
        return None
    try:
        return (hoje + timedelta(days=int((falta / ritmo * 30).to_integral_value(ROUND_CEILING)))).strftime("%Y-%m")
    except OverflowError:  # ritmo ínfimo: depois do ano 9999
        return None
