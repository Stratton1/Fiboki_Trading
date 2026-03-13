/**
 * Market session definitions and utilities.
 * Sessions are defined in UTC hours.
 */

export interface MarketSession {
  name: string;
  startHour: number;
  endHour: number;
  color: string;
}

export const MARKET_SESSIONS: MarketSession[] = [
  { name: "Asian", startHour: 0, endHour: 8, color: "rgba(76, 175, 80, 0.06)" },
  { name: "London", startHour: 8, endHour: 12, color: "rgba(33, 150, 243, 0.06)" },
  { name: "London-NY", startHour: 12, endHour: 16, color: "rgba(255, 152, 0, 0.06)" },
  { name: "New York", startHour: 16, endHour: 21, color: "rgba(233, 30, 99, 0.06)" },
  { name: "Off-Hours", startHour: 21, endHour: 24, color: "rgba(158, 158, 158, 0.04)" },
];

/**
 * Get the session name for a given UTC timestamp (ms).
 */
export function getSessionForTimestamp(timestampMs: number): MarketSession {
  const date = new Date(timestampMs);
  const hour = date.getUTCHours();
  for (const session of MARKET_SESSIONS) {
    if (hour >= session.startHour && hour < session.endHour) {
      return session;
    }
  }
  return MARKET_SESSIONS[4]; // Off-Hours
}
