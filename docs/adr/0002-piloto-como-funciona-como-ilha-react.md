# Piloto de `/como-funciona` como ilha React

**Status:** aceita em 14/09/2026; implementação ainda não autorizada.

## Contexto

O PigBank é uma MPA servida pelo FastAPI. O HTML de `/como-funciona` passa por
`frontend/routes/static_pages.py` e `html_file`, que aplica rastreamento, Clarity,
versionamento de assets e headers de cache. O repositório já compila ilhas React
com Vite em `webapp/` e commita os artefatos de nome fixo em `frontend/`.

O piloto precisa validar a adoção incremental de React sem transferir roteamento,
HTML ou deploy para outro servidor e sem enfraquecer o markup que hoje funciona
sem o bundle.

## Decisão

`/como-funciona` será o piloto de uma nova ilha React dentro da MPA atual:

- FastAPI continua proprietário da rota e entrega `como-funciona.html` por
  `html_file(..., clarity=True)`; cada navegação continua sendo uma requisição MPA;
- React possuirá somente os descendentes de um mount exclusivo, a ser nomeado
  `#como-funciona-app`. Navegação, `<head>`, cabeçalho, rodapé e qualquer nó fora
  dele continuam pertencendo ao documento clássico;
- o HTML do mount conterá o markup legado completo e funcional do subtree que
  React possuirá. Todo CTA sujeito ao rewrite assíncrono de `nav-auth.js`, inclusive
  a faixa final de `/cadastro`, ficará como HTML clássico fora do mount; React não
  o renderiza nem o altera. O bundle só monta após validar o contrato esperado;
  se faltar, atrasar ou recusar o mount, deve preservar ou restaurar o conteúdo
  legado, enquanto os CTAs externos continuam utilizáveis e sob propriedade de
  `nav-auth.js`;
- a fonte entrará no projeto Vite existente em `webapp/`. O artefato será o IIFE
  de nome fixo `frontend/como-funciona-app.js`, commitado e reproduzível pelo
  build/gate já existentes. CSS emitido, se houver, seguirá a mesma convenção de
  nome fixo e artefato commitado;
- cada artefato terá rota explícita em `frontend/routes/static_pages.py`, MIME
  correto e a mesma política de revalidação das ilhas atuais. O HTML o referenciará
  com `?v=` para `stamp_asset_versions` aplicar o hash de conteúdo;
- desligar a ilha exige remover do HTML tanto a referência ao bundle JS quanto
  todas as referências ao CSS opcional emitido por ela. Isso restaura o markup
  legado sem estilos residuais da ilha e sem troca de rota, servidor ou deploy;

## Limites

Esta decisão não autoriza Next, SPA, exportação estática da rota, `StaticFiles`,
script `build` no `package.json` da raiz, outro projeto npm ou um segundo pipeline
de build/deploy. Também não autoriza migrar outras rotas, alterar cadastro,
checkout, APIs, service worker, navegação ou os contratos de conteúdo da página.

Devem permanecer: injeção única de GA4/Meta e opt-in do Clarity por `html_file`,
CSP vigente, `safe-area.js`, navegação convencional, CTAs de `/cadastro`, headers
de HTML sem cache, cache-busting de assets e compatibilidade Safari 14/iOS.

## Critérios para ativar o piloto

A implementação futura só pode ser ativada depois de provar:

1. equivalência do conteúdo, semântica, links e estados responsivos do subtree nos
   temas e larguras suportados;
2. página utilizável com bundle ausente, bloqueado, atrasado e incompatível com o
   markup, sempre preservando o legado;
3. propriedade exclusiva do mount, sem React ou scripts clássicos escreverem nos
   nós internos um do outro, e todos os CTAs reescritos por `nav-auth.js` fora dele;
4. rota, status, MIME, cache/hash, CSP, safe areas e Safari 14 corretos;
5. uma única emissão de tracking/Clarity e ausência de regressão no caminho para
   `/cadastro`;
6. build determinístico no `webapp/`, artefatos commitados idênticos aos gerados,
   testes documentais/estruturais, pytest aplicável e teste real de navegador;
7. rollback ensaiado para o markup legado, removendo as referências ao JS e a todo
   CSS opcional da ilha, antes de qualquer exposição.

## Consequências

O piloto reaproveita dependências, build, gate e deploy existentes, mantendo uma
única arquitetura operacional. Em troca, cada ilha exige contrato explícito de
DOM, fallback funcional, rota manual para seus artefatos e validação de integração
com os scripts clássicos antes do mount.
