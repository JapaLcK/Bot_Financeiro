"""As três TTFs do app (`app/assets/fonts/`) são espelho das woff2 do site
(`frontend/fonts/`), convertidas com fontTools — não uma fonte baixada à parte.

O app carrega fonte local (`require()`, ver `app/app/_layout.tsx`), não a
mesma URL do site; sem este teste, as duas árvores podem divergir em silêncio
(peso errado, subset diferente, versão velha) e só apareceria no aparelho.

Compara estrutura (conjunto de glifos, cmap, versão) em vez de bytes crus: o
TTF não é woff2 recomprimido, é o MESMO grafo salvo sem a flag `flavor`, com
`cv11`+`ss01`+`ss03` congeladas (ver `app/assets/fonts/README.md`).
"""
from __future__ import annotations

import hashlib
import pathlib
import re

from fontTools.ttLib import TTFont

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FONTES_APP = RAIZ / "app" / "assets" / "fonts"
FONTES_SITE = RAIZ / "frontend" / "fonts"
STICKERS_APP = RAIZ / "app" / "assets" / "stickers"
STICKERS_SITE = RAIZ / "frontend" / "brand" / "stickers"
SIMBOLO_APP = RAIZ / "app" / "assets" / "brand" / "simbolo.png"
SIMBOLO_SITE = RAIZ / "frontend" / "brand" / "email-logo.png"
CONNECTION_STATUS_TS = RAIZ / "app" / "src" / "ui" / "componentes" / "ConnectionStatus.tsx"
APP_CONFIG_TS = RAIZ / "app" / "app.config.ts"
VOLTA_OF_TS = RAIZ / "app" / "src" / "features" / "openFinance" / "volta.ts"
ATUALIZAR_BANCO_TS = RAIZ / "app" / "src" / "features" / "painel" / "atualizarBanco.ts"
SERVICO_OF_TS = RAIZ / "app" / "src" / "services" / "openFinance.ts"
OF_STATUS_POLL_JS = RAIZ / "frontend" / "of-status-poll.js"
SETTINGS_HTML = RAIZ / "frontend" / "settings.html"

PESOS = ["Regular", "Medium", "SemiBold"]

# `Texto.tsx` liga `fontVariant: ["tabular-nums"]` em valor numérico — sem a
# feature `tnum` o número desalinha sem erro (mesmo risco do
# `test_inter_subset.py` para o site). U+2212 é o menos tipográfico que
# `Money.tsx` (PR B) usa para saída negativa, nunca o hífen `-`; U+2022 é o
# ponto da máscara de valor oculto ("R$ ••••").
GLIFOS_OBRIGATORIOS = {0x2212: "− (U+2212)", 0x2022: "• (U+2022)"}


def _par(peso: str) -> tuple[pathlib.Path, pathlib.Path]:
    return (FONTES_APP / f"Inter-{peso}.ttf", FONTES_SITE / f"Inter-{peso}.woff2")


def _nomes_reais(font: TTFont) -> set[str]:
    return {n for n in font.getGlyphOrder() if not re.fullmatch(r"glyph\d+", n)}


def test_os_tres_pesos_existem_dos_dois_lados():
    for peso in PESOS:
        ttf, woff2 = _par(peso)
        assert ttf.is_file(), f"falta {ttf}"
        assert woff2.is_file(), f"falta a origem {woff2}"


