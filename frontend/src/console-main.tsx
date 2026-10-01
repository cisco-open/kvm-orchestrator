import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import Console from "./pages/Console";
import { applyBrandChrome } from "./branding";

// Keep the "VM Console" title but pick up the branded favicon.
applyBrandChrome();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Console />
  </StrictMode>
);
