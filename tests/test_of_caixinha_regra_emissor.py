"""A regra "CDB emitido pelo próprio banco conectado" (db.open_finance).

Medida sem banco de dados de propósito: é regra de DINHEIRO (o que ela aceita
vira caixinha read-only e SAI de `list_of_fixed_income`), e regra de dinheiro tem
de ser medível contra o catálogo de bancos inteiro sem subir um Postgres. O
último teste é a exceção deliberada — o controle negativo no CAMINHO DO SYNC,
porque a função pura sozinha fica verde com a regra ladra de volta lá dentro.

A versão anterior casava por PREFIXO DO PRIMEIRO TOKEN com piso de 2 caracteres
("nu" casava com "nubank"), e isso roubava investimento de verdade: o CDB da Nu
Invest numa conexão Nubank virava caixinha do banco. Cada par de `ROUBOS` foi
provado vermelho pelo Tester antes desta correção.

ESTA REGRA TEM TRADEOFF, e ele está em `LIMITES` lá embaixo com os números. Não
é "melhor em tudo" que a versão em SQL: em pessoa física ela perde 2 conectores
que a anterior pegava e ganha 30 que a anterior errava; em pessoa jurídica o
falso negativo mais que DOBRA contra a anterior — número no cabeçalho de
`LIMITES` (o falso positivo em PJ não foi medido).
"""
import re
from pathlib import Path

import db
from db.open_finance import _emitido_pelo_banco_conectado as casa, _marca, _nucleo
from test_of_caixinha_autoimport import _nubank_raws, _pockets, _save, _seed_connection

RAIZ = Path(__file__).resolve().parents[1]

# (issuer que a Pluggy manda em raw.issuer, institution_name da conexão)
ROUBOS = [
    # Nu Invest é corretora do grupo NUBANK (ex-Easynvest), não do grupo XP — e é
    # justamente por ser irmã de casa que o prefixo "nu" a pegava. A corretora da
    # XP é a "XP INVESTIMENTOS CCTVM", outra razão social.
    ("NU INVEST CORRETORA DE VALORES S.A.", "Nubank"),
    ("NU INVESTIMENTOS S.A. - CORRETORA DE TÍTULOS E VALORES MOBILIÁRIOS", "Nubank"),
    ("XP INVESTIMENTOS CCTVM", "Nubank"),
    ("NU ", "Nubank"),
    ("BANCO NU", "Nubank"),
    ("AME DIGITAL", "American Express"),
    ("BRB - BANCO DE BRASILIA", "BR Partners"),
    ("PI INVESTIMENTOS", "PicPay"),
    ("XP INVESTIMENTOS CCTVM", "XPTO Bank"),
    # emissor de outro banco, sem parentesco nenhum
    ("BANCO INTER S.A.", "Nubank"),
    ("BANCO BRADESCO S.A.", "Banco do Brasil"),
    ("BANCO DO BRASIL S.A.", "Banco Bradesco"),
    ("NUBANK", "Nu Invest"),
    ("PAGUE VELOZ SERASA S.A.", "PagBank"),
    # o alias é procurado pela marca INTEIRA do conector: "Nubank Empresas" não é
    # "Nubank" e não herda a porta que o alias abre.
    ("NU INVEST CORRETORA DE VALORES S.A.", "Nubank Empresas"),
]