def test_ttf_e_o_ttf_puro_do_mesmo_grafo_da_woff2():
    """Mesmo desenho, formato de arquivo diferente: TTF não pode carregar a
    compressão woff2 (Metro/Expo não entendem woff2 em `require()`)."""
    for peso in PESOS:
        ttf_path, woff2_path = _par(peso)
        ttf = TTFont(ttf_path)
        woff2 = TTFont(woff2_path)
        assert ttf.flavor is None, f"{ttf_path.name} não é TTF puro (flavor={ttf.flavor})"
        assert woff2.flavor == "woff2", f"{woff2_path.name} não é a woff2 de origem"
        # O freeze (pyftfeatfreeze) PERMUTA glifos: a ordem, os nomes sintéticos
        # `glyphNNNNN` e o `hmtx` por nome divergem da woff2. Igual: o total, os
        # nomes reais e a multiset das métricas; um peso copiado por cima de
        # outro muda a multiset.
        assert len(ttf.getGlyphOrder()) == len(woff2.getGlyphOrder()), (
            f"{ttf_path.name} tem número de glifos diferente da origem — não é a mesma conversão"
        )
        assert _nomes_reais(ttf) == _nomes_reais(woff2), (
            f"{ttf_path.name} tem glifos nomeados diferentes da origem — não é a mesma conversão"
        )
        assert ttf.getBestCmap() == woff2.getBestCmap(), (
            f"{ttf_path.name} tem cmap diferente da origem"
        )
        assert ttf["head"].fontRevision == woff2["head"].fontRevision, (
            f"{ttf_path.name} é de uma versão da Inter diferente da woff2 servida ao site"
        )
        # `usWeightClass` e `hmtx` pegam o defeito que glyph order/cmap/versão
        # não pegam: um peso copiado por cima de outro (Bold sobre Regular)
        # tem o MESMO grafo (mesmo alfabeto, mesmo cmap) e só denuncia pelo
        # peso declarado e pela métrica horizontal de cada glifo.
        assert ttf["OS/2"].usWeightClass == woff2["OS/2"].usWeightClass, (
            f"{ttf_path.name} tem usWeightClass diferente da woff2 — peso trocado na conversão"
        )
        assert sorted(ttf["hmtx"].metrics.values()) == sorted(woff2["hmtx"].metrics.values()), (
            f"{ttf_path.name} tem métricas horizontais diferentes da woff2 — peso trocado na conversão"
        )


def test_ttf_tem_o_freeze_aplicado():
    """O TTF cru do site tem o `a` de dois andares trocado pelo de um andar
    (`cv11`) só via feature; o app congela isso no glifo. Com o freeze, o
    GID do `a` (U+0061) no TTF difere do da woff2 — se alguém recolocar o
    TTF cru, os GIDs voltam a ser iguais e este teste fica vermelho."""
    for peso in PESOS:
        ttf_path, woff2_path = _par(peso)
        gid = lambda f: (lambda font: font.getGlyphID(font.getBestCmap()[0x61]))(f)
        assert gid(TTFont(ttf_path)) != gid(TTFont(woff2_path)), (
            f"{ttf_path.name} sem o freeze cv11/ss01/ss03 (igual ao TTF cru do site)"
        )


def test_ttf_preserva_tabular_nums():
    for peso in PESOS:
        ttf_path, _ = _par(peso)
        font = TTFont(ttf_path)
        feats = {rec.FeatureTag for rec in font["GSUB"].table.FeatureList.FeatureRecord}
        assert "tnum" in feats, f"{ttf_path.name} perdeu a feature tnum na conversão"


def test_ttf_tem_os_glifos_que_o_app_usa_fora_do_alfabeto_comum():
    for peso in PESOS:
        ttf_path, _ = _par(peso)
        cmap = TTFont(ttf_path).getBestCmap()
        faltando = [nome for codepoint, nome in GLIFOS_OBRIGATORIOS.items() if codepoint not in cmap]
        assert not faltando, f"{ttf_path.name} sem glifo(s) {faltando}"


