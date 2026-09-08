"""Imagem de `/brand/` que o site público baixa tem teto de peso — e o teto vale
para o que o HTML/CSS realmente referencia, não para uma lista escrita à mão.

O gate anterior era um teto de caminho fixo (`frontend/brand/icon.png` <= 20 KiB) e
passou verde enquanto a `/agents` servia **10,3 MB** de imagem: oito heróis PNG de
0,9 a 2,5 MB (`faria_limer_hero.png` sozinho tinha 2.548.303 B) mais um `logo.png`
de 658.801 B repetido em 18 páginas. Nenhum deles estava na lista, então nenhum era
pesado. Uma lista de arquivos só protege o que alguém lembrou de escrever nela; por
isso aqui a lista é DERIVADA do código, e o arquivo novo entra no teto sozinho.

São três perguntas, e as três precisam da varredura:

1. a referência aponta para arquivo que existe? (`/brand/x.webp` errado é 404 em
   produção e verde no CI — ninguém pegava isso);
2. algum arquivo isolado passa do teto?
3. a soma de uma página passa do teto? Oito arquivos de 120 KB passam no teto
   individual e ainda assim entregam ~1 MB numa tela só. A soma é da PÁGINA: o que o
   HTML cita mais o que os stylesheets que ele carrega citam.

Fora do teto de propósito:

- **`.mp4`** — o `vsl.mp4` tem 10,4 MB e é `preload="metadata"` (`index.html:229-230`),
  decisão de produto: o navegador baixa o cabeçalho, não o vídeo. Está fora por
  EXTENSÃO, não por nome, então trocar o arquivo não fura o gate por engano.
- **caminho montado em runtime** — `/brand/stickers/{{STICKER}}.webp` (`blog-article`)
  e `/brand/agents/${esc(kind)}.png` (`preview_agentes`, `dashboard.js`) não nomeiam
  UM arquivo a pesar. Ficam fora porque o char class da regex não aceita `{` nem `$`.
- **`.js`** — `dashboard.js` serve os heróis em PNG de propósito (o dashboard não é
  site público) e `email_service.py` também (Outlook não renderiza WebP). Os `.png`
  originais continuam no disco por causa desses dois — e isso é teste
  (`test_png_nomeado_por_consumidor_continua_no_disco`), não promessa de docstring.
  Esse teste cobre TODO PNG que essas duas fontes nomeiam, com ou sem WebP irmão, e
  cada fonte prova a própria contribuição: piso sobre a união deixa a fonte que zerou
  passar escondida atrás da outra.

A arte em duplicata (PNG para e-mail/dashboard, WebP para o site) tem o seu próprio
gate: `test_webp_derivado_tem_a_MESMA_arte_e_proporcao_do_png` compara pixel, não só
proporção — dois heróis 2:1 com artes trocadas passariam por proporção.
"""

import pathlib
import re
from fractions import Fraction

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = RAIZ / "frontend"
BRAND = FRONTEND / "brand"

