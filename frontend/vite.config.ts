/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// tests run in UTC (inherited by vitest's workers), so any date or time that slips
// back to the machine's zone instead of the app's shows the wrong hour or day.
if (process.env.VITEST) process.env.TZ = "UTC";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
  build: {
    rolldownOptions: {
      output: {
        // Framework code changes rarely; keep it in long-lived chunks apart from app code.
        codeSplitting: {
          groups: [
            { name: "react", test: /node_modules[\\/](react|react-dom|react-router|scheduler)[\\/]/ },
            { name: "ui", test: /node_modules[\\/](radix-ui|@radix-ui|@floating-ui|sonner|@tanstack)[\\/]/ },
            // The chart kit's pinned d3 modules (and their own dependencies), for the deep dives.
            { name: "d3", test: /node_modules[\\/](d3-[^\\/]+|internmap)[\\/]/ },
          ],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Only the token sheet is loaded, so tests can assert the design scale (e.g. z-index order).
    css: { include: [/globals\.css/] },
    restoreMocks: true,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
