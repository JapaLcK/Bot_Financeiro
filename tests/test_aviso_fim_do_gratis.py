"""
tests/test_aviso_fim_do_gratis.py — a SQL do aviso × o predicado Python, com
banco real.

O aviso de corte (`scripts/aviso_fim_do_gratis.py`) e o gate de acesso
(`plan_service.tem_direito_hoje`) respondem à MESMA pergunta em duas
linguagens: "esta conta tem direito de uso hoje?". Discordando, alguém é
cortado sem aviso ou avisado à toa. Este arquivo monta o espaço de estados,
roda os dois lados e afirma que as partições são COMPLEMENTARES — ninguém nos
dois lados, ninguém fora dos dois. O que ele mede é CONCORDÂNCIA: fica vermelho
no dia em que alguém mexer num lado só.

O espaço de estados mora em `_aviso_fim_do_gratis_helpers.py`, e cada conta
dele é um jeito conhecido de os dois lados divergirem — vitalício
(`plan_expires_at IS NULL`, o que sozinho justifica o arquivo), pago vigente ×
expirado, `trialing`, grant do admin, as duas bordas de `DUNNING_GRACE_DAYS`,
status em caixa mista, e o assinante em dunning com período vencido.

**O LAÇO do script (dry-run, dedupe, lote, copy, guarda do `--corte`) é o
arquivo irmão `test_aviso_fim_do_gratis_lote.py`** — assunto diferente, e o
teto de 350 linhas do `tests/test_max_lines_python.py` chegou junto com a
segunda responsabilidade (§0.5).

A conta só-WhatsApp e a que nunca escolheu plano ficam FORA do universo de
propósito: as duas são "sem direito E sem aviso", o par que a complementaridade
proíbe DENTRO dele. O universo é a população possível pela política — conta com
`plan_selected_at` e e-mail —, e cada uma das duas tem asserção própria.

O §3 do `CLAUDE.md` dispensa os DOIS CONTROLES para tabela de entrada/saída
como esta; os abaixo existem porque foram medidos, não por cerimônia.

CONTROLES MEDIDOS (`docs/controles_declarados.md`: injeção que muda VALOR, nunca
que apaga pedaço de expressão; só os VERMELHOS nomeados; sem contagem). Todos
mexem no `where` de `listar_contas_do_aviso` ou no predicado Python, então
reprovam também testes do arquivo irmão — os nomes de lá estão marcados
`[lote]`:

  • A — trate vitalício como expirado: troque
    `(plan_expires_at is null or plan_expires_at > now())` por
    `(coalesce(plan_expires_at, now() - interval '1 day') > now())`:
      VERMELHO: test_sql_do_aviso_e_o_predicado_sao_complementares,
                test_vitalicio_e_grant_admin_ficam_de_fora,
                [lote] test_dry_run_nao_envia_e_a_segunda_rodada_nao_reenvia,
                [lote] test_revalidacao_que_estoura_nao_aborta_o_lote,
                [lote] test_o_coorte_de_cada_conta_decide_a_copy.
    **NÃO use `(plan_expires_at > now())` sozinho**: medido, todos passam.
    `null > now()` é NULL, o `not (... and NULL)` é NULL e a linha cai fora do
    `where` do mesmo jeito — a lógica de três valores mascara a injeção.
  • B — tire a normalização de caixa do status da SQL: troque
    `lower(coalesce(last_payment_status, ''))` por
    `coalesce(last_payment_status, '')`:
      VERMELHO: test_sql_do_aviso_e_o_predicado_sao_complementares,
                [lote] test_dry_run_nao_envia_e_a_segunda_rodada_nao_reenvia,
                [lote] test_revalidacao_que_estoura_nao_aborta_o_lote,
                [lote] test_o_coorte_de_cada_conta_decide_a_copy.
    Discrimina por `CAIXA_MISTA`, que ENTRA na população indevidamente — falso
    positivo: aviso de fim de acesso para quem está na carência.
  • C — alargue a janela SÓ na SQL: em `params`, troque `DUNNING_GRACE_DAYS`
    por `DUNNING_GRACE_DAYS + 30`:
      VERMELHO: os mesmos quatro do B.
    Discrimina por `CARENCIA_8D`, que SAI da população — falso negativo, a
    direção OPOSTA à do B: corte sem aviso. **B e C não são duas grafias da
    mesma injeção**: contas diferentes, direções opostas, propriedades
    diferentes (caixa do status × largura da janela). O corolário do
    `docs/controles_declarados.md` ("mesmo vermelho → apague uma") não se
    aplica a teste diferencial, onde toda injeção no `where` cai nas mesmas
    asserções — o critério certo é "a mesma conta, na mesma direção", e está
    corrigido lá.
  • D — alargue a janela SÓ no Python: em `billing_dunning.carencia_aberta`,
    troque `timedelta(days=DUNNING_GRACE_DAYS)` por
    `timedelta(days=DUNNING_GRACE_DAYS + 30)`:
      VERMELHO: test_sql_do_aviso_e_o_predicado_sao_complementares.
    C e D são a MESMA janela vista dos dois lados, e é por isso que as duas
    entram: o par prova que o arquivo mede a CONCORDÂNCIA, não um lado só.
  • E — restrinja o termo do relógio ao Grátis: dentro do
    `not (past_due_since is not null ...)`, acrescente
    `lower(coalesce(plan, '')) = 'free' and` antes do primeiro termo:
      VERMELHO: test_sql_do_aviso_e_o_predicado_sao_complementares,
                [lote] test_dry_run_nao_envia_e_a_segunda_rodada_nao_reenvia,
                [lote] test_revalidacao_que_estoura_nao_aborta_o_lote,
                [lote] test_o_coorte_de_cada_conta_decide_a_copy.
    Quem discrimina é UMA conta — `PAGO_EXPIRADO_EM_CARENCIA`, o assinante com
    cartão recusado, que SAI da população (falso negativo). Medido: com ela
    trocada por `plan='free'` (a cobertura anterior, só `CARENCIA_*` no
    Grátis) esta injeção fica INVISÍVEL.

DOIS TERMOS DO `where` FICAM SEM CONTROLE, e a razão é alcançabilidade medida,
não esquecimento:

  • **a caixa do `plan`** (só o `status` tem `CAIXA_MISTA`). `plan` em caixa
    mista não tem writer: `core.admin_dashboard.set_account_plan` faz
    `.strip().lower()` contra `ADMIN_PLAN_VALUES`, e o webhook grava o valor de
    `TIER_TO_STORED_PLAN`, todo minúsculo. **Vale registrar a direção do dano,
    que é a pior possível**: sem o `lower()`, um cliente PAGANTE com `plan` em
    caixa mista cairia na população e receberia "seu acesso acaba";
  • **o termo de e-mail** (`email is not null and email <> ''`):
    `auth_accounts.email` é `not null` no schema e nenhum writer o esvazia.

Fechá-los seria blindagem de teste contra estado que o banco não produz — a
decisão é não fechar, e reabri-la exige antes um writer que crie o estado.
"""
from __future__ import annotations