TETO_ARQUIVO = 131_072  # 128 KiB — pós-WebP o maior é o cofre_hero, ~110 KB
TETO_PAGINA = 921_600  # 900 KiB — pós-WebP a /agents soma 731.185 B e a / 230.990 B
# Diferença média por canal (0-255) entre o PNG e o WebP, em miniatura 64x64.
# Medido nos 12 pares de hoje: 0,23 a 0,83. Medido com a arte TROCADA (os 132
# cruzamentos png x webp de outro agente): mínimo 35,21.
#
# O teto não pode ser dimensionado só pela arte TROCADA — a troca é o ataque
# fácil. O que decide o número é a arte RETOCADA, e aí a folga é o buraco: com
# teto 10 passavam brilho +15% (5,91), tarja preta de 1600x60 (4,18) e 25% do
# quadro substituído pela arte do Barão (9,95).
#
# 3,0 é 1,68x o pior ruído de compressão legítimo NO EIXO QUALIDADE — reconversão
# dos 12 PNG com `cwebp -q`: q95 0,61 / q80 0,89 / q60 1,19 / q40 1,47 / q25 1,79
# (sempre o `xerife.png`). Nesse eixo, reconverter a biblioteca inteira até q25
# continua verde. Com 3,0 reprovam o brilho +15% (5,91), a tarja (4,18), a faixa
# branca de 300x60 (3,23), 10% do quadro com arte de outro agente (3,77), o preto e
# branco (17,66) e o espelhamento (44,85).
#
# Essa margem NÃO existe no eixo que este próprio PR executa — DOWNSCALE (logo
# 1214->607, heróis 1600->1200). Medido, LANCZOS antes do `cwebp`, pior dos 12
# (`carteiro.png` sempre que a escala < 1):
#   0,75x q80  1,29 | 0,5x q80  1,98 | 0,5x q40  2,87 | 0,5x q25  3,50 | 0,25x q80  4,27
# A margem real contra downscale é 1,04x (3,0 / 2,87), e os dois últimos são FALSO
# POSITIVO: encolher mais um herói — o passo natural de um PR de peso — pode
# disparar "arte diferente" sem arte nenhuma ter mudado. O teto ficou em 3,0 mesmo
# assim porque o máximo real de hoje é 0,83 e esse falso positivo é ruidoso e
# corrigível, não silencioso. Se ele disparar numa reconversão: confira a arte a
# olho, e reconverta com menos redução ou `-q` maior — não mexa no teto.
#
# Cegueira residual (`faria_limer_hero`, 1600x800, dMean base 0,39): o que escapa
# depende da POSIÇÃO, não do tamanho. A arte tem regiões chapadas, e retoque caído
# sobre elas some na média. Varrendo todas as posições com bloco preto: passa SEMPRE
# até 120x120 (1,12% da área, pior 2,90); 140x140 já reprova em alguma posição
# (3,35); e 300x300 (7,03% da área) ainda passa em 28 das 84 posições varridas, com
# mínimo 0,39 — o mesmo valor do arquivo intacto. Colar arte de outro agente em 5%
# do quadro (358x179) deu 6,91 no canto superior esquerdo e 0,39 em três dos cinco
# pontos testados. Os números de "reprova" acima valem para a posição medida.
TETO_DMEAN = 3.0

# `?v=1` e aspas param a captura sozinhos: o casamento termina na extensão.
REF = re.compile(r"/brand/([A-Za-z0-9_/-]+\.(?:png|jpg|jpeg|webp|gif|svg))")


