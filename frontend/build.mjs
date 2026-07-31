import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { build } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const mcpDir = path.resolve(__dirname, "mcp");
const mcpNames = fs.existsSync(mcpDir)
  ? fs
      .readdirSync(mcpDir)
      .filter((name) => fs.existsSync(path.resolve(mcpDir, name, "index.html")))
  : [];

for (const name of mcpNames) {
  console.log(`Building mcp/${name} ...`);
  process.env.RBT_BUILD_TARGET = `mcp:${name}`;
  await build();
}

if (fs.existsSync(path.resolve(__dirname, "web", "index.html"))) {
  console.log("Building web ...");
  process.env.RBT_BUILD_TARGET = "web";
  await build();
}
