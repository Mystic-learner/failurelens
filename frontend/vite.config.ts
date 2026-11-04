import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev proxy: /api/* -> backend (prefix stripped), /ws/* -> backend WebSocket.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/ws": { target: "http://localhost:8000", ws: true },
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});