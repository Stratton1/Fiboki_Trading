"use client";

import { MoreHorizontal, Settings2 } from "lucide-react";
import { useState } from "react";
import { useInspector } from "@/components/shell/Inspector";
import { Badge } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconButton } from "@/components/ui/IconButton";
import { Kbd, Shortcut } from "@/components/ui/Kbd";
import { Menu, MenuGroup, MenuItem, MenuRadioGroup, MenuSeparator } from "@/components/ui/Menu";
import { Popover } from "@/components/ui/Popover";
import { Segmented } from "@/components/ui/Segmented";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { SplitPane } from "@/components/ui/SplitPane";
import { useToast } from "@/components/ui/Toast";
import { Tooltip } from "@/components/ui/Tooltip";

/**
 * The owned primitives, live, for the Legend's Controls tab. Loaded only when
 * that tab is opened, so the Legend's first load stays inside its budget.
 * Nothing here touches the platform.
 */
export default function Controls() {
  const notify = useToast();
  const inspector = useInspector();
  const [segment, setSegment] = useState<"one" | "two" | "three">("one");
  const [choice, setChoice] = useState<"first" | "second">("first");
  const [menuValue, setMenuValue] = useState<"a" | "b">("a");
  return (
    <div className="stack" data-testid="legend-controls">
      <section className="card">
        <h2 className="card__title">Buttons</h2>
        <div className="row">
          <Button>Secondary</Button>
          <Button variant="primary">Primary</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="warn">Warn</Button>
          <Button variant="ghost">Ghost</Button>
          <Button disabled>Disabled</Button>
          <IconButton label="Settings example">
            <Settings2 size={16} aria-hidden="true" />
          </IconButton>
        </div>
      </section>
      <section className="card">
        <h2 className="card__title">Labels, keys and states</h2>
        <div className="row">
          <Badge>tag</Badge>
          <Kbd>Esc</Kbd>
          <Shortcut keys={["⌘", "K"]} label="Command K" />
          <Tooltip label="A tooltip opens on hover, focus and tap">
            <Button size="sm">Tooltip</Button>
          </Tooltip>
          <Popover title="Popover" trigger={<Button size="sm">Popover</Button>}>
            <p>Reachable by keyboard and touch.</p>
          </Popover>
          <Button size="sm" onClick={() => notify("A toast confirms a display change.")}>
            Toast
          </Button>
          <Button
            size="sm"
            data-testid="legend-open-inspector"
            onClick={() =>
              inspector.open({ title: "Inspector", body: <p>The inspector sheet.</p> })
            }
          >
            Inspector
          </Button>
        </div>
      </section>
      <section className="card">
        <h2 className="card__title">Choosers</h2>
        <div className="row items-end gap-6">
          <Segmented
            label="Segmented"
            value={segment}
            onValueChange={setSegment}
            options={[
              { value: "one", label: "One" },
              { value: "two", label: "Two" },
              { value: "three", label: "Three" },
            ]}
          />
          <Select
            label="Select"
            value={choice}
            onValueChange={setChoice}
            options={[
              { value: "first", label: "First option" },
              { value: "second", label: "Second option" },
            ]}
          />
          <Menu
            trigger={
              <Button size="sm" aria-label="Menu example">
                <MoreHorizontal size={14} aria-hidden="true" />
                Menu
              </Button>
            }
          >
            <MenuItem onClick={() => notify("Menu item chosen.")}>An action</MenuItem>
            <MenuSeparator />
            <MenuGroup label="A choice">
              <MenuRadioGroup
                value={menuValue}
                onValueChange={setMenuValue}
                options={[
                  { value: "a", label: "Option A" },
                  { value: "b", label: "Option B" },
                ]}
              />
            </MenuGroup>
          </Menu>
        </div>
      </section>
      <section className="card">
        <h2 className="card__title">Loading and empty</h2>
        <Skeleton className="w-[60%]" />
        <Skeleton className="w-[40%]" />
        <EmptyState title="Nothing here yet" testId="legend-empty">
          A successful, empty answer. Distinct from loading and from failure.
        </EmptyState>
      </section>
      <section className="card">
        <h2 className="card__title">Split</h2>
        <SplitPane
          id="legend-split"
          firstLabel="Left pane"
          secondLabel="Right pane"
          first={<p className="muted">Drag the separator, or focus it and use the arrow keys.</p>}
          second={<p className="muted">The layout is remembered in this browser.</p>}
        />
      </section>
    </div>
  );
}
