import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base './' keeps asset paths relative -> works on GitHub Pages AND Flask /ui/
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: { port: 5173 },
});