def _referencias() -> dict[str, set[str]]:
    """`{"frontend/x.html": {"logo.webp", "agents/xerife.webp"}}`.

    `set` por página porque o navegador baixa uma vez o que o HTML cita duas
    (`blog-article.html` repete o `apple-touch-icon.png` em duas linhas).
    """
    paginas = {}
    for f in sorted(FRONTEND.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in {".html", ".css"}:
            continue
        achados = set(REF.findall(f.read_text(encoding="utf-8", errors="ignore")))
        if achados:
            paginas[str(f.relative_to(RAIZ))] = achados
    return paginas


def test_a_varredura_acha_alguma_coisa():
    """Sem isto o arquivo inteiro fica verde medindo NADA.

    Os três testes abaixo iteram sobre o resultado da regex: se ela parar de casar
    (alguém trocou o prefixo `/brand/`, mudou as aspas, moveu o HTML de lugar), a
    varredura devolve `{}`, os `for` não rodam e os `assert` passam. O gate vira
    decoração sem uma linha vermelha. O piso é folgado de propósito — ele existe
    para pegar a varredura ZERADA, não para congelar a contagem de hoje (21 páginas,
    18 referências distintas).
    """
    paginas = _referencias()
    distintas = {ref for refs in paginas.values() for ref in refs}
    assert len(paginas) >= 15, f"só {len(paginas)} páginas com imagem de /brand/"
    assert len(distintas) >= 8, f"só {len(distintas)} imagens distintas: {distintas}"


def test_toda_imagem_de_brand_referenciada_existe():
    """Referência quebrada é 404 em produção e verde no CI. Aqui não."""
    faltando = sorted(
        f"{pagina} -> /brand/{ref}"
        for pagina, refs in _referencias().items()
        for ref in refs
        if not (BRAND / ref).is_file()
    )
    assert not faltando, "referência a arquivo que não existe em frontend/brand/:\n" + "\n".join(faltando)


def test_nenhuma_imagem_de_brand_passa_do_teto():
    pesados = sorted(
        {
            f"/brand/{ref}: {(BRAND / ref).stat().st_size} B"
            for refs in _referencias().values()
            for ref in refs
            if (BRAND / ref).is_file() and (BRAND / ref).stat().st_size > TETO_ARQUIVO
        }
    )
    assert not pesados, f"acima do teto de {TETO_ARQUIVO} B por arquivo:\n" + "\n".join(pesados)


# `href` vem sempre depois do `rel` e sempre absoluto (`/site.css?v=5`); o `?v=` para
# a captura sozinho, como na REF.
CSS_LINK = re.compile(r"""<link[^>]+stylesheet[^>]+href=["']/([A-Za-z0-9_./-]+\.css)""")


def _referencias_por_pagina() -> dict[str, set[str]]:
    """O que a PÁGINA baixa: o que o HTML cita mais o que os stylesheets dele citam.

    `_referencias()` indexa cada `.css` como uma entrada própria, e isso não é uma
    página: imagem citada no `site.css` é baixada em TODA página que carrega o
    `site.css`, então HTML e CSS podiam ficar cada um abaixo do teto enquanto a
    página real passava dele. Hoje nenhum `.css` referencia `/brand/` — o buraco é
    latente, e é por isso que ele fecha barato.

    O `for` é sobre TODO `.html`, não sobre as chaves de `_referencias()`: página sem
    referência direta nenhuma, servindo 1 MB pelo stylesheet, não estaria naquelas
    chaves e nunca seria pesada.
    """
    por_arquivo = _referencias()
    paginas = {}
    for f in sorted(FRONTEND.rglob("*.html")):
        refs = set(por_arquivo.get(str(f.relative_to(RAIZ)), ()))
        for css in CSS_LINK.findall(f.read_text(encoding="utf-8", errors="ignore")):
            refs |= por_arquivo.get(f"frontend/{css}", set())
        if refs:
            paginas[str(f.relative_to(RAIZ))] = refs
    return paginas


def test_nenhuma_pagina_passa_do_teto_de_imagens():
    estouradas = []
    for pagina, refs in sorted(_referencias_por_pagina().items()):
        existentes = [BRAND / r for r in sorted(refs) if (BRAND / r).is_file()]
        total = sum(p.stat().st_size for p in existentes)
        if total > TETO_PAGINA:
            detalhe = ", ".join(f"{p.name} {p.stat().st_size}" for p in existentes)
            estouradas.append(f"{pagina}: {total} B ({detalhe})")
    assert not estouradas, f"acima do teto de {TETO_PAGINA} B por página:\n" + "\n".join(estouradas)


# (arquivo, regex do bloco, quantos kinds a fonte tem HOJE). O piso é POR FONTE
# de propósito: um piso sobre a união não afere fonte nenhuma, afere a redundante.
# Os 7 kinds do `email_service.py` são subconjunto dos 8 do `dashboard.js`, então
# `len(uniao) >= 7` passava com o dashboard contribuindo ZERO — e zerar o dashboard
# custava trocar `"xerife"` por `'xerife'`, que é o que um `prettier --single-quote`
# faz sozinho, sem mexer na semântica: o `re.search` casava o bloco e o `findall`
# voltava vazio. Daí o piso por fonte e o `_NOME` aceitando as duas aspas.
FONTES_DE_ARTE = (
    (FRONTEND / "dashboard.js", r"_AGENT_ART\s*=\s*new Set\((\[.*?\])\)", 8),
    (RAIZ / "core/services/email_service.py", r"_AGENT_ART_KINDS\s*=\s*(\{.*?\})", 7),
)
_NOME = re.compile(r"""['"](\w+)['"]""")

# `dashboard.js:11047` serve `/brand/agents/aviador.png` e esse arquivo NUNCA esteve
# no disco (só o `aviador_hero.png`): o medalhão do Aviador no feed já nasce 404.
# É buraco pré-existente do dashboard, não regressão da varredura — e fica NOMEADO
# aqui em vez de cair num filtro genérico ("só cobro PNG que tem WebP irmão"), que
# soltava junto os quatro PNG sem WebP irmão: `barao`, `cofre`, `faria_limer` e
# `reporter` — 4 dos 7 medalhões do e-mail.
PNG_AUSENTE_CONHECIDO = {"aviador"}


def _kinds_com_arte_png() -> set[str]:
    """Kinds cujo PNG o e-mail e o dashboard montam em RUNTIME.

    `email_service.py:380` escreve `/brand/agents/{agent_kind}.png` e
    `dashboard.js:11045,11047` escrevem `/brand/agents/${kind}_hero.png` e
    `${kind}.png`: a regex do topo não vê nenhum dos três, porque nenhum nomeia um
    arquivo. Quem nomeia é o conjunto de kinds — então é dele que a lista sai, e não
    de uma segunda lista escrita aqui, que envelheceria sozinha.

    Casar o bloco e extrair ZERO nome é falha, não silêncio: sem o piso por fonte a
    varredura degradava para "a outra fonte cobre", que é exatamente o que ninguém
    percebe quando a primeira para de contribuir.
    """
    kinds: set[str] = set()
    for arquivo, padrao, piso in FONTES_DE_ARTE:
        bloco = re.search(padrao, arquivo.read_text(encoding="utf-8"), re.S)
        assert bloco, f"não achei o conjunto de kinds em {arquivo.name}: regex desatualizada"
        nomes = set(_NOME.findall(bloco.group(1)))
        assert len(nomes) >= piso, (
            f"{arquivo.name}: o bloco casou mas saíram {len(nomes)} kinds dele (piso {piso}): "
            f"{sorted(nomes)}. A fonte parou de contribuir — a extração está desatualizada."
        )
        kinds |= nomes
    return kinds


def test_png_nomeado_por_consumidor_continua_no_disco():
    """O site passou a servir WebP; o e-mail e o dashboard continuam servindo PNG.

    Sem este teste o invariante mora só numa docstring, e apagar os PNG "pesados que
    ninguém usa mais" passa verde — quebrando o herói e o medalhão do e-mail dos
    agentes (Outlook não renderiza WebP) e o card e o medalhão do feed do dashboard.
    Pior: apagar o PNG também DESLIGA a checagem de arte/proporção abaixo, que só
    roda sobre o par. Este teste é o que impede esse desligamento silencioso.

    Cobre TODO PNG que um consumidor nomeia, tenha WebP irmão ou não: são os 8 kinds
    do `dashboard.js` x (`{k}.png`, `{k}_hero.png`), menos o `aviador.png` que nunca
    existiu (`PNG_AUSENTE_CONHECIDO`) — 15 arquivos. Filtrar por "tem WebP irmão" era
    o mesmo interruptor circular que este arquivo existe para fechar: `barao.png`,
    `cofre.png`, `faria_limer.png` e `reporter.png` não têm WebP irmão, e são 4 dos 7
    medalhões do e-mail (`email_service.py:380`) — apagar os quatro passava verde.

    `logo.png` fica de fora de propósito: nenhum código
    o serve (o e-mail usa `email-logo.png`, o app iOS tem cópia própria em
    `mobile/…/BrandLogo.imageset/`), então apagá-lo é limpeza legítima de 658.801 B —
    e sem PNG não há arte duplicada para sair de sincronia.
    """
    kinds = _kinds_com_arte_png()
    faltando = sorted(
        f"brand/agents/{nome}.png — nomeado em runtime por dashboard.js/email_service.py"
        for kind in kinds
        for nome in (kind, f"{kind}_hero")
        if nome not in PNG_AUSENTE_CONHECIDO and not (BRAND / f"agents/{nome}.png").is_file()
    )
    assert not faltando, "PNG nomeado por consumidor apagado:\n" + "\n".join(faltando)
    # A exceção nomeada não pode virar dispensa permanente: no dia em que a arte do
    # Aviador chegar, ela sai da lista e passa a ser protegida como as outras. A
    # varredura é sobre o CONJUNTO, não sobre o nome: cobrar o arquivo direto deixava
    # a instrução sem saída — quem tirasse "aviador" da lista continuaria vermelho.
    obsoletas = sorted(
        nome for nome in PNG_AUSENTE_CONHECIDO if (BRAND / f"agents/{nome}.png").is_file()
    )
    assert not obsoletas, (
        "PNG existe agora, a exceção ficou obsoleta — tire de PNG_AUSENTE_CONHECIDO: "
        + ", ".join(obsoletas)
    )


def _miniatura(caminho, lado: int = 64):
    """Miniatura RGB comparável: alpha achatado em branco e escala fixa.

    A escala fixa é o que torna o dMean insensível ao `-q` da conversão (o ruído de
    compressão some na média) sem cegar para arte trocada.
    """
    from PIL import Image

    im = Image.open(caminho)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im)
    return im.convert("RGB").resize((lado, lado), Image.BILINEAR)


