// `expo-router/testing-library` registra `toHavePathname` via `expect.extend`
// em tempo de execução (`build/testing-library/expect.js`), mas não publica
// tipo para ele — `expect.d.ts` do próprio pacote é um arquivo vazio. Sem
// esta ampliação, o teste passa no Jest e falha só no `tsc`.
declare global {
  namespace jest {
    interface Matchers<R> {
      toHavePathname(expected: string): R;
    }
  }
}

export {};
