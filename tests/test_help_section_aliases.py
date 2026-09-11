"""`ajuda extrato` tem de cair na seção de importação (`ofx`) — e, NESTE BRANCH,
passou a ser também o que o usuário recebe de fato.

São duas coisas que só coincidiram agora. O `resolve_section` já mandava
"extrato" pro `ofx`, mas antes o `ofx` só ganhava do `launches` por ordem de
inserção do dict — desempate invisível que qualquer reordenação de seção
quebraria; um commit anterior deste branch tirou "extrato" do `launches`. E o
usuário continuava recebendo "Comece aqui" em TODA seção pedida por texto,
porque o `answer_help` passava o fragmento ("extrato") pro `resolve_section`,
que só casa o texto inteiro ("ajuda extrato"). Isso foi corrigido em
`core/handlers/help_handler.py` — antes deste branch, nada disso valia na tela.
"""
from core.handlers.help_handler import answer_help
from core.help_text import _SECTION_ALIASES, resolve_section


def test_ajuda_extrato_resolve_para_ofx():
    assert resolve_section("ajuda extrato") == "ofx"


def test_answer_help_entrega_a_secao_pedida():
    """O caminho real (o que o usuário recebe), não só o resolvedor.

    Controle negativo: trocar `help_section(text, ...)` por
    `help_section(text.split(maxsplit=1)[1], ...)` no `answer_help` derruba as
    duas primeiras asserções.
    """
    assert answer_help("help", "ajuda ofx", "whatsapp").startswith(
        "🧾 *Importar extrato ou fatura"
    )
    assert answer_help("help", "ajuda investimentos", "whatsapp").startswith(
        "📈 *Investimentos*"
    )
    # Controle positivo: `ajuda` sozinho continua no menu completo, e uma seção
    # inexistente continua caindo no fallback.
    assert answer_help("help", "ajuda", "whatsapp").startswith("👋 *Comece aqui*")
    assert answer_help("help", "ajuda metas", "whatsapp").startswith("👋 *Comece aqui*")


def test_extrato_nao_esta_mais_em_launches():
    assert "extrato" not in _SECTION_ALIASES["launches"]
