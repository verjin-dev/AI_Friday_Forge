import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      // The dashboard reads live fleet, route and constraint data from the
      // LogiPilot backend. Proxying keeps the browser on one origin.
      "/api": {
        target: "http://127.0.0.1:8010",
        changeOrigin: true,
      },
    },
  },
});
