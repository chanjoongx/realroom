import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.REALROOM_API ?? "http://127.0.0.1:8787";

/** Preloads the two Instrument Serif files the hero heading uses, by their hashed names in the build,
 *  so the heading paints in its own font instead of shifting the page when it arrives. */
function preloadDisplayFonts(): Plugin {
  return {
    name: "preload-display-fonts",
    transformIndexHtml: {
      order: "post",
      handler(_html, ctx) {
        return Object.keys(ctx.bundle ?? {})
          .filter((f) => /instrument-serif-latin-400-(normal|italic)-[\w-]+\.woff2$/.test(f))
          .sort()
          .map((f) => ({
            tag: "link",
            attrs: { rel: "preload", as: "font", type: "font/woff2", href: `/${f}`, crossorigin: "" },
            injectTo: "head" as const,
          }));
      },
    },
  };
}

export default defineConfig({
  plugins: [react(), preloadDisplayFonts()],
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
