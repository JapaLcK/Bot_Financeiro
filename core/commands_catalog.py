"""
core/commands_catalog.py — fonte única do catálogo "O que pedir ao Piggy".

Diferente do `core/help_text.HELP_SECTIONS` (tutor pra quem está aprendendo
o bot do zero), este catálogo é pra quem já sabe o básico e quer **explorar
ao máximo** o que o bot consegue fazer. Cada categoria lista comandos
concretos com exemplos do jeito que o user fala — Pro user pode mandar
qualquer um direto, sem prefixo.

Usado em:
  - Página web `/comandos` (a fonte HTML usa o mesmo conteúdo mas é estática)
  - WhatsApp `adapters/whatsapp/wa_commands_menu.py`
  - Discord (cogs/general_cog.py via lista clicável)

Pra adicionar uma categoria ou comando, edita aqui e os 2 canais ganham
automaticamente.
"""
from __future__ import annotations

from typing import TypedDict


class CommandExample(TypedDict, total=False):
    text: str
    note: str  # opcional: explicação curta tipo "N = número da listagem"


class CommandCategory(TypedDict):
    id: str  # ID curto pra interactive list (max 200 chars)
    emoji: str
    title: str  # max 24 chars (limite WhatsApp list row)
    description: str  # max 72 chars (limite WhatsApp)
    commands: list[CommandExample]


CATALOG: list[CommandCategory] = [
    {
        "id": "cmds_plano",
        "emoji": "💎",
        "title": "Plano e assinatura",
        "description": "Vira Pro, gerencia, vê status do plano",
        "commands": [
            {"text": "assinar plano"},
            {"text": "cancelar plano"},
            {"text": "plano", "note": "vê seu status atual"},
            {"text": "renovar plano"},
        ],
    },
    {
        "id": "cmds_saldo",
        "emoji": "💰",
        "title": "Saldo e lançamentos",
        "description": "Quanto tem, o que entrou, o que saiu",
        "commands": [
            {"text": "saldo"},
            {"text": "gastei 50 no mercado"},
            {"text": "recebi 2000 de salário"},
            {"text": "últimos lançamentos"},
            {"text": "apaga o lançamento #N", "note": "N é o número que aparece na listagem"},
            {"text": "muda a categoria do #N pra alimentação"},
        ],
    },
    {
        "id": "cmds_cartao",
        "emoji": "💳",
        "title": "Cartão de crédito",
        "description": "Compras, faturas, limite, pagamento",
        "commands": [
            {"text": "gastei 200 no Nubank"},
            {"text": "parcelei 1200 em 6x no Nubank"},
            {"text": "minha fatura do Nubank"},
            {"text": "próxima fatura", "note": "projeção da fatura que vai fechar"},
            {"text": "quanto eu devo", "note": "soma de todas as faturas em aberto"},
            {"text": "quanto tenho de limite no Nubank"},
            {"text": "paguei a fatura do Nubank"},
        ],
    },
    {
        "id": "cmds_parcel",
        "emoji": "📅",
        "title": "Parcelamentos",
        "description": "Grupos ativos e próximas cobranças",
        "commands": [
            {"text": "meus parcelamentos", "note": "mostra grupos ativos + datas das próximas cobranças"},
            {"text": "apaga o parcelamento PCxxxxxx", "note": "PCxxxxxx é o código que aparece nos parcelamentos"},
        ],
    },
    {
        "id": "cmds_orca",
        "emoji": "🎯",
        "title": "Orçamentos",
        "description": "Limites por categoria, avisos de estouro",
        "commands": [
            {"text": "define orçamento de 500 em alimentação"},
            {"text": "meus orçamentos", "note": "mostra todos e quanto já gastou"},
            {"text": "como tá meu orçamento de lazer"},
            {"text": "apaga orçamento de lazer"},
        ],
    },
    {
        "id": "cmds_caixa",
        "emoji": "🏦",
        "title": "Caixinhas",
        "description": "Reservas separadas por objetivo",
        "commands": [
            {"text": "cria caixinha viagem"},
            {"text": "deposita 200 na caixinha viagem"},
            {"text": "saca 50 da caixinha viagem"},
            {"text": "minhas caixinhas"},
            {"text": "apaga caixinha viagem", "note": "precisa ter saldo zero"},
        ],
    },
    {
        "id": "cmds_invest",
        "emoji": "📈",
        "title": "Investimentos",
        "description": "CDB, Tesouro, LCI — aporta e resgata",
        "commands": [
            {"text": "cria investimento Tesouro Selic 13.75 anual", "note": "nome, taxa (% anual), periodicidade"},
            {"text": "aporta 500 no Tesouro Selic"},
            {"text": "resgata 200 do Tesouro Selic"},
            {"text": "meus investimentos"},
            {"text": "quanto tenho investido", "note": "total da carteira"},
            {"text": "quanto aportei esse mês"},
        ],
    },
    {
        "id": "cmds_analise",
        "emoji": "📊",
        "title": "Análise e relatórios",
        "description": "Onde vai a grana, comparações, projeções",
        "commands": [
            {"text": "onde gastei mais esse mês", "note": "top categorias"},
            {"text": "meus 5 maiores gastos do mês"},
            {"text": "tendência últimos 6 meses"},
            {"text": "compara abril com maio"},
            {"text": "projeção do mês", "note": '"vou fechar no negativo?"'},
        ],
    },
    {
        "id": "cmds_diario",
        "emoji": "🌅",
        "title": "Relatório diário",
        "description": "Resumo automático todo dia no horário que quiser",
        "commands": [
            {"text": "liga relatório diário"},
            {"text": "desliga relatório diário"},
            {"text": "muda hora do relatório pra 8h"},
        ],
    },
]


CATEGORY_IDS: set[str] = {cat["id"] for cat in CATALOG}


def get_category(cat_id: str) -> CommandCategory | None:
    for cat in CATALOG:
        if cat["id"] == cat_id:
            return cat
    return None


def render_category_body(cat: CommandCategory) -> str:
    """Conteúdo da categoria SEM repetir o título.

    Usado no `body` de mensagens com header próprio (WhatsApp interactive
    buttons já mostra o título no header do componente — se a gente
    repetir aqui fica duplicado na tela). Começa pela descrição, depois
    lista os comandos com bullets. Notas aparecem em linha abaixo.
    """
    lines = [cat["description"], ""]
    for cmd in cat["commands"]:
        lines.append(f"• {cmd['text']}")
        if cmd.get("note"):
            lines.append(f"  _{cmd['note']}_")
    return "\n".join(lines)


def render_category_full(cat: CommandCategory) -> str:
    """Mesma coisa mas com cabeçalho. Usado em fallback de texto puro
    (quando o body é grande demais pra caber no interactive_buttons e
    a gente manda como send_text antes)."""
    return f"{cat['emoji']} *{cat['title']}*\n{render_category_body(cat)}"
