/**
 * Base UI popups load on first use, not on first paint.
 *
 * The owned primitives render their own trigger immediately (so the page is
 * operable and every trigger has its accessible name and state from the first
 * frame) and import the Base UI layer that draws the popup only when it is
 * first opened. The shell prefetches the layers once the page is idle, so in
 * practice the first open is instant; the first-load budget (180 KB for the
 * shell and Overview) is kept honest either way. See report E §3.7.
 */
export const loadPopoverLayer = () => import("./PopoverLayer");
export const loadTooltipLayer = () => import("./TooltipLayer");
export const loadConfirmDialogLayer = () => import("./ConfirmDialogLayer");
export const loadSheetLayer = () => import("./SheetLayer");

export function prefetchLayers() {
  void loadPopoverLayer();
  void loadTooltipLayer();
  void loadConfirmDialogLayer();
  void loadSheetLayer();
}
