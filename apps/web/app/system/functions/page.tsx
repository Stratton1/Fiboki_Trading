"use client";

import Link from "next/link";
import { Card, PageHead } from "@/components/primitives";
import { COMMAND_CODES, INSTRUMENT_FUNCTIONS, SCREEN_FUNCTIONS } from "@/components/shell/functions";
import { CHART_SHORTCUTS, SHORTCUTS } from "@/components/shell/ShortcutSheet";
import { Kbd } from "@/components/ui/Kbd";

/**
 * SYSTEM · Functions (FUNC, HELP): every command-line function and key,
 * generated from the same registry the command line, the palette and the
 * shortcut sheet use (components/shell/functions.ts), so it cannot drift.
 */
export default function FunctionsPage() {
  return (
    <>
      <PageHead
        title="Functions"
        intro="Every command-line code, the instrument functions (SYMBOL FUNCTION), and every key. Type a code into the command line (/ focuses it) and press Enter."
      />
      <div className="functions-grid">
        <Card title="Screens">
          <table className="dense" data-testid="functions-screens">
            <thead>
              <tr>
                <th scope="col">Code</th>
                <th scope="col">Screen</th>
                <th scope="col">Section</th>
                <th scope="col">Keys</th>
                <th scope="col">What it shows</th>
              </tr>
            </thead>
            <tbody>
              {SCREEN_FUNCTIONS.map((fn) => (
                <tr key={fn.code} data-testid="function-row" data-code={fn.code}>
                  <th scope="row" className="mono">
                    <Link href={fn.href}>{fn.code}</Link>
                  </th>
                  <td>{fn.label}</td>
                  <td className="muted">{fn.section}</td>
                  <td>
                    {fn.keys.map((key) => (
                      <Kbd key={key}>{key}</Kbd>
                    ))}
                  </td>
                  <td className="wrap">{fn.description}</td>
                </tr>
              ))}
              {COMMAND_CODES.map((fn) => (
                <tr key={fn.code} data-testid="function-row" data-code={fn.code}>
                  <th scope="row" className="mono">
                    {fn.code}
                  </th>
                  <td>{fn.label}</td>
                  <td className="muted">command</td>
                  <td>
                    {fn.keys.map((key) => (
                      <Kbd key={key}>{key}</Kbd>
                    ))}
                  </td>
                  <td className="wrap">{fn.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <div className="stack">
          <Card title="Instrument functions: SYMBOL FUNCTION">
            <table className="dense" data-testid="functions-instrument">
              <thead>
                <tr>
                  <th scope="col">Type</th>
                  <th scope="col">Opens</th>
                </tr>
              </thead>
              <tbody>
                {INSTRUMENT_FUNCTIONS.map((fn) => (
                  <tr key={fn.code} data-testid="instrument-function-row" data-code={fn.code}>
                    <th scope="row" className="mono">
                      <Link href={fn.href("EURUSD")}>EURUSD {fn.code}</Link>
                    </th>
                    <td className="wrap">
                      {fn.label}: {fn.description}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted">
              The symbol must be one the platform lists. Text that is neither a code nor a listed symbol is searched, and
              opens only an exact or chosen match.
            </p>
          </Card>
          <Card title="Keys">
            <table className="dense" data-testid="functions-keys">
              <tbody>
                {[...SHORTCUTS, ...CHART_SHORTCUTS].map((s) => (
                  <tr key={s.label}>
                    <th scope="row">
                      {s.keys.map((k) => (
                        <Kbd key={k}>{k}</Kbd>
                      ))}
                    </th>
                    <td className="wrap">{s.what}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      </div>
    </>
  );
}