# O banco emitiu o próprio papel: tem de casar, senão a caixinha do usuário some
# da tela e ele fica sem saber por quê.
PROPRIOS = [
    # dado real do dono, 10 posições numa conexão Nubank (via `_EMISSOR_ALIAS`)
    ("NU FINANCEIRA S.A. - SOCIEDADE DE CREDITO, FINANCIAMENTO E INVESTIMENTO", "Nubank"),
    ("NU PAGAMENTOS S.A.", "Nubank"),
    ("PAGSEGURO INTERNET S.A.", "PagBank"),   # falso negativo da regra anterior
    ("BANCO INTER S.A.", "Banco Inter"),
    ("BANCO BTG PACTUAL S.A.", "BTG Pactual"),
    ("BANCO BMG S.A.", "Banco BMG"),
    ("ITAU UNIBANCO S.A.", "Itaú Unibanco"),  # acento só de um lado
    ("ITAÚ UNIBANCO S.A.", "Itau Unibanco"),
    ("BANCO DO BRASIL S.A.", "Banco do Brasil"),
    ("CAIXA ECONOMICA FEDERAL", "Caixa"),     # "caixa" é MARCA, não palavra genérica
    ("MERCADO PAGO", "Mercado Pago"),         # "mercado" idem
    ("BANCO SANTANDER (BRASIL) S.A.", "Santander"),
    ("SICREDI", "Sicredi"),
    ("ＮＵ FINANCEIRA", "Nubank"),              # unicode fullwidth
    # "cooperativo" é forma jurídica, não marca
    ("BANCO COOPERATIVO SICREDI S.A.", "Sicredi"),
    ("BANCO COOPERATIVO SICOOB S.A.", "Sicoob"),
    # PINO DO PISO DE 3 do lado do CONECTOR, lado caro. "brb" tem exatamente 3
    # caracteres: subir o piso para 4 derruba este par (`Banco BRB` entrega
    # INVESTMENTS, então é caixinha que some da tela) e nenhum outro par deste
    # arquivo. Sem ele, `len(banco) >= 3` → `>= 4` passava verde em 44 testes.
    ("BRB - BANCO DE BRASILIA S.A.", "Banco BRB"),
]

# Quem carrega o qualificador é o CONECTOR, e a razão social é que é curta. Só a
# 2ª tentativa (`_nucleo`, o emissor sem a forma societária do fim) pega estes —
# e os cinco primeiros eram REGRESSÃO contra a regra em SQL, que os pegava. Foi o
# Tester que achou: a razão social ATUAL do BV no BACEN (ISPB 01858774) é
# "BANCO BV S.A." e não "BANCO VOTORANTIM S.A.", e com o nome certo a regra sem
# esta 2ª tentativa perdia 3 conectores conectáveis hoje.
CONECTOR_QUALIFICADO = [
    ("BANCO BV S.A.", "BV - Pessoa Física - APP"),
    ("BANCO BV S.A.", "BV - Pessoa Física - Web"),
    ("BANCO BV S.A.", "BV - Private"),
    ("Banco do Brasil S.A.", "Banco do Brasil Previdência"),
    ("Banco Safra S.A.", "Safra Financeira"),
    ("BANCO C6 S.A.", "C6 Bank"),
    ("BANCO XP S.A.", "XP Banking"),
    ("BANCO SAFRA S.A.", "SafraPay"),
]

