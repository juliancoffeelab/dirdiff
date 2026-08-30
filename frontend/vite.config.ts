import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

export default defineConfig(({ command }) => {
  const backendOrigin = process.env.VITE_DIRDIFF_BACKEND_ORIGIN;
  if (command === "serve" && !backendOrigin) {
    throw new Error(
      "VITE_DIRDIFF_BACKEND_ORIGIN is required. Start Vite through `dirdiff` so the frontend is paired with its backend.",
    );
  }
  const outputPath = process.env.DIRDIFF_FRONTEND_OUT_DIR;
  if (command === "build" && !outputPath) {
    throw new Error(
      "DIRDIFF_FRONTEND_OUT_DIR is required for a production frontend build.",
    );
  }

  return {
    plugins: [
      {
        name: "full-reload-javascript",
        hotUpdate(options) {
          if (
            this.environment.name === "client" &&
            options.modules.some((module) => module.type === "js")
          ) {
            // Solid contexts cannot span independently replaced module graphs.
            // Stop the JavaScript update and reload the complete application.
            this.environment.hot.send({
              type: "full-reload",
              path: "*",
              triggeredBy: options.file,
            });
            return [];
          }
        },
      },
      solid(),
    ],
    build: {
      outDir: outputPath,
      emptyOutDir: true,
    },
    server: {
      proxy: {
        "/api": backendOrigin,
      },
    },
  };
});
