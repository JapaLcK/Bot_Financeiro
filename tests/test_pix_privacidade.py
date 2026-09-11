"""O que a exclusão da conta faz com o Pix — e o DDL sem o qual ela não faz nada.

Caso 43 do §16 do plano: a linha de `pix_charges` **sobrevive** à exclusão (é o
registro do dinheiro que entrou, e reconciliar pagamento é obrigação fiscal),
com `user_id is null` posto pelo BANCO, e com o que não é registro financeiro
apagado — rastreio publicitário, QR e o id do cliente no provedor.

**Os dois assuntos estão no mesmo arquivo porque são a mesma falha.** O UPDATE
de `delete_user_data` cita `ga_client_id`, `fbp` e `fbc`; se essas colunas
nunca chegarem à `pix_charges` **que já existe em produção**, o UPDATE não
purga nada — ele estoura com `UndefinedColumn` e derruba a exclusão da conta
inteira. `create table if not exists` NÃO acrescenta coluna nem constraint a
tabela existente, e foi exatamente assim que quatro das cinco invariantes do
1b-A existiram só no `create table` e zero no `pigbank_ci_test`
(`tests/test_pix_invariantes_do_banco.py`). Aqui o mesmo teto é medido para o
que o 1b-B acrescenta.

CONTROLES NEGATIVOS MEDIDOS:

  * tire `ga_client_id = null, fbp = null, fbc = null` do UPDATE de
    `delete_user_data` → `test_exclusao_apaga_o_rastreio_e_o_qr` VERMELHO;
  * troque os três `alter table … add column` por colunas inline no
    `create table if not exists pix_charges` →
    `test_init_db_poe_o_rastreio_em_tabela_que_JA_EXISTE` VERMELHO, sozinho;
  * idem para o par `drop`+`add` do `pix_unmatched_ref_formato` →
    `test_init_db_poe_o_check_da_tabela_nova_que_JA_EXISTE` VERMELHO.

POSITIVOS: `test_exclusao_preserva_o_dinheiro` (sem ele o grupo passaria numa
exclusão que apaga a cobrança inteira, que é pior que o bug) e
`test_pix_unmatched_aceita_referencia_nossa` (sem ele o `check` podia recusar
tudo e a fila de conciliação nunca receberia linha).
"""

import secrets
import uuid

import pytest
from psycopg import errors

from db.connection import get_conn
from db.pix_charges import criar_cobranca
from db.privacy import build_user_export_zip, delete_user_data

RASTREIO = ("ga_client_id", "fbp", "fbc")


def _nova(user_id: int) -> dict:
    linha = criar_cobranca(
        user_id, public_token=secrets.token_urlsafe(16), plan="pro_max",
        plan_stored="pro_max", price_cents=29900, credit_cents=0,
        amount_cents=29900, duration_days=365,
    )
    assert linha is not None
    return linha


def _sujar(charge_id: int) -> None:
    """Põe na linha exatamente o que a exclusão tem de apagar. Sem isto o teste
    passaria por construção — colunas que nascem nulas ficam nulas."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pix_charges"
                "   set ga_client_id = %s, fbp = %s, fbc = %s,"
                "       qr_payload_enc = %s, asaas_customer_id = %s"
                " where id = %s",
                ("GA1.1.123.456", "fb.1.1700.9", "fb.1.1700.AbC",
                 "enc:00020126...copia-e-cola", "cus_000123", charge_id),
            )
        conn.commit()


def _por_ref(external_reference: str) -> dict | None:
    """`select *` cru, e não `buscar_por_external_reference`: aquela função lista
    as colunas uma a uma e não conhece as três do rastreio (o dreno as lê no
    commit que o traz). O que se mede aqui é o estado da LINHA."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from pix_charges where external_reference = %s",
                    (external_reference,))
        return cur.fetchone()


def _colunas(tabela: str) -> set[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns"
            " where table_name = %s", (tabela,),
        )
        return {linha["column_name"] for linha in cur.fetchall()}


def _checks(tabela: str) -> set[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select conname from pg_constraint"
                    " where conrelid = %s::regclass and contype = 'c'", (tabela,))
        return {linha["conname"] for linha in cur.fetchall()}


# ── caso 43: a exclusão da conta ─────────────────────────────────────────────

def test_exclusao_apaga_o_rastreio_e_o_qr(user_id):
    """As cinco colunas que NÃO são registro financeiro somem, e `purged_at` é
    carimbado.

    `ga_client_id`/`fbp`/`fbc` são os identificadores com que GA e Meta
    reidentificam a pessoa e não reconciliam centavo nenhum; `qr_payload_enc` é
    instrumento ao portador (§13.6); `asaas_customer_id` liga a linha ao
    cadastro dela no provedor.
    """
    linha = _nova(user_id)
    _sujar(linha["id"])
    ref = linha["external_reference"]

    delete_user_data(user_id)

    sobrevivente = _por_ref(ref)
    assert sobrevivente is not None, "a exclusão apagou o registro do pagamento"
    for coluna in RASTREIO + ("qr_payload_enc", "asaas_customer_id"):
        assert sobrevivente[coluna] is None, f"{coluna} sobreviveu à exclusão"
    assert sobrevivente["purged_at"] is not None


