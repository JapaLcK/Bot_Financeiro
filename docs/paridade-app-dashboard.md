# Paridade app × dashboard: onde cada um está atrás

Regra do dono (2026-10-16): app (`app/`, Expo/React Native) e `/painel` (`webapp/src/dashboard`, `api/v2/`) **andam juntos, nos dois sentidos**, com as mesmas funções adaptadas à plataforma. No app valem "visual, mínimo de texto, quanto mais nativo melhor".
Este documento **só lista as diferenças**. Não propõe solução, ordem nem prazo: cada linha vira uma conversa própria com o dono, uma por vez. Por isso toda linha termina em "a discutir".
O plano do card "Seu dia" no app (`docs/plano-seu-dia-app.md`) não é repetido aqui, só citado.
Medido na `main` em 53681b4e (2026-10-17). As colunas comparam telas; o chat do Piggy do app também lê e escreve (lançamentos, metas), e isso é dito nas linhas em que muda o quadro. Paridade plena existe em várias funções (por exemplo MFA, receitas do extrato, Sair) e não aparece nas tabelas.

"Exemplo" no Web quer dizer que o bloco desenha dado de demonstração (`lib/api.ts`, sem rede), não o do usuário.

## Resumo

| Função | Web | App | Imparidade | Quem está atrás | Como resolver |
|---|---|---|---|---|---|
| Blocos e perfis da home | `Board.tsx`; esconde bloco que o plano não cobre (`locked`); ordem guardada sem o id do usuário (`pigbank.dashboard.layout.v1.<perfil>`) | `catalogo.ts`; oferece os blocos da Home (`BLOCOS_HOME`: "Adicionar blocos" não oferece `metas`); ordem guardada por usuário e perfil | Plano filtra bloco só no web; chave sem usuário só no web; o app já não tem o bloco "Metas e caixinhas" (a aba Metas tem o seu, o `metas`, fora da home como no web) | App (filtro por plano) e web (chave) | a discutir com o dono |
| Contas | Nome, saldo e selos | Soma a data da última sincronização ("Atualizado dd/mm") e uma barra de composição (os lançamentos do mês de cada conta só aparecem nos cards do chat, não na home) | O contrato do web já traz `sincronizado_em`, mas a tela não mostra | Web | a discutir com o dono |
| Resumo do mês | Compara com o mês anterior inteiro, sem porcentagem (comentário "decisão N4" em `Stats.tsx`); o card "Fatura" é de exemplo | Compara no mesmo ponto do mês, com variação em % e curva | Duas comparações diferentes | Não está claro; pode ser decisão | a discutir com o dono |
| Entrou e saiu no mês (gráfico de linha) | Não há | Topo da aba Gastos (`numerosMes.tsx`): duas linhas acumuladas, Entrou e Saiu, de `mes-detalhes`; sem Saldo do mês nem Guardado líquido | O dado só existe no namespace do app (`api/nativo/mes_detalhes.py`) | Web | a discutir com o dono |
| Esconder valores (modo privacidade) | Não há no `/painel` | Botão e `oculto` em todo o app | — | Web | a discutir com o dono |
| Seletor de mês | Os 6 últimos meses (`MESES` em `lib/v2.ts`) | Até o início do histórico do plano (`history_earliest_date`) | Quem tem plano maior vê mais meses no app | Web | a discutir com o dono |
| Comparação de períodos | PR #1033 aberto, fora da `main` | Não há | Nenhum lado tem | Ambos | a discutir com o dono |

## Gastos

| Função | Web | App | Imparidade | Quem está atrás | Como resolver |
|---|---|---|---|---|---|
| Para onde vai | `/gastos`: estabelecimentos, série de meses, detalhe da categoria | Lê só `mes-detalhes`; o schema de `/gastos` existe, mas só o hook do "Seu dia" o usa | Sem estabelecimentos, série nem detalhe | App | a discutir com o dono |
| Dia a dia (calendário) | Quantidade e categorias por dia | Só entrou e saiu por dia; o toque abre o extrato do dia | Menos informação no app | App | a discutir com o dono |
| Seu dia | Card em `/gastos` (`GastosDiaTopo.tsx`) | Só a base: `useGastosMes` (nenhuma tela o usa) e o Extrato que aceita `dia` | O card não existe no app | App (plano em `docs/plano-seu-dia-app.md`) | a discutir com o dono |
| Simulador | Exemplo; mais controles (gasto novo, trajetória de 90 dias, metas afetadas) | Real, mas só corte por categoria | Um tem dado real, o outro tem mais controles | Ambos | a discutir com o dono |
| Assinaturas | "Não é assinatura", "Voltar a mostrar" (restaura a marca), "Desfazer", e o aviso de reajuste com o valor anterior | Só "Ignorar", "É assinatura" e "Reconsiderar", que sempre manda `nenhuma` e apaga a marca antiga; não mostra o reajuste | O app perde a marca manual ao reconsiderar | App | a discutir com o dono |