def test_webp_derivado_tem_a_MESMA_arte_e_proporcao_do_png():
    """`width:auto` lê a proporção do arquivo — mudá-la mexe no layout.

    O logo é servido por `.nav-logo .logo-full{height:30px;width:auto}`
    (`site.css:74`, `settings.html:109`) e `.onb-logo{height:26px}`
    (`comecar.css:61`): a LARGURA sai da proporção intrínseca do arquivo, não do
    HTML. Reduzir 1214×360 para 404×120 parece inofensivo e não é — 404/120 é
    3,36667 contra 3,37222 do original, e a nav inteira andou 0,17 px. Medido:
    o `<a class="nav-logo">` deu 102×31 px com o PNG e 101×31 px com aquele WebP
    a 1280×800. Com 607×180 (a razão 607/180 exata) as duas medidas voltaram a
    bater. Os oito heróis dependem do mesmo: `.agent-hero` é `aspect-ratio:2/1`
    com `object-fit:cover`, e proporção diferente muda o recorte.

    E a MESMA ARTE. A arte existe em duplicata desde a migração — PNG no e-mail e
    no dashboard, WebP no site — e nada as mantinha em sincronia: regerar
    `xerife.webp` a partir da arte do Repórter, ou atualizar só um dos lados, passava
    verde e deixava as duas superfícies mostrando coisas diferentes. Proporção igual
    não prova arte igual (todos os heróis são 2:1).

    Vale para todo `<x>.webp` que tenha um `<x>.png` irmão — a lista sai do
    disco, não daqui. O `continue` do par ausente não desliga nada: quem garante que
    os PNG de origem continuam lá é o `test_png_nomeado_por_consumidor_continua_no_disco`.
    """
    from PIL import Image, ImageChops, ImageStat

    ruins, trocadas = [], []
    for webp in sorted(BRAND.rglob("*.webp")):
        png = webp.with_suffix(".png")
        if not png.is_file():
            continue  # .webp sem PNG de origem (avatar, mascot, stickers): nada a comparar
        (pw, ph), (ww, wh) = Image.open(png).size, Image.open(webp).size
        if Fraction(pw, ph) != Fraction(ww, wh):
            ruins.append(
                f"{webp.relative_to(BRAND)}: {ww}x{wh} ({ww / wh:.5f}) != "
                f"{png.name} {pw}x{ph} ({pw / ph:.5f})"
            )
        diff = ImageChops.difference(_miniatura(png), _miniatura(webp))
        dmean = sum(ImageStat.Stat(diff).mean) / 3
        if dmean > TETO_DMEAN:
            trocadas.append(f"{webp.relative_to(BRAND)}: dMean {dmean:.2f} contra {png.name}")
    assert not ruins, "proporção diferente do PNG de origem:\n" + "\n".join(ruins)
    assert not trocadas, (
        f"arte diferente da do PNG de origem (teto {TETO_DMEAN}):\n" + "\n".join(trocadas)
    )