def test_exclusao_preserva_o_dinheiro(user_id):
    """POSITIVO. O vínculo some pela FK `on delete set null`; valores, ids e
    datas ficam — é o que reconcilia dinheiro que já entrou.

    Sem este caso o grupo passaria numa exclusão que apaga a cobrança inteira.
    """
    linha = _nova(user_id)
    ref, token = linha["external_reference"], linha["public_token"]

    resultado = delete_user_data(user_id)
    assert resultado["deleted"] is True   # concluiu, e sem `leftovers`

    sobrevivente = _por_ref(ref)
    assert sobrevivente["user_id"] is None
    assert sobrevivente["amount_cents"] == 29900
    assert sobrevivente["price_cents"] == 29900
    assert sobrevivente["plan_stored"] == "pro_max"
    assert sobrevivente["public_token"] == token
    assert sobrevivente["created_at"] is not None


# ── §13.2: a VARREDURA, que é o outro lado do UPDATE acima ──────────────────
#
# O UPDATE de `delete_user_data` não é a garantia: quem desfaz o vínculo é a FK
# `on delete set null`, e entre o UPDATE e o `delete from users` cabe um webhook
# que commite depois. A linha fica órfã com o rastreio VIVO e `purged_at` nulo, e
# a exclusão nunca revisita a tabela. É essa linha que a varredura alcança.

