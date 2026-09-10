"""`db/pix_charges.py` — o snapshot financeiro da venda Pix (§3.2, §10, §11).

Três coisas aqui decidem DINHEIRO, e cada uma tem grupo próprio com o negativo
rodado (desligar o conserto, ver vermelho, repor):

  1. **uma cobrança ativa por usuário**, garantida pelo índice parcial. Duas
     seriam duas cobranças precificadas contra o MESMO crédito (§10, nº 6).
  2. **transição condicional**: o `returning` vazio diz que o estado já
     avançou — e **nada mais**. Quem decide efeito é o registro do efeito, e
     isso mora em `tests/test_pix_transicao_efeitos.py` (P1-A).
  3. **a FK `set null`**: a linha sobrevive à exclusão da conta,
     pseudonimizada (§13.2). Com `cascade`, a prova do pagamento some junto com
     o titular.

CONTROLES NEGATIVOS MEDIDOS (rodados nesta ordem, com restauração por `cp` de
backup — nunca `git checkout --`, que come trabalho não commitado):

  * tire o `where status = any(%s)` de `transicionar` →
    `test_transicao_de_status_errado_nao_aplica` vermelho;
  * troque o `on conflict … do nothing` de `criar_cobranca` por um insert seco →
    `test_segunda_cobranca_ativa_do_mesmo_dono_e_recusada` vermelho;
  * tire `"pix_charges"` de `_USER_FK_SET_NULL_TABLES` →
    `test_exclusao_da_conta_pseudonimiza_em_vez_de_apagar` vermelho.

POSITIVOS do grupo (o que impede um código que RECUSA TUDO de passar):
`test_cobranca_encerrada_nao_bloqueia_venda_nova`,
`test_transicao_do_status_esperado_aplica`, e a leitura por `public_token` do
próprio dono.
"""

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from db.connection import get_conn
from db.pix_charges import (
    ESTADOS_ATIVOS,
    buscar_por_asaas_payment_id,
    buscar_por_external_reference,
    buscar_por_public_token,
    criar_cobranca,
    transicionar,
)
# `attach_pagamento` mudou de módulo no 1b-B (§2.2): o `rastreio` de
# `criar_cobranca` estourou o teto de 350 linhas e a saga saiu inteira.
from db.pix_charges_saga import attach_pagamento


def _nova(user_id: int, **kw) -> dict | None:
    """Uma cobrança plausível. `public_token` é único por chamada — a unique é
    global, e reusar valor entre testes faria a falha aparecer no teste errado."""
    # 29900 aqui é VALOR DE FIXTURE, não o preço de nenhum plano — o anual do
    # `pro_max` é R$ 499 (`tests/test_pix_pricing_contrato.py` lê da fonte).
    # `external_reference` NÃO é passado: quem o gera é a própria função, no
    # formato `pix:<id>` que o dreno exige (P1-3).
    campos = dict(
        public_token=secrets.token_urlsafe(16),
        plan="pro_max", plan_stored="pro_max",
        price_cents=29900, credit_cents=0, amount_cents=29900,
        duration_days=365,
    )
    campos.update(kw)
    return criar_cobranca(user_id, **campos)


# A janela de acesso, que o CHECK `pix_charges_pago_tem_janela` passou a EXIGIR
# de toda cobrança COM TITULAR que ganha `paid_at`. As três formulações medidas
# estão em `tests/test_pix_invariantes_do_banco.py`. Aqui é fixture: quem calcula
# a vigência é `plano_da_cobranca` (`core/services/pix_pricing.py`), que é 1b-A e
# está neste mesmo PR; quem CARIMBA `access_starts_at`/`access_expires_at` na
# linha é o dreno do webhook, que é 1b-B e ainda não existe.
_JANELA = dict(access_starts_at=datetime.now(timezone.utc),
               access_expires_at=datetime.now(timezone.utc) + timedelta(days=365))


# ── 1. uma cobrança ativa por usuário ────────────────────────────────────────

def test_primeira_cobranca_nasce_em_draft(user_id):
    linha = _nova(user_id)
    assert linha is not None
    assert linha["status"] == "draft"
    assert linha["user_id"] == user_id
    assert linha["amount_cents"] == 29900
    assert linha["currency"] == "BRL"
    assert linha["duration_days"] == 365
    # `access_starts_at` é decidido NO PAGAMENTO (§7), não na criação.
    assert linha["access_starts_at"] is None
    assert linha["access_expires_at"] is None


def test_segunda_cobranca_ativa_do_mesmo_dono_e_recusada(user_id):
    """O furo financeiro do §10: duas cobranças ativas seriam precificadas
    contra o mesmo crédito. Quem recusa é o BANCO (`uniq_pix_charge_ativa`), não
    um `select` antes do `insert` — entre os dois cabe a segunda requisição.

    `None` é a corrida perdida, e é o que faz o chamador cair no caminho de
    substituição (que cancela no Asaas ANTES de criar a nova).
    """
    assert _nova(user_id) is not None
    assert _nova(user_id) is None


