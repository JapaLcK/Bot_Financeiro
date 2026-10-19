# Plano — "Bancos conectados" no /painel (par web da tela do app)

Par do [`plano-bancos-conectados-app.md`](plano-bancos-conectados-app.md). Regra do dono (2026-10-16): app e
`/painel` andam juntos, cada um com a cara da sua plataforma. Este plano **não implementa nada**: diz o que a web
mostra, de onde vem cada dado, em que PRs, o que pode quebrar e como provar.

Medido na `main` em `8bc0ebdb` (2026-10-18). Remeça antes de usar qualquer linha citada: a `main` anda durante a sessão.

**Faixa: Completo** nos PRs W1 a W3 (saldo, Open Finance, isolamento por usuário). Coder invoca a skill
`pigbank-frontend`. O W0 é Leve (só copia uma tabela de cores e a prende por teste).

## O que já existe (e não se recria)

Servidor, todo pronto:
- `GET /api/v2/bancos` (`api/v2/bancos.py`, regra em `db/bancos.py`, PR 1 do app, já na `main`, commit `dde9a985`).
  Devolve `{total, motivos, bancos: [{conexao_id, saldo_conta, investido, total, contas, cartoes, investimentos, motivos}]}`
  numa transação só. Dinheiro em texto decimal; `null` ≠ zero. **Não traz nome do banco nem estado da conexão.**
  O tipo já está gerado em `webapp/src/dashboard/lib/api-v2.gen.ts` (`Banco`, `Bancos`, `"/bancos"` em `RotasGet`).
  Nenhuma tela o consome ainda.
