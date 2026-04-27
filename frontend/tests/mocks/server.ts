/**
 * MSW Node-side 서버 (vitest jsdom 환경에서 fetch 가로채기).
 *
 * 라이프사이클은 tests/setup.ts 에서 관리한다.
 *
 * 참조: BUILD_ORDER.md 작업 0-3
 */
import { setupServer } from "msw/node";
import { handlers } from "./handlers";

export const server = setupServer(...handlers);
