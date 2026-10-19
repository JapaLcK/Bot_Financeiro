# Design system do PigBank — de onde vem e como usar

Cópia versionada do design system publicado em
<https://claude.ai/artifact/9NfvFxRU6MiWLKT3GhPDSr> (privado; abre com a conta do dono).
Leia `README.md` desta pasta antes de mexer em frontend (app, `/painel`, site ou e-mail):
ele diz que token, estilo de tipo e componente usar, e o que não fazer.

## Quem manda quando os dois discordam

Esta pasta é **referência**, não fonte. A fonte de cada valor continua no código:

- cores, espaço, raio e tipo do produto: `app/src/ui/tokens.ts`;
- paleta e escala de marketing: `frontend/brand.css`;
- dashboard escuro: `DESIGN.md` na raiz;
- componentes: `app/src/ui/componentes/` (os daqui são versões web deles).

Se o código mudar, o código vale: atualize esta pasta **e** o artifact no mesmo PR, para
que os dois não divirjam (CLAUDE.md §0.7). Os contrastes citados nas notas de token
foram medidos em 2026-10-18 sobre os valores de então; remeça se a paleta mudar.

## O que tem aqui

| caminho | o quê |
|---|---|
| `README.md` | guia de marca: conteúdo, cor, tipo, espaço, movimento, ícones, quando usar cada componente |
| `tokens.json` | todos os tokens, com nota de uso em cada um (temas `light` e `dark`) |
| `components/<Nome>/README.md` | quando usar o componente e o que quem usa fornece |
| `components/<Nome>/preview.html` | exemplo vivo; roda dentro da página do artifact, não sozinho |
| `components/bundle.js`, `bundle.css`, `index.d.ts` | as versões web dos componentes (`window.PigBank`) e seus tipos |
| `components/Cover/preview.html` | a capa do artifact |
| `assets/<Grupo>/README.md` | regras de uso de logos, Piggy, agentes e stickers |
| `design-system.json` | índice do artifact (ids dos assets enviados) |

As fontes não foram copiadas: são as de `frontend/fonts/`. Os assets ficam no artifact e
no repositório em `frontend/brand/` e `app/assets/brand/` (os nomes do artifact têm
prefixo, por exemplo `sticker-hello.webp` é `frontend/brand/stickers/hello.webp`).
