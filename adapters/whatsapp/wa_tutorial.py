# adapters/whatsapp/wa_tutorial.py
"""
Tutorial interativo via botões do WhatsApp Cloud API.

Máquina de estados simples (in-memory) com 6 passos guiados,
navegação para frente/trás e possibilidade de pular.

IDs de botões gerenciados aqui:
  tut_start, tut_skip
  tut_2, tut_3, tut_4, tut_5, tut_6
  tut_done
  tut_back_1 … tut_back_5
"""
from __future__ import annotations

import logging
import time

from adapters.whatsapp.wa_client import (
    send_interactive_buttons,
    send_text,
)

logger = logging.getLogger(__name__)

# ── Estado em memória ───────────────────────────────────────────────────────
# { wa_id: {"step": str, "at": float} }
_STATE: dict[str, dict] = {}
_TTL = 3600  # 1 hora — tutorial expira após isso

# ── IDs de botões que pertencem ao tutorial ─────────────────────────────────
TUTORIAL_BUTTON_IDS: set[str] = {
    "tut_start", "tut_skip",
    "tut_2", "tut_3", "tut_4", "tut_5", "tut_6",
    "tut_done",
    "tut_back_1", "tut_back_2", "tut_back_3",
    "tut_back_4", "tut_back_5",
}


# ── Helper ──────────────────────────────────────────────────────────────────

def get_tutorial_button_id(raw: dict) -> str | None:
    """
    Retorna o ID do botão se o payload for um clique de botão do tutorial,
    senão None.
    """
    if raw.get("type") != "interactive":
        return None
    inter = raw.get("interactive") or {}
    br = inter.get("button_reply") or {}
    bid = br.get("id") or ""
    return bid if bid in TUTORIAL_BUTTON_IDS else None


# ── Passos do tutorial ───────────────────────────────────────────────────────

def _step_1(wa_id: str) -> None:
    _STATE[wa_id] = {"step": "step_1", "at": time.time()}
    send_interactive_buttons(
        to=wa_id,
        header="Passo 1 de 6 — Lançamentos 📝",
        body=(
            "Registre qualquer movimentação em linguagem natural:\n\n"
            "• *gastei 50 no mercado*\n"
            "• *recebi 1000 de salário*\n"
            "• *paguei 80 de luz*\n"
            "• *ganhei 300 de freela*\n\n"
            "Não precisa de comando exato — escreva do seu jeito! 😊\n\n"
            "Quer desfazer? Só dizer *desfazer*."
        ),
        footer="Próximo: verificar saldo e histórico",
        buttons=[
            {"id": "tut_2",    "title": "➡️ Próximo"},
            {"id": "tut_skip", "title": "⚡ Pular tutorial"},
        ],
    )


def _step_2(wa_id: str) -> None:
    _STATE[wa_id] = {"step": "step_2", "at": time.time()}
    send_interactive_buttons(
        to=wa_id,
        header="Passo 2 de 6 — Saldo e histórico 💰",
        body=(
            "Consulte sua situação financeira a qualquer hora:\n\n"
            "• *saldo* → saldo atual da conta\n"
            "• *listar lançamentos* → histórico com IDs\n"
            "• *desfazer* → cancela o último registro\n"
            "• *apagar 42* → apaga um lançamento pelo ID\n\n"
            "Os IDs aparecem ao listar — use-os para apagar registros específicos."
        ),
        footer="Próximo: caixinhas de poupança",
        buttons=[
            {"id": "tut_3",      "title": "➡️ Próximo"},
            {"id": "tut_back_1", "title": "⬅️ Anterior"},
        ],
    )


def _step_3(wa_id: str) -> None:
    _STATE[wa_id] = {"step": "step_3", "at": time.time()}
    send_interactive_buttons(
        to=wa_id,
        header="Passo 3 de 6 — Caixinhas 📦",
        body=(
            "Separe dinheiro para seus objetivos:\n\n"
            "• *criar caixinha viagem*\n"
            "• *coloquei 300 na caixinha viagem*\n"
            "• *retirei 100 da caixinha viagem*\n"
            "• *saldo caixinhas* → saldo de todas\n"
            "• *listar caixinhas* → ver todas\n"
            "• *excluir caixinha viagem*\n\n"
            "🎯 Perfeito para reserva de emergência, férias e metas!"
        ),
        footer="Próximo: investimentos",
        buttons=[
            {"id": "tut_4",      "title": "➡️ Próximo"},
            {"id": "tut_back_2", "title": "⬅️ Anterior"},
        ],
    )


def _step_4(wa_id: str) -> None:
    _STATE[wa_id] = {"step": "step_4", "at": time.time()}
    send_interactive_buttons(
        to=wa_id,
        header="Passo 4 de 6 — Investimentos 📈",
        body=(
            "Acompanhe suas aplicações com rendimento automático:\n\n"
            "• *criar investimento CDB 110% CDI*\n"
            "• *criar investimento Tesouro 0,03% ao dia*\n"
            "• *apliquei 200 no investimento CDB*\n"
            "• *retirei 100 do investimento CDB*\n"
            "• *listar investimentos*\n"
            "• *ver cdi* → taxa CDI atual\n\n"
            "💡 O rendimento é calculado automaticamente pelo bot!"
        ),
        footer="Próximo: importar extrato bancário",
        buttons=[
            {"id": "tut_5",      "title": "➡️ Próximo"},
            {"id": "tut_back_3", "title": "⬅️ Anterior"},
        ],
    )


