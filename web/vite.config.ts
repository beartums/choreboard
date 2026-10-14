import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The built UI lands inside the Python package, so the image serves it from
// the same process as the API. `npm run dev` proxies /api to a local
// `uvicorn --factory choreboard.api:create_app` (with CHOREBOARD_DEV_EMAIL).
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../choreboard/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://localhost:8000" } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
