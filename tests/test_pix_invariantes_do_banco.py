"""As invariantes de `pix_charges` que vivem no BANCO, e não em Python.

Reunidas num arquivo só porque são o mesmo assunto e a mesma prova: um
`INSERT`/`UPDATE` cru, contornando `db/pix_charges.py`, contra o `CHECK` que o
`init_db` instalou. `tests/test_pix_charges.py` ficou com o CRUD, a transição,
o isolamento por dono e a FK — e saiu do teto de 350 linhas (§0.5) na mesma
mudança que trouxe a quinta invariante para cá.

## `pix_charges_pago_tem_janela` — pagamento com titular TEM janela de acesso

Sem constraint, "pagou, tem acesso" é uma frase: o dreno do 1b-B pode gravar
`paid_at` e esquecer `access_starts_at`/`access_expires_at`, e a linha fica
**paga sem conceder**, em silêncio. Com o `CHECK`, a mesma escrita levanta
`CheckViolation` — falha alta, não linha muda.

*(A frase "a janela é decidida uma vez, no pagamento" mora no §6.1 do plano, mas
lá ela é sobre o **grant** — `plan_grants`, criação única por
`on conflict do nothing` —, não sobre `pix_charges.access_*`. Este `CHECK` diz
outra coisa: se a cobrança tem `paid_at` e tem titular, os dois carimbos têm de
estar preenchidos. Citar o §6.1 como fonte dele era citação errada.)*

## Por que a exceção é `user_id is null` e NÃO `status = 'paid_orphan'`

Órfão (§13.4) é pagamento de conta já excluída: não há a quem conceder, então
ele é a única linha que pode ficar `paid_at` sem janela. A formulação óbvia
(excetuar o *status*) parece equivalente e não é — **a exceção por status se
apoia num valor que a linha DEIXA PARA TRÁS**. As três formulações, medidas
contra o Postgres:

| formulação | órfão pago sem janela | estorno desse órfão | `paid` com titular, sem janela |
|---|---|---|---|
| A `status = 'paid_orphan'` | aceita | **RECUSA** | recusa |
| B `user_id is null` (a escolhida) | aceita | aceita | recusa |
| C sem exceção | **RECUSA** | aceita | recusa |

`user_id is null` é a condição do **titular ausente**, e ela PERSISTE pela
transição — a FK é `on delete set null` e `pix_charges` está em
`_USER_FK_SET_NULL_TABLES` (`db/schema_repairs.py`). Por isso ela cobre também o
caso inverso: a conta excluída DEPOIS de pagar, que nasce `paid` com janela e
vira órfã sem passar por `paid_orphan` nenhum.

CONTROLE NEGATIVO DO GRUPO — `test_estorno_do_orfao_e_aceito`. Ele é o único
caso que separa B de A, e ele fica **VERMELHO** se alguém trocar a condição do
`CHECK` por `status = 'paid_orphan'`: o estorno leva o status para `refunded`, a
linha continua com `paid_at` e sem janela, e a formulação A a recusa.

MEDIDO assim (mutando `db/schema.py`, banco novo a cada rodada, restaurando por
`cp` — nunca `git checkout --`, que come trabalho não commitado):

  * formulação **A** → 2 vermelhos: `test_estorno_do_orfao_e_aceito` e
    `test_conta_excluida_DEPOIS_de_pagar_sobrevive_e_ainda_estorna`;
  * formulação **C** → 3 vermelhos: os dois acima mais
    `test_orfao_pago_sem_janela_e_aceito`;
  * **sem o `CHECK`** → 2 vermelhos: `test_paid_com_titular_e_sem_janela_e_RECUSADO`
    e `test_meia_janela_tambem_e_RECUSADA`;
  * como está (B) → 6 verdes.

POSITIVO DO GRUPO — `test_paid_com_titular_e_com_janela_e_aceito`: a venda
legítima continua entrando. **A justificativa anterior estava errada, e a
correção é medição, não releitura.** Ela dizia que a formulação C deixaria este
caso VERMELHO e que "o resto do arquivo passaria igual"; mutando de fato para C,
o resultado é o oposto nos dois pontos — este caso fica **VERDE** (C aceita
`paid` com titular e com janela, que é exatamente o que ele insere) e são os
outros **TRÊS** que ficam vermelhos (os do órfão e o da conta excluída).

O que ele cobre de verdade é a mutação que tira o ramo da janela do `or` —
`check (paid_at is null or user_id is null)`: ali o órfão e as duas recusas
continuam verdes e **a venda legítima passa a ser RECUSADA**. Ele não é o único
vermelho dessa mutação (`test_conta_excluida_DEPOIS_de_pagar…` também insere
`paid` + janela e cai junto), e isso fica escrito porque a versão anterior
afirmou exclusividade sem medir.

CEGUEIRA DECLARADA: isto prova o que o BANCO recusa, não que o dreno passe a
janela. Quem escreve o dreno é o 1b-B, e é lá que a `CheckViolation` tem de
virar retentativa em vez de 500 mudo.

## O QUE O `CHECK` NÃO COBRE — limites, e são para ficar assim

Medido por SQL cru contra o Postgres. O `CHECK` **ACEITA** hoje:

  * `status = 'paid'` **sem** `paid_at` (o gatilho é o carimbo, não o status);
  * janela **invertida** (`access_expires_at < access_starts_at`);
  * janela de **duração zero** (os dois carimbos iguais);
  * janela **inteira no passado** (2020);
  * órfão indo a `paid` liso, sem janela (é o mesmo ramo `user_id is null`).

**Não ampliar é decisão do dono**, e a razão é ponytail: nenhum desses estados é
alcançável pela API deste módulo — `transicionar` sempre carimba `paid_at` junto
do status, e a janela vem de `plano_da_cobranca`, que devolve
`access_expires_at > access_starts_at` por construção. Cada ampliação tentada
nesta sessão custou uma rodada. Quem escrever o dreno do 1b-B, e quiser reparar
linha à mão por `psql`, é quem tem de saber que estes cinco passam.

**Sem segunda cópia do CHECK aqui, de propósito** (§0.7): o teste exercita a
constraint que o `init_db` instalou, nunca um DDL repetido no arquivo de teste.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from psycopg.errors import CheckViolation

from db.connection import get_conn

AGORA = datetime.now(timezone.utc)
UM_ANO = AGORA + timedelta(days=365)


def _insere_cru(cur, user_id, **campos):
    """INSERT direto, contornando `criar_cobranca`. É o ponto: as invariantes
    têm de valer contra QUALQUER escritor, inclusive um `psql` na madrugada."""
    base = dict(user_id=user_id, external_reference="pix:999999999",
                public_token=secrets.token_urlsafe(16), plan="pro_max",
                plan_stored="pro_max", price_cents=29900, credit_cents=0,
                amount_cents=29900, status="draft")
    base.update(campos)
    cols = ", ".join(base)
    cur.execute(f"insert into pix_charges ({cols}) values "
                f"({', '.join(['%s'] * len(base))})", tuple(base.values()))


def _ref() -> str:
    """`^pix:[0-9]+$` e único — a coluna é unique global, e reusar valor faria a
    falha aparecer no teste errado."""
    return f"pix:{uuid.uuid4().int % 10**12}"


def _cru(cur, user_id, **campos) -> int:
    """`_insere_cru` com referência/token únicos, devolvendo o id — que os casos
    da janela precisam para o `UPDATE` que vem depois do `INSERT`."""
    campos.setdefault("external_reference", _ref())
    campos.setdefault("public_token", secrets.token_urlsafe(16))
    _insere_cru(cur, user_id, **campos)
    cur.execute("select id from pix_charges where external_reference = %s",
                (campos["external_reference"],))
    return cur.fetchone()["id"]


def test_orfao_pago_sem_janela_e_aceito():
    """§13.4: conta excluída paga depois. Não há a quem conceder — a linha
    existe só para reconciliar dinheiro que entrou."""
    with get_conn() as conn, conn.cursor() as cur:
        _cru(cur, None, status="paid_orphan", paid_at=AGORA)
        conn.commit()


def test_estorno_do_orfao_e_aceito():
    """**CONTROLE NEGATIVO DO GRUPO.**

    Caminho previsto do §13.4: o órfão é estornado no painel do Asaas, e
    `PAYMENT_REFUNDED` leva `paid_orphan` → `refunded`. A linha continua com
    `paid_at` e continua sem janela — mas já **não é mais** `paid_orphan`.

    Com o `CHECK` na formulação A (`status = 'paid_orphan'`), este `update`
    levanta `CheckViolation` e o estorno do órfão fica impossível de registrar.
    Este teste é o que discrimina as duas: ele estava VERDE antes da mutação (é
    onde a injeção discrimina, `CLAUDE.md` §3) e fica vermelho com ela.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cid = _cru(cur, None, status="paid_orphan", paid_at=AGORA)
        cur.execute("update pix_charges set status = 'refunded', "
                    "refunded_at = now() where id = %s", (cid,))
        conn.commit()

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select status, paid_at, access_expires_at "
                    "from pix_charges where id = %s", (cid,))
        linha = cur.fetchone()
    assert linha["status"] == "refunded"
    assert linha["paid_at"] is not None
    assert linha["access_expires_at"] is None