# O QUE ELA ERRA — não é lista de desejo, é o que está medido hoje. Existe pra
# que ninguém leia esta regra como "casa marca certa e só ela".
#
# Medição (2026-09-10, instrumento do Tester: 473 razões sociais reais do BACEN
# via BrasilAPI × os 134 conectores PERSONAL_BANK do catálogo de produção da
# Pluggy; 43 pares positivos, 63.312 negativos). O instrumento mora em
# `scratchpad/TESTER_R5|R6/`, que NÃO viaja com o repositório: quem for remedir
# refaz o corpus (BrasilAPI `/api/banks/v1` + catálogo da Pluggy) antes de
# confiar nos números abaixo. REMEDIR antes de reusar:
#     python scratchpad/TESTER_R5/colB_tester.py    # colunas HEAD × esta regra
#     python scratchpad/TESTER_R6/delecao.py        # seção (7): o eixo PJ
#     python scratchpad/TESTER_R6/ingrupo.py        # a corretora-irmã do grupo
#   regra em SQL (HEAD):  falso negativo 10, falso positivo 46
#   esta regra:           falso negativo  9, falso positivo 16
# O saldo é BOM mas NÃO é de graça, e o conjunto de erros MUDOU — não encolheu:
#   saíram do falso negativo: PagBank, Sicoob, Sicredi (os 3 entregam INVESTMENTS)
#   entraram:                 Itaú Cartões (não entrega), Stone Pagamentos (entrega)
# Em conector que entrega INVESTMENTS, que é o único que produz CDB: falso
# negativo 7 → 5, falso positivo 19 → 7. O preço é 1 conector (Stone Pagamentos)
# que perde o auto-import; quem cobre é a tela de vínculo do Banqueiro, que lista
# QUALQUER CDB. Falso positivo não tem essa saída — ele mexe no dinheiro sozinho.
#
# TUDO ACIMA É SÓ PESSOA FÍSICA, e no eixo PJ a regra PIORA. Nos 36 pares
# positivos de conector BUSINESS_BANK do mesmo catálogo (mesma medição, seção 7
# do `delecao.py`), o falso negativo vai de 8/36 no HEAD para 17/36 aqui — dos
# quais entregam INVESTMENTS, que é o que produz CDB, 7 → 14. Perdem o
# auto-import contra o HEAD, entre outros: `Nubank Empresas`, `Itaú Empresas`,
# `Santander Empresas`, `Mercado Pago Empresas`, `Stone Pagamentos Empresas`,
# `Banco BRB Empresas`, `Banco Mercantil Empresas`. A classe é sempre a mesma: o
# QUALIFICADOR está no conector ("Nubank Empresas" → marca "nubankempresas") e a
# razão social não começa por ele, enquanto a 2ª tentativa (`_nucleo`) também
# não alcança, porque o núcleo do emissor é longo. Isto NÃO é hipotético por
# decisão nossa: `POST /pluggy-item` (`frontend/routes/open_finance.py`) não
# valida tipo de conector contra `_CONNECTABLE_TYPES`, e o widget é aberto com
# `BUSINESS_BANK` na lista — se a navegação interna do widget deixa chegar num
# conector PJ (UI de terceiro, NÃO verificada aqui), a conexão grava. O que está
# medido em PJ é só o falso NEGATIVO acima, que tem saída pela tela de vínculo;
# o falso POSITIVO em PJ ninguém mediu, e é o lado que mexe no dinheiro sozinho.
# Quem for calibrar para PJ mede esse lado e mexe no `§0.7` dos dois lados.
LIMITES = [
    # marcas diferentes que esta regra casa mesmo assim (falso positivo real,
    # medido contra razão social do BACEN)
    ("INTERCAM CORRETORA DE CÂMBIO LTDA.", "Inter"),
    # a 2ª tentativa aceita razão social curta como prefixo do conector, e não
    # tem como saber que "Original Asset" não é do Banco Original. Nenhum
    # conector com este padrão existe no catálogo de hoje (medido: 0 pares novos
    # de falso positivo em 63.312) — mas a categoria é real e fica pinada aqui.
    ("BANCO ORIGINAL", "Original Asset"),
    # PINO DO PISO DE 3, o outro lado do par de `Banco BRB` em PROPRIOS: "asa"
    # tem 3 caracteres, e é por causa do piso que o gateway Asaas (que não é o
    # ASA Bank) casa com o conector `ASA`. Subir o piso para 4 apaga este falso
    # positivo — e é para isso que ele está PINADO aqui: quem subir o piso vê
    # este teste vermelho e tem de vir atualizar a medição acima.
    ("ASAAS GESTÃO FINANCEIRA INSTITUIÇÃO DE PAGAMENTO S.A.", "ASA"),
    # A CORRETORA-IRMÃ ainda casa para OUTROS bancos — é a MESMA classe do roubo
    # do `NU INVEST` em `ROUBOS`, que esta regra fecha só porque a razão social
    # da Nu Invest não começa por "nubank". Quando a corretora do grupo carrega o
    # nome comercial do banco no começo, nada aqui separa as duas, e o CDB dela
    # (papel de TERCEIRO) vira caixinha read-only do banco. Medido, e esta regra
    # é a melhor das três nessa conta (falso positivo dentro do grupo 22 na regra
    # em SQL → 19 → 16). Estes 3 são o que sobra.
    ("PICPAY INVEST DISTRIBUIDORA DE TÍTULOS E VALORES MOBILIÁRIOS LTDA", "PicPay"),
    ("NEON CORRETORA DE TÍTULOS E VALORES MOBILIÁRIOS S.A.", "Neon"),
    ("SANTANDER CORRETORA DE TÍTULOS E VALORES MOBILIÁRIOS S.A.", "Santander"),
]

