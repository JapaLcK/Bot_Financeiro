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

1. **Topo:** `total` de `/bancos` (saldo em conta + investido, decisão 1 do dono no plano do app). Rótulo **não** é
   "Disponível agora". Com `motivos` não vazio, selo "a conferir" pelos `Selos` existentes. `total: null` = "—".
2. **Barra por banco** (`.comp-bar`), um segmento por banco com a cor de `corDoBanco`. Desenha só se: nenhum motivo,
   no topo ou num banco (lista branca **vazia**: o `MOTIVOS_SEM_EFEITO_NO_SALDO` do app é de `/contas`, e em `/bancos`
   `outra_moeda` tira dinheiro do valor); todo `total` de banco é não nulo e finito, nunca negativo; há ao menos 2
   segmentos; e a soma em inteiros exatos (BigInt, não centavos) fecha **exatamente** com o `total` do topo. Banco com
   total exatamente zero não vira segmento e não impede a barra (como o app, `composicaoContas.ts`). Senão, sem barra.
   A barra compara, não mostra soma: o número na tela é sempre o do servidor. Menos de 2 segmentos não é composição.
   `role="img"` com `aria-label` "Nubank 67%, Inter 33%", como em `Wealth.tsx`.
3. **Vagas do plano:** "2 de 3 bancos" de `/limite` (`em_uso`, `of_banks_max`). `of_banks_max: null` = nada (sem teto).
   Com `pode_adicionar: false`, mostra a `message` do servidor no lugar do botão de conectar.
4. **Card por banco** (um por conexão do snapshot, na ordem do snapshot):
   - logo (`logo_url` quando há; sem ele, inicial num círculo com a cor do banco), nome
     (`institution_name`), total do banco;
   - pílula do estado (`ui.label`, classe por `ui.state`, mesmo mapa de `OF_PILL_CLASS` do settings);
   - linha curta: saldo em conta · investido · "1 conta · 1 cartão · 4 investimentos" (contagens de `/bancos`);
     `null` = "—", nunca "R$ 0,00";
   - "há 12 min" de `last_sync_at` (texto inteiro no `title`/`<time datetime>`);
   - **no W1 o card não tem `<details>`:** sem ação dentro só revelaria uma linha, e `ui.detail` já aparece no card. O
     `<details>`/`<summary>` (inline, sem folha nem modal) entra no W2, com Desconectar; depois o W3 acrescenta
     Acompanhar sincronização (estado `updating`) e Reconectar.
5. **Nenhum banco:** o Piggy (`ICON`) e um botão "Conectar banco". Sem frase longa.
6. **Conexão sem linha em `/bancos`** (recém-conectada, só posição sem conta, `DELETED`): o card aparece pelo snapshot
   com "—" no valor. Junta-se por `conexao_id` = `connection.id`, **nunca** por nome nem por posição.

Cara de web (onde difere do app de propósito): tooltip com data completa, foco por teclado nos cards, confirmação
em `<dialog>` em vez de `Alert`, grade de 2 colunas no desktop (≥ 900 px) e uma coluna no celular.

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
4. `conta/Bancos.tsx` (página) e, só se passar do teto de linhas, `conta/BancoCard.tsx`. `juntar(conexoes, bancos)` por
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

### W3 — Reconectar e Acompanhar (Completo; depende de staging)

- O widget da Pluggy só existe no legado (`open-finance-connect.js`, IIFE, `window.PBOpenFinance`). Decidido (decisão 3):
  `PBOpenFinance` ganha um parâmetro opcional `itemId` que vai no corpo do
  `connect-token` e no `updateItem` do `PluggyConnect`; o painel carrega o script e o SDK da Pluggy só ao clicar.
  Quem não passa `itemId` (o settings) recebe o mesmo comportamento de hoje.
- **Não verificado, condição para começar:** que o SDK da Pluggy na versão carregada aceita `updateItem` com um token
  criado com `itemId`. Só se prova em staging (conta demo), com um banco real.
- "Acompanhar sincronização" quando `ui.state === "updating"`: reaproveitar `PBColetaOF` (`frontend/of-status-poll.js`)
  se ele aceitar um item; senão, só reler o snapshot pelo `vivo` (foco de aba) e mostrar "Atualizando…" (decisão 4: só a pílula).
- Regras de quando mostrar Reconectar: as do app (`provider_item_id` presente, `of_banks_max !== 0`, estado fora de
  `removed`, `item_missing`, `paused`). Reconectar não conta vaga nova (P1 do `/limite`).

### Fora do escopo

Esconder valores (o painel não tem; linha própria na paridade), validade da autorização e fatura aberta (fora do v1
também no app), reordenar bancos, mexer na aba Open Finance do `settings.html` além do parâmetro do W3.

## Riscos

- **Junção errada:** juntar por `institution_name` funde duas "Nubank". Junta-se por id; teste com dois "Nubank".
- **Somar no cliente:** o topo pode não fechar com os cards se a web somar. O número do topo é o do servidor; a barra
  só compara em centavos inteiros e some quando não fecha.
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
- **Barra:** desenha com dois bancos que fecham; some com um `total` nulo e com motivo `banco_desatualizado`. Negativo:
  tirar a checagem de fechamento desenha barra com total nulo e reprova.
