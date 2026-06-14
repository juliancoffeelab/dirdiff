import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

export default defineConfig(() => {
  const backendOrigin = process.env.VITE_DIRDIFF_BACKEND_ORIGIN;
  if (!backendOrigin) {
    throw new Error(
      "VITE_DIRDIFF_BACKEND_ORIGIN is required. Start Vite through `dirdiff` so the frontend is paired with its backend.",
    );
  }

  return {
    plugins: [solid()],
    build: {
      outDir: "../src/dirdiff/frontend",
      emptyOutDir: true,
    },
    server: {
      proxy: {
        "/api": backendOrigin,
      },
    },
  };
});