def _orfanar(charge_id: int) -> None:
    """A corrida, reproduzida: `user_id` cai para NULL (é o que a FK faz) e o
    rastreio continua lá, com `purged_at` nulo — o estado que o UPDATE perdeu."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set user_id = null, purged_at = null"
                    " where id = %s", (charge_id,))
        conn.commit()


def test_varredura_rezera_o_rastreio_da_cobranca_orfa(user_id):
    """DISCRIMINA. A cobrança perdeu o dono sem passar pelo UPDATE, e é a
    varredura diária quem a limpa.

    *Negativo: apague o corpo de `rezerar_rastreio_de_orfas` → as cinco colunas
    continuam preenchidas e `purged_at` nulo, que é PII de quem pediu exclusão
    sobrevivendo para sempre. **Tirar a CHAMADA de `purgar_retencao` não deixa
    este caso vermelho** — ele chama a folha direto; quem mede o elo é
    `test_purgar_retencao_roda_as_DUAS_purgas`, em `tests/test_pix_outbox_purga.py`.*
    """
    from db.pix_charges import rezerar_rastreio_de_orfas

    linha = _nova(user_id)
    _sujar(linha["id"])
    _orfanar(linha["id"])

    assert rezerar_rastreio_de_orfas() >= 1

    depois = _por_ref(linha["external_reference"])
    for coluna in RASTREIO + ("qr_payload_enc", "asaas_customer_id"):
        assert depois[coluna] is None, f"{coluna} sobreviveu à varredura"
    assert depois["purged_at"] is not None
    assert depois["amount_cents"] == 29900, "a varredura mexeu no dinheiro"


def test_varredura_nao_toca_em_cobranca_COM_DONO(user_id):
    """POSITIVO, e sem ele o grupo passaria numa varredura que zera a tabela
    inteira — que apagaria o QR de todo cliente que está com a tela aberta.

    `user_id is null` no `where` é o que separa os dois casos.
    """
    from db.pix_charges import rezerar_rastreio_de_orfas

    linha = _nova(user_id)
    _sujar(linha["id"])

    rezerar_rastreio_de_orfas()

    viva = _por_ref(linha["external_reference"])
    assert viva["ga_client_id"] == "GA1.1.123.456"
    assert viva["qr_payload_enc"] is not None
    assert viva["purged_at"] is None


def test_varredura_repetida_nao_reescreve_o_carimbo(user_id):
    """`purged_at is null` no `where`: sem ela a varredura DIÁRIA reescreveria o
    carimbo todo dia e `purged_at` deixaria de datar a purga."""
    from db.pix_charges import rezerar_rastreio_de_orfas

    linha = _nova(user_id)
    _sujar(linha["id"])
    _orfanar(linha["id"])

    rezerar_rastreio_de_orfas()
    primeiro = _por_ref(linha["external_reference"])["purged_at"]
    assert primeiro is not None

    rezerar_rastreio_de_orfas()
    assert _por_ref(linha["external_reference"])["purged_at"] == primeiro


def test_export_traz_a_cobranca_e_nao_traz_o_QR(user_id):
    """O export ganha a cobrança (§14 item 15) com colunas NOMEADAS: o
    `qr_payload_enc` fica de fora porque é o "copia e cola" que MOVE dinheiro —
    um `select *` o mandaria dentro do ZIP."""
    import json
    import zipfile
    import io

    linha = _nova(user_id)
    _sujar(linha["id"])

    with zipfile.ZipFile(io.BytesIO(build_user_export_zip(user_id))) as zf:
        dados = json.loads(zf.read("dados.json"))["dados"]

    cobrancas = dados["cobrancas_pix"]
    assert len(cobrancas) == 1
    assert cobrancas[0]["external_reference"] == linha["external_reference"]
    assert cobrancas[0]["ga_client_id"] == "GA1.1.123.456"
    assert "qr_payload_enc" not in cobrancas[0]


# ── o DDL que alcança a tabela que JÁ EXISTE ─────────────────────────────────

def test_init_db_poe_o_rastreio_em_tabela_que_JA_EXISTE():
    """As três colunas do 1b-B chegam a uma `pix_charges` pré-existente.

    É o teto que `create table if not exists` tem: no banco descartável do
    `conftest` o `create table` sempre roda, então coluna inline passaria verde
    aqui e NUNCA existiria no Railway — onde a purga da exclusão de conta
    estouraria com `UndefinedColumn`, derrubando o job inteiro.
    """
    from db.schema import init_db

    assert set(RASTREIO) <= _colunas("pix_charges"), "o boot do conftest já não as põe"
    with get_conn() as conn, conn.cursor() as cur:
        for nome in RASTREIO:
            cur.execute(f"alter table pix_charges drop column {nome}")
        conn.commit()
    assert not (set(RASTREIO) & _colunas("pix_charges"))

    init_db()

    faltando = sorted(set(RASTREIO) - _colunas("pix_charges"))
    assert not faltando, (
        f"`init_db` não repôs {faltando} numa `pix_charges` que JÁ EXISTIA — "
        "use `alter table … add column if not exists`, nunca coluna inline."
    )


def test_init_db_poe_o_check_da_tabela_nova_que_JA_EXISTE():
    """Mesmo teto para `pix_unmatched_payments`: a tabela nasce neste PR, mas o
    PR seguinte que quiser acrescentar constraint já a encontra existente."""
    from db.schema import init_db

    assert "pix_unmatched_ref_formato" in _checks("pix_unmatched_payments")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("alter table pix_unmatched_payments"
                    " drop constraint pix_unmatched_ref_formato")
        conn.commit()
    assert "pix_unmatched_ref_formato" not in _checks("pix_unmatched_payments")

    init_db()

    assert "pix_unmatched_ref_formato" in _checks("pix_unmatched_payments"), (
        "`init_db` não repôs o check numa tabela que JÁ EXISTIA — use o par "
        "`drop constraint if exists` + `add constraint … not valid`."
    )


def test_pix_unmatched_nao_tem_user_id():
    """Não há dono, e é esse o ponto (§6). A ausência é o que impede alguém de
    tratar a fila de conciliação como se fosse cobrança nossa — e é por isso que
    a tabela não entra em `user_owned_tables` de `db/privacy.py`."""
    assert "user_id" not in _colunas("pix_unmatched_payments")
    assert {"asaas_payment_id", "external_reference", "value", "net_value",
            "status", "date_created", "customer", "received_at",
            "notified_at"} <= _colunas("pix_unmatched_payments")


def test_pix_unmatched_aceita_referencia_nossa():
    """POSITIVO do par: sem ele o `check` podia recusar tudo e a fila nunca
    receberia linha — que é pior que aceitar demais."""
    pid = f"pay_{uuid.uuid4().hex[:12]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pix_unmatched_payments"
                "  (asaas_payment_id, external_reference, value, status)"
                " values (%s, 'pix:99999', 299.00, 'RECEIVED')", (pid,),
            )
        conn.commit()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select value from pix_unmatched_payments"
                    " where asaas_payment_id = %s", (pid,))
        assert float(cur.fetchone()["value"]) == 299.00


@pytest.mark.parametrize("ref", ["boleto-loja-42", "", "pix:abc", "pix:"])
def test_pix_unmatched_recusa_referencia_de_terceiro(ref):
    """`^pix:[0-9]+$` no BANCO: dinheiro de terceiro não entra na nossa fila de
    conciliação nem por um chamador distraído (§8.2 A)."""
    with pytest.raises(errors.CheckViolation):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into pix_unmatched_payments"
                    "  (asaas_payment_id, external_reference)"
                    " values (%s, %s)", (f"pay_{uuid.uuid4().hex[:12]}", ref),
                )
            conn.commit()