- **Positivo das leituras opcionais:** `/bancos` em 500 e em 404 = os cards aparecem com nomes e estados, sem números;
  `/limite` em 500 = sem a linha de vagas e o resto inteiro.
- **Estado vazio:** snapshot sem conexão mostra o Piggy e "Conectar banco" com `href` para `/settings?view=open-finance`.
- **Tempo relativo** com relógio congelado: 12 min, 3 h, 2 dias.
- **Layout medido, não olhado:** no celular nenhum card passa da largura da janela (`scrollWidth <= innerWidth`), o alvo
  dos botões ≥ 44 px; no desktop duas colunas. Pergunta "quanto daria sem a correção?" respondida em cada medida.
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
- **Só em staging:** reconectar um banco de verdade com a conta demo e ver a conexão voltar a "Atualizado" sem gastar vaga.

Só depois do deploy (todos os PRs): números reais com a conta demo, painel e app lado a lado mostrando o mesmo topo e os
mesmos bancos; Safari do iPhone no `/painel`.

Decisões de execução do W2 (2026-10-18), implementado em `conta/Bancos.tsx`, `conta/Confirma.tsx` (extraído de `Sessoes.tsx`), `conta/BancoCard.tsx`, `tests/frontend/dashboard_v2_bancos_desconectar.test.mjs`, `tests/frontend/dashboard_v2_bancos_desconectar_bordas.test.mjs` e o helper `tests/frontend/_bancos_desconectar.mjs`:
- **D1:** o `<summary>` diz "Gerenciar" (o nome do banco só para leitor de tela) e fica a 44 px; o botão "Desconectar <banco>" mora dentro.
- **D2:** textos do app (`conexoes.tsx`): "Desconectar <banco>?", "Os dados importados deste banco serão removidos. Seus outros bancos e lançamentos manuais serão preservados.", "Manter banco" / "Desconectar" / "Desconectando…".
- **D3:** o sucesso só é anunciado ao leitor de tela (`role="status"` `sr-only`), sem toast; a falha é um `role="alert"` visível acima da lista.
- **D4:** quando o card some (sucesso, 401, erro de releitura) o foco vai ao título da página; com o card ainda presente, volta ao botão que abriu. No ramo do 403 (e quando o card some com o botão já fora do DOM, o caso B1) o foco também vai ao título (`#page-title`).
- **D5:** o botão de confirmar herda o `btn-primary` do `Confirma` (o mesmo das sessões); não há variante destrutiva no design system do painel.
- Só um DELETE por vez, POR MONTAGEM da página (remontar a tela zera a trava), com o `uid` capturado no clique; resposta perdida, 404, 5xx e prazo relêem o snapshot (sem o id = foi, com o id = falha); 403 não relê com o `uid` velho, relê o perfil; qualquer erro, menos o 401 (que vai a "sessão terminou"), com a conta trocada (o `uid` da sessão no cache difere do `uid` do pedido) cai no ramo do 403.
- Limites: sair da rota com o DELETE no ar não anuncia; dois bancos com o mesmo nome têm rótulos iguais; troca de conta com o DELETE já processado anuncia "desconectado" sobre a conta antiga; uma remoção por vez só vale por montagem; o alerta do 403 persiste até a próxima ação e não aparece se a conta nova não tem bancos (conta nova com 0 bancos nunca mostra o alerta); um `detail` em texto vindo do servidor vai literal ao alerta; duplo clique rápido no Desconectar tira o foco do botão.

## Decisões do dono (fechadas em 2026-10-18: "pode seguir com as suas recomendações")

1. **A tela mora no `/painel`** (`#/conta/bancos`, seção Conta). Os links "Conectar banco" do painel passam a apontar
   para a página nova quando o W3 entrar; até lá seguem no `/settings?view=open-finance`.
2. **Total do topo = saldo em conta + investimentos**, como o app e como `/bancos` devolve.
3. **Reconectar estende `PBOpenFinance`** com `itemId` e o painel o carrega (um widget só). É o W3.
4. **Acompanhar sincronização:** só a pílula "Atualizando…", relida pelo SSE e pelo foco da aba. Sem `PBColetaOF` no
   painel; o W3 fica sem esse botão.
5. **"↻ Atualizar" e "Remover todas as conexões" ficam no settings.** O app não os tem; a página nova não os ganha.
6. **Esconder valores:** a tela nasce sem, como o resto do painel. Fica a linha própria na paridade.
7. **Logo: feito** (#1142, junto do plano B do servidor). O W1 saiu com a inicial colorida; agora o círculo traz o
   `logo_url` quando há (host fixado no cliente, cópia da regra do app, com teste de paridade) e volta à inicial se a
   imagem falhar. Prova do logo real da Pluggy só depois do deploy.

Ajuste de execução: o W0 (cores) entra dentro do W1, para um ciclo de revisão a menos. O teste de paridade das cores
continua obrigatório.
