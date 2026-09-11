"""
tests/test_billing_email_falha_nao_promete_recursos.py — o e-mail de pagamento
falhou não promete o que o produto não entrega na carência.

Arquivo próprio por assunto: `test_billing_email_nome_do_plano_irmaos.py` cobre
o NOME DO PLANO nesta mesma copy, e diz com todas as letras que "os asserts
abaixo afirmam só o NOME de propósito". A CONTEÚDO da promessa não tinha dono —
e foi exatamente por isso que ela envelheceu.

**A frase envelhecida**: "Enquanto isso, seu plano fica como past_due e você
continua usando normalmente." Ela é verdade em METADE da janela. Medido
2026-09-11, conta `plus` com `last_payment_status='past_due'`:

| trecho | `has_app_access` | `get_plan_tier` | limites |
|---|---|---|---|
| período pago VIGENTE | True | `plus` | `of_banks_max=2`, `agents_max=3` |
| `plan_expires_at` VENCIDO, carência aberta | True | **`free`** | 30 lançamentos, `of_banks_max=0`, `agents_max=0` |

O e-mail sai na virada entre os dois e é lido depois, então a copy tem de ser
verdadeira nos DOIS trechos. A mesma promessa já tinha saído de
`billing_copy.COBRANCA_EM_ATRASO` na rodada 4 deste PR; o e-mail ficou dizendo o
contrário na mesma janela (§0.7: a regra em dois lugares, um atualizado).

CONTROLE DECLARADO (`docs/controles_declarados.md`)
────────────────────────────────────────────────────
**Negativo** — em `send_payment_failed_email`, reponha a frase antiga no lugar
da nova (troca de TEXTO num parágrafo; nada é apagado)::

      <p style="font-size:13px;color:rgba(255,255,255,.55)">Enquanto isso, seu plano fica como <strong>past_due</strong>
      e você continua usando normalmente. Se as tentativas falharem, a assinatura é encerrada e
      <strong>o acesso ao PigBank é bloqueado</strong> — não existe mais plano Free pra onde voltar.</p>

VERMELHOS (medido 2026-09-11 — são QUATRO, e a primeira redação desta instrução
nomeava dois):
  `test_a_falha_nao_promete_uso_normal`
  `test_a_falha_diz_a_regra_dos_recursos_em_vez_de_um_estado`
  `test_a_falha_nao_mostra_status_tecnico_ao_cliente`
  `test_a_falha_continua_dizendo_que_o_acesso_nao_caiu_agora`
Direção: promessa falsa de recursos — quem lê no dia seguinte à virada já está
com 30 lançamentos, sem Open Finance e sem agentes.

**Positivo do grupo, e é UM só**:
  `test_a_falha_continua_avisando_do_bloqueio_se_as_tentativas_falharem`

**Por que só um, e por que eu errei ao declarar dois.** A injeção troca o
PARÁGRAFO INTEIRO, então todo teste que afirme texto NOVO cai junto com ela —
inclusive o "continua entrando no PigBank", que eu tinha classificado como
positivo e que, medido, fica VERMELHO. Positivo é o que sobrevive à injeção, e
o único texto presente nas DUAS versões do parágrafo é o aviso do bloqueio.

Ele é um positivo de verdade, e o modo de falha que ele pega foi medido: **o
"conserto" preguiçoso de apagar o parágrafo inteiro** deixa o negativo
`test_a_falha_nao_promete_uso_normal` VERDE (a frase some, logo não promete
nada) e derruba três, este entre eles::

    FAILED test_a_falha_diz_a_regra_dos_recursos_em_vez_de_um_estado
    FAILED test_a_falha_continua_dizendo_que_o_acesso_nao_caiu_agora
    FAILED test_a_falha_continua_avisando_do_bloqueio_se_as_tentativas_falharem
    3 failed, 2 passed

É a diferença entre "parou de mentir" e "parou de falar": sem o aviso do
bloqueio o cliente perde a informação que o #354 pagou para colocar ali.

O que este arquivo NÃO alcança: o envio de verdade. `send_email` é
monkeypatchado pela fixture `capturado`, e este ambiente não tem `RESEND_API_KEY`
— nenhum e-mail sai daqui (§6).
"""
import re

