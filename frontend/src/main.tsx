import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "@/app/App";
import { Providers } from "@/app/providers";
import { initTheme } from "@/lib/theme";
import "@/styles/globals.css";

initTheme();

const root = document.getElementById("root");
if (!root) throw new Error("FrogEye: #root element missing from index.html");

createRoot(root).render(
  <StrictMode>
    <Providers>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </Providers>
  </StrictMode>,
);