## Previsão

| Função | Web | App | Imparidade | Quem está atrás | Como resolver |
|---|---|---|---|---|---|
| Saldo previsto: motivos | Lista cada motivo da previsão com a direção do erro (`MotivosPrevisao`), o estado ("Previsão condicional", "A conferir") e os selos da base | Âncora, bancos fora do cálculo, pior dia com desde e causas já aparecem (`futuro.tsx`: a curva fica sempre no card e o resto (âncora, marcos, pior dia, receitas) na folha que o toque no card abre; no web é tudo inline, divergência de plataforma por decisão do dono, 2026-10-18; sem "Como foi feita": premissas e cobertura saíram do app por decisão do dono, 2026-10-18); os motivos e o estado não aparecem (`cobertura.fontes_incompletas` são os mesmos códigos, sem rótulo no app) | O app não diz para que lado o saldo pode errar | App | a discutir com o dono |
| "Próximos 30 dias" no Resumo | `Bills.tsx`, de exemplo (a versão real só existe na página Previsão) | Real | O bloco do Resumo não mostra dado real | Web | a discutir com o dono |

## Metas, patrimônio e cartões

| Função | Web | App | Imparidade | Quem está atrás | Como resolver |
|---|---|---|---|---|---|
| Metas: ler | `GET /metas` | Aba Metas (primeiro build arquivado depois deste merge): `GET /api/app/metas` (a mesma v2), as mesmas pílulas do chat, todas as metas (tocar na pílula abre a folha de edição; a escrita está na linha seguinte); a rota do app não filtra pela chave `dashboard_v2_enabled`: a lista vem vazia só para quem não tem meta ("Nenhuma meta ainda."); builds ≤31 seguem na rota legada | Nenhuma: a mesma fonte | Nenhum | resolvido no PR 1 do app |
| Metas: criar, editar, apagar | Formulário (`MetaForm.tsx`, `MetaFonte.tsx`); edição manda só o diff; confirmação do apagar no próprio diálogo | Folha nativa (`app/app/(app)/meta.tsx`, `metaForm.tsx`) aberta pelo "+" do cartão, pela pílula e por "Montar reserva", nas mesmas rotas `/api/app/metas*` (todo usuário do app, sem a chave); mesmas regras (`metasEscrita.ts`, presa por teste de paridade); prazo pelo seletor nativo, fonte numa lista expansível, `Alert` nativo para apagar; o chat do Piggy segue criando, editando e apagando (só para quem tem a chave) | Telas e textos próprios de cada plataforma (mínimo de texto no app); no app a conta sem a chave cria meta que o Piggy e o WhatsApp dela não enxergam até a chave abrir (P1-b); o web não tem "Tirar prazo" na criação | Nenhum no recurso; limite P1-b é de servidor | PR 2 do app (aguarda build novo: datetimepicker é nativo) |
| Patrimônio e "Onde está o dinheiro" | Exemplo | Real (`/api/app/patrimonio`) | A rota só existe no namespace do app | Web | a discutir com o dono |
| Rendimento | Exemplo ("Rendimento × CDI") | Real (taxa contratada, `/api/app/rendimento`) | Idem | Web | a discutir com o dono |
| Fatura do cartão, parcelas futuras, renda mês a mês, "Piggy notou" | Exemplo | Reais, por rotas legadas com `user_id` no caminho (`/cards`, `/installments`, `/analytics`, `/insights`) | Web sem dado real; app preso a rota legada | Web (e o app, na rota) | a discutir com o dono |

## Extrato

