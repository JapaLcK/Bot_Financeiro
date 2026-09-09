"""Setup e CATÁLOGO DE CONTROLES das pendências de cartão.

Sem prefixo `test_` de propósito: o pytest não coleta este arquivo. Ele existe
por dois motivos, e o segundo é o que importa mais.

1. O setup é o mesmo para os três arquivos de teste do assunto
   (`test_pendencia_credito_abandono`, `_portoes`, `_respostas_legitimas`), e
   três cópias de `arma_pay_bill_choice` seriam três testes medindo coisas
   diferentes achando que medem a mesma — mesmo motivo do
   `tests/_paywall_gate_helpers.py` e do `tests/_billing_grants_helpers.py`.

2. O catálogo de controles negativos (A–M) mora AQUI, uma vez. Ele descreve o
   grupo INTEIRO, e repetí-lo em três arquivos seria três versões divergindo em
   silêncio (§0.7). Cada arquivo de teste aponta para cá.

NÃO usa `tests/_paywall_gate_helpers.py` de propósito: a fixture `v2_ligado`
dele liga `PLANS_V2_ENABLED=1`, que não é o mundo destes testes.


Os vizinhos `investment_pick` e `funding_source_choice` já tinham; o bloco das
cinco (`credit_card_setup`, `credit_card_set_primary`, `credit_delete_card`,
`installment_pending`, `pay_bill_choice`) não, e nessas etapas QUALQUER texto
vira resposta. Medido nesta árvore, antes da correção:

  - `installment_pending` + `saldo` → 5 `credit_transactions`, R$ 500, com
    descrição "saldo";
  - `pay_bill_choice` + `quanto gastei no nubank` → PAGA a fatura (R$ 300), por
    `_find_card_name_in_text` casar o nome dentro de qualquer frase;
  - `excluir cartao nubank` → `saldo` → `sim` → cartão apagado em cascata: o
    footgun de 3 turnos, SOMBREADO (o bloco retorna antes do guard anti-órfão,
    e `credit_delete_card` não está em `DESTRUCTIVE_PENDING_TYPES`).

O predicado é o `abandona_pergunta_de_credito` (whitelist derivada do
`ABANDONA`). O defeito tem DUAS direções, logo dois controles:

CONTROLE NEGATIVO A — abandonar de MENOS: `if abandona_pergunta_de_credito(
text):` vira `if False:` (o `if` incondicional de antes). VERMELHOS:

    test_saldo_abandona_sem_escrever[credit_card_setup]
    test_saldo_abandona_sem_escrever[credit_card_set_primary]
    test_saldo_abandona_sem_escrever[credit_delete_card]
    test_saldo_abandona_sem_escrever[installment_pending]
    test_saldo_abandona_sem_escrever[pay_bill_choice]
    test_footgun_tres_turnos_nao_apaga_cartao
    test_pay_bill_choice_nao_paga_com_pergunta_de_gasto
    test_aviso_aparece_quando_abandona

CONTROLE NEGATIVO B — abandonar DEMAIS: troque pela blacklist de antes
(`confidence >= 0.55 and intent != "out_of_scope" and intent not in
("confirm.yes", "confirm.no")`). VERMELHOS:

    test_installment_todas_as_descricoes_registram[comprei uma tv]
    test_installment_todas_as_descricoes_registram[comprei um sofa]
    test_installment_todas_as_descricoes_registram[paguei o notebook]
    test_installment_todas_as_descricoes_registram[gastei com o celular]
    test_installment_todas_as_descricoes_registram[2 passagens]
    test_installment_todas_as_descricoes_registram[10 cadeiras]
    test_installment_todas_as_descricoes_registram[3 camisas]
    test_installment_todas_as_descricoes_registram[2 pneus]
    test_duplicate_card_name_aceita_as_sete_do_is_delete[excluir cartao]
    test_duplicate_card_name_aceita_as_sete_do_is_delete[excluir cartão]

A e B medem defeitos OPOSTOS e nenhum basta sozinho: sem o B o grupo é cego à
classe que a blacklist quebrava (`comprei uma tv` virava despesa de R$ 1,00).

O abandono tem DUAS portas; os controles acima cobrem só a primeira (allowlist
de intent). A segunda é a RESPOSTA NÃO RECONHECIDA num espaço enumerável — ela
alcança o que allowlist nenhuma alcançaria (`excluir cartao nubank`, `tchau`:
out_of_scope/0.00). Três controles:

CONTROLE NEGATIVO C — o casamento por SUBSTRING volta. Em
`core/handlers/credit.py::_resolve_pay_bill_choice`, troque
`_card_name_da_resposta` de volta por `_find_card_name_in_text`:

    test_pay_bill_choice_nao_paga_com_frase_que_contem_o_cartao[quanto tenho na fatura do nubank]
    test_pay_bill_choice_nao_paga_com_frase_que_contem_o_cartao[quando vence o nubank]
    test_pay_bill_choice_nao_paga_com_frase_que_contem_o_cartao[excluir cartao nubank]
    test_pay_bill_choice_nao_paga_com_frase_que_contem_o_cartao[meu nubank fecha quando]
    test_pay_bill_choice_nao_paga_com_frase_que_contem_o_cartao[qual o limite do nubank]
    test_pay_bill_choice_nao_paga_com_frase_que_contem_o_cartao[gastei 50 no nubank]

CONTROLE NEGATIVO D — no `_resolve_delete_card`, o `return None` final vira a
re-pergunta "Responda **sim**…": `test_footgun_tres_turnos_com_qualquer_meio`
[tchau] e [obrigado]. Só estes dois — `oi`/`bom dia` são pegos pela OUTRA porta
(a allowlist), e é por isso que as duas metades vivem no mesmo teste.

CONTROLE NEGATIVO E — tire `"greeting"` do `_ABANDONA_CREDITO`:

    test_saudacao_nao_vira_descricao_de_parcelamento[oi]
    test_saudacao_nao_vira_descricao_de_parcelamento[olá]
    test_saudacao_nao_vira_descricao_de_parcelamento[bom dia]
    test_saudacao_nao_vira_descricao_de_parcelamento[boa tarde]
    test_saudacao_nao_vira_descricao_de_parcelamento[boa noite]

RE-PERGUNTAR × ABANDONAR é escolha por portão; a regra está em
`core/handlers/credit.py`, acima do `_so_numero`. Os dois sentidos, medidos:

CONTROLE NEGATIVO F — o `pay_bill_choice` passa a ABANDONAR (`return None` no
lugar da lista numerada). VERMELHOS:

    test_pay_bill_choice_nao_reconhecida_nunca_paga[setembro/2026]
    test_pay_bill_choice_nao_reconhecida_nunca_paga[09/2026]
    test_pay_bill_choice_nao_reconhecida_nunca_paga[9/26]
    test_pay_bill_choice_nao_reconhecida_nunca_paga[a primeira]
    test_pay_bill_choice_nao_reconhecida_nunca_paga[a segunda]

e a assertiva que cai é a de UX (`AVISO not in resposta`) — as de DINHEIRO
seguem verdes nas duas leituras, que é por que esta perna é UX, não segurança.

CONTROLE NEGATIVO G — os três destrutivos passam a RE-PERGUNTAR: os MESMOS dois
casos do D, mas agora a assertiva que cai é a do CARTÃO APAGADO. Prova de que a
assimetria não é gosto — uniformizar os nove portões para "re-perguntar" reabre
o footgun de 3 turnos.

POSITIVOS da segunda porta ("qual resposta legítima o portão RECUSA?"): 20 do
`pay_bill_choice`, 8 do `_is_yes`, 7 do `_is_no`, o nome no `choose` e os steps
restritos. Medidos em DUAS COLUNAS (main × branch): 49 linhas, 48 idênticas — a
única diferença é a intencional (nome inexistente no `choose` passa a
abandonar). `cancelar` fica fora dos positivos porque o `handle_billing_command`
o intercepta antes do `route()`; igual nas duas colunas, anterior a este PR.

O ESPELHO: os portões acima recusavam RESPOSTA LEGÍTIMA, defeito do mesmo
jeito. Quatro mutantes:

CONTROLES NEGATIVOS H e I — apague o `if not _is_yes(answer) and not
_is_no(answer): return None` do step `reminder_opt_in` (H) e do `set_primary`
(I): morrem `test_reminder_opt_in_recusa_o_que_nao_e_sim_nem_nao` e
`test_set_primary_step_recusa_o_que_nao_e_sim_nem_nao`, [talvez|depois|tchau].
Os dois portões existiam sem NENHUM teste — o Tester apagou as duas linhas e a
suíte inteira ficou verde.

CONTROLE NEGATIVO J — o `_card_name_da_resposta` volta a normalizar SÓ a
resposta (núcleo + `get_card_id_by_name`, que só faz `lower()`): 17 vermelhos,
todo o `test_pay_bill_choice_aceita_nome_com_acento_pontuacao_e_filler` de
`Itaú`, `Cartão Único`, `Méliuz`, `C6-Carbon`, `BTG+`, `Banco do Brasil` e
`Conta`.

CONTROLE NEGATIVO K — o `_so_numero` volta à regex ancorada: 13 vermelhos, todo
o `test_forma_legitima_passa_pelo_portao` de `todo dia 10`, `cinco mil`,
`3 dias antes` etc.

O outro lado de J e K tem de continuar VERDE nos dois:
`test_pay_bill_choice_nao_paga_com_frase_que_contem_o_cartao` e
`test_forma_com_residuo_semantico_continua_recusada`.

CONTROLE NEGATIVO L — o `_ABANDONA_CREDITO` volta ao conjunto de antes (os 12
intents que herdaram a ausência do `ABANDONA` de fora): 13 vermelhos, todo o
`test_comando_de_leitura_abandona_em_vez_de_virar_parcelamento`, incluindo as
duas assimetrias que critério nenhum sustentava (`resumo semanal` com
`report.monthly` já dentro, `investimentos` com `pockets.list` já dentro).

CONTROLE NEGATIVO M — o `_FALA_E_MOEDA` volta a ser lista escrita à mão em vez
de seleção do `MEMORY_STOP_TOKENS`: 5 vermelhos —
`test_fala_e_moeda_nao_divergiu_do_memory_stop_tokens` e os quatro casos de
`test_so_numero_aceita_o_vocabulario_canonico_de_fala` (`5000 pilas`,
`5000 mangos`, `acho que 5000`, `5 mil reais e 50 centavos`).

A TERCEIRA PORTA da mesma classe: SUFIXO e PREFIXO VERBAL (#323). O controle N
desta série media o `_VERBO_CONVERSACIONAL`, REMOVIDO na rodada 9 por ser código
morto — quem cobre a categoria hoje é o controle S.

CONTROLE NEGATIVO O — apague o `while ... in _CORTESIA_FINAL` do
`_leituras_da_resposta`: 12 vermelhos no
`test_pay_bill_choice_aceita_nome_com_acento_pontuacao_e_filler` (`nubank por
favor`, `nubank obrigado`, `itau obrigado`, `btg por favor`, …).

CONTROLE NEGATIVO P — o mais importante dos três, e é o do SENTIDO OPOSTO:
troque `_CORTESIA_FINAL` pelo `_FILLER` na poda de sufixo, que é a simplificação
tentadora ("é a mesma coisa das duas pontas"). 5 vermelhos em
`test_pay_bill_choice_nao_paga_com_comando_depois_do_nome`, e o que eles medem é
`nubank excluir` PAGANDO R$ 300 — um comando de EXCLUIR virando pagamento. As
duas pontas não correm o mesmo risco, e é isso que o conjunto pequeno protege.

A QUARTA porta da mesma classe, e a primeira que virou BUG DE DINHEIRO NOSSO
(Codex no #323, `5be9a6a`). Três controles:

CONTROLE NEGATIVO Q — apare o sufixo de cortesia ANTES de gerar os prefixos
(`for fim in {sem_cortesia}` no lugar do `range`): 3 vermelhos em
`test_pay_bill_choice_paga_o_cartao_especifico_nao_o_generico`. O que eles
medem é `a do nubank pf` PAGANDO A FATURA DO `Nubank` com o `Nubank PF`
cadastrado — cartão errado, R$ 300 no lugar de R$ 700. `pf` é cortesia E é nome
de cartão real ("pessoa física").

CONTROLE NEGATIVO R — inverta a ordem do retorno (`key=len` sem `reverse`): os
MESMOS 3 vermelhos. Gerar as duas leituras não basta; quem resolve o empate
entre `nubank` e `nubank pf` é a ORDEM, e é por isso que
`_leituras_da_resposta` devolve lista e não conjunto.

CONTROLE NEGATIVO S — tire a segunda via do `_so_numero` (`return False` no
lugar do oráculo): 8 vermelhos, as conjugações (`coloque 5000`, `ponha 5000`,
`deixe 3000`, `bote 3 dias`, `quero que seja dia 10`, `me parece 5000`) e os
dois casos de `test_custo_do_oraculo_frase_sem_comando_vira_limite`.

POR QUE UNIÃO (tokens OU oráculo) e não só o oráculo — medido, e é o contrário
do palpite: sozinho, o oráculo QUEBRA 10 respostas legítimas que hoje funcionam
(`5 mil`, `10 mil`, `3 dias`, `3 dias antes`, `5 de cada mes`, `5000 pila`,
`5000 pilas`, `5000 contos`, `5000 mangos`, `5 mil reais e 50 centavos`), todas
`launches.add/0.95`. Trocar tokens POR oráculo reabriria o R3-2 inteiro.

A QUINTA porta: PREFIXO CONVERSACIONAL no nome e NEGATIVA NATURAL (#323).

CONTROLE NEGATIVO T — volte a podar só o prefixo de `_FILLER` (o `for i in
range(fim)` vira a varredura que parava no primeiro não-filler): 13 vermelhos
em `test_pay_bill_choice_aceita_prefixo_conversacional` e
`test_set_primary_choose_aceita_prefixo_conversacional`.

CONTROLE NEGATIVO U — tire o veto de `_VERBO_DE_COMANDO`: 8 vermelhos em
`test_pay_bill_choice_nao_paga_com_comando_apesar_da_poda_livre`. Note QUAIS:
só os casos em que o nome do cartão é SUFIXO da mensagem (`excluir cartao
nubank`, `limite do nubank`, `ver fatura nubank`...). `nubank excluir` e
`nubank fatura` seguem verdes sem o veto, porque ali quem segura é a poda ser
só de prefixo. São DOIS mecanismos independentes, e o teste mede os dois.

CONTROLE NEGATIVO V — `_is_no` volta a só aceitar literais: 10 vermelhos em
`test_reminder_opt_in_aceita_negativa_natural` e
`test_delete_card_negativa_natural_mantem_o_cartao`.

POR QUE NÃO O ORÁCULO no nome do cartão, ao contrário do `_so_numero`: medido,
`nubank excluir` é `out_of_scope/0.00` — o classificador não vê comando —,
então o oráculo o deixaria passar, ele casaria `nubank` e pagaria os R$ 300.
Para nome de cartão a veto-list é obrigatória; para número, o oráculo é o que
evita enumerar conjugação. Ferramentas diferentes para portas diferentes.

DOIS RESÍDUOS CONHECIDOS, medidos e não consertados, os dois pela porta da
ALLOWLIST (não pelos portões): (1) cartão chamado como um comando — `Conta`
perde a resposta `conta` sozinha (`balance.check`/1.0); `a conta` e
`minha conta` funcionam. (2) `greeting` não é mundo fechado (4 regexes com
`re.search` ancorados só no começo), então `opa 5000` e `oi, a do nubank`
abandonam sendo legítimas. O (2) está fixado em
`test_saudacao_seguida_de_resposta_legitima_abandona_sem_escrever`: a direção é
fail-safe — avisa e não escreve. (3) `o nubank mesmo` não casa: a leitura
sempre termina no fim da mensagem, e é ESSA regra que mantém `nubank fatura` e
`nubank saldo` fora. Custo aceito — trocar por busca em qualquer posição
reabriria o mutante P.

HISTÓRICO: `test_credit_limit_ask_nao_vira_limite_de_50` saiu daqui por só
passar com `launches.add` no predicado — o que comia `comprei uma tv`, o teste
puxando o design para o lado inseguro. Voltou como
`test_portao_de_forma_nao_deixa_numero_de_frase_virar_valor`, medindo o portão
de FORMA (`_so_numero`) em vez do predicado, sem tocar no `parse_money`.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

import db
import core.handle_incoming as hi
from core.types import IncomingMessage



AVISO = "Cancelei a pergunta anterior"

_TABELAS_COM_USER_ID: list[str] = []


def escrituras(uid: int) -> dict:
    """Quantas linhas este usuário tem em CADA tabela que tem `user_id`.

    Genérica de propósito (mesmo motivo do `escrituras` do #308): cada
    action_type escreveria numa tabela diferente — transação de cartão, fatura,
    lançamento, cartão, a própria pendência — e enumerar isso à mão é
    exatamente como se esquece uma. Não enxerga UPDATE, por isso o limite do
    cartão e o cartão principal são conferidos à parte.
    """
    with db.get_conn() as conn, conn.cursor() as cur:
        if not _TABELAS_COM_USER_ID:
            cur.execute(
                "select table_name from information_schema.columns "
                "where table_schema = 'public' and column_name = 'user_id'"
            )
            _TABELAS_COM_USER_ID.extend(sorted(r["table_name"] for r in cur.fetchall()))
        return {
            t: cur.execute(
                f'select count(*) as n from "{t}" where user_id = %s', (uid,)
            ).fetchone()["n"]
            for t in _TABELAS_COM_USER_ID
        }


def novo_uid() -> int:
    """Usuário PAGANTE — o gate de plano não pode ser o que segura a mensagem,
    senão o teste mede o gate e não a escotilha."""
    user = db.register_auth_user(f"abandono-{uuid.uuid4().hex[:12]}@t.com", "senha-forte-123")
    uid = int(user["user_id"])
    db.mark_plan_selected(uid)
    return uid


def diga(uid: int, texto: str) -> str:
    """Uma mensagem pelo `handle_incoming` — a conversa, não a função
    (§3 do CLAUDE.md)."""
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text=texto,
        message_id=uuid.uuid4().hex, attachments=[], external_id="", raw={},
    ))
    return "\n".join(m.text for m in out)


def cartao(uid: int) -> int:
    return db.create_card(uid, "Nubank", 10, 17)


# ---------------------------------------------------------------------------
# Armadores das cinco pendências
# ---------------------------------------------------------------------------

def arma_installment(uid: int) -> None:
    db.set_pending_action(uid, "installment_pending", {
        "valor": 500.0, "n": 5, "card_id": cartao(uid), "card_name": "Nubank",
        "purchased_at": date.today().isoformat(), "categoria": "outros",
    })


def arma_pay_bill_choice(uid: int) -> None:
    card_id = cartao(uid)
    db.add_credit_purchase_installments(
        user_id=uid, card_id=card_id, valor_total=300.0, categoria="outros",
        nota="mercado", purchased_at=date.today(), installments=1,
    )
    bill_ids = [int(b["id"]) for b in db.list_open_bills(uid)]
    assert bill_ids, "setup do pay_bill_choice não gerou fatura em aberto"
    db.set_pending_action(uid, "pay_bill_choice", {"bill_ids": bill_ids, "amount": None})


def arma_set_primary(uid: int) -> None:
    db.set_pending_action(uid, "credit_card_set_primary", {"card_id": cartao(uid)})


def arma_delete_card(uid: int) -> None:
    db.set_pending_action(uid, "credit_delete_card",
                          {"card_id": cartao(uid), "card_name": "Nubank"})


def arma_card_setup(uid: int) -> None:
    """"criar cartao" → o bot perguntou o NOME e está esperando."""
    db.set_pending_action(uid, "credit_card_setup", {"step": "name"})


AS_CINCO = [
    ("credit_card_setup", arma_card_setup),
    ("credit_card_set_primary", arma_set_primary),
    ("credit_delete_card", arma_delete_card),
    ("installment_pending", arma_installment),
    ("pay_bill_choice", arma_pay_bill_choice),
]

