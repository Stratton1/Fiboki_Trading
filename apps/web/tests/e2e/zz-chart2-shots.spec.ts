import { test } from "@playwright/test";
import { mockChart } from "./fixtures";

const OUT = "/tmp/claude-0/-home-claude/9877530d-b85e-5cd6-8708-2bf1b965546f/scratchpad/chart2";

test("shots", async ({ page }, info) => {
  if (info.project.name === "desktop") await page.setViewportSize({ width: 1440, height: 900 });
  await mockChart(page);
  await page.goto("/markets/EURUSD?run=bt_new");
  await page.getByTestId("price-chart").locator("canvas").first().waitFor();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/${info.project.name}-chart.png`, fullPage: false });
  const rows = await page.evaluate(() => {
    const head = document.querySelector('[data-testid="chart-head"]') as HTMLElement | null;
    const table = document.querySelector(".cw-trades__table") as HTMLElement | null;
    const scroll = document.querySelector(".cw-trades__scroll") as HTMLElement | null;
    return {
      head: head ? { h: head.getBoundingClientRect().height, sw: head.scrollWidth, cw: head.clientWidth } : null,
      table: table ? table.getBoundingClientRect().width : null,
      scroll: scroll ? { cw: scroll.clientWidth, sw: scroll.scrollWidth } : null,
      chartTop: document.querySelector('[data-testid="price-chart"]')?.getBoundingClientRect().top,
    };
  });
  console.log(info.project.name, JSON.stringify(rows));
});