def test_paid_com_titular_e_sem_janela_e_RECUSADO(user_id):
    """O bug que a constraint existe para tornar impossível: pago e sem
    conceder. `duration_days` sozinho não é a janela — ele é o parâmetro, e o
    que vale é o par de carimbos."""
    with get_conn() as conn, conn.cursor() as cur:
        with pytest.raises(CheckViolation):
            _cru(cur, user_id, status="paid", paid_at=AGORA)
        conn.rollback()


def test_meia_janela_tambem_e_RECUSADA(user_id):
    """`access_starts_at` sem `access_expires_at` é acesso sem fim — vitalício
    por omissão, em cima de uma cobrança de 365 dias. O `and` do `CHECK` é o que
    fecha isto, e sem este caso ele poderia ser um `or` sem uma linha vermelha."""
    with get_conn() as conn, conn.cursor() as cur:
        with pytest.raises(CheckViolation):
            _cru(cur, user_id, status="paid", paid_at=AGORA,
                 access_starts_at=AGORA)
        conn.rollback()


def test_paid_com_titular_e_com_janela_e_aceito(user_id):
    """**POSITIVO do grupo**: a venda legítima continua entrando. Sem ele, a
    formulação C (sem exceção nenhuma) passaria por todo o resto do arquivo."""
    with get_conn() as conn, conn.cursor() as cur:
        cid = _cru(cur, user_id, status="paid", paid_at=AGORA,
                   access_starts_at=AGORA, access_expires_at=UM_ANO)
        conn.commit()
    assert cid


