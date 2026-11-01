import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.REALROOM_API ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": api,
      "/runs": api,
      "/catalog": api,
    },
  },
  build: { target: "es2022", sourcemap: false, chunkSizeWarningLimit: 900 },
});
