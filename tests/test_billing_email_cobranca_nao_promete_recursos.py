"""
tests/test_billing_email_cobranca_nao_promete_recursos.py — os DOIS e-mails de
cobrança em atraso não prometem o que o produto não entrega na carência.

Arquivo próprio por assunto: `test_billing_email_nome_do_plano_irmaos.py` cobre
o NOME DO PLANO nesta mesma copy, e diz com todas as letras que "os asserts
abaixo afirmam só o NOME de propósito". O CONTEÚDO da promessa não tinha dono —
e foi exatamente por isso que ela envelheceu.

**Nasceu cobrindo só o `send_payment_failed_email` e cresceu no mesmo dia**, o
que é o registro da lição: a frase foi consertada num e-mail e o irmão
(`send_payment_reminder_email`) ficou dizendo o contrário. "Achei um caso" ≠
"resolvi a categoria" (§2), e aqui a categoria são os dois e-mails que falam
com quem está com a cobrança em atraso. O nome do arquivo diz `cobranca`, e não
`falha`, por isso.

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

A VARREDURA DA CATEGORIA (2026-09-11)
────────────────────────────────────
A categoria é "copy que afirma o que o usuário PODE FAZER enquanto está em
carência ou cortado". Fechada por busca, não por leitura::

    grep -rnE "continua (funcionando|usando|anotando|valendo|ativo|ativa|com acesso|tudo)|normalmente|sem interrupç|segue funcionando|nada muda|acesso (continua|segue)" \
      --include=*.py --include=*.html --include=*.js --include=*.mjs . \
      | grep -v __pycache__ | grep -vE "^\./(tests|node_modules|\.venv)/" | grep -v "^\./docs/"

Tirados os comentários de código, sobram QUATRO copies, e o discriminador é a
JANELA a que a frase se refere:

| copy | janela | veredito |
|---|---|---|
| `send_payment_failed_email` | a da retentativa | **era falsa** na 2ª metade — consertada |
| `send_payment_reminder_email` | o 6º dia do relógio | **era falsa** quase sempre — consertada |
| `send_subscription_canceled_email` (ramo com carência) | até `plan_expires_at` | **verdadeira** |
| `billing_commands._handle_cancelar` | "até o fim do período já pago" | **verdadeira** |

As duas de baixo prometem acesso ATÉ O FIM DO PERÍODO JÁ PAGO, e essa é
exatamente a janela em que o tier ainda não caiu. Medido, conta `plus`
`canceled` com `plan_expires_at` 10 dias no futuro::

    has_app_access=True  get_plan_tier='plus'
    launches_month_max=None  of_banks_max=2  agents_max=3

E o ramo SEM carência do cancelamento (`plan_expires_at` vencido) diz que o
acesso foi encerrado — `has_app_access=False`, também verdadeiro. Nenhuma das
duas foi tocada, e isso está aqui para que "não mexi" seja uma medição e não um
esquecimento.

**A regra que separa as falsas das verdadeiras**, para a próxima copy: frase
escopada ao PERÍODO PAGO é segura; frase escopada à JANELA DE INADIMPLÊNCIA não
pode prometer recursos, porque ali o tier já é `free`.

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


# ── O IRMÃO: o lembrete do 6º dia ───────────────────────────────────────────
#
# `send_payment_reminder_email` dizia "Seu PigBank continua funcionando
# normalmente — só a cobrança está pendente", em HTML e em texto. Nele a frase é
# PIOR que no e-mail de falha: lá era falsa em metade da janela, aqui é falsa
# para quase toda a população alcançada. O lembrete sai no 6º dia do relógio, e
# o relógio começa na falha da fatura de RENOVAÇÃO — então o `plan_expires_at`
# já venceu. Medido 2026-09-11, conta `plus`, relógio de 6,2 dias:
#
#     has_app_access = True     get_plan_tier = 'free'
#     launches_month_max=30  of_banks_max=0  agents_max=0
#
# **DUAS regras diferentes, e é para não confundi-las que isto está escrito.**
# A antiga — este e-mail NÃO fala em perda de acesso — continua valendo inteira:
# no 6º dia o acesso não caiu, e prometer corte que não vem é o defeito que o
# trabalho do corte existe para não cometer. A nova — ele também não promete
# funcionamento PLENO — não afrouxa a primeira: uma é sobre ACESSO, a outra
# sobre RECURSOS. `test_o_lembrete_continua_sem_falar_em_perda_de_acesso`
# prende a antiga, e ele é o positivo que impede o conserto de virar ameaça.
#
# CONTROLES DECLARADOS (`docs/controles_declarados.md`) — TRÊS injeções, e o que
# prova que os quatro casos medem coisas diferentes é que os conjuntos de
# vermelhos NÃO se repetem. Todas medidas em 2026-09-11.
#
# **(a) a frase antiga de volta** — no HTML de `send_payment_reminder_email`,
# troque o parágrafo novo por::
#
#       Seu PigBank continua funcionando normalmente — só a cobrança está pendente.</p>
#
# VERMELHOS: `..._nao_promete_funcionamento_pleno`, `..._diz_a_regra_dos_recursos`,
#            `..._continua_dizendo_que_da_pra_entrar`
# Direção: promessa falsa de recursos, para uma população que no 6º dia já está
# com 30 lançamentos, sem Open Finance e sem agentes.
#
# **(b) o "conserto" preguiçoso: apagar o parágrafo inteiro.**
# VERMELHOS: `..._diz_a_regra_dos_recursos`, `..._continua_dizendo_que_da_pra_entrar`
# E repare no que fica VERDE: `..._nao_promete_funcionamento_pleno`. A frase
# sumiu, logo não promete nada — é a diferença entre "parou de mentir" e "parou
# de falar", e sem (b) o grupo aceitaria o segundo.
#
# **(c) o outro jeito de errar: o conserto vira AMEAÇA.** Acrescente ao
# parágrafo "Se a cobrança não passar, o acesso é bloqueado."
# VERMELHO: `..._continua_sem_falar_em_perda_de_acesso` — e SÓ ele.
# Direção: mentira na direção oposta. No 6º dia o acesso não caiu.
#
# **Não há um positivo do grupo inteiro, e tentar nomear um foi erro meu duas
# vezes.** Quando a injeção troca o PARÁGRAFO, todo caso que afirme texto novo
# cai junto — "continua entrando no PigBank" parece positivo e é vermelho em (a)
# e em (b). O que separa os casos aqui é a TABELA acima: cada injeção tem um
# conjunto de vermelhos próprio, e cada caso é verde em pelo menos uma delas.
# Essa é a forma certa de declarar controle num grupo de COPY, onde a unidade
# injetada é o parágrafo e não um predicado.


def _partes_do_lembrete(capturado) -> list[str]:  # noqa: F811
    """Assunto, HTML e texto, normalizados — para as asserções de AUSÊNCIA.

    Inclui o assunto de propósito: uma promessa não pode reaparecer ali.
    """
    assert es.send_payment_reminder_email("a@b.com")
    return [re.sub(r"\s+", " ", parte) for parte in _tudo(capturado)]


def _corpos_do_lembrete(capturado) -> list[str]:  # noqa: F811
    """Só HTML e texto — para as asserções de PRESENÇA.

    O assunto fica de fora porque ele não carrega a frase, e exigi-la dele
    reprovaria por motivo errado (custou dois vermelhos falsos antes de esta
    separação existir). Os DOIS corpos entram: a frase antiga vivia nos dois, e
    consertar só o HTML deixaria quem lê em texto puro com a promessa velha.
    """
    partes = _partes_do_lembrete(capturado)
    corpos = [p for p in partes if "nao passou" in p or "não passou" in p]
    assert len(corpos) == 2, (
        f"esperava achar a frase da falha no HTML e no texto, achei em "
        f"{len(corpos)}: {partes}")
    return corpos


def test_o_lembrete_nao_promete_funcionamento_pleno(capturado):  # noqa: F811
    """No 6º dia o tier já é `free`: 30 lançamentos, sem OF, sem agentes."""
    for parte in _partes_do_lembrete(capturado):
        baixa = parte.lower()
        assert "funcionando normalmente" not in baixa, (
            f"o lembrete promete funcionamento pleno no 6º dia: {parte}")
        assert "usando normalmente" not in baixa, parte


def test_o_lembrete_diz_a_regra_dos_recursos(capturado):  # noqa: F811
    """E não a afirmação oposta. A regra não tem tempo verbal e é verdadeira
    antes e depois da virada do `plan_expires_at`."""
    for parte in _corpos_do_lembrete(capturado):
        assert ("período que você já pagou" in parte
                or "periodo que voce ja pagou" in parte), parte
        assert ("assim que a cobrança entrar" in parte
                or "assim que a cobranca entrar" in parte), parte


def test_o_lembrete_continua_dizendo_que_da_pra_entrar(capturado):  # noqa: F811
    """POSITIVO: `has_app_access` é True no 6º dia — a carência concede. Sem
    este caso, apagar o parágrafo passaria no negativo acima."""
    for parte in _corpos_do_lembrete(capturado):
        assert "continua entrando no PigBank" in parte, parte


def test_o_lembrete_continua_sem_falar_em_perda_de_acesso(capturado):  # noqa: F811
    """POSITIVO da regra ANTIGA, que o conserto novo não pode ter afrouxado.

    No 6º dia o acesso NÃO caiu, e este e-mail continua proibido de prometer
    corte — a diferença para o `send_payment_failed_email` é de fato, não de
    tom. Sem este caso, "parar de prometer recursos" poderia ter virado "avisar
    que vai bloquear", que é mentira na direção oposta.
    """
    for parte in _partes_do_lembrete(capturado):
        baixa = parte.lower()
        for proibido in ("bloquead", "bloqueio", "encerrad", "perde o acesso",
                         "perder o acesso", "sem acesso", "suspens"):
            assert proibido not in baixa, (
                f"o lembrete do 6º dia ameaça o acesso ({proibido!r}), e no 6º dia "
                f"o acesso não caiu: {parte}")
