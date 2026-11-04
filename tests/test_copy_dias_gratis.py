"""O "7 dias grátis" escrito à mão na copy bate com `TRIAL_DAYS_DEFAULT` (§0.7).

HTML estático não importa Python, então o número fica duplicado nos CTAs da /
(`index.html`), no título e no subtítulo do S1 (`assinar.html`) e no título que o
`assinar.js` monta. Este teste é o que impede as cópias de divergirem da fonte
(`core/services/plan_service.py`). O S4 não entra: lá o número vem do
`trial_days` do backend.

Negativo medido: trocar um "7" de qualquer um dos três arquivos por "8" deixa vermelho.

Garantia (termos §7): o número da janela de reembolso da landing é o dos termos, nenhum bloco
de "dinheiro de volta" deixa de dizer que ele conta da cobrança, e reembolso é pedido, nunca
automático. Trial (termos §4): na /precos, todo "dias grátis" diz "no cartão". Negativos
medidos: a janela em "8 dias" na landing; "Garantia de 7 dias" num <span> e num <div>; o FAQ
antigo; "Reembolso automático"; um "7 dias grátis" sem "no cartão" na /precos.

O prazo de desistência do e-mail de assinatura `active` não é o trial: a fonte
dele é `termos.html` §7 (Art. 49 do CDC), e o último teste compara os dois.
"""
from __future__ import annotations

import pathlib
import re
from html.parser import HTMLParser

from core.services import email_service as es
from core.services.plan_service import TRIAL_DAYS_DEFAULT

FRONTEND = pathlib.Path(__file__).resolve().parent.parent / "frontend"


def _ler(nome: str) -> str:
    return (FRONTEND / nome).read_text(encoding="utf-8")


def test_os_4_ctas_da_landing_prometem_os_dias_do_default():
    links = re.findall(r'<a\b[^>]*\bdata-utm\b[^>]*>.*?</a>', _ler("index.html"), re.S)
    ctas = [a for a in links if "so-anual" not in a and "so-mensal" not in a]
    dias = [int(n) for a in ctas for n in re.findall(r"(\d+) dias grátis", a)]
    assert dias == [TRIAL_DAYS_DEFAULT] * 4, dias


def test_s1_da_assinar_promete_os_dias_do_default():
    html = _ler("assinar.html")
    titulo = re.search(r'<h1 id="s1-titulo">Seus (\d+) dias grátis do Plus começam aqui</h1>', html)
    sub = re.search(r"a primeira cobrança só vem depois dos (\d+) dias\.", html)
    js = re.search(r'"Seus (\d+) dias grátis do "', _ler("assinar.js"))
    assert titulo and sub and js, "a copy do S1 mudou: atualize este teste junto"
    assert [int(m.group(1)) for m in (titulo, sub, js)] == [TRIAL_DAYS_DEFAULT] * 3


def test_todo_dias_gratis_da_landing_e_o_default():
    dias = [int(n) for n in re.findall(r"(\d+) dias grátis", _ler("index.html"))]
    assert dias and set(dias) == {TRIAL_DAYS_DEFAULT}, dias


def test_janela_de_reembolso_da_landing_e_a_dos_termos():
    """Os termos (§7) são a fonte da janela de reembolso; a landing só repete o número."""
    termos = re.search(r"reembolso integral em\s+até\s+<strong>(\d+) dias</strong>", _ler("termos.html"))
    assert termos, "o texto do §7 dos termos mudou: atualize este teste junto"
    landing = re.findall(r"Pode pedir reembolso até (\d+) dias após a 1ª cobrança"
                         r"|você tem (?:mais )?(\d+) dias(?: a partir dela)? pra pedir 100% de volta", _ler("index.html"))
    numeros = [int(a or b) for a, b in landing]
    assert len(numeros) == 3 and set(numeros) == {int(termos.group(1))}, numeros


_BLOCOS = {"p", "div", "li", "section", "details", "summary", "footer", "header", "main", "nav",
           "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dd", "dt", "blockquote", "figcaption", "form"}


class _Textos(HTMLParser):
    """Cada nó de texto visível (fora de <script>/<style>), com o texto inteiro do bloco que o
    contém (o <p>, <div>, <li>… mais próximo, com o que vem dentro dele)."""

    def __init__(self):
        super().__init__()
        self.blocos: list[list] = []          # pilha de [tag, texto]
        self.nos: list[tuple[str, list]] = []  # (texto do nó, bloco mais próximo)
        self.codigo = False

    def handle_starttag(self, tag, attrs):
        self.codigo = self.codigo or tag in ("script", "style")
        if tag in _BLOCOS:
            self.blocos.append([tag, ""])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.codigo = False
        if tag in _BLOCOS and any(b[0] == tag for b in self.blocos):
            while self.blocos.pop()[0] != tag:
                pass

    def handle_data(self, data):
        if self.codigo or not data.strip():
            return
        for bloco in self.blocos:
            bloco[1] += data
        self.nos.append((" ".join(data.split()), self.blocos[-1] if self.blocos else ["", data]))


