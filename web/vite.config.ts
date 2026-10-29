import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, forward API calls to the FastAPI service so the browser sees one origin.
const api = process.env.URJA_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/v1": api, "/health": api } },
});