| Função | Web | App | Imparidade | Quem está atrás | Como resolver |
|---|---|---|---|---|---|
| Filtros | Busca, origem (inclui `registro_antigo`), tipo, categoria, dia e **conta** | Os mesmos, menos conta e menos a origem `registro_antigo` (o `dia` vai ao servidor desde o PR #1061) | Faltam o filtro por conta e o de registro antigo | App | a discutir com o dono |
| Busca curta | Bloqueia termo só de 1 letra e avisa | Manda o termo, o servidor o ignora e a tela diz "Buscando em todo o histórico" | Mensagem enganosa no app | App | a discutir com o dono |
| Aportes e saques (`guardado`) | Pede `guardado: true` | Não pede | Linhas de guardado não aparecem no app | App | a discutir com o dono |
| Lançar, editar e apagar na Carteira | Formulário no Extrato | O Extrato só lê; pelo chat do Piggy a IA registra, apaga e recategoriza lançamentos | Sem formulário no Extrato; o chat não cobre toda edição (data e valor, por exemplo) | App | a discutir com o dono |

## Chat do Piggy e guia

| Função | Web | App | Imparidade | Quem está atrás | Como resolver |
|---|---|---|---|---|---|
| Conversa | De exemplo (`CHAT_REAL = false`); só o assunto `investido` é real | Real (`/ai/chat/stream`, feedback, cancelar pendência) | — | Web | a discutir com o dono |
| Cards no chat | Renderiza cards de exemplo montados no cliente (`lib/topics.tsx`), não os que o servidor manda | Renderiza os cards reais que o servidor manda (período, caixinhas, metas, gastos e os blocos do catálogo) | O web não lê os cards do servidor | Web | a discutir com o dono |
| Guia do Piggy e dicas | `Guia.tsx`, `Dica.tsx` | Não há | — | App | a discutir com o dono |

## Conta, segurança e sistema

No web, o Perfil do `/painel` é página real (nome, e-mail, telefone, Conectar WhatsApp e "Desde …", pelas rotas `/settings/{uid}/security[/contact]`, `/auth/dashboard-profile`, `/auth/me` (só `has_password`) e `/auth/link-code`); a Cobrança do `/painel` segue página-ponte para a tela antiga `/settings` (`conta/Ponte.tsx`); Bancos conectados é página real, que desconecta um banco por vez (`conta/Bancos.tsx`, linha abaixo); as Configurações mostram a Segurança (senha por link de e-mail e MFA, pelas rotas `/auth/mfa/*` e `/settings/{uid}/password-reset`; `conta/Seguranca.tsx`), os dispositivos conectados (sessões ativas, pelas rotas `/settings/{uid}/sessions`; `conta/Sessoes.tsx`), as preferências de notificação (pelas rotas `/settings/{uid}/notifications`; `conta/Notificacoes.tsx`) e o link para a `/settings`, onde mora o resto do conteúdo abaixo.

| Função | Web | App | Imparidade | Quem está atrás | Como resolver |
|---|---|---|---|---|---|
| Excluir conta e exportar dados | `/settings` | Configurações → "Exportar meus dados" e "Excluir conta" (`excluir-conta.tsx`, `exportar-dados.tsx`; exclusão imediata, código por e-mail, Alert nativo) | O `/painel` não tem (só a `/settings`) | Web | C1 (app) feito; falta C3 (`/painel`) no [`plano-app-conta-excluir-exportar-perfil.md`](plano-app-conta-excluir-exportar-perfil.md); a Apple não revoga o token (S3/C2, fora) |
| Recomeçar do zero | `/settings` | Não há | — | App | a discutir com o dono |
| Sessões ativas | `/settings` e Configurações do `/painel` | Não há (o código diz que "entram como seção irmã") | — | App | a discutir com o dono |
| Preferências de notificação | `/settings` e Configurações do `/painel` (C2, #1124): os mesmos controles; horário só em hora cheia da lista (06–12 e 18–21) | `notificacoes.tsx` (Configurações > Notificações): os mesmos controles pela mesma rota `/settings/{uid}/notifications`, um PATCH por controle, horário com qualquer hora e minuto (roda nativa); trocar o horário não religa o diário | Horário: o web só escolhe hora cheia da lista (o `/painel` mostra um horário com minuto como opção extra; o `/settings` arredonda: 20:15 aparece como 20:00, 03:00 deixa a lista sem seleção). Só o `/settings`, ao trocar a hora, grava minuto 0 e religa o diário (`set_daily_report_hour`). Push só no app | Web | issue #1122 (horário do `/settings`); a seção de push do app está em [`plano-push-app.md`](plano-push-app.md) |
| Contato (e-mail, nome, telefone) | Perfil do `/painel` (e `/settings`) | Só lê (`perfil.tsx`, P1): nome, e-mail, telefone, Conectar WhatsApp e "Desde …"; não edita | Edição (P2) | App | issue #1083; plano em [`plano-app-conta-excluir-exportar-perfil.md`](plano-app-conta-excluir-exportar-perfil.md), seção B |
| Trocar a senha de quem já tem senha | `/settings` e Configurações do `/painel` (link por e-mail, `password-reset`) | Só o link por e-mail para conta sem senha (`seguranca.tsx`) | — | App | a discutir com o dono |
| Bancos conectados: quanto vale cada banco, total e vagas | `#/conta/bancos`: topo de `/bancos`, barra, "N de M bancos", linha clicável (estado, total) que abre o detalhe com "Desconectar banco" (um por vez, com confirmação) | `conexoes.tsx`: lista com estado, reconectar, desconectar, `message` do `/limite`; sem valores nem "N de M" (PR 3 de `plano-bancos-conectados-app.md`) | O app não mostra valores nem vagas | App | a discutir com o dono |
| Open Finance: reconectar e desconectar um banco | `#/conta/bancos` desconecta um por vez; `/settings` desconecta todos; reconectar não | Reconecta e desconecta um por vez | O web não reconecta | Web | a discutir com o dono |
| Open Finance: atualizar agora, desconectar todos | Sim (`/refresh`, `disconnectAll`) | Não há | — | App | a discutir com o dono |
| Vincular caixinha do banco a uma meta | `/settings` | Não há | — | App | a discutir com o dono |
| Política de Privacidade | Link em Configurações | Só no cadastro | Quem já tem conta não a alcança | App | a discutir com o dono |
| Suporte | Sem link no `/painel` | "Falar com o suporte" | — | Web | a discutir com o dono |
| Tema | Só escuro (`color-scheme: dark`) | Segue o sistema | — | Web | a discutir com o dono |
| Categorias: criar, renomear, fundir, apagar | Só lê | Só lê | Nenhum lado tem | Ambos | a discutir com o dono |

## Específico da plataforma

- Trava biométrica e tampa de privacidade (`features/bloqueio`): só existem em aparelho.
- Cmd-K e atalhos de teclado (`Command.tsx`): não têm par em toque.
- Compras Apple e restauração: regra da loja; o `app/README.md` as dá como "entrega separada".
- Tempo real: o web recebe SSE (`/eventos`); o app não tem cliente dele, e `docs/api-app-painel.md` registra o SSE só no namespace web.
- Push: o servidor já envia por APNs (dormente sem as envs, e nenhum evento de produto dispara push) e tem as rotas de registro, feitas para o app Capacitor antigo; o app Expo não registra token (`expo-notifications` não está em `app/package.json`); o `/painel` não tem web push, e fica sem. Plano e decisões do dono em [`plano-push-app.md`](plano-push-app.md).

## Como medi

Tudo por leitura de código na `main`; comandos principais (rode de novo antes de reusar):

```bash
git fetch origin && git log -1 --format=%h origin/main
grep -ln "real" webapp/src/dashboard/widgets/*.tsx webapp/src/dashboard/parts/*.tsx   # blocos do web com dado real
grep -n "Query = \|export const .*Query" webapp/src/dashboard/lib/v2.ts              # o que o web lê de verdade
grep -n "const rota\|function rota" -A4 app/src/features/painel/provider.tsx          # o que o app lê
grep -rn "useGastosMes" app/src                                                      # hook do "Seu dia" sem uso
grep -hE "^@router\.(get|post|put|patch|delete)" api/v2/*.py                         # rotas do v2
grep -n "Excluir conta\|Exportar\|sessions\|notifications\|security/contact\|password-reset\|caixinhas" frontend/settings.html
gh pr view 1033 --json state                                                         # comparação de períodos
```

Também li `webapp/src/dashboard/{pages.tsx,router.ts,conta/Ponte.tsx,parts/Board.tsx,parts/Lancamentos.tsx,widgets/{Contas,Stats,Subscriptions,Previsao}.tsx}` e, no app, `catalogo.ts`, `cabecalho.tsx`, `gastos.tsx`, `extrato.tsx`, `complementos.tsx`, `contasResumo.tsx`, `gastosMes.ts`, `app/(app)/{configuracoes,seguranca,conexoes}.tsx`.

## Não verificado

- Nada foi aberto em aparelho, simulador, navegador nem produção: tudo vem do código.
- Não comparei campo a campo os fluxos de entrar, criar conta e esqueci a senha.
- Não comparei o conteúdo de cada card do chat, só que o app os renderiza e o web não.
- Não comparei a lista de perfis do web com a do app além da home, nem se os presets têm os mesmos blocos.
- Não verifiquei se as rotas legadas de `/settings` aceitariam a sessão do app.
- Não verifiquei a qualidade por ocorrência dos compromissos nem o texto dos selos de motivo nos dois lados.
- "Exemplo" e "real" no web vêm da marcação `real` do `Frame` e dos imports de `lib/api.ts`; não abri cada tela com dados.
- Funções que só existem no painel antigo (`/app`: orçamentos, contas a pagar, cartões em CRUD, agentes) ficaram fora.
