import { fileURLToPath } from "node:url";
import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  resolve: { alias: { "~": fileURLToPath(new URL("./app", import.meta.url)) } },
  server: { host: "127.0.0.1", proxy: { "/api": "http://127.0.0.1:8765" } },
});
