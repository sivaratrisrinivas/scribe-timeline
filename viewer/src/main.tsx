/**
 * Browser entry point.
 *
 * Mounts the app and nothing else. Everything worth reading lives in `App`, which
 * loads the exported bundle; this file exists so the module graph has one root.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App.js";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("no #root element to mount into; index.html is missing or altered");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