BORDAS = [
    ("", "Nubank"), (None, "Nubank"), ("Nubank", ""), ("Nubank", None),
    ("Banco", "Banco do Brasil"),     # só palavra genérica dos dois lados
    ("B", "Banco B"),                 # 1 caractere: abaixo do piso de 3
    ("S.A.", "S.A."),                 # só forma societária: `_nucleo` zera
]


def test_cdb_de_terceiro_nao_e_caixinha_do_banco():
    erradas = [(i, n) for i, n in ROUBOS if casa(i, n)]
    assert erradas == []


def test_cdb_do_proprio_banco_e_reconhecido():
    perdidas = [(i, n) for i, n in PROPRIOS + CONECTOR_QUALIFICADO if not casa(i, n)]
    assert perdidas == []


def test_bordas_nao_casam():
    assert [(i, n) for i, n in BORDAS if casa(i, n)] == []


def test_limites_conhecidos_continuam_sendo_o_que_sao():
    """Controle de honestidade: estes pares CASAM e não deviam.

    O que ele prende são ESTES pares, não os números da medição acima — ele não
    tem o corpo do BACEN para contar nada. A afirmação anterior ("se a regra
    melhorar, isto fica vermelho") era falsa e foi medida como falsa: trocar
    `len(banco) >= 3` por `>= 4` em `_emitido_pelo_banco_conectado` mexia na
    medição (falso positivo 16 → 14, falso negativo 9 → 10) com os 44 testes
    VERDES. O que fecha esse furo são os dois pares de PINO — `ASAAS … × ASA`
    aqui e `BRB - BANCO DE BRASILIA S.A. × Banco BRB` em `PROPRIOS` — que ficam
    dos dois lados do piso de 3: mexer no piso vira vermelho nos dois testes.
    Piso ainda é o único parâmetro pinado; melhora de OUTRA natureza (alias novo,
    palavra genérica nova) continua podendo passar verde deixando a medição
    velha — quem mexer na regra remede, é a regra do §2 e não dá para testar.
    """
    sumiram = [(i, n) for i, n in LIMITES if not casa(i, n)]
    assert sumiram == [], f"melhorou: tire de LIMITES e corrija o comentário — {sumiram}"


def test_segunda_tentativa_corta_so_a_forma_societaria():
    # é o que separa "BANCO BV S.A." (nucleo "bv", casa com o conector "BV - …")
    # de "NU INVEST CORRETORA DE VALORES S.A." (nucleo inteiro, não casa com nada)
    assert _nucleo("BANCO BV S.A.") == "bv"
    assert _nucleo("Banco C6 S.A.") == "c6"
    assert _nucleo("Banco do Brasil S.A.") == "brasil"
    assert _nucleo("NU INVEST CORRETORA DE VALORES S.A.") == "nuinvestcorretoradevalores"
    assert _nucleo("S.A.") == ""


def test_marca_nao_zera_com_caixa_nem_mercado():
    # A lista de palavras genéricas anterior comia "caixa" e "mercado": com marca
    # vazia, a conexão Caixa NUNCA casava com nada (3 conectores do catálogo).
    assert _marca("Caixa") == "caixa"
    assert _marca("Mercado Pago") == "mercadopago"
    # e continua comendo o que é genérico mesmo
    assert _marca("BANCO INTER S.A.") == "intersa"
    assert _marca("Banco do Brasil") == "brasil"