@pytest.mark.parametrize("terminal", ["paid", "canceled", "expired"])
def test_cobranca_encerrada_nao_bloqueia_venda_nova(user_id, terminal):
    """POSITIVO — sem ele o grupo passaria num código que recusa TODA segunda
    cobrança, que é pior que o bug: o cliente que já pagou um ano não
    conseguiria renovar nunca mais.

    O índice é PARCIAL de propósito: só `ESTADOS_ATIVOS` ocupam a vaga.
    """
    primeira = _nova(user_id)
    assert transicionar(primeira["id"], de="draft", para=terminal,
                        **(_JANELA if terminal == "paid" else {})) is not None
    assert _nova(user_id) is not None


def test_estados_ativos_batem_com_o_indice_parcial():
    """FONTE ÚNICA (CLAUDE.md §0.7): `ESTADOS_ATIVOS` e o predicado do índice no
    banco são a MESMA lista. Se divergirem, o `on conflict` deixa de casar o
    índice e o Postgres levanta em runtime — na venda, não aqui.

    Lê o predicado do BANCO (`pg_get_expr`), e não o texto de `db/schema.py`:
    é o índice que de fato existe que decide.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select pg_get_expr(indpred, indrelid) as pred from pg_index"
            " where indexrelid = 'uniq_pix_charge_ativa'::regclass"
        )
        pred = cur.fetchone()["pred"]
    assert pred, "uniq_pix_charge_ativa deixou de ser índice PARCIAL"
    for estado in ESTADOS_ATIVOS:
        assert f"'{estado}'" in pred, f"{estado} saiu do predicado do índice: {pred}"


# ── 2. transição condicional ─────────────────────────────────────────────────

def test_transicao_do_status_esperado_aplica(user_id):
    """POSITIVO do par."""
    linha = _nova(user_id)
    assert attach_pagamento(linha["id"], "pay_123") is True
    nova = transicionar(linha["id"], de="pending", para="paid",
                        asaas_payment_id="pay_123", **_JANELA)
    assert nova is not None
    assert nova["status"] == "paid"
    assert nova["paid_at"] is not None


def test_transicao_de_status_errado_nao_aplica(user_id):
    """O `returning` vazio diz **só** que o estado já avançou.

    **Não** diz "pule os efeitos" — essa regra foi removida (P1-A). Quem impede
    o segundo `purchase` no GA4 é o registro do par `(asaas_payment_id, 'ga4')`
    em `pix_payment_effects`, não esta função. O teste que prova isso é
    `test_efeito_roda_na_RETENTATIVA_mesmo_com_a_transicao_ja_commitada`.
    """
    linha = _nova(user_id)
    assert transicionar(linha["id"], de="draft", para="paid", **_JANELA) is not None
    assert transicionar(linha["id"], de="draft", para="paid", **_JANELA) is None
    # E o estado NÃO foi corrompido pela tentativa recusada.
    assert buscar_por_external_reference(linha["external_reference"])["status"] == "paid"


def test_transicao_aceita_varias_origens_da_mesma_celula(user_id):
    """Pagamento tardio (§11): `RECEIVED` concede a partir de `pending`,
    `canceling`, `canceled` E `expired` — a MESMA célula, quatro origens. Passar
    a tupla é o que evita quatro chamadas com quatro `if` no dreno."""
    linha = _nova(user_id)
    assert transicionar(linha["id"], de="draft", para="expired") is not None
    nova = transicionar(linha["id"],
                        de=("pending", "canceling", "canceled", "expired"),
                        para="paid", **_JANELA)
    assert nova is not None and nova["status"] == "paid"


def test_transicao_terminal_apaga_o_qr(user_id):
    """§13.6: o `qr_payload_enc` é o "copia e cola" que MOVE DINHEIRO. Depois de
    `paid`/`canceled`/`expired` confirmado não há uso legítimo dele, e o que
    fica no banco é instrumento ao portador esperando um dump."""
    linha = _nova(user_id)
    assert attach_pagamento(linha["id"], "pay_qr", qr_payload_enc="gAAAA-cifrado")
    assert buscar_por_external_reference(
        linha["external_reference"]) is not None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select qr_payload_enc from pix_charges where id = %s", (linha["id"],))
        assert cur.fetchone()["qr_payload_enc"] == "gAAAA-cifrado"

    transicionar(linha["id"], de="pending", para="paid", apagar_qr=True, **_JANELA)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select qr_payload_enc from pix_charges where id = %s", (linha["id"],))
        assert cur.fetchone()["qr_payload_enc"] is None


def test_attach_e_idempotente(user_id):
    """O estado AMBÍGUO do §10: o POST ao Asaas pode ter efetivado com a resposta
    perdida, então a varredura reconcilia e chama `attach`. Duas passadas não
    podem sobrescrever o id que a primeira gravou — a segunda devolve False."""
    linha = _nova(user_id)
    assert attach_pagamento(linha["id"], "pay_A") is True
    assert attach_pagamento(linha["id"], "pay_B") is False
    assert buscar_por_asaas_payment_id("pay_A")["id"] == linha["id"]
    assert buscar_por_asaas_payment_id("pay_B") is None


# ── 3. isolamento por usuário (CLAUDE.md §0) ─────────────────────────────────

def test_public_token_de_um_dono_nao_vaza_para_outro(user_id):
    """O `public_token` chega na URL do poll — ou seja, VEM DO CLIENTE. Sem
    `user_id = %s`, um token vazado devolve plano, valores e datas de outra
    pessoa.

    Negativo: tire o `user_id = %s` de `buscar_por_public_token` → este teste
    fica vermelho com a cobrança do outro dono na mão.
    """
    outro = int(uuid.uuid4().int % 10_000_000_000)
    from conftest import _cleanup_user
    from db import ensure_user

    ensure_user(outro)
    try:
        alheia = _nova(outro)
        assert buscar_por_public_token(user_id, alheia["public_token"]) is None
        # POSITIVO: o dono legítimo LÊ. Sem esta linha o teste passaria num
        # código que devolve None para todo mundo.
        assert buscar_por_public_token(outro, alheia["public_token"])["id"] == alheia["id"]
    finally:
        _cleanup_user(outro)


def test_asaas_payment_id_nao_serve_de_token_publico(user_id):
    """§13.6, caso 42b: o identificador que sai do servidor é o `public_token`.
    Buscar pelo `asaas_payment_id` na função do poll tem de dar nada — é o que
    garante que o id do provedor não vira `sid` na URL nem `eventID` do pixel da
    Meta."""
    linha = _nova(user_id)
    attach_pagamento(linha["id"], "pay_secreto")
    assert buscar_por_public_token(user_id, "pay_secreto") is None


# ── 4. pseudonimização pelo banco (§13.2) ────────────────────────────────────

def test_exclusao_da_conta_pseudonimiza_em_vez_de_apagar(user_id):
    """Caso 43 do §16. A linha SOBREVIVE com `user_id is null`, e valores, ids e
    datas ficam — é o que reconcilia dinheiro que já entrou.

    Quem garante é o BANCO (FK `on delete set null`), não um UPDATE no job de
    exclusão: o UPDATE perde a corrida com um webhook que commite depois, e a
    varredura pós-commit nunca revisita a tabela (mesmo raciocínio da
    `plan_trials`, `db/schema_repairs.py`).

    Negativo MEDIDO: tirando `"pix_charges"` de `_USER_FK_SET_NULL_TABLES`, o
    `repair_user_fk_cascades` converte a FK para CASCADE no `init_db` seguinte e
    o `select` abaixo não acha nada.
    """
    from conftest import _cleanup_user

    linha = _nova(user_id)
    ref = linha["external_reference"]
    _cleanup_user(user_id)

    sobrevivente = buscar_por_external_reference(ref)
    assert sobrevivente is not None, "CASCADE apagou a prova do pagamento"
    assert sobrevivente["user_id"] is None
    assert sobrevivente["amount_cents"] == 29900
    assert sobrevivente["plan_stored"] == "pro_max"


def test_fk_de_pix_charges_e_set_null_depois_do_repair():
    """A declaração no DDL não basta: `repair_user_fk_cascades` roda em TODO
    `init_db` e reescreve o `on delete` de toda FK em `users(id)`. Sem
    `"pix_charges"` no conjunto, ele converte a FK declarada `set null` em
    CASCADE na primeira subida — em silêncio.

    Mede o estado REAL depois do reparo, que é quem vale em runtime.
    """
    from db.schema_repairs import repair_user_fk_cascades

    with get_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            repair_user_fk_cascades(cur)
            cur.execute(
                "select confdeltype from pg_constraint"
                " where contype = 'f' and conrelid = 'pix_charges'::regclass"
                "   and confrelid = 'users'::regclass"
            )
            row = cur.fetchone()
    assert row is not None, "pix_charges perdeu a FK para users(id)"
    assert row["confdeltype"] == "n", "FK virou CASCADE — a pseudonimização morreu"


def test_criar_cobranca_gera_a_referencia_no_formato_do_dreno(user_id):
    """P1-3: a referência deixou de ser parâmetro. `pix:<id>` com o `id` da
    própria linha, que é o que `^pix:[0-9]+$` exige e o que o dreno usa para
    saber que o dinheiro é NOSSO."""
    linha = _nova(user_id)
    assert linha["external_reference"] == f"pix:{linha['id']}"
    assert re.fullmatch(r"pix:[0-9]+", linha["external_reference"])
