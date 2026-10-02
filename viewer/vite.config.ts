/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Asset URLs relative to the document, not to the domain root.
  //
  // The default is "/assets/...", which resolves against the origin. That is correct
  // only where the site is served from "/", and the published bundle is not: a
  // GitHub Pages project site lives at "/<repo>/", so the browser would ask for
  // /assets/index-*.js, find nothing at the domain root, and render a blank page --
  // with no error the page can report, because the page never loaded. The files are
  // all present and correctly named on disk, so nothing else in the project would
  // notice. "./" makes the emitted references resolve against the page's own URL, so
  // the same directory works at the root, under a repo path, or from a file:// URL.
  //
  // Guarded by tests/test_publication.py, which serves `dist` from a subpath over
  // HTTP and requests every reference the way a browser would.
  base: "./",
  test: {
    // Component tests need a DOM. The pure modules (timeline, playback) are
    // environment-agnostic, so they are unaffected by this.
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
