"""Os e-mails de billing do Stripe chamavam TODO assinante de "PigBank+" (#351).

Mesmo defeito que o #348 corrigiu no Pix, no canal mais antigo e de mais volume.
"PigBank+" é o nome comercial do **Plus**: quem assinava Essencial ou Pro lia o
nome de outro plano nas boas-vindas, no aviso de fim de trial e — pior — na
confirmação de cobrança, que sai em toda renovação.

**O vocabulário aqui é o LEGADO**, e isso não é escolha de estilo: o `plan_value`
do webhook sai de `_stored_plan_for_price`
(`frontend/finance_bot_websocket_custom.py:293`), que devolve `essencial`, `pro`
(= Plus) ou `pro_max` (= Pro) a partir do price da assinatura. `plus` nunca
chega. Chavear no vocabulário público mandaria "PigBank Pro" para quem assinou
Plus — foi a primeira versão do #348, e é regressão, não conserto.

Os dois controles do CLAUDE.md §3, para o GRUPO. As três mutações abaixo foram
RODADAS, não previstas — cada uma com o vermelho que produziu:

  · **negativo, a copy** — volte `nome` para a constante `"PigBank+"` em
    `send_pro_charged_email` e ficam vermelhos `test_essencial_cobrado...` e
    `test_pro_max_cobrado...`, que estavam VERDES.
  · **negativo, o vocabulário** — chaveie `PLAN_DISPLAY_NAMES` em
    `plus`/`pro` (os slugs do frontend) e ficam vermelhos
    `test_pro_max_cobrado...`, `test_boas_vindas...` e — a regressão que o #348
    quase mandou para produção — o CONTROLE POSITIVO
    `test_plus_cobrado_continua_pigbank_mais`, além de dois casos do arquivo do
    Pix, que compartilham o mesmo mapa.
  · **negativo, o seam** — tire `plan_value` da chamada de
    `_fire_email(user_id, send_pro_charged_email, ...)`: só
    `test_a_conversa_do_webhook...` fica vermelho, com
    `send_pro_charged_email recebeu 9.9`. **Em produção esta mutação não
    levanta nada**, e o que ela produz foi RODADO, não deduzido: os argumentos
    andam uma casa, `amount_brl` recebe o `datetime` do vencimento e o
    `f"R$ {amount_brl:,.2f}"` cai no `__format__` do `datetime` — que é o
    `strftime` e devolve a própria string de formato. O e-mail sai
    `✓ Pagamento confirmado — PigBank (R$ .,2f)`: nome genérico, valor
    destruído, zero exceção. É a única das três que a copy não alcança, e por
    isso este teste existe.
  · **positivo** — `test_plus_cobrado_continua_pigbank_mais` é o plano que já
    estava CERTO com o texto fixo, e continua verde na 1ª mutação. Sem ele, o
    grupo passaria num conserto que trocasse todo mundo por um genérico — que
    é pior que o bug.

Os outros DOIS e-mails da mesma família — `send_payment_failed_email` e
`send_subscription_canceled_email` — moram em
`tests/test_billing_email_nome_do_plano_irmaos.py`, e o motivo de estarem lá é
o teto de 350 linhas por arquivo (§0.5): são outro assunto (o que acontece
quando a cobrança FALHA), com seams em outros dois ramos do webhook.

O que este arquivo NÃO alcança: o envio de verdade (`send_email` é
monkeypatchado) e o `_check_trial_ending` do scheduler (nenhum teste do repo
exercita aquele laço).
"""
from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import (
    conta as _conta, espiao_email as _espiao, evt_checkout as _evt_checkout,
    evt_paid as _evt_paid, garantir_system_event_logs, sub_stripe as _sub,
)
import frontend.finance_bot_websocket_custom as dashboard
from core.services import email_service as es
# A fixture `capturado` (intercepta o `send_email` e devolve o que a função
# montou) é a MESMA do arquivo do Pix — importar em vez de copiar (§0.1): duas
# cópias é como dois testes passam a medir coisas diferentes achando que medem
# a mesma.
from test_pix_paid_email_copy import capturado  # noqa: F401
from test_billing_webhook_lifecycle import _post, _setup

PROXIMA = datetime.now(timezone.utc) + timedelta(days=30)


def _tudo(caixa):
    return (caixa["subject"], caixa["html"], caixa["text"])


# ── a copy, por plano ────────────────────────────────────────────────────────

def test_essencial_cobrado_nao_vira_plus(capturado):  # noqa: F811
    """R$ 9,90 cobrados e o e-mail dizia "Seu PigBank+ tá renovado"."""
    assert es.send_pro_charged_email("a@b.com", "essencial", 9.9, PROXIMA)
    for parte in _tudo(capturado):
        assert "PigBank Essencial" in parte, parte
        assert "PigBank+" not in parte, f"nome de outro plano na cobrança: {parte}"


