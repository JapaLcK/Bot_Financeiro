Botão em pílula de 44px (`size="M"`) ou 52px (`"L"`), reescrito de `app/src/ui/componentes/Button.tsx`.

**Quem fornece:** `children` (o rótulo, em sentence case, verbo no imperativo: "Pagar fatura"), `onClick`, e opcionalmente `icon` (nome de um `Icon`, como `Bank`) e `loading`.

- `primary`: `action` com `on-action`. **Um por tela.** É a única superfície rosa de um formulário.
- `secondary`: `surface-control` com `ink`. Ação de apoio ao lado do primário.
- `ghost`: sem fundo, texto `brand-ink`. Ação em linha ("Ver 90 dias").
- `link`: só texto, sem padding ("Esqueci a senha").
- `danger`: `danger` com `on-danger`. Só para ação destrutiva.

Não troque o rótulo do `primary` para branco: sobre o rosa dá 3,5:1; `on-action` dá 5,1:1. Com `loading`, a largura não muda. Desativado fica a 50% de opacidade.