from core.services import email_service as es
# A fixture `capturado` e o `_tudo` são os MESMOS dos arquivos irmãos —
# importar em vez de copiar (§0.1).
from test_pix_paid_email_copy import capturado  # noqa: F401
from test_billing_email_nome_do_plano import _tudo


def _html_da_falha(capturado) -> str:  # noqa: F811
    """O HTML do e-mail com os espaços NORMALIZADOS.

    A normalização não é conveniência: o parágrafo é uma f-string indentada no
    fonte, então "limitada até a\\n      cobrança entrar" tem uma quebra no meio
    da frase, num ponto que muda sozinho quando o nome do plano é mais longo ou
    quando alguém reindenta o bloco. Asserção contra o texto cru mediria a FORMA
    do arquivo, e forma é o que envelhece primeiro
    (`docs/controles_declarados.md`) — aqui custou um vermelho antes de o
    conserto estar errado.
    """
    assert es.send_payment_failed_email("a@b.com", "pro")
    partes = _tudo(capturado)
    # O HTML é a parte que tem o parágrafo; o texto nunca teve a frase.
    html = re.sub(r"\s+", " ", max(partes, key=len))
    assert "não passou" in html, f"não achei o corpo da falha: {partes}"
    return html


def test_a_falha_nao_promete_uso_normal(capturado):  # noqa: F811
    """A promessa que morreu na virada do `plan_expires_at`."""
    html = _html_da_falha(capturado)
    baixa = html.lower()
    assert "usando normalmente" not in baixa, (
        "o e-mail promete uso normal, e na carência o tier já é `free`: 30 "
        f"lançamentos, sem Open Finance e sem agentes — {html}")
    assert "funcionando normalmente" not in baixa, html


def test_a_falha_diz_a_regra_dos_recursos_em_vez_de_um_estado(capturado):  # noqa: F811
    """E não a troca pela afirmação oposta, que seria falsa no outro trecho.

    "sua conta está limitada" é tão errado quanto "usando normalmente" — só que
    no primeiro trecho da janela. A frase tem de enunciar a REGRA, que não tem
    tempo verbal: os recursos valem até o fim do período já pago."""
    html = _html_da_falha(capturado)
    assert "até o fim do período que você já pagou" in html, html
    assert "limitada até a cobrança entrar" in html, html


def test_a_falha_nao_mostra_status_tecnico_ao_cliente(capturado):  # noqa: F811
    """`past_due` é nome de coluna, em inglês, num e-mail para cliente."""
    html = _html_da_falha(capturado)
    assert "past_due" not in html, f"status técnico cru na copy: {html}"


def test_a_falha_continua_dizendo_que_o_acesso_nao_caiu_agora(capturado):  # noqa: F811
    """POSITIVO: `has_app_access` é True nos DOIS trechos da janela, e a copy
    não pode assustar quem ainda está dentro. Sem este caso, apagar o parágrafo
    passaria no negativo acima."""
    html = _html_da_falha(capturado)
    assert "continua entrando no PigBank" in html, html


def test_a_falha_continua_avisando_do_bloqueio_se_as_tentativas_falharem(capturado):  # noqa: F811
    """POSITIVO do #354: este e-mail PODE falar em perda de acesso, e deve — é o
    irmão `send_payment_reminder_email` que não pode. O aviso e a ausência de
    plano Free foram conquistados naquele PR e não podem sair neste."""
    html = _html_da_falha(capturado)
    assert "o acesso ao PigBank é bloqueado" in html, html
    assert "não existe mais plano Free" in html, html
