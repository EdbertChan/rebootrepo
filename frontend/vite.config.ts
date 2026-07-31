import fs from "fs";
import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

function redirectFrontendDirTrailingSlash(root: string): Plugin {
  const prefix = "/__/frontend/";
  return {
    name: "reboot-frontend-dir-trailing-slash",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url ?? "";
        const queryAt = url.indexOf("?");
        const pathname = queryAt === -1 ? url : url.slice(0, queryAt);
        if (pathname.startsWith(prefix) && !pathname.endsWith("/")) {
          const subpath = pathname.slice(prefix.length);
          if (fs.existsSync(path.join(root, subpath, "index.html"))) {
            const query = queryAt === -1 ? "" : url.slice(queryAt);
            res.statusCode = 302;
            res.setHeader("Location", `${pathname}/${query}`);
            res.end();
            return;
          }
        }
        next();
      });
    },
  };
}

function serveWebAppAtRoot(root: string): Plugin {
  const webIndex = path.resolve(root, "web", "index.html");
  const target = "/__/frontend/web/";
  const homes = new Set(["/", "/__/frontend", "/__/frontend/"]);
  return {
    name: "reboot-serve-web-app-at-root",
    configureServer(server) {
      if (!fs.existsSync(webIndex)) return;
      server.middlewares.use((req, res, next) => {
        const url = req.url ?? "";
        const queryAt = url.indexOf("?");
        const pathname = queryAt === -1 ? url : url.slice(0, queryAt);
        if (homes.has(pathname)) {
          const query = queryAt === -1 ? "" : url.slice(queryAt);
          res.statusCode = 302;
          res.setHeader("Location", target + query);
          res.end();
          return;
        }
        next();
      });
      const printUrls = server.printUrls.bind(server);
      server.printUrls = () => {
        const urls = server.resolvedUrls;
        if (urls) {
          const toRoot = (u: string) => u.replace(/\/__\/frontend\/$/, "/");
          urls.local = urls.local.map(toRoot);
          urls.network = urls.network.map(toRoot);
        }
        printUrls();
      };
    },
  };
}

const mcpDir = path.resolve(__dirname, "mcp");
const mcpNames: string[] = fs.existsSync(mcpDir)
  ? fs
      .readdirSync(mcpDir)
      .filter((name) => fs.existsSync(path.resolve(mcpDir, name, "index.html")))
  : [];

const resolve = {
  alias: {
    "@api": path.resolve(__dirname, "./api"),
  },
  dedupe: ["react", "react-dom", "zod"],
};

export default defineConfig(({ command }) => {
  if (command === "serve") {
    const port = parseInt(process.env.RBT_VITE_PORT || "4444", 10);
    return {
      plugins: [
        react(),
        redirectFrontendDirTrailingSlash(__dirname),
        serveWebAppAtRoot(__dirname),
      ],
      root: ".",
      envDir: path.resolve(__dirname, "web"),
      resolve,
      base: "/__/frontend/",
      server: {
        port,
        strictPort: true,
        host: true,
        allowedHosts: true,
      },
    };
  }

  const target = process.env.RBT_BUILD_TARGET ?? "";

  if (target === "web") {
    return {
      plugins: [react()],
      root: path.resolve(__dirname, "web"),
      base: "/__/frontend/web/",
      build: {
        outDir: path.resolve(__dirname, "dist/web"),
        emptyOutDir: true,
      },
      resolve,
    };
  }

  const name = target.startsWith("mcp:") ? target.slice("mcp:".length) : "";
  if (!mcpNames.includes(name)) {
    const valid = mcpNames.map((n) => `mcp:${n}`).join(", ");
    throw new Error(
      `Unknown build target: ${target || "(unset)"}. Set ` +
        `RBT_BUILD_TARGET=web or one of: ${valid}.`,
    );
  }

  return {
    plugins: [react(), viteSingleFile()],
    root: path.resolve(__dirname, "mcp", name),
    base: "/__/frontend/",
    envDir: path.resolve(__dirname, "web"),
    build: {
      outDir: path.resolve(__dirname, "dist/mcp", name),
      emptyOutDir: true,
      assetsInlineLimit: 100000000,
      cssCodeSplit: false,
      rollupOptions: {
        output: {
          inlineDynamicImports: true,
        },
      },
    },
    resolve,
  };
});