def test_as_duas_listas_de_tipo_de_conector_continuam_discordando():
    """§0.7 — o picker do site e o widget da Pluggy NÃO pedem os mesmos tipos.

    A regra acima é calibrada só contra os PERSONAL_BANK do catálogo. Se alguém
    mexer num dos dois lados sem mexer no outro, isto fica vermelho e manda ler
    o comentário — em vez de o desacordo virar surpresa em produção.
    """
    py = (RAIZ / "frontend/routes/open_finance.py").read_text(encoding="utf-8")
    js = (RAIZ / "frontend/open-finance-connect.js").read_text(encoding="utf-8")
    backend = re.search(r"^_CONNECTABLE_TYPES = \{(.+?)\}$", py, re.M)
    widget = re.search(r"^\s*const connectorTypes = \[(.+?)\];$", js, re.M)
    assert backend and widget, "os dois sites mudaram de forma; releia §0.7 antes de ajustar"
    tipos = lambda s: {t.strip(" \"'") for t in s.group(1).split(",")}
    assert tipos(backend) == {"PERSONAL_BANK"}
    assert tipos(widget) == {"PERSONAL_BANK", "BUSINESS_BANK"}


def test_corretora_irma_nao_vira_caixinha_no_sync(user_id):
    """Controle negativo NO CAMINHO DO SYNC, e não só na função pura.

    Com a semântica anterior (primeiro token, piso 2, simétrica) "nu" casa com
    "nubank" e o CDB da Nu Invest — corretora do MESMO grupo Nubank, mas que
    vende papel de terceiro — vira caixinha read-only do banco. Este é o caso que
    discrimina: `test_cdb_de_outro_emissor_nao_vira_caixinha` fica VERDE com a
    regra ladra de volta, porque lá o banco conectado é outro.
    """
    conn_id = _seed_connection(user_id, institution="Nubank", item="cx-nuinvest")
    nu_invest = {"id": "xp1", "name": "CDB NU INVEST", "number": None,
                 "issuer": "NU INVEST CORRETORA DE VALORES S.A.",
                 "type": "FIXED_INCOME", "subtype": "CDB", "balance": 5000.0}
    _save(conn_id, _nubank_raws() + [nu_invest])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)

    assert res["caixinhas_created"] == 10        # as 10 do dono, e só elas
    assert len(_pockets(user_id)) == 10
    from db.rv import list_of_fixed_income
    rf = list_of_fixed_income(user_id)          # segue investimento, com o saldo dele
    assert [float(r["balance"]) for r in rf] == [5000.0]


def test_raw_escalar_nao_derruba_a_tela_de_vinculo(user_id):
    """`raw` é jsonb: escalar ali fazia `.get` explodir.

    No sync o `except` de pluggy_sync engole e o import só some; em
    `list_caixinha_candidates` não há guarda nenhuma e o erro sai como 500 na
    tela do Banqueiro. A regra em SQL (`raw->>'issuer'`) era total nesse ponto.
    """
    from db import get_conn
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, [{"id": "cx-raw", "name": "CDB Qualquer", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 120.0}])
    with get_conn() as conn:
        with conn.cursor() as cur:
            # o que um provider novo (ou um banco esquisito) pode mandar no lugar
            # do objeto: uma string JSON, que é jsonb válido e não é dict
            cur.execute("update open_finance_investments set raw = %s::jsonb "
                        "where connection_id = %s", ('"sem-issuer"', conn_id))
        conn.commit()

    assert db.sync_open_finance_caixinhas(conn_id, user_id)["caixinhas_created"] == 0
    nomes = {c["name"] for c in db.list_caixinha_candidates(user_id)}
    assert "CDB Qualquer" in nomes     # a tela abre, e ainda oferece o papel