def _sha256(caminho: pathlib.Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def test_stickers_do_app_sao_byte_a_byte_iguais_aos_do_site():
    """`EmptyState`/`InsightCard` (PR C2) usam `app/assets/stickers/`, cópia
    dos 12 `.webp` de `frontend/brand/stickers/` — sem este teste as duas
    árvores podem divergir em silêncio (arte trocada, versão velha)."""
    arquivos_site = sorted(p.name for p in STICKERS_SITE.glob("*.webp"))
    assert arquivos_site, f"nenhum sticker em {STICKERS_SITE}"
    for nome in arquivos_site:
        app_path = STICKERS_APP / nome
        assert app_path.is_file(), f"falta {app_path}"
        assert _sha256(app_path) == _sha256(STICKERS_SITE / nome), f"{nome} diverge do site"

    # Espelho de mão única antes: só ia site → app. Sticker a MAIS no app
    # (não removido do site, ou nunca existiu lá) passava em silêncio.
    arquivos_app = sorted(p.name for p in STICKERS_APP.glob("*.webp"))
    assert arquivos_app == arquivos_site, (
        f"app tem sticker(s) a mais/a menos que o site: app={arquivos_app} × site={arquivos_site}"
    )


def test_simbolo_da_boas_vindas_e_byte_a_byte_o_do_site():
    """A Boas-vindas do app (`app/app/(auth)/boas-vindas.tsx`) usa o símbolo
    rosa do porquinho copiado de `frontend/brand/email-logo.png`."""
    assert _sha256(SIMBOLO_APP) == _sha256(SIMBOLO_SITE), "simbolo.png diverge de email-logo.png"


def test_estados_do_connection_status_batem_com_pluggy_health():
    """`ConnectionStatus.tsx` mapeia estado→(tom, ícone) para as mesmas
    chaves de `_LABELS` — uma chave nova lá sem entrada aqui não pode passar
    em silêncio (CLAUDE.md §0.7: uma fonte de verdade é o backend)."""
    from core.services.pluggy_health import _LABELS

    texto = CONNECTION_STATUS_TS.read_text()
    # Ancorado no bloco do `VISUAL`, não no arquivo inteiro: uma regex solta
    # casava qualquer `chave: {` no arquivo, então mover uma chave para um
    # objeto morto no MESMO arquivo passava verde (medido).
    bloco = re.search(r"const VISUAL[^=]*=\s*\{(.*?)\n\};", texto, re.S)
    assert bloco, f"bloco `const VISUAL = {{...}}` não encontrado em {CONNECTION_STATUS_TS}"
    estados_ts = set(re.findall(r"^\s*(\w+):\s*\{", bloco.group(1), re.MULTILINE))
    assert estados_ts == set(_LABELS), f"TS: {sorted(estados_ts)} × Python: {sorted(_LABELS)}"


def _ambientes_e_schemes(texto: str) -> tuple[list[str], list[str]]:
    # Ancorado no bloco `POR_AMBIENTE`, como o teste do `VISUAL` acima; sem os
    # comentários, para um `// scheme: "x"` dentro dele não contar como scheme.
    bloco = re.search(r"const POR_AMBIENTE[^=]*=\s*\{(.*?)\n\};", texto, re.S)
    assert bloco, f"bloco `const POR_AMBIENTE = {{...}}` não encontrado em {APP_CONFIG_TS}"
    corpo = re.sub(r"/\*.*?\*/|//[^\n]*", "", bloco.group(1), flags=re.S)
    return re.findall(r"^\s*(\w+):\s*\{", corpo, re.MULTILINE), re.findall(r'scheme:\s*"([^"]*)"', corpo)


def test_extrator_de_schemes_ignora_comentarios():
    texto = (
        "const POR_AMBIENTE: Record<string, X> = {\n"
        '  // antes era scheme: "fake"\n'
        '  dev: { scheme: "a" }, /* scheme: "fake2" */\n'
        '  /*\n  prod: { scheme: "fake3" },\n  */\n'
        '  prod: { scheme: "b" },\n'
        "};\n"
    )
    assert _ambientes_e_schemes(texto) == (["dev", "prod"], ["a", "b"])


def test_schemes_do_app_batem_com_a_lista_do_connect_token():
    """`app.config.ts` registra um scheme por ambiente e o `connect-token`
    só aceita os de `_APP_SCHEMES` (400 no resto). Um scheme trocado de um
    lado só faz o binário pedir um `app_scheme` que o servidor recusa — o
    Open Finance do app inteiro quebra, sem erro de build (CLAUDE.md §0.7)."""
    from frontend.routes.open_finance import _APP_SCHEMES

    ambientes, schemes = _ambientes_e_schemes(APP_CONFIG_TS.read_text())
    assert len(schemes) == len(ambientes), f"ambientes {ambientes} × schemes {schemes}"
    assert set(schemes) == set(_APP_SCHEMES), f"TS: {sorted(schemes)} × Python: {sorted(_APP_SCHEMES)}"


def test_regex_do_item_da_volta_e_a_do_servidor():
    """O app recusa o `itemId` do link com a MESMA regra que o servidor usa
    para montar `/items/{id}` (`_ITEM_ID_OK`): lá o id inválido vira 502, que
    o app trataria como instabilidade e repetiria pela janela inteira (`JANELA_MS`)."""
    from core.services.pluggy import _ITEM_ID_OK

    m = re.search(r"const ID_DO_ITEM = /\^(.*?)\$/;", VOLTA_OF_TS.read_text())
    assert m, f"`const ID_DO_ITEM = /^...$/;` não encontrado em {VOLTA_OF_TS}"
    assert m.group(1) == _ITEM_ID_OK.pattern.removesuffix(r"\Z"), (
        f"TS: {m.group(1)} × Python: {_ITEM_ID_OK.pattern}"
    )


def test_detalhes_do_atualizar_do_banco_sao_os_do_servidor():
    """O aviso de banco do app escolhe o botão pelo `ui.detail` do snapshot: o de dispositivo
    ("Autorize o acesso no app do banco") NÃO pode virar "Reconectar" (perde a janela, Codex #166)
    e o de coleta vencida é o que libera o "Atualizar agora". Frase mudada num lado só e o botão
    errado aparece sem erro de build (CLAUDE.md §0.7)."""
    from core.services.pluggy_health import _AUTORIZE_NO_APP, _DETALHE_COLETA_VENCIDA

    texto = ATUALIZAR_BANCO_TS.read_text()
    for nome, servidor in (("AUTORIZE_NO_APP", _AUTORIZE_NO_APP), ("COLETA_VENCIDA", _DETALHE_COLETA_VENCIDA)):
        m = re.search(rf'export const {nome} = "(.*?)";', texto)
        assert m, f"`export const {nome} = \"...\";` não encontrado em {ATUALIZAR_BANCO_TS}"
        assert m.group(1) == servidor, f"{nome} TS: {m.group(1)!r} × Python: {servidor!r}"


def _achar(arquivo: pathlib.Path, padrao: str) -> str:
    m = re.search(padrao, arquivo.read_text(), re.S)
    assert m, f"`{padrao}` não encontrado em {arquivo}"
    return m.group(1)


def test_cadencia_e_prazo_do_atualizar_do_banco_sao_os_do_web():
    """O app acompanha a coleta com a MESMA cadência e a MESMA lista de estados do `of-status-poll.js`
    e dá ao POST o MESMO prazo do `refreshOpenFinance` do settings.html (§0.7). Uma mudança num lado só
    faria o app e o site discordarem sobre quando desistir, sem erro de build.

    O TETO NÃO é espelhado: o app para em 5 min (`JANELA_MS`, o da volta do OAuth) e o web em 30 min
    (`TETO`), de propósito."""
    numeros = lambda t: [int(x.replace("_", "")) for x in re.findall(r"[\d_]+", t)]
    nomes = lambda t: re.findall(r'"([^"]+)"', t)

    assert numeros(_achar(ATUALIZAR_BANCO_TS, r"const CADENCIA_MS = \[(.*?)\];")) == numeros(
        _achar(OF_STATUS_POLL_JS, r"const CADENCIA = \[(.*?)\];"))
    assert numeros(_achar(ATUALIZAR_BANCO_TS, r"const DEPOIS_MS = ([\d_]+);")) == numeros(
        _achar(OF_STATUS_POLL_JS, r"const DEPOIS = (\d+);"))
    assert nomes(_achar(ATUALIZAR_BANCO_TS, r"const SEGUE = new Set\(\[(.*?)\]\)")) == nomes(
        _achar(OF_STATUS_POLL_JS, r"const SEGUE_ESPERANDO = \[(.*?)\];"))
    # `updating` só é coleta com estes motivos; `""` conta, por isso a regex aceita aspas vazias.
    com_vazio = lambda t: re.findall(r'"([^"]*)"', t)
    assert com_vazio(_achar(ATUALIZAR_BANCO_TS, r"const MOTIVOS_COLETANDO = \[(.*?)\];")) == com_vazio(
        _achar(SETTINGS_HTML, r"const OF_REASONS_OK = \[(.*?)\];")) + com_vazio(
        _achar(SETTINGS_HTML, r"const OF_REASONS_COLETANDO = \[(.*?)\];"))
    assert numeros(_achar(SERVICO_OF_TS, r"const PRAZO_REFRESH_MS = ([\d_]+);")) == numeros(
        _achar(SETTINGS_HTML, r"const OF_REFRESH_PRAZO_MS = (\d+);"))
