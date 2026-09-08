"""
core/services/payment_reminder_wa.py — o canal WhatsApp do lembrete de pagamento.

Arquivo próprio porque o assunto é OUTRO (um sistema externo, com template
aprovado na Meta, envio por destino e veredito por resposta HTTP) e porque o
`payment_reminder.py` estourou o teto de 350 linhas de
`tests/test_max_lines_python.py` — §0.5, dividir por assunto em vez de cortar a
razão das decisões dos comentários.

O e-mail continua sendo o caminho GARANTIDO e mora no módulo irmão; isto aqui é
melhoria, dormente por padrão. Testes em
`tests/test_payment_reminder_whatsapp.py`.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _wa_lembrete(user_id: int) -> bool:
    """Manda o lembrete por WhatsApp. **True se ALGUM destino aceitou.**

    **O consentimento é LIDO AQUI, fresco, no ponto do envio.** Quem desligou
    "atualizações do Piggy" em Configurações > Notificações (ou pelo botão do
    próprio WhatsApp, `wa_runtime.py:878`) grava
    `auth_accounts.whatsapp_updates_opt_out`, e mandar mesmo assim não é bug de
    mecânica, é violação de CONSENTIMENTO.

    **Por que a leitura mora aqui e o parâmetro obrigatório SAIU.** A versão
    anterior recebia `wa_opt_out` do chamador, tirado do snapshot do funil, e o
    parâmetro era obrigatório e keyword-only "para ninguém esquecer". Ele
    protegia contra OMISSÃO e não contra OBSOLESCÊNCIA: quem desligasse o canal
    durante o lote — enquanto as linhas anteriores eram decifradas ou enviadas —
    tinha `False` no mapa e recebia mensagem de todo jeito. "Ninguém pode
    esquecer de passar" não é "o valor passado é verdadeiro no momento do uso",
    e a invariante forte é a segunda. Lendo aqui, nenhum chamador consegue nem
    esquecer nem envelhecer o valor — e não há mais default para o chamador
    escolher errado.

    A leitura acontece uma vez por lembrete **ENVIADO** (não por candidato) e
    logo depois de um envio de e-mail de 100-300 ms de HTTP: é uma linha de
    `auth_accounts` no meio disso. Sem cache de propósito
    (`db.reports.get_whatsapp_updates_opt_out` não passa por `get_auth_user`,
    que tem TTL de 10 s) — consentimento não se lê de cache.

    FAIL-CLOSED: falha de leitura devolve False (não manda). O e-mail, que é o
    caminho garantido, já saiu, então errar para o lado fechado custa uma
    MELHORIA e errar para o aberto custa mensagem em canal desligado.

    Não checa `whatsapp_updates_available` da tela de Configurações
    (`frontend/routes/settings.py:74`), e isso é decisão: aquilo é
    `bool(phone_e164)`, uma CAPACIDADE de exibição, não uma preferência. Os
    destinos daqui saem de `user_identities` (`list_identities_by_user`), que é
    fonte independente de `auth_accounts.phone_e164` — gatear por
    "disponível" sumiria com o lembrete de quem tem WhatsApp funcionando e
    `phone_e164` vazio, trocando um erro por outro. Quem não tem identidade
    nenhuma já cai fora: a lista de destinos vem vazia e o retorno é False.

    A conta pode ter mais de um número (`_dedupe_whatsapp_targets`), e falha de
    um não é falha do lembrete: o valor que sai daqui vira `whatsapp` nos
    `details` do `payment_reminder_sent`, e ele responde "a pessoa foi
    alcançada?", não "todos os números responderam 200?". Cada destino tem
    `try` próprio — ver o comentário no laço, que é onde a razão mora.

    Mensagem proativa fora da janela de 24 h exige TEMPLATE APROVADO NA META,
    e o nome do template vive em `WA_TEMPLATE_PAYMENT_REMINDER`, VAZIO por
    padrão — sem a env este caminho é dormente e nem importa o `wa_client`
    (mesmo desenho do `open_finance_proactive._template_cfg`). O texto do
    template é escrito na Meta, fora deste repositório: quem o aprovar tem de
    manter a mesma regra da copy do e-mail e NÃO prometer perda de acesso.

    Nunca levanta: o e-mail já saiu quando isto roda, e derrubar o tick por
    causa de um template não aprovado transformaria a melhoria em regressão.
    """
    # CONSENTIMENTO PRIMEIRO, antes até do template: é a razão mais forte para
    # não enviar, e a ordem faz o código dizer isso.
    try:
        from db.reports import get_whatsapp_updates_opt_out
        if get_whatsapp_updates_opt_out(user_id):
            logger.info("[cobranca] WhatsApp pulado por opt-out do canal"
                        " user_id=%s", user_id)
            return False
    except Exception as exc:
        logger.warning("[cobranca] leitura do opt-out de WhatsApp falhou"
                       " user_id=%s: %s — nao envia", user_id, exc)
        return False
    nome = (os.getenv("WA_TEMPLATE_PAYMENT_REMINDER") or "").strip()
    if not nome:
        return False
    try:
        from adapters.whatsapp.wa_app import _dedupe_whatsapp_targets
        from adapters.whatsapp.wa_client import send_template
        from db import list_identities_by_user
        idioma = (os.getenv("WA_TEMPLATE_PAYMENT_REMINDER_LANGUAGE") or "pt_BR").strip()
        destinos = _dedupe_whatsapp_targets(list_identities_by_user(user_id))
        aceitos = 0
        for to in destinos:
            # `try` POR DESTINO, e o de fora continua existindo — os dois
            # cobrem coisas diferentes: aqui, falha DESTE número; lá, falha do
            # `import`, do `list_identities_by_user` ou do
            # `_dedupe_whatsapp_targets`, que não são por destino e valem para
            # o lembrete inteiro.
            #
            # `send_template` tem TRÊS desfechos, e a rodada anterior tratou só
            # um: `None` no 401 (token inválido/expirado, sem levantar,
            # `adapters/whatsapp/wa_client.py:220`); `raise` em erro de
            # TRANSPORTE (`:189-195`, depois de logar `whatsapp_send_exception`)
            # e em todo outro `>= 400` (`:237`); e o JSON da resposta no 2xx.
            # Os dois últimos são POR DESTINO — sem este `try`, o número 2
            # levantando abortava o laço antes do 3 e, pior, fazia o `except`
            # de fora devolver False DEPOIS de o número 1 ter aceitado: sucesso
            # real virava `whatsapp: false` no registro.
            #
            # `is not None`, não truthiness: o contrato é "None no 401, JSON da
            # resposta nos outros casos".
            try:
                if send_template(to, nome, language_code=idioma) is not None:
                    aceitos += 1
            except Exception as exc:
                # Sem o número no log: o `wa_client` já grava `to` nos
                # `details` de `system_event_logs` em todos os caminhos de
                # falha, e repetir aqui só espalharia PII (§0.1 — se um dia
                # precisar, `utils_phone.mask_phone` já existe).
                logger.warning("[cobranca] WhatsApp: um destino falhou"
                               " user_id=%s: %s", user_id, exc)
        if aceitos < len(destinos):
            # A informação útil na falha PARCIAL é a razão, não "deu erro":
            # 2/3 é melhoria entregue com um número morto, 0/3 é token ou
            # template quebrado. Sem isto os dois davam o mesmo log.
            logger.warning("[cobranca] WhatsApp parcial user_id=%s:"
                           " %d de %d destinos aceitaram", user_id,
                           aceitos, len(destinos))
        # "ALGUM destino aceitou" — a semântica que a rodada anterior
        # DOCUMENTOU e o código não implementava, porque o `return` vivia
        # dentro do `try` de fora. É o que interessa ao lembrete: a pessoa foi
        # alcançada em pelo menos um número.
        return aceitos > 0
    except Exception as exc:
        # Só chega aqui a falha de SETUP (import/identidades/dedupe), e aí
        # nenhum destino foi tentado — `False` é o veredito certo.
        logger.warning("[cobranca] WhatsApp não enviado user_id=%s: %s", user_id, exc)
        return False