- `GET /open-finance/{uid}` (snapshot, `frontend/routes/open_finance.py`, `open_finance_snapshot_route`): cada conexão
  traz `id`, `institution_name`, `provider_item_id`, `last_sync_at`, `status` e `ui = {state, label, detail,
  stale_products, dados_de}`. O PR 2 do app (#1106) acrescentou `logo_url`.
- `GET /open-finance/{uid}/limite` → `{of_banks_max, em_uso, pode_adicionar, code, message}`. `of_banks_max: null` =
  sem teto. O app usa; a web nunca chamou.
- `DELETE /open-finance/{uid}/connections/{connection_id}` (desconectar um). Existe, o app usa, **nada na web chama**.
- `POST /open-finance/{uid}/connect-token` aceita `item_id` para reconectar (PR #830). A web manda sempre `{}`.

Web hoje:
- `frontend/settings.html`, aba Open Finance (só com `me.of_ui_enabled`): JS inline. `renderConnections` desenha nome,
  "Última sync", `ui.detail` e a pílula de `ui.label`. **Não há botão por conexão.** Ações: "↻ Atualizar"
  (`refreshOpenFinance`, `POST /refresh`), "Remover todas as conexões" (`disconnectAll`, `DELETE /open-finance/{uid}`)
  e "Conectar banco" (`PBOpenFinance.open()`, de `frontend/open-finance-connect.js`). Não mostra vagas do plano.
- `/painel` (`webapp/src/dashboard`): **nenhuma tela lista conexões.** "Configurações" da seção Conta é ponte para
  `/settings` (`conta/Ponte.tsx`). Os links "Conectar banco" do painel apontam para `/settings?view=open-finance`
  (`MetaFonte.tsx`, `MetaCartao.tsx`, `Subscriptions.tsx`, `guia-falas.ts`).
- `docs/paridade-app-dashboard.md`, seção "Conta, segurança e sistema": a linha "reconectar e desconectar um banco"
  marca a web como atrasada.

Peças para reaproveitar no painel (§0.1):
- `lib/format.js`: `moneyText` (texto decimal da v2, sem float) e `relativeDays`.
- `.comp-bar` (`styles/pages.css:129`): a marcação da barra já usada em `GastosDiaTopo.tsx` e `Wealth.tsx`.
- `parts/Selos.tsx` (`Selos`/`Motivo`): já tem texto para `conexao_pausada`, `banco_desatualizado` e os outros motivos.
- `parts/Frame.tsx`, `ErroApi`, `lib/brand.ts` (`ICON`, a imagem do Piggy usada em `Entrada.tsx` e `Dica.tsx`).
- `conta/api.ts`: precedente de rota legada pelo `chamar` (`/settings/${uid}/…`).
- `<dialog>` nativo para confirmar (como o apagar de `MetaForm.tsx`).

O que só existe no app e a web precisa: `coresBanco` (`app/src/ui/tokens.ts`) e a regra `corDoBanco`
(`app/src/features/painel/composicaoContas.ts`, cor só se passar 3:1 sobre o card, senão escala de cinza).

## O que a tela mostra

Rota nova do painel: `#/conta/bancos`, "Bancos conectados", na seção Conta (`CONTA` em `router.ts`), fora do
seletor de mês (`NO_MONTH` já inclui `...CONTA`). Decisão 1.

Os mesmos 3 estados do mockup do app (tudo em dia, pede ação, nenhum banco); o painel é só escuro.

1. **Topo e barra por banco: removidos no redesenho B (2026-10-19).** A lista não mostra valor nenhum (nem `total` do topo, nem
   selos de motivos do topo, nem a barra por banco, nem o total de cada banco): os números moram só no detalhe. Saíram do
   código `barraDosBancos` e o resto que só servia a eles. Decisão do dono (mockup B): menos texto e menos dado na lista.
2. **Redesenho B:** título "Bancos conectados" + "Gerencie as conexões via Open Finance." e o "+" rosa no topo; os bancos
   num único grupo (`.banco-grupo`: uma superfície `--plane-1`, cantos de 16 px, divisor de 0,5 px entre as linhas, sem card por banco).
3. **Vagas do plano:** "2 de 3 bancos" de `/limite` (`em_uso`, `of_banks_max`). `of_banks_max: null` = nada (sem teto).
   Com `pode_adicionar: false`, mostra a `message` do servidor no lugar do botão de conectar.
4. **Banco por conexão** (uma por conexão do snapshot, na ordem do snapshot). Desenho atual (redesenhado depois do W2,
   "cards mais interativos e com menos texto"; o desenho do W1/W2, com `<details>` "Gerenciar", saiu):
   - **Linha clicável** (`conta/BancoCard.tsx`, um `<button>` de 44 px ou mais dentro de `li.banco-card`): ladrilho quadrado arredondado com o logo
     (`logo_url` quando há; sem ele, a inicial na cor do banco), nome, o estado como pontinho no tom de `ui.state` (mesmo
     mapa de `OF_PILL_CLASS` do settings) mais o `ui.label` (a cor só acompanha o texto) e um chevron. Sem valor, sem barra. A linha não traz "há N min", contagens nem `ui.detail`.
     As classes `.cor-conta`/`.cor-inv` de `bancos.css` ficaram só para os pontinhos do detalhe (dado não leva o rosa; `--serie-1` vem de `metas.css`).
   - **Detalhe** (`conta/BancoDetalhe.tsx`, `<dialog>` modal: tela cheia no celular, painel à direita a partir de 900 px;
     fecha com X, Esc e clique no fundo, e não fecha se o mouse desceu dentro do painel e subiu no fundo; o X fica num cabeçalho `position: sticky` de fundo opaco, à vista com o painel rolado; fecha sozinho quando a conexão some): ladrilho grande, nome, estado, `ui.detail`,
     total grande; linhas "Última atualização" (`<time datetime title>`), "Em conta" e "Investido" (`null` = "—", nunca
     "R$ 0,00"), cada uma com um pontinho na cor do segmento da barra (`.marca` + `.cor-conta`/`.cor-inv`) e as contagens de `/bancos` com ícone, só as maiores que zero; uma faixa estática "Conexão regulamentada
     pelo Banco Central, oficial pelo Open Finance." (decisão do dono, só no detalhe, no padrão do Banner do design system,
     sem link); a `falha` do desconectar; e as ações em `.banco-det-acoes` (grid de uma coluna, 44 px por botão, recebem
     `bloqueado`), "Reconectar banco" (W3, só quando o banco é reconectável) e "Desconectar banco", nesta ordem, sem CSS novo. "Acompanhar
     sincronização" NÃO existe no painel (decisão 4): só a pílula do estado.
   - **Conectar:** com bancos na lista, o botão redondo "+" (`.banco-mais`, `aria-label="Conectar banco"`) no cabeçalho; o estado vazio (item 5) mantém o botão de texto. Os dois (e o de cada disponível) são `<a href="/settings?view=open-finance">` com `onClick`: com `window.PBOpenFinance` abrem o seletor ali mesmo, sem ele o href segue.
   - **Disponíveis para conectar** (`conta/Disponiveis.tsx`, `lib/bancos.ts::disponiveis`; o PR #1142): `GET /open-finance/{uid}/connectors`, leitura opcional (falha = a seção some). Até 5 dos populares, na ordem fixa Itaú, Bradesco, Banco do Brasil, Santander,
     Caixa, Inter, C6, Nubank, BTG, Mercado Pago (casamento por nome normalizado só para a ORDEM), que ainda não estão conectados: "já conectado" é por ID (`institution_id` do snapshot × `id` do conector; o gêmeo Open Finance de mesmo nome e id
     diferente continua disponível). Linhas num grupo igual ao de cima: ladrilho (logo pela regra `logoSeguro`; sem ele a inicial na cor do conector, hex de 6 dígitos com 3:1 sobre o card, senão a tinta neutra), nome, "Conecte sua conta" e o "+" cinza (`aria-label="Conectar <nome>"`, 44 px); por último "Ver todos os bancos" (o seletor inteiro). A seção só existe com vaga (`pode`) e some se não sobra banco.
     O "+" chama `PBOpenFinance.open({id, name})` (o seletor abre com o banco escolhido; o usuário ainda toca em "Conectar", e o teto/plano/confirmação é o do módulo). O painel carrega `open-finance-connect.css/.js` e o SDK da Pluggy no `painel.html`, faz `init` ao montar e `destroy` ao desmontar, salvo com widget da Pluggy já aberto (`busy()`): aí o `destroy` não roda, para o `onSuccess` ainda salvar o item.
   - Fora, por não termos dado nem promessa real: "Dados compartilhados" e "Validade da conexão".
5. **Nenhum banco:** o Piggy (`ICON`) e um botão "Conectar banco". Sem frase longa.
6. **Conexão sem linha em `/bancos`** (recém-conectada, só posição sem conta, `DELETED`): o card aparece pelo snapshot
   com "—" no valor. Junta-se por `conexao_id` = `connection.id`, **nunca** por nome nem por posição.

Cara de web (onde difere do app de propósito): tooltip com data completa, foco por teclado nos cards, confirmação
em `<dialog>` em vez de `Alert`, e o grupo de bancos numa coluna só (até 720 px de largura) no desktop e no celular.

## PRs

### W0 — cores dos bancos na web (Leve)

- `webapp/src/dashboard/lib/bancos.ts` (novo, curto): `CORES_BANCO` (cópia de `coresBanco`) e `corDoBanco(nome, indice)`
  com o mesmo casamento por palavra sem acento e a mesma regra de 3:1, contra `--plane-1` do painel (o painel é só
  escuro). Antes de escrever o contraste, procurar se o webapp já tem cálculo de luminância (`git grep -n "luminanc"
  webapp/src`); não achando, porta-se a função do app (`app/src/ui/contraste.ts`).
- **Uma fonte de verdade (§0.7):** os dois projetos npm não importam um do outro. Teste
  `tests/frontend/bancos_paridade.test.mjs` lê os dois arquivos e exige o mesmo mapa (precedente:
  `tests/test_phosphor_subset.py`). Negativo: mudar um hex só de um lado reprova.
- Por que separado: é a única peça que pode ser provada sem tela, e o W1 fica menor.

### W1 — tela só de leitura (Completo)

Arquivos, nesta ordem:
1. `lib/v2.ts`: `bancosQuery` (`apiGet("/bancos")`, `...vivo`) ao lado de `contasQuery`; e o `chamar` passa a não
   prefixar `/open-finance/` (hoje só `auth|settings`). Sem isso a rota legada vira `/api/v2/open-finance/...` e dá 404.
2. `conta/api.ts`: `conexoesQuery(uid)` (snapshot) e `limiteQuery(uid)`, no molde de `segurancaQuery`
   (`enabled: !DEMO && !!uid`).
3. `router.ts`: rota `/conta/bancos` em `ROUTES` e em `CONTA`. `pages.tsx`: a página no `PAGES`.
4. `conta/Bancos.tsx` (página), `conta/BancoCard.tsx` (a linha) e `conta/BancoDetalhe.tsx` (o detalhe). `juntar(conexoes, bancos)` por
   id, **sem aritmética de dinheiro** (o mesmo princípio do app).
5. `lib/format.js`: estender o tempo relativo para minutos e horas ("há 12 min", "há 3 h"), ao lado de `relativeDays`.
   O "há N min" de `settings.html` (`formatActivityTime`) é JS inline e não se importa: não copiar, escrever uma vez aqui.
6. Rebuild do `webapp/` com o artefato `frontend/dashboard-app.js` commitado (gate do CI).
7. `tests/frontend/api_v2_respostas.json`: chave `bancos` (validada por `tests/test_api_v2_contrato.py`). Reaproveitar os
   valores de `tests/fixtures/app_bancos.json` para os dois lados contarem a mesma história.
8. `docs/paridade-app-dashboard.md`: a linha nova "Bancos conectados" (web e app) e atualizar a de Contas se mudar.

Leituras opcionais, como no app: `/bancos` em 403/404/5xx/rede = os cards aparecem pelo snapshot, sem números
(cada valor "—", sem topo nem barra). `/limite` em falha = sem a linha de vagas. Só o snapshot é obrigatório.

Desta vez as ações ficam como hoje: "Conectar banco" leva a `/settings?view=open-finance`.

Atualização sem código novo: o cliente do SSE (`lib/eventos.ts`) já invalida todas as consultas a cada aviso, e o fim
do sync do Open Finance avisa (`recurso: "open_finance"`). A página relê sozinha quando um banco termina de atualizar.

Motivos: conferir que `parts/Selos.tsx` tem texto para os 7 de `Bancos.motivos` (inclui `plano_sem_investimentos`,
que hoje só `/bancos` emite); faltando, acrescentar ali, que é a fonte do painel para texto de motivo.

Decisões de execução do W1 (2026-10-18), além das já anotadas acima (barra, sem `<details>`, teste de paridade):
- **Resposta fora do contrato não derruba a página:** `/bancos` malformado (sem `bancos`/`motivos`, motivo que não é
  texto) é leitura falha (cards sem números); campo do snapshot que não é texto (nome, `ui.label`, `ui.detail`,
  `last_sync_at`) vira nulo. Chave como `constructor` em mapa (motivo, estado, direção do erro) sai como texto cru ou
  "pendente", nunca como função herdada de `Object`.
- **O `@media` compacto do trilho subiu para `max-height: 768px`** (e o gêmeo em `flexgap.css`), porque a Conta ganhou
  um item e o rodapé era cortado entre 741 e 748 px. Afeta o trilho de **todas** as páginas do painel nessas alturas
  (itens de 36 para 32 px). O de altura curta foi de 620 para 650 px.
- **Nubank e BTG saem em cinza na web** pela regra 3:1 sobre o card (2,64:1 e 1,23:1; decisão do dono, 2026-10-18).

### W2 — Desconectar um banco (Completo)

- O card ganha o `<details>`/`<summary>` (fora do W1, ver acima); o botão "Desconectar <banco>" mora dentro do card aberto, `<dialog>` de confirmação ("Desconectar" / "Manter banco"),
  `DELETE /open-finance/{uid}/connections/{id}` por `escrever` (manda `pbCsrfHeaders`).
- Mesmas regras do app (`conexoes.tsx`, `remover`): uma remoção por vez (outro "Desconectar" desabilitado enquanto uma
  voa); resposta perdida não manda segundo DELETE, relê o snapshot e a ausência confirma; 401 de sessão vai para o fluxo
  de sessão expirada do painel.
- Depois: invalida `conexoes`, `bancos`, `limite` e `contas` (o Resumo também muda).
- O botão "Remover todas as conexões" do settings fica onde está (o app não o tem; a paridade já registra).

### W3 — Reconectar (FEITO; Completo; a prova com banco real é em PRODUÇÃO). "Acompanhar" ficou de fora (decisão 4)

- O widget da Pluggy só existe no legado (`open-finance-connect.js`, IIFE, `window.PBOpenFinance`). Decidido (decisão 3):
  `PBOpenFinance` ganha um parâmetro opcional `itemId` que vai no corpo do
  `connect-token` e no `updateItem` do `PluggyConnect` (feito no PR A, #1139, `reconnect(itemId)`); o painel usa o módulo e o SDK que o `painel.html` já carrega (#1142), sem carga sob demanda.
  Quem não passa `itemId` (o settings) recebe o mesmo comportamento de hoje.
- **Não verificado, condição para começar:** que o SDK da Pluggy na versão carregada aceita `updateItem` com um token
  criado com `itemId`. Só se prova com um banco real, em PRODUÇÃO (não há staging para esta prova).
- "Acompanhar sincronização" quando `ui.state === "updating"`: reaproveitar `PBColetaOF` (`frontend/of-status-poll.js`)
  se ele aceitar um item; senão, só reler o snapshot pelo `vivo` (foco de aba) e mostrar "Atualizando…" (decisão 4: só a pílula).
- Regras de quando mostrar Reconectar: as do app (`provider_item_id` presente, `of_banks_max !== 0`, estado fora de
  `removed`, `item_missing`, `paused`). Reconectar não conta vaga nova (P1 do `/limite`).

### Redesenho B + Disponíveis para conectar (PR #1142, Completo: Open Finance)

- Lista num grupo só, sem valor (item 1 e 2 acima); `BancoCard` perde `v` e a barra; `Ladrilho` passa a receber `nome`/`logo`/`cor` (serve ao banco e ao conector).
- `conexoesQuery` lê `institution_id` (o servidor já o devolve; fora do contrato = `null`); `conectoresQuery` (`conta/api.ts`).
- `open-finance-connect.js`: `open({id, name})` opcional (pré-escolha). Sem caminho paralelo de connect.
- `painel.html`: css/js do seletor e o SDK da Pluggy (`async`, mesma versão e SRI do settings). O W3 usa esse mesmo carregamento (o plano antigo de carregar o script só ao clicar caiu; carga sob demanda continua follow-up).
- Reconectar (W3) entrou depois, no contêiner `.banco-det-acoes` do detalhe.
- Par no app: o "Disponíveis" é plano futuro (PR próprio, `paridade-app-dashboard.md`).

### Fora do escopo

Esconder valores (o painel não tem; linha própria na paridade), validade da autorização e fatura aberta (fora do v1
também no app), reordenar bancos, mexer na aba Open Finance do `settings.html` além do parâmetro do W3.

## Riscos

- **Junção errada:** juntar por `institution_name` funde duas "Nubank". Junta-se por id; teste com dois "Nubank".
- **Somar no cliente:** o redesenho B tirou o total do topo e a barra da lista (nenhum R$ nela); os valores só moram no
  detalhe de cada banco, vindos do servidor (`/bancos`), e o cliente não soma nada.
- **`null` virar zero:** `investido: null` com `motivos: []` é o caso normal de banco sem investimento; mostrar "—".
- **Decimal em notação científica** (`"1E-7"`, risco que o Manager listou para o app): conferir que `moneyText` aceita ou
  trata por item, sem derrubar a tela.
- **Duas leituras** (snapshot e `/bancos`) podem discordar por um instante: conexão numa e não na outra. O card vem do
  snapshot; id só em `/bancos` é ignorado.
- **Isolamento por usuário:** o `uid` do caminho sai do `me` da sessão, e as três rotas legadas passam por
  `authorize_dashboard_access` (403 se não for a sessão). Nenhum id de outro usuário entra por URL do painel.
  O DELETE com id alheio ou inexistente não apaga nada e responde 404 `OF_CONNECTION_NOT_FOUND` (uniforme; provado por `tests/test_fase4_open_finance.py::test_delete_individual_inexistente_ou_alheio_404_uniforme` e pelo teste do próprio id, que apaga só o dele e devolve 404 no 2º DELETE). O W2 não escreve pytest novo.
- **Prefixo do `chamar`:** acrescentar `open-finance` à exceção muda o roteamento de qualquer chamada futura com esse
  começo. Varrer `git grep -n 'chamar\|apiGet' webapp/src` antes; hoje não há.
- **Artefato do webapp:** sem rebuild commitado o CI reprova (`exigeArtefatoEmDia`).
- **Chave do painel:** `/api/v2` exige `dashboard_v2_enabled`; a tela mora no painel, então já está atrás dela.
- **W3 mexe no legado** que o settings usa: o parâmetro é opcional e os testes de `of_connect_settings.test.mjs`
  continuam verdes sem mudança.

## Como provar

Todos no `node --test` com Playwright (`npm run test:frontend`, só os arquivos tocados na máquina; a suíte inteira roda
no CI), pelo harness `tests/frontend/_painel.mjs` (`servir`, `abrirPainel` com relógio congelado). As rotas legadas
(`/open-finance/...`) entram por `ctx.route` no próprio teste, como em `dashboard_v2_conta.test.mjs`. Desktop
(1280×800) **e** celular (390×844) em cada teste de layout.

W1:
- **O painel não soma:** fixture com topo `67000.00` e o snapshot só com o Nubank (45.000): a tela mostra 67.000.
  Negativo: trocar o topo pela soma dos cards mostra 45.000 e reprova.
- **Mesmo nome:** dois "Nubank" com ids 1 e 2 e valores diferentes: cada card com o seu. Negativo: juntar por nome reprova.
- **`null`:** banco com `investido: null` mostra "—" e nenhum "R$ 0,00". Negativo: tratar `null` como 0 reprova.
- **Sem valor na lista (redesenho B):** a lista não tem R$, total, barra nem selo de motivo; o valor só aparece no detalhe.
  Substitui o teste da barra (que saiu com ela). Negativo: pôr o R$ de volta na linha reprova (`dinheiro` em
  `dashboard_v2_bancos_disponiveis.test.mjs`, "grupos de uma superfície só").
- **Positivo das leituras opcionais:** `/bancos` em 500 e em 404 = os cards aparecem com nomes e estados, sem números;
  `/limite` em 500 = sem a linha de vagas e o resto inteiro.
- **Estado vazio:** snapshot sem conexão mostra o Piggy e "Conectar banco" com `href` para `/settings?view=open-finance`.
- **Tempo relativo** com relógio congelado: 12 min, 3 h, 2 dias.
- **Layout medido, não olhado:** no celular nenhum card passa da largura da janela (`scrollWidth <= innerWidth`), o alvo
  dos botões ≥ 44 px; no desktop a lista e os Disponíveis são dois grupos de uma coluna só, de até 720 px. Pergunta "quanto daria sem a correção?" respondida em cada medida.
- **Rota:** `#/conta/bancos` aparece na seção Conta da lateral e no Menu do celular; não aparece no protótipo (`DEMO`).

W2:
- Desconectar: o DELETE sai com o id da conexão e o CSRF; depois do 200 o card some e o topo relê.
- Resposta perdida (rota aborta) + snapshot sem o id = sucesso sem segundo DELETE; snapshot ainda com o id = erro na tela.
  Negativo: sem a releitura, a resposta perdida vira erro e reprova.
- Dois cliques em bancos diferentes: só um DELETE sai.
- Isolamento (pytest, rota já existente): DELETE com id de outro usuário não apaga a conexão dele (controle positivo:
  o próprio id apaga). Conferir se `tests/` já cobre antes de escrever (§0.1).

W3:
- O corpo do `connect-token` leva `item_id` quando vem do painel e `{}` quando vem do settings (os testes do settings
  ficam como estão: controle positivo).
- **Só em produção:** reconectar um banco de verdade e ver a conexão voltar a "Atualizado" sem gastar vaga.

Só depois do deploy (todos os PRs): números reais com a conta demo, painel e app lado a lado mostrando o mesmo topo e os
mesmos bancos; Safari do iPhone no `/painel`.

Decisões de execução do W2 (2026-10-18), implementado em `conta/Bancos.tsx`, `conta/Confirma.tsx` (extraído de `Sessoes.tsx`), `conta/BancoCard.tsx`, `tests/frontend/dashboard_v2_bancos_desconectar.test.mjs`, `tests/frontend/dashboard_v2_bancos_desconectar_bordas.test.mjs` e o helper `tests/frontend/_bancos_desconectar.mjs`:
- **D1 (substituída pelo redesenho):** o `<summary>` "Gerenciar" saiu; o botão "Desconectar banco" mora no detalhe do banco (`BancoDetalhe.tsx`), que a linha abre. Abrir o Confirma por cima do detalhe é o mesmo fluxo de antes.
- **D2:** textos do app (`conexoes.tsx`): "Desconectar <banco>?", "Os dados importados deste banco serão removidos. Seus outros bancos e lançamentos manuais serão preservados.", "Manter banco" / "Desconectar" / "Desconectando…".
- **D3:** o sucesso só é anunciado ao leitor de tela (`role="status"` `sr-only`), sem toast; a falha é um `role="alert"` visível: dentro do detalhe quando ele está aberto (ele cobre a página), senão acima da lista, nunca nos dois. A falha COM id (erro ambíguo de um banco) só aparece no detalhe daquele banco; a SEM id (403, "recarregue a página") aparece em qualquer detalhe.
- **D4:** quando o banco some (sucesso, 401, erro de releitura, outra aba o tirou) o detalhe fecha sozinho e o foco vai ao título da página; com o banco ainda presente (inclusive no 403 com a mesma conta) o detalhe segue aberto e o foco volta ao botão que abriu o Confirma. Fechado o detalhe por X, Esc ou fundo, o foco volta à linha que o abriu (explícito: o Safari não foca o botão ao clicar). Com um Confirma aberto ou em voo, o detalhe não fecha nem troca de banco.
- **D5:** o botão de confirmar herda o `btn-primary` do `Confirma` (o mesmo das sessões); não há variante destrutiva no design system do painel. Decisão visual do redesenho: o "Desconectar banco" do detalhe (`.banco-det-desc`, `bancos.css`) é contorno na cor `--alert`, sem preenchimento.
- Só um DELETE por vez, POR MONTAGEM da página (remontar a tela zera a trava), com o `uid` capturado no clique; resposta perdida, 404, 5xx e prazo relêem o snapshot (sem o id = foi, com o id = falha); 403 não relê com o `uid` velho, relê o perfil; qualquer erro, menos o 401 (que vai a "sessão terminou"), com a conta trocada (o `uid` da sessão no cache difere do `uid` do pedido) cai no ramo do 403.
- Limites: sair da rota com o DELETE no ar não anuncia; dois bancos com o mesmo nome têm rótulos iguais; troca de conta com o DELETE já processado anuncia "desconectado" sobre a conta antiga; uma remoção por vez só vale por montagem; o alerta do 403 persiste até a próxima ação e não aparece se a conta nova não tem bancos (conta nova com 0 bancos nunca mostra o alerta); um `detail` em texto vindo do servidor vai literal ao alerta; duplo clique rápido no Desconectar tira o foco do botão.

Decisões de execução do W3 (2026-10-19), em `conta/reconectar.ts` (novo: `iniciar`, a máquina de UM fim por reconexão, e `textoDaFalha`), `conta/Bancos.tsx`, `conta/BancoDetalhe.tsx`, `conta/BancoCard.tsx` (`SEM_RECONEXAO`), `conta/Disponiveis.tsx` (tipo `SeletorOF.reconnect`), `frontend/open-finance-connect.js` (`reconnect()` devolve `false` se há conexão em curso) e `tests/frontend/dashboard_v2_bancos_reconectar*.test.mjs` (+ `_bancos_reconectar.mjs`):
- **Um `init` só.** `Bancos.tsx` roteia os callbacks do módulo (`onOpen`, `onConnected`, `onError`, `onClose`) para a reconexão em curso (`emCurso`, ref) e, sem ela, para o seletor de sempre; `open()` e `reconnect()` ignoram um ao outro pela guarda "uma conexão por vez" do módulo. Nenhum caminho paralelo.
- **O detalhe fecha ANTES do widget** (o `<dialog>` modal, na top layer, cobriria o widget da Pluggy, que vai no `body`); o resultado (aviso sr-only "<banco> reconectado.", ou o alerta de falha) aparece NA LISTA, com o foco devolvido à linha (ao título se o banco sumiu, 401 ou 403). Provado com o módulo real e um SDK falso que anexa uma camada em tela cheia: `elementFromPoint` no centro cai no widget.
- **Regra do botão:** a do app (`provider_item_id`, estado fora de `SEM_RECONEXAO` = `removed`/`item_missing`/`paused`, lista comparada com o app em `bancos_paridade.test.mjs`) e `of_banks_max !== 0`; sem o módulo carregado não há botão. NÃO usa `pode_adicionar`: reconectar não gasta vaga (`test_b_p1_reconexao_passa_no_teto`). `btn-primary` só em `needs_user_action` (como o app), senão `btn-ghost`.
- **Uma ação por vez**, por montagem: `emCurso` e `dialogo` (refs síncronas, para dois cliques no mesmo tick) mais `bloqueado` no detalhe; linha e Desconectar ficam inertes durante a reconexão. Prazo de 30 s do clique até o widget abrir (então `destroy()`, que aborta o connect-token em voo); depois de aberto não há prazo.
- **Fim único** (ver o comentário de `iniciar`): `onClose` termina com `null`; `onConnected` e `onError` com o widget aberto terminam na hora e sem mexer no foco (o widget segue por cima; o SDK real pode não mandar `onClose`); `reconnect()` que devolve `false` termina "ocupado" sem destruir a conexão alheia. O `onConnected` que chega depois do fim cai no handler do seletor e relê as listas.
- **Textos:** o do servidor só nos status 400/402/404/409/429 e só se for texto de pessoa (sem `<`, `{`, `[`; até 300 caracteres); 403 e 5xx/rede têm texto fixo; 401 = "Sua sessão terminou.".
- **Isolamento:** o `uid` é o da sessão, fixado no clique; o módulo fixa o `uid` no `connect()`: com troca de conta no meio o POST do save vai à conta do clique (teste E7: `/open-finance/7/pluggy-item`) e, com a sessão já de outra conta, o servidor o recusa (403) e nada é gravado; com a conta trocada o `terminou` não anuncia nem relê nada na conta nova e pede o perfil de novo.
- **Layout (corrige o #1142, misturado aqui por tocar os mesmos arquivos):** o cabeçalho ocupava a página inteira enquanto a coluna da lista tinha 720 px, e o "+" ficava colado na borda da página. Agora, com "Disponíveis para conectar" e viewport >= 1101 px (o ponto em que o `.page-grid` deixa de ser uma coluna só), DUAS colunas (bancos | disponíveis, `1.4fr 1fr`, conjunto de até 1100 px, topos alinhados) e o "+" na borda direita da coluna da direita; abaixo disso, ou sem "Disponíveis", uma coluna de até 720 px (conectados primeiro, na ordem do DOM) e o "+" na borda direita dela. O rótulo "Disponíveis para conectar" perdeu o recuo de 4 px. Medido em `dashboard_v2_bancos_alinhamento.test.mjs` (1440, 1280, 1024, 390 e a tela sem "Disponíveis"); o detalhe (fixo, 440 px à direita) não desloca as colunas.
- Não feito (fora do pedido): os "Conectar banco" de outras telas do painel (`parts/MetaCartao.tsx`, `MetaFonte.tsx`, `Subscriptions.tsx`) e o `OF` do guia seguem para `/settings?view=open-finance` (decisão 1 pedia a página nova quando o W3 entrasse).
- **Só em produção:** o SDK v2.7.0 aceitar `updateItem` com o token criado com `itemId` e a conexão voltar a "Atualizado" sem mudar "N de M"; o overlay REAL da Pluggy ficar acima do detalhe/lateral/Menu (o teste usa uma camada falsa); a ordem real de `onClose`/`onSuccess`/`onError` do SDK.
- **Foco com o widget por cima:** nos fins `onConnected` e `onError` o foco não é roubado; ele volta à linha (ao título se o banco sumiu, 401 ou 403) no `onClose` tardio do widget (`pendente` em `Bancos.tsx`). Se o SDK real nunca mandar `onClose`, o foco fica onde estava (limite). A reconexão que já terminou não engole mais o que vem do seletor (`acabou()`); trocar de conta limpa o aviso sr-only e o toast da conta que saiu (a `falha` fica: o alerta do 403 do Desconectar nasce da troca de conta; a de reconexão guarda o `uid` do clique e só aparece nessa conta).
- Limites (B2-B4): (B2) o alerta de falha acima da lista, na coluna da esquerda, empurra o grupo para baixo e desalinha os topos das colunas enquanto existir (41 px a 1280); (B3) com `of_banks_max` nulo e "Disponíveis" há uma linha vazia (nbsp, 25 px) acima da lista da esquerda, só nas duas colunas (>= 1101 px; em uma coluna ela some por CSS); (B4) o aviso do resultado ("<banco> reconectado.") só sai depois da releitura (até 30 s se lenta); um `onClose` que chega antes do fim da releitura, com o card sumindo nela, perde o foco; o foco do clique (`fecharDet`) cai no `<body>` porque o `<dialog>` ainda deixa a linha inerte, e só volta no fim.
- Limites: sair da rota com o widget JÁ aberto não cancela o save (de propósito), mas o anúncio se perde, e um novo clique depois de voltar mostra "Já tem uma conexão com banco aberta..." até o widget terminar; o aviso "ocupado" fica até a próxima ação; com o `/limite` carregando ou em falha o botão aparece (o servidor recusa com 402 em português); retentar na mesma janela do widget depois de um `onError` salva o banco, mas só relê a lista no `onConnected` que cair no seletor.

## Decisões do dono (fechadas em 2026-10-18: "pode seguir com as suas recomendações")

1. **A tela mora no `/painel`** (`#/conta/bancos`, seção Conta). Os links "Conectar banco" dos outros pontos do painel
   passam a apontar para a página nova; isso sai em PR seguinte (NÃO no PR do W3), e até lá seguem no `/settings?view=open-finance`.
2. **Total do topo = saldo em conta + investimentos**, como o app e como `/bancos` devolve.
3. **Reconectar estende `PBOpenFinance`** com `itemId` e o painel o usa (um widget só, um `init` só). É o W3 (feito).
4. **Acompanhar sincronização:** só a pílula "Atualizando…", relida pelo SSE e pelo foco da aba. Sem `PBColetaOF` no
   painel; o W3 fica sem esse botão.
5. **"↻ Atualizar" e "Remover todas as conexões" ficam no settings.** O app não os tem; a página nova não os ganha.
6. **Esconder valores:** a tela nasce sem, como o resto do painel. Fica a linha própria na paridade.
7. **Logo: feito** (#1142, junto do plano B do servidor). O W1 saiu com a inicial colorida; agora o ladrilho traz o
   `logo_url` quando há (host fixado no cliente, cópia da regra do app, com teste de paridade) e volta à inicial se a
   imagem falhar. Prova do logo real da Pluggy só depois do deploy.
8. **Faixa de segurança (2026-10-18):** entra só no detalhe do banco, entre as linhas e o botão, com o texto exato
   "Conexão regulamentada pelo Banco Central, oficial pelo Open Finance.", estática, com cadeado (`ph-lock`, que existe no
   subset), fundo de superfície e texto em `--ink-2` (contraste medido em teste, 4,5:1 ou mais). Não aparece na linha.

Ajuste de execução: o W0 (cores) entra dentro do W1, para um ciclo de revisão a menos. O teste de paridade das cores
continua obrigatório.
