import { defineConfig } from "vite";

// The page calls our API through this proxy so the browser sees a single origin: no CORS
// setup on the server and no second port to allow. Override the target when the API runs
// elsewhere (for example on another machine in the local network).
export default defineConfig({
  server: {
    proxy: {
      "/v1": process.env.API_URL ?? "http://127.0.0.1:8000",
    },
  },
});
