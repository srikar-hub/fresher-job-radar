import { defineConfig } from "vitest/config";
import { cloudflareTest } from "@cloudflare/vitest-plugin";
export default defineConfig({ plugins: [cloudflareTest({ remoteBindings: false, wrangler: { configPath: "./wrangler.jsonc" } })] });
