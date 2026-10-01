import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    target: ["es2022", "chrome89", "firefox89", "safari15"],
    rollupOptions: {
      input: {
        main:    path.resolve(__dirname, "index.html"),
        console: path.resolve(__dirname, "console.html"),
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/console-ws": {
        target: "ws://localhost:8765",
        ws: true,
      },
    },
  },
});
