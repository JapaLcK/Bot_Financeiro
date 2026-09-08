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
