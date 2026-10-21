# Plano da Etapa 6 — Patrimônio (dashboard v2)

Aprovado pelo dono em 2026-10-20. Base: `docs/plano-dashboard-v2.md` §4 (Q14), §7
("Etapa 6: variação do período só dentro de um trecho sem quebra"; "Patrimônio sem contar duas
vezes") e §8 (PR 6, #723). Este plano é base, não especificação: o detalhe de cada PR sai do
Arquiteto da faixa.

## 1. O que já existe (inventário de 2026-10-20, branch em dia com a `main` 3cb04c21)

- **A conta única**: `db/patrimonio.calcular(cur, uid)` — Carteira (com a fusão devolvida) +
  contas BANK em reais + posições do banco em reais + caixinhas manuais + investimentos manuais;
  cartão fora. Devolve `total`, as 5 partes, `base` (`v`, `contas`, `posicoes`, `conexoes`,
  `fora`) e `motivos`. A tabela de estados do espelho do Open Finance está no plano §8 (PR 6).
  "Sem contar duas vezes" já é resolvido aqui (caixinha espelhada só pela posição, uma linha por
  identidade do provedor, `manual_e_banco` e `caixinha_espelhada_fora` como motivos).
- **A foto diária**: `patrimonio_fotos` (PK `user_id, dia`), gravada por
  `core/services/patrimonio_foto.py` a partir das 18h de São Paulo, atrás de
  `PATRIMONIO_FOTO_ENABLED`.
- **Chave em produção**: não dá para ler daqui (env do Railway). A memória do projeto registra que
  o dono a LIGOU em 2026-10-01 e conferiu o 1º dia (25 fotos, todas com
  `carteira_nao_confirmada`, nenhuma exata). Se ainda está ligada, o histórico vai de 01/10 a
  hoje: cerca de 19 a 20 dias (a foto de hoje só existe depois das 18h). Confirmar com
  `select min(dia), max(dia), count(distinct dia) from patrimonio_fotos;` antes do PR B.
- **A rota já existe, mas só no app**: `api/nativo/patrimonio.py`, `GET /api/app/patrimonio`
  → `{total, partes, motivos, historico[{dia, total, partes, motivos}], historico_desde}`.
  Histórico cortado pela janela do plano (`history_earliest_date`), sem teto de 12 meses e sem
  quebra de linha. Dinheiro em centavos (`api/nativo/dinheiro.Dinheiro`).
  `docs/paridade-app-dashboard.md` linha 45 já marca a divergência ("a rota só existe no namespace
  do app", "a discutir com o dono").
- **Quem lê essa rota hoje**: o app (`app/src/features/painel/patrimonio.tsx`, blocos
  `patrimonio` = valor + curva + 7 dias, e `wealth` = anel + 5 partes; `patrimonioSchema` em
  `app/src/api/schemas/painel.ts`) e o card do chat (`core/services/ai_chat/cards.py:149`, importa
  `api.nativo.patrimonio.patrimonio`; a visão do modelo pega `total, partes, motivos,
  historico_desde` e os 7 últimos itens de `historico`).
- **Outras leituras de `calcular`**: `/api/v2/progresso-milhao`, `db/contas_hoje`, `db/investido`
  (`POSICOES_BANCO_SQL`/`separar_posicoes`), `db/bancos`, `db/metas_ritmo`.
- **Web**: a página `/painel#/patrimonio` e os blocos `patrimonio` e `wealth` do Resumo são do
  protótipo (`widgets/NetWorth.tsx`, `widgets/Wealth.tsx`, dados de `lib/api`), sem rota real.

## 2. Divisão proposta (decisão do dono)

**PR A — API (faixa Completo).**
- Leva a rota para `api/v2/patrimonio.py` e a monta pelo `criar_app`: passa a existir em
  `/api/v2/patrimonio` e continua em `/api/app/patrimonio` (mesma URL, contrato só acrescenta).
  `api/nativo/patrimonio.py` reexporta a função, para o card do chat seguir igual (a Etapa 7 está
  em outra sessão; o chat não é tocado).
- Acrescenta, no topo da resposta (não dentro de cada foto, para a visão do modelo no chat não
  mudar): os trechos sem quebra e a variação do trecho (regra na seção 3). O dinheiro da variação
  sai do servidor; a tela não soma.
- Teto de 12 meses (Q14) além da janela do plano. Hoje não muda nada (o histórico começa em
  01/10/2026).
- A regra da quebra é função pura em `db/patrimonio.py`, testada com bases de foto montadas à mão
  e com fotos gravadas por `gravar_foto` (Postgres real), mais o teste de isolamento ("B não vê as
  fotos de A") e o contrato (gerador de tipos + fixture).

**PR B — tela no `/painel` (faixa Leve).**
- A página `/patrimonio` e os blocos `patrimonio` e `wealth` do Resumo passam a ler
  `/api/v2/patrimonio`: total de hoje, linha das fotos quebrada entre trechos, variação do trecho,
  composição pelas partes do servidor, avisos pelos `motivos`, estados de carregando, erro e "ainda
  sem histórico" (menos de 2 fotos). O protótipo (DEMO) segue com o exemplo.
- Verificação em desktop e celular (skill `pigbank-frontend`).

**PR C — app (faixa Leve), par do PR B.**
- O bloco Patrimônio do app já existe; ele passa a usar os campos novos (quebra da linha e
  variação). Só chega com build novo (sem `expo-updates`).

Par no outro lado (regra do CLAUDE.md §4): o PR A serve os dois; B e C são o par um do outro.

## 3. Decisões do dono (2026-10-20)

1. **Divisão**: A primeiro; depois B e C em paralelo, cada um no seu PR.
2. **Quebra**: banco que entra, sai, pausa ou volta; conta ou posição que muda de moeda; mudança
   da regra da conta (`base.v`). Investimento novo ou resgatado dentro do mesmo banco não quebra.
3. **Variação**: só a do trecho atual ("desde DD/MM" até hoje); a quebra recomeça a contagem.
4. **Incerteza**: a tela do patrimônio (web e app) não mostra `carteira_nao_confirmada`; os
   outros motivos seguem como avisos. A API mantém o motivo (o card do chat lê a mesma função e
   não é tocado nesta etapa): quem omite é a tela, nos PRs B e C.

As perguntas como foram feitas ficam abaixo, para registro.

## 3a. Perguntas feitas ao dono

1. **Divisão em PRs**: A → B → C, ou A → (B ∥ C), ou A e B agora e o app depois.
2. **O que quebra a linha** (Q14: "banco entrou ou saiu, conta em outra moeda, correção de base"):
   - (a) banco que entra ou sai (conexão nova, removida, pausada ou que volta), conta ou posição
     que muda de moeda, e mudança da regra da conta (`base.v`); investimento novo ou resgatado
     dentro do mesmo banco NÃO quebra (é dinheiro mudando de lugar). Recomendado.
   - (b) qualquer mudança no conjunto de contas ou posições quebra (todo CDB novo quebra a linha).
   - (c) só banco entrando ou saindo e mudança de regra.
   Risco a medir no PR A: a reconexão pode trocar os ids do provedor e quebrar a linha sem o
   total ter mudado.
3. **Variação do período**:
   - (a) só a variação do trecho atual, "desde DD/MM" até hoje; uma quebra recomeça a contagem.
     Recomendado (com 20 dias de histórico, períodos fixos ficam vazios).
   - (b) períodos fixos (30 dias, 3 meses, 12 meses), cada um só quando cai inteiro num trecho;
     senão "sem comparação".
4. **Fotos incertas**: toda foto tem `carteira_nao_confirmada` (Q37 nunca existiu).
   - (a) a linha é uma só e os avisos aparecem agregados embaixo do gráfico, como o app já faz.
     Recomendado.
   - (b) ignorar `carteira_nao_confirmada` só na tela do patrimônio (os outros motivos seguem).
   - (c) marcar o ponto incerto no gráfico (hoje seriam todos).

## 4. Fora desta etapa

- O bloco de rendimento (`/api/app/rendimento` segue só no app; o widget `rendimento` do Resumo
  web aponta para `/patrimonio` e segue de exemplo).
- Alinhar a flag `carteira_nao_confirmada` à revisão de 2026-10-05 (§4 do plano geral).
- Câmbio, reconstrução do passado, poda das fotos.
- O chat (Etapa 7, outra sessão).
