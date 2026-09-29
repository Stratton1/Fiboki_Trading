/**
 * Per-route first-load JavaScript budgets (gzip), enforced with size-limit.
 *
 * The file lists are read from the production build: each route is
 * prerendered to .next/server/app/<route>.html and its module <script> tags
 * are exactly the JavaScript a browser downloads to show it
 * (scripts/first-load.mjs). Run after `npm run build`:
 *
 *   npm run size
 *
 * Budgets (report E §3.7, plan D-F7): the shell and Overview 180 KiB; every
 * other route 230 KiB for now. The chart workstation (300 KiB) and Research
 * Lab (420 KiB) budgets apply when those screens exist.
 */
import { relative } from "node:path";
import { measureRoutes, WEB_ROOT } from "./scripts/first-load.mjs";

export default measureRoutes().map((row) => ({
  name: `first load ${row.route}`,
  path: row.js.map((file) => relative(WEB_ROOT, file)),
  gzip: true,
  limit: `${row.budgetKb} KiB`,
}));
