"""
tests/test_billing_email_downsell_nao_promete_gratis.py — o e-mail de fim de
trial não promete um plano Grátis que não existe mais.

Arquivo próprio porque `test_billing_email_cobranca_nao_promete_recursos.py`
está em 301 linhas (teto de 350, `tests/test_max_lines_python.py`) e porque o
assunto é vizinho, não o mesmo: lá é a janela de INADIMPLÊNCIA, aqui é o FIM DO
TRIAL. **A tabela de varredura da categoria mora lá** e cobre os dois — não
duplicar (§0.7).

`send_trial_downsell_email` dizia: "Sua conta continua no plano Grátis: seus
dados estão todos guardados, mas o banco conectado ficou pausado e a Piggy
voltou pro modo básico." Depois do corte isso é falso do jeito mais direto —
quem termina o teste sem assinar fica SEM ACESSO, não "no básico". E é
automático, uma vez por conta (`trial_downsell_sent_at`), então começaria a
mentir sozinho no dia do merge.

Medido 2026-09-11, conta cujo trial venceu há 1 dia sem assinatura — a
população exata de `db.plans.list_trial_downsell_candidates`::

    has_app_access = False
    get_plan_tier  = 'free'
    launches_month_max=30  of_banks_max=0  agents_max=0
    motivo_trial_indisponivel  = 'telefone_ja_usou'

`get_plan_tier` ainda diz `'free'`, e é aí que a copy antiga se enganava: o tier
sobrevive como conjunto de LIMITES, o que morreu é o Grátis como DESTINO COM
ACESSO. A frase antiga vendia os limites como se viessem com a porta aberta.

CONTROLES DECLARADOS (`docs/controles_declarados.md`)
────────────────────────────────────────────────────
TRÊS injeções, e o que prova que os casos medem coisas diferentes é que os
conjuntos de vermelhos não se repetem. Todas medidas em 2026-09-11.

**(a) a frase antiga de volta** — troque o parágrafo novo por::

      <p>Sua conta continua no plano Grátis: seus dados estão todos guardados,
      mas o banco conectado ficou <strong>pausado</strong> e a Piggy voltou pro modo básico.</p>

VERMELHOS: `test_o_downsell_nao_diz_que_a_conta_continua_no_gratis`,
           `test_o_downsell_diz_que_o_acesso_fica_bloqueado`
Direção: promessa falsa de produto — a pessoa acha que ainda tem o básico e não
tem nada.

**(b) o "conserto" preguiçoso: apagar o parágrafo.**
VERMELHOS: `test_o_downsell_diz_que_o_acesso_fica_bloqueado`,
           `test_o_downsell_continua_dizendo_que_os_dados_estao_guardados`
E `..._nao_diz_que_a_conta_continua_no_gratis` fica VERDE — a frase sumiu, logo
não afirma nada. É "parou de mentir" × "parou de falar" outra vez.

**(c) a oferta some** — tire o `<ul>` dos planos. VERMELHO:
  `test_o_downsell_continua_sendo_uma_oferta`
Direção: o e-mail perde a RAZÃO DE EXISTIR. Ele não é um aviso de corte; é o
downsell que tenta segurar a pessoa antes do churn, e um "conserto" de copy que
o transforme em aviso já terá custado a conversão que ele existe para buscar.

Não há positivo do grupo inteiro: quando a injeção troca o PARÁGRAFO, todo caso
que afirme texto novo cai junto. O que separa os casos é a tabela acima — cada
um é verde em pelo menos uma injeção.

O que este arquivo NÃO alcança: o envio de verdade. `send_email` é
monkeypatchado pela fixture `capturado`, e este ambiente não tem
`RESEND_API_KEY` — nenhum e-mail sai daqui (§6).
"""
import re

from core.services import email_service as es
# Fixture e helper compartilhados dos irmãos — importar em vez de copiar (§0.1).
from test_pix_paid_email_copy import capturado  # noqa: F401
from test_billing_email_nome_do_plano import _tudo


def _corpo_do_downsell(capturado) -> str:  # noqa: F811
    """Só o HTML, normalizado. Este e-mail não tem `text_body` — o `send_email`
    o recebe vazio —, então o corpo é um só; e o assunto fica de fora das
    asserções de presença pelo mesmo motivo dos irmãos (ele não carrega a
    frase, e exigi-la dele reprovaria por motivo errado).
    """
    assert es.send_trial_downsell_email("a@b.com")
    partes = [re.sub(r"\s+", " ", parte) for parte in _tudo(capturado)]
    corpos = [p for p in partes if "chegaram ao fim" in p]
    assert len(corpos) == 1, f"esperava um corpo com a frase do fim do teste: {partes}"
    return corpos[0]


def test_o_downsell_nao_diz_que_a_conta_continua_no_gratis(capturado):  # noqa: F811
    """O Grátis como DESTINO morreu; `has_app_access` é False para quem recebe."""
    corpo = _corpo_do_downsell(capturado)
    baixa = corpo.lower()
    assert "continua no plano grátis" not in baixa, corpo
    assert "modo básico" not in baixa, corpo
    assert "voltou pro" not in baixa, corpo


def test_o_downsell_diz_que_o_acesso_fica_bloqueado(capturado):  # noqa: F811
    """O que de fato acontece, e é o oposto do que a copy dizia."""
    corpo = _corpo_do_downsell(capturado)
    assert "sem plano ativo" in corpo, corpo
    assert "acesso ao PigBank fica bloqueado" in corpo, corpo
    assert "Não existe mais versão gratuita" in corpo, corpo


def test_o_downsell_continua_dizendo_que_os_dados_estao_guardados(capturado):  # noqa: F811
    """REQUISITO, não estilo: o corte bloqueia acesso e não apaga nada, e é essa
    frase que separa "sua conta parou" de "seus dados sumiram". Ela já estava na
    copy antiga e tinha de sobreviver ao conserto."""
    corpo = _corpo_do_downsell(capturado)
    assert "dados estão todos guardados" in corpo, corpo


def test_o_downsell_nao_afirma_nem_nega_o_periodo_gratis(capturado):  # noqa: F811
    """O caso comum é `telefone_ja_usou` (medido), mas não é o único: uma conta
    que desvinculou o telefone depois do teste cai em `sem_telefone`, e aí
    "seu telefone já usou" seria falso. Quem sabe é o checkout."""
    corpo = _corpo_do_downsell(capturado)
    baixa = corpo.lower()
    for frase in ("já usou o período", "ja usou o periodo",
                  "você ainda tem 15 dias", "novo período grátis"):
        assert frase not in baixa, f"o downsell afirmou algo sobre o trial: {corpo}"


def test_o_downsell_continua_sendo_uma_oferta(capturado):  # noqa: F811
    """A RAZÃO DE EXISTIR do e-mail, e o que o separa de um aviso de corte.

    Ele sai uma vez, no momento de maior chance de churn, para oferecer o
    Essencial. Um "conserto" de copy que o transformasse em comunicado de
    bloqueio teria consertado a verdade e perdido a conversão."""
    corpo = _corpo_do_downsell(capturado)
    assert "R$ 9,90" in corpo, corpo
    assert "Essencial" in corpo, corpo
    assert "/precos" in corpo, corpo
