/**
 * Test setup.
 *
 * Two things, both deliberate:
 *
 * - The jest-dom matchers, so component tests can assert on what a reader sees
 *   (`toBeInTheDocument`, `toHaveAccessibleName`) rather than on markup shape.
 * - An explicit `cleanup` after each test. Testing Library registers this
 *   automatically only when vitest globals are enabled; this project does not
 *   enable them, so without this every render would stay in `document.body` and
 *   later queries would find several matches for the same text.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(cleanup);

// `fetch` is stubbed per test to serve the exported bundle from memory. Leaving a
// stub in place would let one test's fake bundle answer another's request.
afterEach(() => {
  vi.unstubAllGlobals();
});