def _step_5(wa_id: str) -> None:
    _STATE[wa_id] = {"step": "step_5", "at": time.time()}
    send_interactive_buttons(
        to=wa_id,
        header="Passo 5 de 6 — Extrato OFX 🧾",
        body=(
            "Importe seu extrato bancário sem digitar nada:\n\n"
            "1️⃣ Exporte o arquivo *.ofx* no seu internet banking\n"
            "2️⃣ Anexe o arquivo aqui no WhatsApp\n"
            "3️⃣ Digite *importar ofx* junto com o envio\n\n"
            "✅ Lançamentos duplicados são detectados e ignorados.\n"
            "🏷️ Configure regras de categoria para auto-categorizar (digita *ajuda categorias*)."
        ),
        footer="Último passo — quase lá! 🎉",
        buttons=[
            {"id": "tut_6",      "title": "➡️ Próximo"},
            {"id": "tut_back_4", "title": "⬅️ Anterior"},
        ],
    )


def _step_6(wa_id: str) -> None:
    _STATE[wa_id] = {"step": "step_6", "at": time.time()}
    send_interactive_buttons(
        to=wa_id,
        header="Passo 6 de 6 — Dashboard 🖥️",
        body=(
            "Visualize tudo num painel interativo e em tempo real:\n\n"
            "• *dashboard* → recebe o link do seu painel pessoal\n"
            "• Atualiza automaticamente a cada 30 segundos\n"
            "• Gráficos de gastos, saldo e histórico mensal\n"
            "• Funciona no celular e no computador\n\n"
            "📊 Ideal para ter uma visão completa das suas finanças!"
        ),
        footer="Você chegou ao fim do tutorial!",
        buttons=[
            {"id": "tut_done",   "title": "✅ Concluir"},
            {"id": "tut_back_5", "title": "⬅️ Anterior"},
        ],
    )


def _step_done(wa_id: str) -> None:
    _STATE.pop(wa_id, None)
    send_text(
        to=wa_id,
        body=(
            "🎉 *Tutorial concluído — parabéns!*\n\n"
            "Agora você domina o PigBank AI.\n\n"
            "📌 *Atalhos para não esquecer:*\n"
            "• *ajuda* → menu completo de comandos\n"
            "• *ajuda caixinhas* → ajuda por tema\n"
            "• *tutorial* → rever este guia a qualquer hora\n"
            "• *dashboard* → seu painel visual\n\n"
            "👉 Que tal testar agora?\n"
            "Tente: *gastei 10 no café* ☕"
        ),
    )


def _step_skip(wa_id: str) -> None:
    _STATE.pop(wa_id, None)
    send_text(
        to=wa_id,
        body=(
            "✅ *Pode usar à vontade!*\n\n"
            "💡 *Comandos básicos para começar:*\n"
            "• *gastei 50 mercado*\n"
            "• *recebi 1000 salario*\n"
            "• *saldo*\n"
            "• *listar lançamentos*\n\n"
            "Precisa de ajuda? Digite *ajuda* para o menu completo\n"
            "ou *tutorial* para refazer o tour. 😊"
        ),
    )


# ── Mapa de ações ────────────────────────────────────────────────────────────

_ACTION_MAP: dict[str, object] = {
    "tut_start":  _step_1,
    "tut_2":      _step_2,
    "tut_3":      _step_3,
    "tut_4":      _step_4,
    "tut_5":      _step_5,
    "tut_6":      _step_6,
    "tut_done":   _step_done,
    "tut_skip":   _step_skip,
    # back buttons
    "tut_back_1": _step_1,
    "tut_back_2": _step_2,
    "tut_back_3": _step_3,
    "tut_back_4": _step_4,
    "tut_back_5": _step_5,
}


# ── API pública ──────────────────────────────────────────────────────────────

def send_welcome(wa_id: str) -> None:
    """
    Envia a mensagem de boas-vindas interativa com botão para iniciar o tutorial.
    Chamada quando o WhatsApp é vinculado à conta com sucesso.
    """
    send_interactive_buttons(
        to=wa_id,
        header="🎉 Bem-vindo ao PigBank AI!",
        body=(
            "Sou seu assistente financeiro pessoal no WhatsApp.\n\n"
            "Com apenas uma mensagem de texto, você consegue:\n\n"
            "💰 Registrar gastos e receitas\n"
            "📊 Consultar seu saldo em tempo real\n"
            "📦 Criar caixinhas de poupança\n"
            "📈 Acompanhar seus investimentos\n"
            "🧾 Importar extratos bancários (.OFX)\n"
            "🖥️ Acessar seu dashboard interativo\n\n"
            "Quer um tour rápido de 2 minutos? 👇"
        ),
        footer="PigBank AI — seu dinheiro, sob controle",
        buttons=[
            {"id": "tut_start", "title": "🚀 Começar tour!"},
            {"id": "tut_skip",  "title": "⚡ Usar direto"},
        ],
    )


def handle_tutorial_button(wa_id: str, button_id: str) -> None:
    """
    Processa um clique de botão do tutorial e envia o próximo passo.
    """
    fn = _ACTION_MAP.get(button_id)
    if fn:
        logger.info("Tutorial step=%s wa_id=%s", button_id, wa_id)
        fn(wa_id)  # type: ignore[call-arg]
    else:
        logger.warning("Tutorial: botão desconhecido id=%s wa_id=%s", button_id, wa_id)
