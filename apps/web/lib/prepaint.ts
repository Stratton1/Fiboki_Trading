/**
 * Storage keys and the pre-paint script for display preferences.
 *
 * Deliberately NOT a client module: app/layout.tsx (a server component)
 * inlines {@link PREPAINT_SCRIPT} into <head>, and a "use client" module would
 * hand it a client reference instead of the string.
 */

export const PREF_KEY = {
  theme: "fiboki.theme",
  density: "fiboki.density",
  pnl: "fiboki.pnl",
  rail: "fiboki.rail",
} as const;

/** Below this width the comfortable density is chosen automatically. */
export const COMFORTABLE_BELOW_PX = 1024;

/**
 * Runs inline in <head> before the body paints, so a light-theme operator
 * never sees a dark flash. Every storage read is inside the try; the catch
 * still honours the OS theme and the viewport width, so a blocked
 * localStorage changes nothing visible.
 */
export const PREPAINT_SCRIPT = `(function(){var d=document.documentElement;function sys(){try{return matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'}catch(e){return'dark'}}var t='system',n=null,p=null;try{var s=window.localStorage;t=s.getItem('${PREF_KEY.theme}')||'system';n=s.getItem('${PREF_KEY.density}');p=s.getItem('${PREF_KEY.pnl}')}catch(e){}if(t!=='light'&&t!=='dark')t='system';d.setAttribute('data-theme-pref',t);d.setAttribute('data-theme',t==='system'?sys():t);if(n!=='compact'&&n!=='regular'&&n!=='comfortable')n=null;d.setAttribute('data-density-pref',n||'auto');d.setAttribute('data-density',n||(window.innerWidth<${COMFORTABLE_BELOW_PX}?'comfortable':'regular'));if(p==='cvd')d.setAttribute('data-pnl','cvd')})();`;