def test_conta_excluida_DEPOIS_de_pagar_sobrevive_e_ainda_estorna(user_id):
    """O caso inverso do órfão, e o que a formulação A nunca cobriria: a linha
    nasce `paid` **com** janela e vira órfã pelo `on delete set null` — sem
    nunca passar por `paid_orphan`. Depois disso ela ainda tem de aceitar o
    estorno.

    A janela FICA (é prova do que foi vendido); some o vínculo com o titular.
    """
    from conftest import _cleanup_user

    with get_conn() as conn, conn.cursor() as cur:
        cid = _cru(cur, user_id, status="paid", paid_at=AGORA,
                   access_starts_at=AGORA, access_expires_at=UM_ANO)
        conn.commit()

    _cleanup_user(user_id)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select user_id, paid_at from pix_charges where id = %s", (cid,))
        linha = cur.fetchone()
        assert linha is not None, "CASCADE apagou a prova do pagamento"
        assert linha["user_id"] is None
        # E o estorno posterior entra — a exceção do titular ausente vale aqui
        # também, e é a mesma condição do órfão.
        cur.execute("update pix_charges set status = 'refunded', "
                    "refunded_at = now(), access_expires_at = null "
                    "where id = %s", (cid,))
        conn.commit()


# ── as invariantes que vivem no BANCO (P2-7 e P1-3 do Codex) ────────────────