from _aviso_fim_do_gratis_helpers import (
    ESPERADO_AVISAR,
    GRANDFATHERED,
    GRANT_ADMIN,
    NUNCA_ESCOLHEU,
    SO_WHATSAPP,
    UNIVERSO,
    avisados as _avisados,
    montar_base,
)
from core.services.plan_service import tem_direito_hoje
from db import get_auth_user


def test_sql_do_aviso_e_o_predicado_sao_complementares():
    montar_base()
    avisados = _avisados() & UNIVERSO
    com_direito = {uid for uid in UNIVERSO if tem_direito_hoje(get_auth_user(uid))}

    # A tabela explícita vem ANTES da complementaridade de propósito: sozinha,
    # a complementaridade também passaria com os dois lados errados do mesmo
    # jeito (o vitalício avisado por ambos, por exemplo).
    assert avisados == ESPERADO_AVISAR
    assert com_direito == UNIVERSO - ESPERADO_AVISAR

    assert avisados & com_direito == set(), "conta nos DOIS lados"
    assert avisados | com_direito == UNIVERSO, "conta FORA dos dois lados"


def test_vitalicio_e_grant_admin_ficam_de_fora():
    """Os dois casos que uma reescrita ingênua da SQL quebra primeiro, com
    asserção nomeada — o teste acima só diz "conjunto diferente"."""
    montar_base()
    avisados = _avisados()
    assert GRANDFATHERED not in avisados, "vitalício (plan_expires_at NULL) avisado"
    assert GRANT_ADMIN not in avisados, "grant do admin avisado"
    assert tem_direito_hoje(get_auth_user(GRANDFATHERED)) is True
    assert tem_direito_hoje(get_auth_user(GRANT_ADMIN)) is True


def test_fora_do_universo_os_dois_lados_dizem_nao_avisa():
    """Nunca escolheu plano e só-WhatsApp: sem direito E sem aviso.

    As duas contas estão fora do universo por razões DIFERENTES, e a segunda é
    uma decisão, não uma limitação técnica. Quem nunca escolheu plano já está
    barrado e não perde nada no corte. Já o **só-WhatsApp** (sem linha em
    `auth_accounts`) tem hoje o produto completo — `has_app_access` é True
    incondicional com o v2 ligado e o gate do bot só exige plano quando a linha
    existe —, **perde acesso no corte** e **não é avisado**. Existe canal para
    alcançá-lo (o WhatsApp); o que falta é template aprovado na Meta, e **o
    dono decidiu cortar essa população sem aviso prévio**, deixando a
    comunicação para a mensagem de bloqueio do bot (PR A). A garantia deste PR
    é "ninguém com cadastro web e e-mail é cortado sem aviso" — o registro
    completo está na docstring de `plan_service.tem_direito_hoje`.
    """
    montar_base()
    avisados = _avisados()

    assert NUNCA_ESCOLHEU not in avisados
    assert tem_direito_hoje(get_auth_user(NUNCA_ESCOLHEU)) is False

    assert get_auth_user(SO_WHATSAPP) is None
    assert SO_WHATSAPP not in avisados
    assert tem_direito_hoje(None) is False
