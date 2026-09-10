"""O e-mail da compra Pix anual chamava TODO MUNDO de "PigBank+".

"PigBank+" é o nome comercial do plano **Plus**. Quem pagava Essencial ou Pro
recebia no assunto, no título e no corpo o nome de um plano que não comprou — e
quem comprava com plano vigente (acesso AGENDADO) lia "tá liberado" sobre um
ano que só começa quando o período atual terminar.

**O vocabulário destes casos é o LEGADO, que é o que a coluna `pix_charges.plan`
guarda**: `essencial`, `pro` (= Plus) e `pro_max` (= Pro). O router só aceita
esses três (`frontend/routes/billing_pix.py`) e o checkout grava `plan =
plan_stored` — `plus` nunca chega ali. Uma versão anterior destes testes usava
os slugs do frontend, e por isso ficou verde em cima de um mapa que mandava
"PigBank Pro" para quem comprou Plus e o fallback genérico "PigBank" para quem
comprou Pro.

Os dois controles do CLAUDE.md §3, para o GRUPO:
  · negativo — chaveie o `PIX_PLAN_NAMES` no vocabulário do frontend
    (`plus`/`pro`) e `test_pro_e_o_plus` e `test_pro_max_e_o_pro` ficam
    vermelhos; volte o `nome` para a constante "PigBank+" (ou apague o
    `agendado`) e `test_essencial_agendado` fica;
  · positivo — `test_pro_e_o_plus` é também o controle positivo: o Plus JÁ
    recebia "PigBank+" antes de tudo isso, e continua recebendo.

O que este arquivo NÃO alcança: o envio de verdade (o `send_email` é
monkeypatchado) e o modal da /home, que é `tests/frontend/welcome_pro_pix.test.mjs`.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.services import email_service as es


@pytest.fixture
def capturado(monkeypatch):
    """Intercepta o `send_email` e devolve o que a função montou."""
    caixa = {}

    def _fake(to, subject, html_body, text_body=None, **kw):
        caixa.update(to=to, subject=subject, html=html_body, text=text_body or "")
        return True

    monkeypatch.setattr(es, "send_email", _fake)
    return caixa


AGORA = datetime.now(timezone.utc)
FUTURO = AGORA + timedelta(days=40)
PASSADO = AGORA - timedelta(minutes=5)


def _tudo(caixa):
    return (caixa["subject"], caixa["html"], caixa["text"])


def test_essencial_agendado(capturado):
    ok = es.send_pix_paid_email("a@b.com", "essencial", 99.0,
                                FUTURO, FUTURO + timedelta(days=365))
    assert ok
    for parte in _tudo(capturado):
        assert "PigBank Essencial" in parte, parte
        assert "PigBank+" not in parte, f"nome de outro plano no e-mail: {parte}"
    assert "a partir de" in capturado["html"].lower()
    assert "a partir de" in capturado["text"].lower()
    # A data do começo é a que o cliente precisa: sem ela "agendado" é só adjetivo.
    assert es._fmt_brl_date(FUTURO) in capturado["html"]


def test_pro_e_o_plus(capturado):
    """`pro` na coluna é o **Plus**, e é o controle positivo do grupo.

    Era o único plano cujo e-mail já estava certo (o texto fixo dizia
    "PigBank+"), então ele é a prova de que o conserto não trocou o nome de
    todo mundo por um genérico — e a prova de que o mapa não foi chaveado no
    vocabulário errado, onde `pro` virava "PigBank Pro".
    """
    es.send_pix_paid_email("a@b.com", "pro", 199.0,
                           PASSADO, AGORA + timedelta(days=365))
    for parte in _tudo(capturado):
        assert "PigBank+" in parte, parte
        assert "PigBank Pro" not in parte, f"nome do plano mais caro: {parte}"
        assert "a partir de" not in parte.lower(), f"prometeu agendamento: {parte}"
    assert "liberado" in capturado["text"]


def test_pro_max_e_o_pro(capturado):
    """`pro_max` é o Pro — e é o valor da fixture real (`_dreno_pix_helpers`).

    Nenhum caso o usava, e por isso o fallback genérico "PigBank" passava batido
    justo no plano de R$ 499.
    """
    es.send_pix_paid_email("a@b.com", "pro_max", 499.0,
                           PASSADO, AGORA + timedelta(days=365))
    for parte in _tudo(capturado):
        assert "PigBank Pro" in parte, parte
        assert "PigBank+" not in parte, f"nome de outro plano no e-mail: {parte}"
    # O que saía era o fallback genérico, "PigBank anual" — o assunto é onde
    # ele aparecia inteiro, e é lá que se mede.
    assert "PigBank Pro anual" in capturado["subject"], capturado["subject"]


def test_starts_at_naive_nao_derruba_o_email(capturado):
    """`access_starts_at` sem fuso não pode levantar `TypeError` aqui.

    O modo de falha é o pior deste PR: a exceção sobe DENTRO do efeito `email`
    do dreno, o efeito nunca é registrado, o dreno retenta para sempre e quem
    pagou nunca recebe confirmação. O `_fmt_brl_date`, três linhas abaixo no
    mesmo arquivo, já tratava naive como UTC — a comparação não tratava.

    Controle negativo (MEDIDO): tire a normalização de `_inicio` em
    `send_pix_paid_email` e SÓ este caso fica vermelho, com
    `TypeError: can't compare offset-naive and offset-aware datetimes`.

    Alcançabilidade: a coluna é `timestamptz` e hoje só chega aware — isto é
    guarda de erro (§0.2), não conserto de bug reproduzido em produção.
    """
    naive_futuro = (AGORA + timedelta(days=40)).replace(tzinfo=None)
    ok = es.send_pix_paid_email("a@b.com", "essencial", 99.0,
                                naive_futuro, FUTURO + timedelta(days=365))
    assert ok
    assert "a partir de" in capturado["html"].lower(), (
        f"naive no futuro deixou de ser agendado: {capturado['html']}")

    naive_passado = (AGORA - timedelta(days=1)).replace(tzinfo=None)
    ok = es.send_pix_paid_email("a@b.com", "essencial", 99.0,
                                naive_passado, FUTURO)
    assert ok
    assert "a partir de" not in capturado["html"].lower(), (
        "naive no passado virou agendado — a normalização inverteu o ramo")


def test_plano_desconhecido_nao_inventa_nome(capturado):
    """Valor fora do mapa (linha antiga, plano descontinuado) cai no genérico —
    e não pode estourar nem chamar a pessoa de Plus."""
    es.send_pix_paid_email("a@b.com", "plano_que_nao_existe", 99.0,
                           PASSADO, AGORA + timedelta(days=365))
    for parte in _tudo(capturado):
        assert "PigBank+" not in parte, parte
        assert "PigBank Pro" not in parte, parte
        assert "PigBank" in parte, parte


def test_o_chamador_manda_o_plano_e_o_comeco(monkeypatch):
    """O seam entre o dreno e o e-mail — onde a ordem dos argumentos erra calada.

    `cobranca` tem DOIS campos de plano, e na prática eles são IGUAIS (o
    checkout grava `plan=plan_stored`): o que o dreno não pode fazer é mandar o
    `amount_cents` no lugar do plano, ou trocar a ordem de
    `amount_brl`/`access_starts_at`. Nada disso levanta erro — só chega e-mail
    errado. Este teste chama o efeito real e lê o que saiu do outro lado.
    """
    from core.services import pix_drain_effects as pde

    monkeypatch.setattr(pde, "_email_do_titular", lambda uid: "a@b.com")
    monkeypatch.setattr(pde, "recent_event_exists", lambda *a, **kw: False)
    monkeypatch.setattr(pde, "log_system_event_sync", lambda *a, **kw: None)

    visto = {}
    monkeypatch.setattr(es, "send_pix_paid_email",
                        lambda *a, **kw: visto.update(args=a, kw=kw) or True)

    pde._email({"user_id": 7, "plan": "pro_max", "plan_stored": "pro_max",
                "amount_cents": 49900, "access_starts_at": FUTURO,
                "access_expires_at": FUTURO + timedelta(days=365)}, {})

    assert visto["args"] == ("a@b.com", "pro_max", 499.0, FUTURO,
                             FUTURO + timedelta(days=365)), visto
