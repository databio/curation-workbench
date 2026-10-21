/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies same-origin paths to a running backend so hot
// reloading behaves like the bundle `cwb serve` serves. Default: the local
// daemon (`cwb serve`, port 8781). For the hosted API set
// VITE_DEV_BACKEND=http://127.0.0.1:8134.
const devBackend = process.env.VITE_DEV_BACKEND ?? "http://127.0.0.1:8781";

export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    port: 5190,
    proxy: {
      "/api": { target: devBackend, changeOrigin: true },
      "/auth": { target: devBackend, changeOrigin: true },
      "/health": { target: devBackend, changeOrigin: true },
    },
  },
  build: {
    target: "esnext",
    // Shipped as package data: `cwb serve` serves it with no Node at run time.
    outDir: "../src/curation_workbench/web",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
