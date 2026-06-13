import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

export default defineConfig(() => {
  const backendOrigin =
    process.env.VITE_DIRDIFF_BACKEND_ORIGIN ?? "http://127.0.0.1:5052";

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