def test_pro_max_cobrado_e_o_pro(capturado):  # noqa: F811
    """`pro_max` é o Pro — o plano mais caro, e o que o mapa erra se for
    chaveado no vocabulário público (lá `pro` já é o Plus)."""
    assert es.send_pro_charged_email("a@b.com", "pro_max", 49.9, PROXIMA)
    for parte in _tudo(capturado):
        assert "PigBank Pro" in parte, parte
        assert "PigBank+" not in parte, f"nome de outro plano na cobrança: {parte}"


def test_plus_cobrado_continua_pigbank_mais(capturado):  # noqa: F811
    """CONTROLE POSITIVO: o Plus já recebia o nome certo e continua recebendo.

    `pro` na coluna é o Plus. Se o conserto tivesse trocado todo mundo por um
    genérico ("PigBank"), ou chaveado no vocabulário do frontend (onde `pro` é
    o Pro), este é o caso que reprova.
    """
    assert es.send_pro_charged_email("a@b.com", "pro", 19.9, PROXIMA)
    for parte in _tudo(capturado):
        assert "PigBank+" in parte, parte
        assert "PigBank Pro" not in parte, f"nome do plano mais caro: {parte}"


def test_boas_vindas_e_fim_de_trial_tambem(capturado):  # noqa: F811
    """As outras duas da família — consertar só a cobrança é achar um caso e
    chamar de categoria resolvida (§2)."""
    assert es.send_pro_welcome_email("a@b.com", "pro_max", PROXIMA)
    for parte in _tudo(capturado):
        assert "PigBank Pro" in parte, parte
        assert "PigBank+" not in parte, parte

    assert es.send_trial_ending_email("a@b.com", "essencial", PROXIMA)
    for parte in _tudo(capturado):
        assert "PigBank Essencial" in parte, parte
        assert "PigBank+" not in parte, parte


# ── o seam: o webhook de verdade, três eventos no mesmo usuário ──────────────

def test_a_conversa_do_webhook_manda_o_plano_nos_tres_emails(user_id, monkeypatch):
    """Três eventos da Stripe, um usuário de Essencial, os três e-mails.

    Aqui é onde a ordem dos argumentos erra CALADA: `_fire_email` chama
    `fn(email, *args, DASHBOARD_URL)` posicionalmente e engole toda exceção
    (`finance_bot_websocket_custom.py:5137`) — trocar `plan` com `amount_brl`
    não levanta nada visível, só manda e-mail errado (ou nenhum). Por isso o
    teste posta o evento inteiro no `/billing/webhook` e lê a tupla que chegou
    do outro lado, em vez de chamar a função de e-mail direto.

    O `price_ess` é o que amarra o vocabulário: ele entra como PRICE do Stripe e
    tem de sair como `"essencial"` — o valor da coluna, não o slug do frontend.
    """
    uid, client, fake = _setup(monkeypatch, f"nome-{user_id}")
    _conta(uid, "free", None, "inactive")
    # Sem a tabela, o `recent_event_exists` do ramo trial_will_end estoura fora
    # do try do `_fire_email` e o webhook devolve 500.
    garantir_system_event_logs()
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_ESSENCIAL_MENSAL", "price_ess")

    vistos: dict[str, tuple] = {}
    for nome in ("send_pro_welcome_email", "send_pro_charged_email",
                 "send_trial_ending_email"):
        monkeypatch.setattr(es, nome, _espiao(vistos, nome), raising=False)

    subs = {"sub_nome": _sub("active", "price_ess", 30)}

    r = _post(client, fake, _evt_checkout(uid, "sub_nome", 1_800_000_000, "cs_nome"),
              subs=subs)
    assert r.status_code == 200, r.text

    trial_end = _sub("trialing", "price_ess", 3)
    trial_end["id"] = "sub_nome"
    trial_end["metadata"] = {"finbot_user_id": str(uid)}
    r = _post(client, fake,
              {"type": "customer.subscription.trial_will_end",
               "id": "evt_nome_twe", "created": 1_800_000_050,
               "data": {"object": trial_end}}, subs=subs)
    assert r.status_code == 200, r.text

    cobranca = _evt_paid(uid, "sub_nome", 1_800_000_100)
    cobranca["data"]["object"]["amount_paid"] = 990
    r = _post(client, fake, cobranca, subs=subs)
    assert r.status_code == 200, r.text

    assert set(vistos) == {"send_pro_welcome_email", "send_trial_ending_email",
                           "send_pro_charged_email"}, vistos
    # O plano é o 2º argumento posicional nas três, logo depois do e-mail.
    for nome, args in vistos.items():
        assert args[1] == "essencial", f"{nome} recebeu {args[1]!r}: {args}"
    # E na cobrança o valor continua no lugar dele — o erro que a troca de
    # ordem produziria sem levantar nada.
    assert vistos["send_pro_charged_email"][2] == 9.90, vistos
