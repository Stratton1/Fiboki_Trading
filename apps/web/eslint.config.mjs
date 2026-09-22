import next from "eslint-config-next";

/**
 * The `no-restricted-syntax` block below is the lint half of the
 * "absence is not zero" rule. `data?.x ?? 0` is how a total backend outage
 * rendered in V1 as a flat, idle, healthy fleet: a £0.00 balance and 0/0 bots
 * running, beside a hardcoded "Connected" badge.
 *
 * The test half lives in tests/e2e/no-zero-coalesce.spec.ts, which greps the
 * source tree, so the ban survives someone disabling the rule inline.
 */
const config = [
  ...next,
  {
    ignores: [".next/**", "node_modules/**", "out/**"],
  },
  {
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "LogicalExpression[operator='??'] > Literal.right[value=0]",
          message:
            "Do not coalesce a missing value to 0. A number the API could not " +
            "supply must render as an explicit 'no data' state, not as zero. " +
            "Use <FigureValue> or an explicit null branch.",
        },
        {
          selector:
            "LogicalExpression[operator='||'] > Literal.right[value=0]",
          message:
            "Do not default a number to 0 with ||. Render the missing state " +
            "explicitly instead.",
        },
        {
          selector:
            "LogicalExpression[operator='??'] > Literal.right[value='0.00']",
          message: "Do not coalesce a missing money value to a zero string.",
        },
      ],
    },
  },
];

export default config;