def _ler_textos(nome: str) -> list[tuple[str, str]]:
    """(texto do nó, texto do bloco) de cada nó visível da página."""
    leitor = _Textos()
    leitor.feed(_ler(nome))
    leitor.close()
    return [(no, " ".join(bloco[1].split())) for no, bloco in leitor.nos]


_DINHEIRO_DE_VOLTA = re.compile(r"de volta|reembols|devolv|dinheiro volta", re.I)


def test_garantia_nao_aparece_solta_e_reembolso_sempre_cita_a_cobranca():
    """Teste grátis ≠ garantia: o reembolso conta da 1ª cobrança (termos §7), então nenhum
    selo "Garantia de N dias" sozinho, em tag nenhuma, e todo bloco que fala de dinheiro de
    volta diz de qual cobrança."""
    textos = _ler_textos("index.html")
    soltos = [no for no, _ in textos if re.fullmatch(r"Garantia de \d+ dias\.?", no)]
    assert not soltos, soltos
    reembolso = [bloco for no, bloco in textos if _DINHEIRO_DE_VOLTA.search(no)]
    assert reembolso, "a landing parou de falar do reembolso: o teste não mede nada"
    sem_cobranca = [b for b in reembolso if "cobrança" not in b]
    assert not sem_cobranca, sem_cobranca


def test_reembolso_e_pedido_nunca_automatico_na_landing_nem_na_precos():
    """O reembolso é PEDIDO ao suporte (termos §7): nada de "automático" nem de "devolvo"."""
    falam = [no for nome in ("index.html", "precos.html") for no, _ in _ler_textos(nome)
             if _DINHEIRO_DE_VOLTA.search(no)]
    assert falam, "nenhum texto de reembolso achado: o teste não mede nada"
    prometem = [no for no in falam if re.search(r"autom|devolv", no, re.I)]
    assert not prometem, prometem


_DIAS = r"(?:\d+|\$\{[^}`\n]*\})\s+dias grátis"   # número literal ou `${variável}` de template
_LITERAIS = {q: re.compile(q + r"((?:[^" + q + r"\n])*" + _DIAS + r"[^" + q + r"\n]*)" + q) for q in ('"', "'", "`")}


def _literais(codigo: str) -> dict[str, list[str]]:
    """Os literais de string de uma linha (aspas duplas, simples e template) que falam em
    "N dias grátis" ou "${x} dias grátis", separados pelo tipo de aspas."""
    return {q: rx.findall(codigo) for q, rx in _LITERAIS.items()}


def test_todo_dias_gratis_da_precos_e_no_cartao():
    """O trial é só no cartão (termos §4): na /precos todo "dias grátis" com número vem com
    "no cartão". Cobre: o texto visível do HTML estático (o bloco do nó precisa ter "no cartão"),
    as duas metas (description e og:description), os literais de string de UMA linha dos <script>
    da página e da ilha React (comparison-3.tsx) em aspas duplas, simples ou template — com número
    literal ou `${variável}`.

    Não cobre: texto montado por concatenação ou em literal de várias linhas, e o que vem do
    servidor pronto (nenhum hoje)."""
    html = _ler("precos.html")
    blocos = [bloco for no, bloco in _ler_textos("precos.html") if re.search(_DIAS, no)]
    metas = re.findall(r'<meta\b[^>]*content="([^"]*' + _DIAS + r'[^"]*)"', html)
    pagina = _literais("\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S)))
    ilha = _literais((FRONTEND.parent / "webapp/src/components/ui/comparison-3.tsx").read_text(encoding="utf-8"))
    # O que existe hoje, para o teste não passar vazio: o subtítulo e a nota (texto), as 2 metas, o
    # gate de escolha de plano (aspas duplas), a frase da retomada com desconto (template) e a ilha.
    assert len(blocos) >= 2 and len(metas) == 2 and pagina['"'] and pagina["`"] and ilha['"'], (blocos, metas, pagina, ilha)
    todos = blocos + metas + [t for grupo in (pagina, ilha) for lista in grupo.values() for t in lista]
    sem_cartao = [t for t in todos if "no cartão" not in t]
    assert not sem_cartao, sem_cartao


def test_o_prazo_de_desistencia_do_email_active_bate_com_os_termos(monkeypatch):
    caixa: list[dict] = []
    monkeypatch.setattr(es, "send_email", lambda **k: caixa.append(k) or True)
    es.send_pro_welcome_email("a@b.com", "pro", None, "active", False)
    (msg,) = caixa
    termos = re.search(r"pedir o reembolso integral em\s+até <strong>(\d+) dias</strong>", _ler("termos.html"))
    assert termos, "a redação da §7 dos termos mudou: atualize este teste junto"
    for corpo in (msg["html_body"], msg["text_body"]):
        email = re.search(r"Em até (\d+) dias você pode desistir", corpo)
        assert email, corpo
        assert email.group(1) == termos.group(1), (email.group(1), termos.group(1))
