/** Testes do app. `jest-expo` entende os módulos nativos e o transform do RN. */
module.exports = {
  preset: "jest-expo",
  setupFilesAfterEnv: ["<rootDir>/jest.setup.js"],
  testMatch: ["<rootDir>/__tests__/**/*.test.ts?(x)"],
  moduleNameMapper: { "^@/(.*)$": "<rootDir>/src/$1" },
};
