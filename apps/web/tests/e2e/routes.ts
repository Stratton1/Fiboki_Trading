import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

/** Every page route in app/, found on disk so a new page cannot escape the sweeps. */
export function allRoutes(): string[] {
  const root = join(__dirname, "..", "..", "app");
  const out: string[] = [];
  const walk = (dir: string, prefix: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full, `${prefix}/${entry}`);
      else if (entry === "page.tsx") out.push(prefix === "" ? "/" : prefix);
    }
  };
  walk(root, "");
  return out.sort();
}