@pytest.mark.parametrize("rotulo,campos", [
    # P2-7: `pendng` sai do índice parcial (que só cobre 4 estados NOMEADOS) e
    # libera uma SEGUNDA cobrança ativa do mesmo usuário — dois QRs pagáveis,
    # que é o furo financeiro que o §10 fecha. Por um typo.
    ("status com typo", {"status": "pendng"}),
    # `orphan_unknown` NÃO é estado desta tabela: o registro não tem plano,
    # preço nem dono. A decisão do P1-2 é do CHECK, não de um comentário.
    ("orphan_unknown", {"status": "orphan_unknown"}),
    # P1-3: fora de `^pix:[0-9]+$` o dreno classifica o pagamento como de
    # TERCEIRO e o descarta em silêncio — dinheiro nosso perdido.
    ("referência hexadecimal", {"external_reference": "pix:a1b2c3"}),
    ("referência sem prefixo", {"external_reference": "boleto-loja-42"}),
    ("referência vazia", {"external_reference": ""}),
    # Centavos negativos produzem cobrança de valor negativo no provedor.
    ("preço negativo", {"price_cents": -1, "amount_cents": -1}),
    ("crédito negativo", {"credit_cents": -1, "amount_cents": 29901}),
    # A única relação entre as três colunas (§7). Sem ela, a cobrança sai com um
    # valor que não bate com o snapshot que a justifica.
    ("amount que não fecha", {"price_cents": 29900, "credit_cents": 100,
                              "amount_cents": 29900}),
])
def test_o_banco_recusa_linha_invalida(user_id, rotulo, campos):
    """As quatro invariantes são `CHECK`, não validação em Python.

    O diff tinha **zero** `check` antes disto (medido). Python valida quem passa
    por `criar_cobranca`; o banco valida todo mundo — a varredura, um reparo
    manual, o dreno do 1b-B, um `psql`.

    *Negativo: tire o par `drop`+`add` correspondente do `init_db` → a linha
    entra, e no caso do `status` a segunda cobrança ativa passa a ser possível.*
    """
    from psycopg.errors import CheckViolation

    with get_conn() as conn, conn.cursor() as cur:
        with pytest.raises(CheckViolation):
            _insere_cru(cur, user_id, **campos)
        conn.rollback()


def test_o_banco_ACEITA_a_linha_legitima(user_id):
    """POSITIVO das quatro: sem ele, uma constraint escrita errado demais
    recusaria toda venda e o grupo acima passaria igual."""
    with get_conn() as conn, conn.cursor() as cur:
        _insere_cru(cur, user_id, price_cents=29900, credit_cents=900,
                    amount_cents=29000)
        conn.commit()


# ── o portão: as CINCO valem em tabela QUE JÁ EXISTE ────────────────────────

CINCO = frozenset({
    "pix_charges_status_valido",
    "pix_charges_ref_formato",
    "pix_charges_centavos_nao_negativos",
    "pix_charges_amount_fecha",
    "pix_charges_pago_tem_janela",
})


def _checks_de_pix_charges() -> set:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select conname from pg_constraint "
                    "where conrelid = 'pix_charges'::regclass and contype = 'c'")
        return {linha["conname"] for linha in cur.fetchall()}


def test_init_db_instala_as_CINCO_em_tabela_que_JA_EXISTE():
    """O ponto cego que todo o resto deste arquivo tem, medido.

    Os casos acima rodam contra o banco que o `conftest` acabou de criar, onde o
    `create table` SEMPRE roda. Prova disso: movendo um `check` para dentro do
    `create table` e apagando o par `drop`+`add`, os 58 testes de Pix ficam
    VERDES — e o Railway, cuja `pix_charges` já existe, nunca ganha a constraint.
    Foi exatamente assim que quatro das cinco invariantes existiram só no
    `create table` e **zero** no `pigbank_ci_test`.

    Este teste é o único que exercita a população "tabela já existente": derruba
    as cinco à mão e exige que um `init_db()` de verdade as reponha. É o que
    `create table if not exists` NÃO faz.

    *Negativo: mova qualquer uma das cinco para dentro do `create table` e apague
    o par `drop`+`add` → `faltando` traz o nome dela e este teste fica vermelho,
    sozinho.*

    CEGUEIRA DECLARADA: ele prova que a constraint EXISTE, não o que ela recusa —
    disso cuidam os casos acima. E não distingue `validated` de `not valid`, de
    propósito: as duas barram linha nova, que é a garantia que interessa aqui.
    """
    from db.schema import init_db

    assert CINCO <= _checks_de_pix_charges(), "o boot do conftest já não instala as cinco"

    with get_conn() as conn, conn.cursor() as cur:
        for nome in sorted(CINCO):
            cur.execute(f"alter table pix_charges drop constraint {nome}")
        conn.commit()
    assert not (CINCO & _checks_de_pix_charges())

    init_db()

    faltando = sorted(CINCO - _checks_de_pix_charges())
    assert not faltando, (
        f"`init_db` não repôs {faltando} numa `pix_charges` que JÁ EXISTIA — "
        "`create table if not exists` não acrescenta constraint a tabela "
        "existente; use o par `drop constraint if exists` + `add constraint`."
    )
