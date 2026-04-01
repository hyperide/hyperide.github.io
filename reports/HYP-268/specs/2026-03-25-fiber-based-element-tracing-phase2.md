# Fiber-Based Element Tracing — Phase 2: Switch to Fiber + Remove data-uniq-id

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all `data-uniq-id` DOM identification with fiber-based tracing (Phase 1 infrastructure), switch all mutation routes from UUID to nodeRef, and remove the entire UUID injection pipeline.

**Architecture:** Phase 2 is a direct switch — no dual mode, no feature flag. The click handler extracts `SourceLocation` from React fiber via `ReactAdapter`, resolves it to `nodeRef` via `NodeMapService` (server round-trip for SaaS, local for extension). All mutation routes accept `nodeRef` instead of UUID. CanvasEngine's `selectedIds` become opaque identifiers that hold nodeRefs instead of UUIDs — the interface is unchanged (`string[]`), only the values change. Overlay rendering uses `ElementTracer.findDOMElement()` for reverse mapping (DOM lookup by source location via fiber tree walk).

**Tech Stack:** TypeScript, React Fiber internals (`__reactFiber$`, `_debugSource`), Bun WebSocket, `bun:test`, Babel AST

**Spec:** `docs/specs/2026-03-24-fiber-based-element-tracing.md`
**Phase 1 Plan:** `docs/plans/2026-03-25-fiber-based-element-tracing-phase1.md`
**Phase 1 Branch:** `worktree-HYP-268-react-devtools-tracing` (infrastructure implemented + hardened: safeParse, locIndex normalization, Set<handler>)

---

## Scope Check

This plan covers three tightly coupled sub-phases that cannot ship independently:

- **2a** — Wire fiber into client click/hover/overlay pipeline
- **2b** — Switch server mutation routes from UUID to nodeRef
- **2c** — Remove data-uniq-id injection pipeline

They are ordered as a pipeline: 2a changes what the client sends, 2b changes what the server accepts, 2c cleans up dead code. Within each sub-phase, tasks are independent enough for parallel agent execution.

**Prerequisites (must land before Phase 2):**
- CF-1: 3-tier stability cascade in `stability.ts` (spec Critical Findings)
- CF-3: `FiberSourceIndex` reverse index for O(1) `findDOMElement()` (spec CF-3)
- CF-5: `getItemIndex` WeakMap cache (spec CF-5)

**NOT in scope (separate tickets):**
- React 19 / Next.js source location resolution (spec "Next.js / React 19 Support Constraints" — separate ticket after Phase 2 is stable; requires source map VLQ decoding or `_debugOwner.stack` parsing; `data-uniq-id` is NOT a fallback option)
- `data-comment-id` persistent comment anchoring (spec Phase 2e — separate ticket)
- Vue/Svelte/Solid adapters
- E2E tests (separate task after Phase 2 is stable)

---

## Key Architectural Decisions

### D1: Selection IDs remain `string[]` — values change from UUID to nodeRef

CanvasEngine methods (`select`, `selectWithItemIndex`, `addToSelection`, etc.) accept `string` IDs. Currently those are UUIDs like `"a1b2c3d4-..."`. After Phase 2, they become nodeRefs like `"src/App.tsx:7"` (opaque `filePath:nodeIndex` — the number is a sequential AST traversal counter, NOT a line number; actual source location is in `NodeMapEntry.loc`). No interface changes needed — the engine doesn't care about the format.

**Implication:** Every call site that passes a UUID to the engine must pass a nodeRef instead. This is the primary wiring change in Phase 2a.

### D2: Click resolution is async — show optimistic selection

Current flow: click → `dataset.uniqId` → sync select. UUID is on the DOM element.

New flow: click → fiber → `SourceLocation` (sync) → send `resolve-element` to server → get `nodeRef` back (async, <10ms WS round-trip).

**Strategy:** `ElementTracer.resolveClick()` returns `ClickResult` (source + itemIndex) synchronously. The client can show an immediate visual feedback (hover-style highlight) while waiting for the server's `resolve-element-response` with the confirmed `nodeRef`. Once confirmed, call `engine.selectWithItemIndex(nodeRef, itemIndex)`.

For the extension, resolution is local (no WS), so it's effectively synchronous.

### D3: Overlay rendering switches to `findDOMElement()`

Current: `doc.querySelectorAll('[data-uniq-id="..."]')`.
New: `elementTracer.findDOMElement(source, itemIndex)` — walks fiber tree, returns `HTMLElement`.

`computeOverlayRects()` needs access to `ElementTracer` instance. Since it runs in a RAF loop on the SaaS side, `ElementTracer` reference is passed through `OverlayState` or as a renderer dependency.

### D4: Mutation routes use `findElementByNodeRef()` instead of `findElementByUuid()`

New helper `findElementByPosition()` in `lib/ast/position-finder.ts`:
- Takes `SourceLocation` (from `NodeMapEntry.loc`)
- Traverses AST, finds the JSXElement whose opening tag `loc.start` **exactly** matches (not "contains")
- Returns same `{ element, path }` as `findElementByUuid()`
- **Note:** `lib/ast/traverser.ts` already has `findElementAtPosition()` which finds the innermost element
  *containing* a position — different semantics. Both are needed: the existing one for cursor-based lookup
  (user cursor is *inside* an element), the new one for nodeRef-based lookup (server knows the *exact* start).

Routes receive `nodeRef` → `NodeMapService.resolveNodeRef()` → `NodeMapEntry.loc` → `findElementByPosition()`.

### D5: Post-mutation re-parse + broadcast

After every `writeAST()`, the mutation route calls `nodeMapService.reparseAndUpdate()` and `broadcastToProject()` to push the updated NodeMap to all clients. This replaces the implicit "UUID survives in file" persistence.

### D6: Extension uses local `NodeMapService` — no WS

Extension's `AstService` already has Babel. `NodeMapService` is instantiated locally. `PostMessageTracingTransport` bridges iframe ↔ extension host via PanelRouter + StateHub. Resolution is local: `nodeMapService.resolveSourceLocation()` → immediate response.

### D7: Dependency inversion for `shared/` ← `client/` boundary

`shared/canvas-interaction/click-handler.ts` cannot import from `client/` (the extension's tsconfig
includes `shared/` and `lib/` but NOT `client/`). Instead, define a `TracingResolver` interface in
`shared/canvas-interaction/types.ts` that click-handler depends on:

```typescript
/** Interface for fiber-based element resolution — implemented by ElementTracer (client). */
export interface TracingResolver {
  getSourceLocation(element: HTMLElement): SourceLocation | null;
  getItemIndex(element: HTMLElement): number;
  resolveClickLocal(element: HTMLElement): LocalResolveResult | null;
  findDOMElement(source: SourceLocation, itemIndex: number): HTMLElement | null;
}
```

`ElementTracer` implements this interface. Callers inject it into `attachClickHandler()`,
`createOverlayRenderer()`, etc. No `client/` imports in `shared/`.

### D8: Board mode instance scoping with fibers

`data-canvas-instance-id` stays on DOM elements (it's about canvas layout/routing, not element identity).
For fiber-based queries scoped to an instance, the approach:
1. Find the instance container: `doc.querySelector('[data-canvas-instance-id="${id}"]')`
2. Get its fiber root: `getFiberFromDOM(instanceContainer)`
3. Walk only that fiber subtree (pass `instanceRoot` to `findDOMElement()`)

This requires `FrameworkAdapter.findDOMElement()` to accept an optional `rootElement` parameter
to restrict the walk scope. Add this in Task 2.

### D9: Undo/Redo with nodeRef persistence

CanvasEngine stores `selectedIds` in undo stack. After undo, the restored file triggers re-parse →
`NodeMapUpdate` with `refMapping`. The client already handles `refMapping` in `ElementTracer._handleMessage()`.

**Strategy:** When `NodeMapUpdate` arrives with `refMapping`, check if any current `selectedIds` are in
`refMapping` keys. If so, remap them: `engine.select(refMapping[oldNodeRef])`. This logic belongs in
the `useElementTracer` hook (Task 8) — subscribe to `node-map-update` messages and update selection.

### D10: Comment anchoring — `data-comment-id` (Phase 2e in spec)

`server/database/schema/comments.ts` stores `elementId` (`text('element_id')`) which holds `data-uniq-id`
values. Existing comments anchored to specific elements will lose their anchoring when data-uniq-id is removed.

**Strategy:** Switch comment anchoring to `data-comment-id` — a persistent, stable attribute
injected by the server specifically for comment anchoring. Unlike `data-uniq-id`, this attribute
is only added to elements that have comments (not to every element). See spec Phase 2e for details.

**NOT in Phase 2 scope** — comment anchoring migration is deferred to a separate ticket.
Phase 2 removes `data-uniq-id` but does NOT remove comment functionality. Existing comments
with UUID-based `element_id` will lose their anchor until the migration runs.

### D11: Initial NodeMap population

When does the server first parse project files? On first tracing WS client connect:
1. `onTracingClientConnect` sends existing maps (empty on first connect)
2. The server needs to scan project source files and call `parseAndBuild()` for each

**Strategy:** Add a `populateNodeMaps(projectId, projectPath)` function to `element-tracing-channel.ts`
that scans `.tsx`/`.jsx` files and parses them. Called from `onTracingClientConnect` when the project
has zero tracked files. This is Task 14b.

---

## File Structure

### New files

```
lib/ast/position-finder.ts                # findElementByPosition(): SourceLocation → { element, path }
lib/ast/position-finder.test.ts           # TDD tests

server/lib/mutation-tracing.ts            # Post-mutation re-parse + broadcast helper
server/lib/mutation-tracing.test.ts       # TDD tests

shared/canvas-interaction/fiber-element-query.ts   # Fiber-based replacements for mapElementQuery.ts
shared/canvas-interaction/fiber-element-query.test.ts
```

### Modified files (by task)

**Phase 2a — Client wiring:**
```
shared/canvas-interaction/click-handler.ts          # Replace closest('[data-uniq-id]') with TracingResolver
shared/canvas-interaction/types.ts                  # TracingResolver interface, updated callbacks
shared/canvas-interaction/overlay-renderer.ts       # Replace querySelectorAll with TracingResolver.findDOMElement
shared/canvas-interaction/empty-container-placeholders.ts  # Replace querySelectorAll with fiber tree walk
shared/canvas-interaction/keyboard-handler.ts       # Replace DOM walk with NodeMap navigation
client/pages/Editor/components/hooks/useElementInteraction.ts  # nodeRef instead of dataset.uniqId
client/pages/Editor/components/hooks/useHotkeysSetup.ts        # Replace parent-walk DOM logic
client/pages/Editor/components/hooks/useOverlayMapCondHighlightComponents.ts  # Replace querySelectorAll
client/pages/Editor/utils/mapElementQuery.ts        # Replace with fiber-based functions
client/lib/dom-utils.ts                            # Replace buildElementSelector
client/lib/element-tracing/element-tracer.ts       # Add resolveClickLocal(), public adapter delegates
```

**Phase 2b — Server mutation routes:**
```
lib/ast/position-finder.ts                          # NEW: findElementByPosition()
server/lib/mutation-tracing.ts                      # NEW: post-mutation re-parse + broadcast
server/routes/updateComponentStyles.ts              # nodeRef input
server/routes/updateComponentProps.ts               # nodeRef input
server/routes/updateComponentPropsBatch.ts          # nodeRef input
server/routes/deleteElement.ts                      # nodeRef input
server/routes/deleteElements.ts                     # nodeRef[] input (batch)
server/routes/duplicateElement.ts                   # nodeRef input + return new nodeRef
server/routes/wrapElement.ts                        # nodeRef input
server/routes/editMap.ts                            # nodeRef input
server/routes/editCondition.ts                      # nodeRef input
server/routes/insertElement.ts                      # nodeRef input (parent)
server/routes/pasteElement.ts                       # nodeRef input (parent)
server/routes/copyElementTsx.ts                     # nodeRef input
server/routes/updateElementText.ts                  # nodeRef input
server/routes/getElementLocation.ts                 # nodeRef input (replaces uniqId)
server/routes/findElementAtPosition.ts              # Return nodeRef instead of uniqId
server/routes/renameComponent.ts                    # nodeRef input
server/routes/ide.ts                                # Replace internal uniqId usage
server/services/element-tracing-channel.ts          # Add populateNodeMaps() for initial parse
```

**Phase 2b — Extension:**
```
vscode-extension/hypercanvas-preview/src/services/AstService.ts  # SourceLocation params
vscode-extension/hypercanvas-preview/src/services/scripts/iframe-interaction.ts  # Fiber-based clicks
vscode-extension/hypercanvas-preview/src/services/SyncPositionService.ts  # nodeRef selection
vscode-extension/hypercanvas-preview/src/StateHub.ts               # Wire PostMessageTracingTransport
vscode-extension/hypercanvas-preview/src/PanelRouter.ts            # Route element-tracing messages
```

**Phase 2c — Removal:**
```
lib/ast/inject-unique-ids.ts              # DELETE
lib/ast/inject-unique-ids.test.ts         # DELETE
lib/ast/uuid.ts                           # DELETE (or strip to only generateUuid if needed elsewhere)
lib/ast/uuid.test.ts                      # DELETE/update
lib/ast/operations.ts                     # Remove injectUniqueIdsIntoAST, findParentElementId, getDirectChildIds
lib/ast/traverser.ts                      # Remove findElementByUuid, keep findElementAtPosition
server/routes/injectUniqueIds.ts          # DELETE route
client/lib/canvas-engine/react/CanvasRenderer.tsx  # Remove addDataUniqIds()
```

---

## Phase 2a: Wire Fiber into Client

### Task 1: Add `resolveClickLocal()` to ElementTracer

Enhance `ElementTracer` with client-side resolution from cached node maps, eliminating the server round-trip for the common case.

**Files:**
- Modify: `client/lib/element-tracing/element-tracer.ts`
- Modify: `client/lib/element-tracing/element-tracer.test.ts`

- [ ] **Step 1: Write failing test for resolveClickLocal**

```typescript
// element-tracer.test.ts — add to existing test file

describe('resolveClickLocal', () => {
  it('should resolve element from cached node map without server round-trip', () => {
    const mockAdapter: FrameworkAdapter = {
      name: 'react',
      detect: () => true,
      getSourceLocation: () => ({ fileName: '/app/src/App.tsx', line: 5, column: 4 }),
      getComponentChain: () => [],
      getItemIndex: () => 0,
      walkComponentTree: () => [],
      findDOMElement: () => null,
    };

    const sentMessages: TracingClientMessage[] = [];
    const mockTransport: TracingTransport = {
      send: (msg) => sentMessages.push(msg),
      onMessage: () => () => {},
      connected: true,
      onConnectionChange: () => () => {},
    };

    const tracer = new ElementTracer(mockAdapter, mockTransport);

    // Simulate server pushing a node map
    const nodeMap: NodeMapEntry[] = [
      {
        nodeRef: '/app/src/App.tsx:0',
        tag: 'div',
        loc: { fileName: '/app/src/App.tsx', line: 5, column: 4 },
        endLoc: { fileName: '/app/src/App.tsx', line: 10, column: 10 },
        parentRef: null,
        children: [],
        isComponent: false,
      },
    ];

    // Feed node map via transport message handler
    tracer['_handleMessage']({
      type: 'node-map-update',
      filePath: '/app/src/App.tsx',
      fileHash: 'abc123',
      version: 1,
      nodes: nodeMap,
    });

    const el = document.createElement('div');
    const result = tracer.resolveClickLocal(el);

    expect(result).not.toBeNull();
    expect(result!.nodeRef).toBe('/app/src/App.tsx:0');
    expect(result!.entry.tag).toBe('div');
    expect(result!.itemIndex).toBe(0);
    // Should NOT have sent a message to server
    expect(sentMessages).toHaveLength(0);
  });

  it('should fall back to server resolution when no cached map matches', () => {
    const mockAdapter: FrameworkAdapter = {
      name: 'react',
      detect: () => true,
      getSourceLocation: () => ({ fileName: '/app/src/Unknown.tsx', line: 1, column: 0 }),
      getComponentChain: () => [],
      getItemIndex: () => 0,
      walkComponentTree: () => [],
      findDOMElement: () => null,
    };

    const sentMessages: TracingClientMessage[] = [];
    const mockTransport: TracingTransport = {
      send: (msg) => sentMessages.push(msg),
      onMessage: () => () => {},
      connected: true,
      onConnectionChange: () => () => {},
    };

    const tracer = new ElementTracer(mockAdapter, mockTransport);
    const el = document.createElement('div');
    const result = tracer.resolveClickLocal(el);

    expect(result).toBeNull();
    // Should have sent resolve-element to server
    expect(sentMessages).toHaveLength(1);
    expect(sentMessages[0].type).toBe('resolve-element');
  });
});
```

- [ ] **Step 2: Run test — verify it fails**

Run: `bun run test client/lib/element-tracing/element-tracer.test.ts`
Expected: FAIL — `resolveClickLocal is not a function`

- [ ] **Step 3: Implement resolveClickLocal**

```typescript
// element-tracer.ts — add to ElementTracer class

interface LocalResolveResult {
  nodeRef: NodeRef;
  entry: NodeMapEntry;
  source: SourceLocation;
  itemIndex: number;
}

/**
 * Try to resolve a click locally from cached node maps.
 * Returns null if no cached map matches — in that case,
 * also sends a resolve-element request to the server as fallback.
 */
resolveClickLocal(element: HTMLElement): LocalResolveResult | null {
  const source = this._adapter.getSourceLocation(element);
  if (source === null) return null;

  const itemIndex = this._adapter.getItemIndex(element);

  // Try local resolution from cached node maps
  const nodes = this._nodeMaps.get(source.fileName);
  if (nodes) {
    const entry = nodes.find(
      (n) => n.loc.fileName === source.fileName && n.loc.line === source.line && n.loc.column === source.column,
    );
    if (entry) {
      return { nodeRef: entry.nodeRef, entry, source, itemIndex };
    }
  }

  // Fallback: send to server
  const requestId = `req-${++this._requestCounter}`;
  this._transport.send({ type: 'resolve-element', requestId, source, itemIndex });
  return null;
}
```

Add `NodeRef` to the import statement. Also export `LocalResolveResult` type.

Also add **public adapter delegate methods** so shared code doesn't need private access:

```typescript
// element-tracer.ts — add to ElementTracer class

/** Delegate to adapter.getSourceLocation — public for shared/ code that can't import adapter. */
getSourceLocation(element: HTMLElement): SourceLocation | null {
  return this._adapter.getSourceLocation(element);
}

/** Delegate to adapter.getItemIndex — public for shared/ code that can't import adapter. */
getItemIndex(element: HTMLElement): number {
  return this._adapter.getItemIndex(element);
}
```

This way `ElementTracer` implements the `TracingResolver` interface (D7) — `shared/` code depends
on the interface, `client/` code injects the concrete implementation.

- [ ] **Step 4: Run test — verify it passes**

Run: `bun run test client/lib/element-tracing/element-tracer.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```
feat(element-tracing): add resolveClickLocal for client-side cached resolution (HYP-268)
```

---

### Task 2: Create fiber-based element query utilities

Replace `mapElementQuery.ts` functions with fiber-based equivalents.

**Files:**
- Create: `shared/canvas-interaction/fiber-element-query.ts`
- Create: `shared/canvas-interaction/fiber-element-query.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// fiber-element-query.test.ts
import { describe, expect, it } from 'bun:test';
import type { FrameworkAdapter, SourceLocation } from '../../shared/element-tracing/types';
import {
  findDOMElementsBySource,
  computeFiberItemIndex,
  buildSourceKey,
} from './fiber-element-query';

describe('buildSourceKey', () => {
  it('should create deterministic key from source location', () => {
    const source: SourceLocation = { fileName: '/app/src/App.tsx', line: 5, column: 4 };
    expect(buildSourceKey(source)).toBe('/app/src/App.tsx:5:4');
  });
});

describe('findDOMElementsBySource', () => {
  it('should delegate to adapter.findDOMElement for single item', () => {
    const mockEl = document.createElement('div');
    const source: SourceLocation = { fileName: 'App.tsx', line: 5, column: 4 };

    const adapter: Pick<FrameworkAdapter, 'findDOMElement'> = {
      findDOMElement: (s, idx) => {
        if (s.fileName === 'App.tsx' && s.line === 5 && idx === 0) return mockEl;
        return null;
      },
    };

    const result = findDOMElementsBySource(adapter, source, 0);
    expect(result).toEqual([mockEl]);
  });

  it('should return empty array when adapter returns null', () => {
    const adapter: Pick<FrameworkAdapter, 'findDOMElement'> = {
      findDOMElement: () => null,
    };

    const source: SourceLocation = { fileName: 'App.tsx', line: 5, column: 4 };
    const result = findDOMElementsBySource(adapter, source, 0);
    expect(result).toEqual([]);
  });
});

describe('computeFiberItemIndex', () => {
  it('should return adapter.getItemIndex result', () => {
    const mockEl = document.createElement('div');
    const adapter: Pick<FrameworkAdapter, 'getItemIndex'> = {
      getItemIndex: () => 2,
    };

    expect(computeFiberItemIndex(adapter, mockEl)).toBe(2);
  });
});
```

- [ ] **Step 2: Run test — verify it fails**

Run: `bun run test shared/canvas-interaction/fiber-element-query.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Implement fiber-element-query.ts**

```typescript
/**
 * @file Fiber-based element query utilities — replacements for mapElementQuery.ts
 *
 * Accessed via: overlay-renderer.ts, click-handler.ts, keyboard-handler.ts
 * Assumptions: FrameworkAdapter is initialized and fiber tree is available in the iframe
 */

import type { FrameworkAdapter, SourceLocation } from '../../shared/element-tracing/types';

/** Create a deterministic lookup key from a source location. */
export function buildSourceKey(source: SourceLocation): string {
  return `${source.fileName}:${source.line}:${source.column}`;
}

/**
 * Find DOM element(s) by source location using the framework adapter.
 * When itemIndex is specified, returns only that element.
 * When null, finds all elements at that source location (for .map() highlighting).
 */
export function findDOMElementsBySource(
  adapter: Pick<FrameworkAdapter, 'findDOMElement'>,
  source: SourceLocation,
  itemIndex: number | null,
): HTMLElement[] {
  if (itemIndex !== null) {
    const el = adapter.findDOMElement(source, itemIndex);
    return el ? [el] : [];
  }

  // Find all elements at this source location (iterate until findDOMElement returns null)
  const elements: HTMLElement[] = [];
  for (let i = 0; i < 1000; i++) {
    const el = adapter.findDOMElement(source, i);
    if (!el) break;
    elements.push(el);
  }
  return elements;
}

/**
 * Compute the item index of an element among fiber siblings with the same source.
 * Wraps adapter.getItemIndex() for consistent API.
 */
export function computeFiberItemIndex(
  adapter: Pick<FrameworkAdapter, 'getItemIndex'>,
  element: HTMLElement,
): number {
  return adapter.getItemIndex(element);
}
```

- [ ] **Step 4: Run test — verify it passes**

Run: `bun run test shared/canvas-interaction/fiber-element-query.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```
feat(element-tracing): add fiber-based element query utilities (HYP-268)
```

---

### Task 3: Update `ClickHandlerCallbacks` and `click-handler.ts`

Replace `closest('[data-uniq-id]')` with `ElementTracer`-based resolution.

**Files:**
- Modify: `shared/canvas-interaction/types.ts`
- Modify: `shared/canvas-interaction/click-handler.ts`
- Modify: `shared/canvas-interaction/click-handler.test.ts` (if exists)

- [ ] **Step 1: Update `ClickHandlerCallbacks` interface**

In `shared/canvas-interaction/types.ts`, change the callbacks to use `nodeRef` and `SourceLocation`:

```typescript
// types.ts — replace ClickHandlerCallbacks

import type { SourceLocation } from '../element-tracing/types';

export interface ClickHandlerCallbacks {
  /**
   * Called when an element is clicked in design mode.
   * nodeRef is null when local resolution failed (server round-trip pending).
   */
  onElementClick: (
    nodeRef: string | null,
    element: HTMLElement,
    event: MouseEvent,
    itemIndex: number,
    source: SourceLocation,
  ) => void;
  /** Called on mouseover/mouseout (null = mouse left all elements) */
  onElementHover: (
    nodeRef: string | null,
    element: HTMLElement | null,
    itemIndex: number | null,
    source: SourceLocation | null,
  ) => void;
  /** Called when clicking empty space (no fiber source found) */
  onEmptyClick?: (event: MouseEvent) => void;
  /** Returns current editor mode */
  getMode: () => 'design' | 'interact';
  /**
   * Optional pre-intercept before default click handling.
   * Return true to skip default handling entirely.
   */
  shouldIntercept?: (event: MouseEvent) => boolean;
}

export interface ClickHandlerOptions {
  activeInstanceId?: string | null;
  getActiveInstanceId?: () => string | null;
}
```

- [ ] **Step 2: Rewrite click-handler.ts to use ElementTracer**

```typescript
/**
 * @file Canvas click/hover handler — attaches DOM listeners to an iframe document.
 *
 * Accessed via: IframeCanvas.tsx, iframe-interaction.ts (extension)
 * Assumptions: ElementTracer is initialized with a valid ReactAdapter before attaching
 */

import type { ClickHandlerCallbacks, ClickHandlerOptions, TracingResolver } from './types';

/** Check if target is a form/editable element that should retain native focus behavior. */
function isInteractiveElement(target: HTMLElement): boolean {
  return (
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA' ||
    target.tagName === 'SELECT' ||
    target.isContentEditable
  );
}

/**
 * Attach click, hover, and focus handlers to an iframe document.
 * Returns a dispose function to remove all listeners.
 *
 * Uses TracingResolver (dependency inversion — shared/ can't import client/) for
 * fiber-based element identification instead of data-uniq-id.
 */
export function attachClickHandler(
  iframeDoc: Document,
  callbacks: ClickHandlerCallbacks,
  resolver: TracingResolver,
  options?: ClickHandlerOptions,
): () => void {
  const { onElementClick, onElementHover, onEmptyClick, getMode, shouldIntercept } = callbacks;

  const handlePointerDown = (e: PointerEvent) => {
    if (e.button !== 0) return;
    if (getMode() === 'design') {
      e.preventDefault();
      e.stopPropagation();
    }
  };

  const handleClick = (e: MouseEvent) => {
    const mode = getMode();
    if (shouldIntercept?.(e)) return;
    if (mode !== 'design' && mode !== 'interact') return;

    const target = e.target as HTMLElement;

    if (mode === 'design') {
      e.preventDefault();
      e.stopPropagation();
    }

    if (mode !== 'design') return;

    // Try local fiber resolution (synchronous from cache)
    const result = resolver.resolveClickLocal(target);
    if (result) {
      onElementClick(result.nodeRef, target, e, result.itemIndex, result.source);
      return;
    }

    // Fallback: fiber gave us a source but no cached nodeRef
    const source = resolver.getSourceLocation(target);
    if (source) {
      const itemIndex = resolver.getItemIndex(target);
      onElementClick(null, target, e, itemIndex, source);
      return;
    }

    // No fiber source — empty click
    onEmptyClick?.(e);
  };

  const handleMouseDown = (e: MouseEvent) => {
    if (getMode() !== 'design') return;
    if (isInteractiveElement(e.target as HTMLElement)) {
      e.preventDefault();
    }
  };

  const handleMouseOver = (e: MouseEvent) => {
    if (getMode() !== 'design') return;
    const target = e.target as HTMLElement;

    const result = resolver.resolveClickLocal(target);
    if (result) {
      onElementHover(result.nodeRef, target, result.itemIndex, result.source);
    }
    // If no fiber source, don't hover — element is not traceable
  };

  const handleMouseOut = (e: MouseEvent) => {
    if (getMode() !== 'design') return;
    const relatedTarget = e.relatedTarget as HTMLElement | null;
    if (relatedTarget) {
      const source = resolver.getSourceLocation(relatedTarget);
      if (source) return; // Pointer moved to another traceable element
    }
    onElementHover(null, null, null, null);
  };

  iframeDoc.addEventListener('pointerdown', handlePointerDown, { capture: true });
  iframeDoc.addEventListener('click', handleClick, { capture: true });
  iframeDoc.addEventListener('mousedown', handleMouseDown, { capture: true });
  iframeDoc.addEventListener('mouseover', handleMouseOver, { capture: true });
  iframeDoc.addEventListener('mouseout', handleMouseOut, { capture: true });

  return () => {
    iframeDoc.removeEventListener('pointerdown', handlePointerDown, { capture: true });
    iframeDoc.removeEventListener('click', handleClick, { capture: true });
    iframeDoc.removeEventListener('mousedown', handleMouseDown, { capture: true });
    iframeDoc.removeEventListener('mouseover', handleMouseOver, { capture: true });
    iframeDoc.removeEventListener('mouseout', handleMouseOut, { capture: true });
  };
}
```

- [ ] **Step 3: Update all `attachClickHandler` call sites**

Search for `attachClickHandler(` in the codebase. Each call site needs to pass the `tracer: ElementTracer` argument. Major sites:
- `client/components/IframeCanvas.tsx`
- `vscode-extension/.../iframe-interaction.ts`

The callback signatures also change — each `onElementClick(uniqId, ...)` becomes `onElementClick(nodeRef, ..., source)`.

- [ ] **Step 4: Update tests**

Run: `bun run test shared/canvas-interaction/`
Fix any failing tests to match new signatures.

- [ ] **Step 5: Commit**

```
feat(element-tracing): rewrite click-handler to use fiber instead of data-uniq-id (HYP-268)
```

---

### Task 4: Update `useElementInteraction` hook

Replace `element.dataset.uniqId` with nodeRef from click handler.

**Files:**
- Modify: `client/pages/Editor/components/hooks/useElementInteraction.ts`

- [ ] **Step 1: Update hook interface**

```typescript
interface UseElementInteractionReturn {
  handleElementClick: (
    nodeRef: string | null,
    element: HTMLElement | null,
    event?: MouseEvent,
    itemIndex?: number,
    source?: SourceLocation,
  ) => void;
  handleElementHover: (
    nodeRef: string | null,
    element: HTMLElement | null,
    itemIndex?: number | null,
    source?: SourceLocation | null,
  ) => void;
  handleHoverElement: (id: string | null) => void;
}
```

- [ ] **Step 2: Update handleElementClick**

```typescript
const handleElementClick = useCallback(
  (
    nodeRef: string | null,
    element: HTMLElement | null,
    event?: MouseEvent,
    itemIndex?: number,
    source?: SourceLocation,
  ) => {
    if (selectedCommentId) setSelectedCommentId(null);
    if (selectedAnnotationIds.length > 0) setSelectedAnnotationIds([]);

    if (!element || !nodeRef) {
      if (!event?.metaKey && !event?.ctrlKey) {
        engine.clearSelection();
      }
      return;
    }

    if (event?.metaKey || event?.ctrlKey) {
      const currentSelection = engine.getSelection();
      if (currentSelection.selectedIds.includes(nodeRef)) {
        engine.removeFromSelection(nodeRef);
      } else {
        engine.addToSelection(nodeRef);
      }
    } else {
      engine.selectWithItemIndex(nodeRef, itemIndex ?? null);
    }
  },
  [engine, selectedCommentId, setSelectedCommentId, selectedAnnotationIds, setSelectedAnnotationIds],
);
```

- [ ] **Step 3: Update handleElementHover**

```typescript
const handleElementHover = useCallback(
  (
    nodeRef: string | null,
    element: HTMLElement | null,
    itemIndex?: number | null,
    source?: SourceLocation | null,
  ) => {
    if (nodeRef) {
      engine.setHoveredWithItemIndex(nodeRef, itemIndex ?? null);
    } else {
      engine.setHovered(null);
    }
  },
  [engine],
);
```

- [ ] **Step 4: Verify compilation**

Run: `bunx tsc --noEmit`

- [ ] **Step 5: Commit**

```
feat(element-tracing): update useElementInteraction to use nodeRef (HYP-268)
```

---

### Task 5: Update overlay rendering

Replace `querySelectorAll('[data-uniq-id="..."]')` in `computeOverlayRects()` with fiber-based lookup.

**Files:**
- Modify: `shared/canvas-interaction/overlay-renderer.ts`
- Modify: `shared/canvas-interaction/types.ts` — add `ElementTracer` to `OverlayState`

- [ ] **Step 1: Add source maps to OverlayState**

In `shared/canvas-interaction/types.ts`:

```typescript
import type { SourceLocation } from '../element-tracing/types';

export interface OverlayState {
  selectedIds: string[];       // nodeRefs
  hoveredId: string | null;    // nodeRef
  hoveredItemIndex?: number | null;
  selectedItemIndices?: Map<string, number | null>;
  activeInstanceId?: string | null;
  viewportZoom?: number;
  /** Source location lookup: nodeRef → SourceLocation (needed for fiber-based DOM queries) */
  sourceMap?: Map<string, SourceLocation>;
}
```

- [ ] **Step 2: Rewrite `computeOverlayRects`**

Replace `querySelectorAll('[data-uniq-id="..."]')` patterns with `FrameworkAdapter.findDOMElement()`. The adapter is passed via `OverlayRendererOptions`:

```typescript
export interface OverlayRendererOptions {
  viewportZoom?: number;
  onPlaceholderClick?: (elementId: string) => void;
  editorMode?: 'design' | 'interact' | 'code';
  /** Framework adapter for fiber-based DOM element lookup */
  findDOMElement?: (source: SourceLocation, itemIndex: number) => HTMLElement | null;
}
```

In `computeOverlayRects`, replace the selector-based lookups:

```typescript
// OLD:
let hoverSelector = `[data-uniq-id="${hoveredId}"]`;
const allHoverElements = doc.querySelectorAll(hoverSelector);

// NEW:
const hoveredSource = state.sourceMap?.get(hoveredId);
if (hoveredSource && findDOMElement) {
  const hoverElement = findDOMElement(hoveredSource, hoveredItemIndex ?? 0);
  // ... use hoverElement for rect
}
```

And for selection:

```typescript
// OLD:
let baseSelector = `[data-uniq-id="${selectedId}"]`;
const allElements = doc.querySelectorAll(baseSelector);

// NEW:
const selectedSource = state.sourceMap?.get(selectedId);
if (selectedSource && findDOMElement) {
  if (itemIndex !== null) {
    const el = findDOMElement(selectedSource, itemIndex);
    if (el) elementsToHighlight.push(el);
  } else {
    // Find all elements at this source (for .map() highlighting)
    for (let i = 0; i < 1000; i++) {
      const el = findDOMElement(selectedSource, i);
      if (!el) break;
      elementsToHighlight.push(el);
    }
  }
}
```

- [ ] **Step 3: Update `createOverlayRenderer` to accept findDOMElement**

Pass through from options to the tick function.

- [ ] **Step 4: Update overlay tests**

Run: `bun run test shared/canvas-interaction/`

- [ ] **Step 5: Commit**

```
feat(element-tracing): rewrite overlay rendering to use fiber-based DOM lookup (HYP-268)
```

---

### Task 6: Update keyboard handler

Replace DOM-based `findParentWithUniqId`, `findDirectChildIds`, `findSiblingId` with fiber-based equivalents.

**Files:**
- Modify: `shared/canvas-interaction/keyboard-handler.ts`
- Modify: `shared/canvas-interaction/keyboard-handler.test.ts`

- [ ] **Step 1: Update helper functions**

The keyboard handler needs fiber-aware navigation. The approach:
- `findParentWithUniqId` → use `ElementTracer` node map parent chain (from `NodeMapEntry.parentRef`)
- `findDirectChildIds` → use `NodeMapEntry.children`
- `findSiblingId` → use parent's `children` array from node map

Add a `NodeMapLookup` parameter to the `DesignKeydownConfig`:

```typescript
interface NodeMapLookup {
  /** Get NodeMapEntry by nodeRef */
  getEntry: (nodeRef: string) => NodeMapEntry | null;
  /** Find DOM element by source location (for keyboard navigation focus) */
  findDOMElement: (source: SourceLocation, itemIndex: number) => HTMLElement | null;
}
```

- [ ] **Step 2: Rewrite navigation functions**

```typescript
/** Find parent nodeRef from the node map. */
function findParentNodeRef(nodeRef: string, lookup: NodeMapLookup): string | null {
  const entry = lookup.getEntry(nodeRef);
  return entry?.parentRef ?? null;
}

/** Find direct child nodeRefs from the node map. */
function findDirectChildNodeRefs(nodeRef: string, lookup: NodeMapLookup): string[] {
  const entry = lookup.getEntry(nodeRef);
  return entry?.children ?? [];
}

/** Find next/prev sibling nodeRef from parent's children. */
function findSiblingNodeRef(
  nodeRef: string,
  direction: 'next' | 'prev',
  lookup: NodeMapLookup,
): string | null {
  const entry = lookup.getEntry(nodeRef);
  if (!entry?.parentRef) return null;

  const parent = lookup.getEntry(entry.parentRef);
  if (!parent) return null;

  const siblings = parent.children;
  const currentIndex = siblings.indexOf(nodeRef);
  if (currentIndex === -1) return null;

  let targetIndex: number;
  if (direction === 'prev') {
    targetIndex = currentIndex === 0 ? siblings.length - 1 : currentIndex - 1;
  } else {
    targetIndex = currentIndex === siblings.length - 1 ? 0 : currentIndex + 1;
  }

  return siblings[targetIndex] ?? null;
}
```

- [ ] **Step 3: Update tests**

Run: `bun run test shared/canvas-interaction/keyboard-handler.test.ts`
Fix tests to use node map lookup instead of DOM queries.

- [ ] **Step 4: Commit**

```
feat(element-tracing): update keyboard handler to use node map navigation (HYP-268)
```

---

### Task 7: Update empty container placeholders

Replace `querySelectorAll('[data-uniq-id]')` with fiber tree walk.

**Files:**
- Modify: `shared/canvas-interaction/empty-container-placeholders.ts`
- Modify: `shared/canvas-interaction/empty-container-placeholders.test.ts`

- [ ] **Step 1: Add adapter parameter**

```typescript
import type { FrameworkAdapter, SourceLocation } from '../../shared/element-tracing/types';

export interface FiberPlaceholderRect {
  nodeRef: string;
  source: SourceLocation;
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Find empty containers using the framework adapter's component tree walk.
 * Each component tree node that renders an empty DOM element gets a placeholder.
 */
export function getEmptyContainerRects(
  doc: Document,
  adapter: FrameworkAdapter,
  nodeEntries: Map<string, { nodeRef: string; source: SourceLocation }>,
): FiberPlaceholderRect[] {
  // Walk all host elements in the fiber tree
  const root = doc.body?.firstElementChild;
  if (!root) return [];

  const tree = adapter.walkComponentTree(root as HTMLElement);
  const rects: FiberPlaceholderRect[] = [];

  function visit(nodes: typeof tree): void {
    for (const node of nodes) {
      if (node.domElement && isContainerEmpty(node.domElement) && node.source) {
        const key = `${node.source.fileName}:${node.source.line}:${node.source.column}`;
        const entry = nodeEntries.get(key);
        if (entry) {
          const rect = node.domElement.getBoundingClientRect();
          const effectiveHeight = Math.max(rect.height, MIN_PLACEHOLDER_HEIGHT);
          const topOffset = (effectiveHeight - rect.height) / 2;
          rects.push({
            nodeRef: entry.nodeRef,
            source: entry.source,
            left: rect.left,
            top: rect.top - topOffset,
            width: rect.width,
            height: effectiveHeight,
          });
        }
      }
      visit(node.children);
    }
  }

  visit(tree);
  return rects;
}
```

Keep the old `getEmptyContainerRects(doc)` signature as a deprecated wrapper until Phase 2c removes it.

- [ ] **Step 2: Update overlay-renderer.ts placeholder section**

In `createOverlayRenderer`'s tick function, call the new function when adapter is available, falling back to old function otherwise.

- [ ] **Step 3: Update tests**

- [ ] **Step 4: Commit**

```
feat(element-tracing): update empty container detection to use fiber tree (HYP-268)
```

---

### Task 8: Wire ElementTracer into IframeCanvas lifecycle

Initialize `ElementTracer` + `ReactAdapter` + `WSTracingTransport` when iframe loads.

**Files:**
- Modify: `client/components/IframeCanvas.tsx`
- Create: `client/hooks/useElementTracer.ts` (lifecycle hook)

- [ ] **Step 1: Create useElementTracer hook**

```typescript
/**
 * @file Hook to manage ElementTracer lifecycle — creates adapter, transport, and tracer
 * when iframe content loads and React is detected.
 *
 * Accessed via: IframeCanvas.tsx
 */

import { useEffect, useRef, useState } from 'react';
import { ElementTracer } from '@/lib/element-tracing/element-tracer';
import { ReactAdapter } from '@/lib/element-tracing/react-adapter';
import { WSTracingTransport } from '@/lib/element-tracing/ws-tracing-transport';
import type { SourceLocation } from '@shared/element-tracing/types';

interface UseElementTracerOptions {
  iframe: HTMLIFrameElement | null;
  projectId: string;
  enabled: boolean;
}

export function useElementTracer({ iframe, projectId, enabled }: UseElementTracerOptions) {
  const tracerRef = useRef<ElementTracer | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!iframe || !enabled || !projectId) return;

    const doc = iframe.contentDocument;
    if (!doc) return;

    const adapter = new ReactAdapter(doc);
    if (!adapter.detect(doc)) {
      // React not detected or _debugSource missing
      return;
    }

    const wsUrl = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/element-tracing?projectId=${projectId}`;
    const transport = new WSTracingTransport(() => new WebSocket(wsUrl));
    const tracer = new ElementTracer(adapter, transport);

    tracerRef.current = tracer;
    setReady(true);

    return () => {
      tracer.dispose();
      tracerRef.current = null;
      setReady(false);
    };
  }, [iframe, projectId, enabled]);

  return { tracer: tracerRef.current, ready };
}
```

- [ ] **Step 2: Wire into IframeCanvas**

In `IframeCanvas.tsx`, call `useElementTracer` and pass the tracer to `attachClickHandler`.

- [ ] **Step 3: Pass tracer to overlay renderer**

Wire `tracer.findDOMElement` into `createOverlayRenderer` options.

- [ ] **Step 4: Verify compilation + manual test**

Run: `bunx tsc --noEmit`

- [ ] **Step 5: Commit**

```
feat(element-tracing): wire ElementTracer into IframeCanvas lifecycle (HYP-268)
```

---

### Task 9: Handle async resolution — selection confirmed callback

When `resolveClickLocal` returns null (cache miss), the click handler fires with `nodeRef = null`. When the server responds, update the selection.

**Files:**
- Modify: `client/components/IframeCanvas.tsx` (or the wiring code from Task 8)

- [ ] **Step 1: Subscribe to `onSelectionResolved`**

```typescript
// In useElementTracer or IframeCanvas setup
useEffect(() => {
  if (!tracer) return;

  const unsub = tracer.onSelectionResolved((response) => {
    if (response.nodeRef && response.entry) {
      // Server confirmed the selection — update engine
      engine.selectWithItemIndex(response.nodeRef, /* itemIndex from pending state */);
    }
  });

  return unsub;
}, [tracer, engine]);
```

- [ ] **Step 2: Track pending selections**

Store `{ source, itemIndex }` when click handler fires with `nodeRef = null`. Clear when server response arrives.

- [ ] **Step 3: Commit**

```
feat(element-tracing): handle async resolution with server-confirmed selection (HYP-268)
```

---

### Task 9b: Update `useHotkeysSetup.ts` parent-walk logic

The 767-line hotkey handler has 4 occurrences of `dataset.uniqId` parent-walking logic for re-selection after delete/cut. These must switch to nodeRef-based navigation via NodeMap.

**Files:**
- Modify: `client/pages/Editor/components/hooks/useHotkeysSetup.ts`

- [ ] **Step 1: Replace DOM parent walk with NodeMap lookup**

Current pattern (4 occurrences):
```typescript
// OLD:
const selector = buildElementSelector(selectedIds[0], activeDesignInstanceId);
const currentElement = iframe.contentDocument.querySelector(selector);
let parent = currentElement?.parentElement;
while (parent && !parent.dataset.uniqId) parent = parent.parentElement;
const foundParentId = parent?.dataset.uniqId;
```

New pattern using NodeMap entry:
```typescript
// NEW:
const parentRef = nodeMapLookup.getEntry(selectedIds[0])?.parentRef;
if (parentRef) {
  engine.select(parentRef);
}
```

The `nodeMapLookup` is the same `NodeMapLookup` interface from Task 6, injected via hook props.

- [ ] **Step 2: Verify all 4 occurrences replaced**

Search: `grep -n 'dataset.uniqId\|data-uniq-id' useHotkeysSetup.ts` → should return 0 results.

- [ ] **Step 3: Run tests**

Run: `bun run test client/pages/Editor/`

- [ ] **Step 4: Commit**

```
feat(element-tracing): update useHotkeysSetup parent navigation to use NodeMap (HYP-268)
```

---

### Task 9c: Update `useOverlayMapCondHighlightComponents.ts`

This hook groups map-rendered elements by instance and renders boundary overlays. It uses `querySelectorAll('[data-uniq-id="..."]')` to find elements.

**Files:**
- Modify: `client/pages/Editor/components/hooks/useOverlayMapCondHighlightComponents.ts`

- [ ] **Step 1: Replace querySelectorAll patterns**

Current pattern:
```typescript
// OLD:
const mapElements = doc.querySelectorAll(`[data-uniq-id="${id}"]`);
```

New pattern using TracingResolver:
```typescript
// NEW:
const source = sourceMap.get(id);
if (source) {
  const elements = findDOMElementsBySource(resolver, source, null);
  // ... group by instance using getCanvasInstanceId() (this stays — uses data-canvas-instance-id)
}
```

Note: `data-canvas-instance-id` stays on DOM — instance scoping logic is unchanged.

- [ ] **Step 2: Run tests**

- [ ] **Step 3: Commit**

```
feat(element-tracing): update map/cond overlay highlighting to use fiber (HYP-268)
```

---

### Task 9d: Wire `PostMessageTracingTransport` through StateHub (extension)

Connect `PostMessageTracingTransport` to PanelRouter + StateHub so element tracing messages flow between the iframe preview and extension host.

**Files:**
- Modify: `vscode-extension/hypercanvas-preview/src/StateHub.ts`
- Modify: `vscode-extension/hypercanvas-preview/src/PanelRouter.ts`

- [ ] **Step 1: Add element-tracing message routing in PanelRouter**

```typescript
// PanelRouter.ts — add to routeMessage()
if (type.startsWith('element-tracing:')) {
  // Forward to PostMessageTracingTransport handler
  this.onElementTracingMessage?.(type.replace('element-tracing:', ''), payload);
  return;
}
```

- [ ] **Step 2: Wire StateHub to broadcast tracing state**

StateHub already broadcasts `state:update` to all panels. Add element-tracing node map updates to the broadcast:

```typescript
// When NodeMapService re-parses locally, broadcast to all panels
// via state:update with { nodeMap: NodeMapUpdate }
```

- [ ] **Step 3: Commit**

```
feat(element-tracing): wire PostMessageTracingTransport through PanelRouter + StateHub (HYP-268)
```

---

### Task 9e: NodeRef-based selection remapping on NodeMapUpdate

When `NodeMapUpdate` arrives with `refMapping`, remap current engine selection from old nodeRefs to new ones. This handles undo/redo, external edits, and position shifts after sibling mutations.

**Files:**
- Modify: `client/hooks/useElementTracer.ts` (from Task 8)

- [ ] **Step 1: Subscribe to node-map-update and remap selection**

```typescript
useEffect(() => {
  if (!tracer) return;

  const unsub = tracer.onMessage((msg) => {
    if (msg.type !== 'node-map-update' || !msg.refMapping) return;

    const currentSelection = engine.getSelection();
    const remapped = currentSelection.selectedIds
      .map((id) => msg.refMapping![id] ?? id)
      .filter((id) => id !== undefined);

    if (remapped.some((id, i) => id !== currentSelection.selectedIds[i])) {
      engine.selectMultiple(remapped);
    }
  });

  return unsub;
}, [tracer, engine]);
```

Note: `ElementTracer` needs to expose `onMessage()` (or a more specific `onNodeMapUpdate()` callback).

- [ ] **Step 2: Test undo/redo scenario**

- [ ] **Step 3: Commit**

```
feat(element-tracing): remap selection on NodeMapUpdate refMapping (HYP-268)
```

---

## Phase 2b: Switch Mutation Routes to nodeRef

### Task 10: Create `findElementByPosition` AST helper

New function that finds a JSX element by its source location, equivalent to `findElementByUuid` but position-based.

**Files:**
- Create: `lib/ast/position-finder.ts`
- Create: `lib/ast/position-finder.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
import { describe, expect, it } from 'bun:test';
import { parse } from '@babel/parser';
import { findElementByPosition } from './position-finder';

describe('findElementByPosition', () => {
  const source = `
import React from 'react';
export function App() {
  return (
    <div className="app">
      <h1>Hello</h1>
      <p>World</p>
    </div>
  );
}`;

  it('should find div at its exact start position', () => {
    const ast = parse(source, { sourceType: 'module', plugins: ['jsx', 'typescript'] });
    // <div> starts at line 5, column 4
    const result = findElementByPosition(ast, 5, 4);
    expect(result).not.toBeNull();
    expect(result!.element.openingElement.name).toHaveProperty('name', 'div');
  });

  it('should find h1 at its exact start position', () => {
    const ast = parse(source, { sourceType: 'module', plugins: ['jsx', 'typescript'] });
    // <h1> starts at line 6, column 6
    const result = findElementByPosition(ast, 6, 6);
    expect(result).not.toBeNull();
    expect(result!.element.openingElement.name).toHaveProperty('name', 'h1');
  });

  it('should return null for non-existent position', () => {
    const ast = parse(source, { sourceType: 'module', plugins: ['jsx', 'typescript'] });
    const result = findElementByPosition(ast, 100, 0);
    expect(result).toBeNull();
  });

  it('should return the innermost element when positions overlap', () => {
    const ast = parse(source, { sourceType: 'module', plugins: ['jsx', 'typescript'] });
    // Position of <h1> — not <div> even though <div> contains it
    const result = findElementByPosition(ast, 6, 6);
    expect(result!.element.openingElement.name).toHaveProperty('name', 'h1');
  });
});
```

- [ ] **Step 2: Run test — verify it fails**

Run: `bun run test lib/ast/position-finder.test.ts`

- [ ] **Step 3: Implement**

```typescript
/**
 * @file Find JSX elements by source position — replaces findElementByUuid for fiber-based tracing.
 *
 * Accessed via: Server mutation routes (resolve nodeRef → position → AST element)
 * Assumptions: AST was parsed with `loc: true` (Babel default)
 */

import _traverse, { type NodePath } from '@babel/traverse';
import * as t from '@babel/types';
import type { FindElementResult } from '../types';

// @ts-expect-error - babel/traverse ESM/CJS
const traverse = _traverse.default || _traverse;

/**
 * Find a JSX element at the given source position (1-based line, 0-based column).
 * Returns the innermost JSXElement whose opening tag starts at that position.
 */
export function findElementByPosition(ast: t.File, line: number, column: number): FindElementResult | null {
  let result: FindElementResult | null = null;

  traverse(ast, {
    JSXElement(path: NodePath<t.JSXElement>) {
      const loc = path.node.loc;
      if (!loc) return;

      if (loc.start.line === line && loc.start.column === column) {
        result = { element: path.node, path };
        path.stop();
      }
    },
  });

  return result;
}
```

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```
feat(element-tracing): add findElementByPosition AST helper (HYP-268)
```

---

### Task 11: Create post-mutation re-parse + broadcast helper

After every mutation route writes the AST, re-parse the file and broadcast the updated NodeMap to all connected clients.

**Files:**
- Create: `server/lib/mutation-tracing.ts`
- Create: `server/lib/mutation-tracing.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
import { describe, expect, it, mock } from 'bun:test';
import { afterMutation } from './mutation-tracing';

describe('afterMutation', () => {
  it('should re-parse file and return NodeMapUpdate', async () => {
    const mockBroadcast = mock(() => {});
    const sourceCode = '<div><span>hello</span></div>';

    const result = await afterMutation({
      filePath: '/app/src/App.tsx',
      projectId: 'proj-1',
      readFile: async () => sourceCode,
      broadcast: mockBroadcast,
    });

    expect(result).not.toBeNull();
    expect(result!.type).toBe('node-map-update');
    expect(result!.filePath).toBe('/app/src/App.tsx');
    expect(result!.nodes.length).toBeGreaterThan(0);
    expect(mockBroadcast).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Implement**

```typescript
/**
 * @file Post-mutation re-parse and broadcast helper.
 *
 * Accessed via: All mutation routes after writeAST()
 * Assumptions: NodeMapService is initialized for the project via element-tracing-channel
 */

import { readFile } from 'node:fs/promises';
import type { NodeMapUpdate } from '../../shared/element-tracing/types';
import { broadcastToProject, getNodeMapService } from '../services/element-tracing-channel';

interface AfterMutationOptions {
  filePath: string;
  projectId: string;
  /** Override for testing */
  readFile?: (path: string) => Promise<string>;
  /** Override for testing */
  broadcast?: (projectId: string, msg: NodeMapUpdate) => void;
}

/**
 * Re-parse a file after mutation and broadcast the updated NodeMap.
 * Returns the NodeMapUpdate or null if tracing is not active for this project.
 */
export async function afterMutation(options: AfterMutationOptions): Promise<NodeMapUpdate | null> {
  const { filePath, projectId } = options;
  const read = options.readFile ?? ((p: string) => readFile(p, 'utf-8'));
  const broadcast = options.broadcast ?? broadcastToProject;

  const nodeMapService = getNodeMapService(projectId);
  if (!nodeMapService) return null;

  const sourceCode = await read(filePath);
  const update = nodeMapService.reparseAndUpdate(sourceCode, filePath);
  broadcast(projectId, update);
  return update;
}
```

- [ ] **Step 3: Run test — verify passes**

- [ ] **Step 4: Commit**

```
feat(element-tracing): add post-mutation re-parse + broadcast helper (HYP-268)
```

---

### Task 12: Migrate simple mutation routes (batch)

These routes follow the same pattern: receive `nodeRef` → resolve to AST position → mutate → write → re-parse.

**Files to modify:**
- `server/routes/updateComponentStyles.ts`
- `server/routes/updateComponentProps.ts`
- `server/routes/updateComponentPropsBatch.ts`
- `server/routes/deleteElement.ts`
- `server/routes/deleteElements.ts` (batch delete — `elementIds[]` → `nodeRefs[]`)
- `server/routes/copyElementTsx.ts`
- `server/routes/updateElementText.ts`
- `server/routes/renameComponent.ts`
- `server/routes/getElementLocation.ts`
- `server/routes/findElementAtPosition.ts` (return nodeRef instead of uniqId)
- `server/routes/ide.ts` (replace internal uniqId usage)

**Pattern for each route (example: updateComponentStyles.ts):**

- [ ] **Step 1: Change input parameter**

```typescript
// OLD:
const { selectedId, filePath, ... } = await c.req.json();
const result = findElementByUuid(ast, selectedId);

// NEW:
import { getNodeMapService } from '../services/element-tracing-channel';
import { findElementByPosition } from '../../lib/ast/position-finder';

const { nodeRef, filePath, ... } = await c.req.json();

const nodeMapService = getNodeMapService(projectId);
if (!nodeMapService) {
  throw errors.operationFailed('Element tracing not initialized', 'No tracing clients connected');
}

const entry = nodeMapService.resolveNodeRef(nodeRef);
if (!entry) {
  throw errors.notFound(`nodeRef "${nodeRef}" not found`);
}

const result = findElementByPosition(ast, entry.loc.line, entry.loc.column);
```

- [ ] **Step 2: Add post-mutation broadcast**

After every `await writeAST(ast, absolutePath)`, add:

```typescript
import { afterMutation } from '../lib/mutation-tracing';

await afterMutation({ filePath: absolutePath, projectId });
```

- [ ] **Step 3: Update response to include MutationResponse fields**

```typescript
return c.json({
  success: true,
  // ... existing fields ...
  nodeRef: entry.nodeRef, // Include for client selection persistence
});
```

- [ ] **Step 4: Repeat for all 11 routes**

Each follows the same pattern. The differences:
- `deleteElement.ts`: input field is `elementId` → `nodeRef`
- `deleteElements.ts`: input field is `elementIds[]` → `nodeRefs[]`; loop resolves each
- `copyElementTsx.ts`: read-only, no writeAST, no broadcast
- `getElementLocation.ts`: read-only, returns position directly from `NodeMapEntry.loc`
- `findElementAtPosition.ts`: returns `nodeRef` instead of `uniqId` in response
- `renameComponent.ts`: uses `selectedId` → `nodeRef`
- `ide.ts`: replace internal uniqId usage in IDE command routing

- [ ] **Step 5: Run tests**

Run: `bun run test server/`

- [ ] **Step 6: Commit**

```
feat(element-tracing): migrate 8 mutation routes from UUID to nodeRef (HYP-268)
```

---

### Task 13: Migrate complex mutation routes

Routes with expression walking, new element creation, or parent targeting.

**Files:**
- `server/routes/duplicateElement.ts`
- `server/routes/insertElement.ts`
- `server/routes/pasteElement.ts`
- `server/routes/wrapElement.ts`
- `server/routes/editMap.ts`
- `server/routes/editCondition.ts`

- [ ] **Step 1: Migrate duplicateElement.ts**

Changes:
- Input: `elementId` → `nodeRef`
- Lookup: `findElementByUuid` → `findElementByPosition` (via nodeRef → entry → loc)
- Remove: `updateAllChildUuids()` call — duplicated elements no longer need UUID assignment
- Return: new `nodeRef` from post-mutation re-parse (the duplicated element's nodeRef)

```typescript
// After duplication and writeAST:
const update = await afterMutation({ filePath: absolutePath, projectId });
// Find the new element's nodeRef — it's the one at the insertion position
const newEntry = update?.nodes.find(n =>
  n.loc.line === insertedAtLine && n.loc.column === insertedAtColumn
);
return c.json({ success: true, newNodeRef: newEntry?.nodeRef ?? null });
```

- [ ] **Step 2: Migrate insertElement.ts**

Changes:
- Input: `parentId` → `parentNodeRef`
- Lookup: resolve parentNodeRef → position → find parent element
- Remove: UUID generation for new element (`ensureUuid()`)
- Return: new element's nodeRef from post-mutation re-parse

- [ ] **Step 3: Migrate pasteElement.ts**

Changes:
- Input: `parentId` → `parentNodeRef`
- Remove: UUID assignment for pasted elements
- Return: new nodeRef(s)

- [ ] **Step 4: Migrate wrapElement.ts**

Changes:
- Input: `elementId` → `nodeRef`
- Uses manual traverse with `data-uniq-id` attr check → switch to `findElementByPosition`
- Remove: `wrapperId` generation (UUID for wrapper)
- Return: wrapper's nodeRef from re-parse

- [ ] **Step 5: Migrate editMap.ts**

Changes:
- Input: `elementId` → `nodeRef`
- Manual traverse → `findElementByPosition` for the element
- Then walk up parent chain to find `.map()` — this logic stays (it walks AST parents, not DOM)

- [ ] **Step 6: Migrate editCondition.ts**

Changes:
- Input: `elementId` → `nodeRef`
- Manual traverse → `findElementByPosition`
- Walk up to find ternary/logical — stays

- [ ] **Step 7: Run tests**

Run: `bun run test server/routes/`

- [ ] **Step 8: Commit**

```
feat(element-tracing): migrate complex mutation routes from UUID to nodeRef (HYP-268)
```

---

### Task 14: Wire NodeMapService into file change detection

When source files change (via mutation or fs watcher), re-parse and broadcast.

**Files:**
- Modify: `server/services/element-tracing-channel.ts` — add `onFileChanged` export
- Modify: server file-watching infrastructure (if exists)

- [ ] **Step 1: Add file-changed handler**

```typescript
// element-tracing-channel.ts

/** Called when a source file changes (mutation, fs watcher, external edit). */
export async function onFileChanged(projectId: string, filePath: string, sourceCode: string): Promise<void> {
  const state = projects.get(projectId);
  if (!state) return;

  const update = state.nodeMapService.reparseAndUpdate(sourceCode, filePath);
  broadcastToProject(projectId, update);
}
```

- [ ] **Step 2: Wire into post-writeAST flow**

Ensure `afterMutation()` calls `onFileChanged` or directly uses `nodeMapService.reparseAndUpdate()` + `broadcastToProject()`.

- [ ] **Step 3: Commit**

```
feat(element-tracing): wire file change detection into NodeMap broadcast (HYP-268)
```

---

### Task 14b: Initial NodeMap population on first connect

When the first tracing WS client connects to a project, the NodeMapService has zero files tracked. The server needs to scan project source files and parse them to populate the initial maps.

**Files:**
- Modify: `server/services/element-tracing-channel.ts`

- [ ] **Step 1: Add `populateNodeMaps` function**

```typescript
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

/** Scan project source files and populate NodeMapService on first connect. */
export async function populateNodeMaps(projectId: string, projectPath: string): Promise<void> {
  const state = projects.get(projectId);
  if (!state) return;
  if (state.nodeMapService.getTrackedFiles().length > 0) return; // Already populated

  // Find all JSX/TSX files in src/ directory
  const srcDir = join(projectPath, 'src');
  const files = await findJsxFiles(srcDir);

  for (const filePath of files) {
    try {
      const sourceCode = await readFile(filePath, 'utf-8');
      state.nodeMapService.parseAndBuild(sourceCode, filePath);
    } catch {
      // Skip unparseable files — Babel errors are logged by NodeMapService.safeParse()
    }
  }
}
```

- [ ] **Step 2: Call from onTracingClientConnect**

```typescript
export async function onTracingClientConnect(ws: ServerWebSocket<WSData>): Promise<void> {
  const { projectId } = ws.data;
  const state = getOrCreateState(projectId);
  state.clients.add(ws);

  // Populate on first connect
  const project = getProjectById(projectId);
  if (project && state.nodeMapService.getTrackedFiles().length === 0) {
    await populateNodeMaps(projectId, project.path);
  }

  // Send all current node maps
  for (const filePath of state.nodeMapService.getTrackedFiles()) {
    const update = state.nodeMapService.buildUpdateMessage(filePath);
    if (update) ws.send(JSON.stringify(update));
  }
}
```

- [ ] **Step 3: Commit**

```
feat(element-tracing): populate NodeMap on first WS client connect (HYP-268)
```

---

### Task 15: Migrate extension AstService

Switch extension's `AstService` from UUID-based methods to position-based.

**Files:**
- Modify: `vscode-extension/hypercanvas-preview/src/services/AstService.ts`
- Modify: `vscode-extension/hypercanvas-preview/src/services/scripts/iframe-interaction.ts`
- Modify: `vscode-extension/hypercanvas-preview/src/services/SyncPositionService.ts`

- [ ] **Step 1: Add local NodeMapService to AstService**

```typescript
// AstService.ts
import { NodeMapService } from '@lib/element-tracing/node-map-service';

class AstService {
  private nodeMapService = new NodeMapService();

  // ... existing methods ...

  /** Parse file and update node map (called after every AST operation). */
  private updateNodeMap(filePath: string, sourceCode: string): void {
    if (this.nodeMapService.getTrackedFiles().includes(filePath)) {
      this.nodeMapService.reparseAndUpdate(sourceCode, filePath);
    } else {
      this.nodeMapService.parseAndBuild(sourceCode, filePath);
    }
  }
}
```

- [ ] **Step 2: Switch AstService methods from UUID to position**

Replace all `findElementByUuid(ast, elementId)` calls with:

```typescript
const entry = this.nodeMapService.resolveNodeRef(nodeRef);
if (!entry) return { success: false, error: 'nodeRef not found' };
const result = findElementByPosition(ast, entry.loc.line, entry.loc.column);
```

- [ ] **Step 3: Update iframe-interaction.ts**

Replace `closest('[data-uniq-id]')` with fiber-based extraction. The extension's iframe loads the same React app — fiber access works identically.

```typescript
// iframe-interaction.ts — click handler section
// OLD:
const element = target.closest('[data-uniq-id]') as HTMLElement | null;
const elementId = element?.dataset.uniqId ?? null;

// NEW:
// Use fiber to get source location, send via postMessage
const fiber = getFiberFromDOM(target);
const source = findNearestDebugSource(fiber);
if (source) {
  postMessage('hypercanvas:elementClick', {
    source: {
      fileName: source.fileName,
      line: source.lineNumber,
      column: (source.columnNumber ?? 1) - 1, // 1-based → 0-based
    },
    itemIndex: getItemIndex(target),
  });
}
```

Note: The fiber utility functions (`getFiberFromDOM`, `findNearestDebugSource`) need to be bundled into the injected iframe script. They're currently in `client/lib/element-tracing/fiber-utils.ts` — extract the pure functions into a self-contained format suitable for injection.

- [ ] **Step 4: Update SyncPositionService**

```typescript
// SyncPositionService.ts — selection sync

// Code → Preview: already uses line:column via AstService.findElementAtPosition
// Minimal change: send nodeRef instead of UUID

// Preview → Code: receive source location from iframe, use it directly
// AstService.getElementLocation() already returns { line, column }
```

- [ ] **Step 5: Run extension tests**

Run: `bun run test vscode-extension/`

- [ ] **Step 6: Commit**

```
feat(element-tracing): migrate extension AstService + iframe-interaction to fiber (HYP-268)
```

---

## Phase 2c: Remove data-uniq-id Pipeline

### Task 16: Remove UUID injection code

**Files to delete:**
- `lib/ast/inject-unique-ids.ts`
- `lib/ast/inject-unique-ids.test.ts`
- `server/routes/injectUniqueIds.ts`

**Files to modify:**
- `lib/ast/operations.ts` — remove `injectUniqueIdsIntoAST()`, `findParentElementId()`, `getDirectChildIds()`
- `lib/ast/operations.test.ts` — remove related tests
- `lib/ast/uuid.ts` — remove `updateAllChildUuids()`, `ensureUuid()`, `hasUuid()`, `removeUuid()`. Keep `generateUuid()` only if used elsewhere (check with `grep`)
- `lib/ast/uuid.test.ts` — update/remove
- `lib/ast/traverser.ts` — remove `findElementByUuid()`, `getUuidFromElement()`. Keep `findElementAtPosition()`.
- `lib/ast/traverser.test.ts` — remove UUID tests

- [ ] **Step 1: Check if generateUuid is used outside UUID injection**

Run: `grep -r 'generateUuid' --include='*.ts' --include='*.tsx' | grep -v test | grep -v inject | grep -v uuid.ts`

If no other usages → delete entire `uuid.ts`.
If used (e.g. for other ID generation) → keep only `generateUuid()`.

- [ ] **Step 2: Delete injection files**

Delete `lib/ast/inject-unique-ids.ts`, `lib/ast/inject-unique-ids.test.ts`, `server/routes/injectUniqueIds.ts`.

- [ ] **Step 3: Clean up operations.ts**

Remove `injectUniqueIdsIntoAST`, `findParentElementId`, `getDirectChildIds` and their tests.

- [ ] **Step 4: Clean up traverser.ts**

Remove `findElementByUuid`, `getStaticStringFromAttrValue`, `getUuidFromElement`.

- [ ] **Step 5: Remove route registration**

Find where `injectUniqueIds` route is registered in `server/main.ts` or route index and remove it.

- [ ] **Step 6: Run tests**

Run: `bun run test lib/ast/`

- [ ] **Step 7: Commit**

```
refactor(element-tracing): remove UUID injection pipeline (HYP-268)
```

---

### Task 17: Remove data-uniq-id from client code

**Files to modify:**
- `client/lib/canvas-engine/react/CanvasRenderer.tsx` — remove `addDataUniqIds()`
- `client/lib/dom-utils.ts` — remove `buildElementSelector` and all `[data-uniq-id]` queries
- `client/pages/Editor/utils/mapElementQuery.ts` — delete entire file (replaced by fiber-element-query.ts)

- [ ] **Step 1: Clean up CanvasRenderer.tsx**

Remove the `addDataUniqIds()` function and its call site. Elements no longer need `data-uniq-id` attributes injected at render time.

- [ ] **Step 2: Clean up dom-utils.ts**

Replace `buildElementSelector(elementId, instanceId)` with fiber-based lookup. Functions that need DOM element access should use `ElementTracer.findDOMElement()`.

- [ ] **Step 3: Delete mapElementQuery.ts**

All functions (`buildElementSelector`, `computeItemIndex`, `findElementByItemIndex`, `findElementsForHighlight`) are replaced by `fiber-element-query.ts`.

Update all importers to use `fiber-element-query.ts` instead.

- [ ] **Step 4: Run tests**

Run: `bun run test client/`

- [ ] **Step 5: Commit**

```
refactor(element-tracing): remove data-uniq-id from client code (HYP-268)
```

---

### Task 18: Remove data-uniq-id from extension code

**Files:**
- Modify: `vscode-extension/hypercanvas-preview/src/services/scripts/iframe-interaction.ts` — remove all `data-uniq-id` selectors
- Modify: `vscode-extension/hypercanvas-preview/src/services/AstService.ts` — remove UUID-based methods
- Modify: `vscode-extension/hypercanvas-preview/src/services/StyleReadService.ts` — update element queries
- Modify: `vscode-extension/hypercanvas-preview/src/mcp/tools/ast-tools.ts` — update tool params
- Modify: `vscode-extension/hypercanvas-preview/src/mcp/tools/component-tools.ts`
- Modify: `vscode-extension/hypercanvas-preview/src/mcp/tools/extension-tools.ts`
- Modify: `vscode-extension/hypercanvas-preview/src/bridges/AIBridge.ts`

- [ ] **Step 1: Clean up iframe-interaction.ts**

Remove all `querySelector('[data-uniq-id="..."]')` patterns. Already replaced with fiber in Task 15.

- [ ] **Step 2: Clean up AstService.ts**

Remove `injectUniqueIds` method and all `findElementByUuid` imports/calls. All UUID-to-position lookups replaced in Task 15.

- [ ] **Step 3: Update MCP tools and bridges**

Replace `elementId`/`uniqId` parameters with `nodeRef` in tool schemas. Update `AIBridge` to pass nodeRef.

- [ ] **Step 4: Run extension tests**

Run: `bun run test vscode-extension/`

- [ ] **Step 5: Commit**

```
refactor(element-tracing): remove data-uniq-id from extension code (HYP-268)
```

---

### Task 19: Remove data-uniq-id from shared code

**Files:**
- Modify: `shared/canvas-interaction/types.ts` — clean up docs referencing data-uniq-id
- Modify: `shared/ai-agent.ts` — update if uses UUID
- Modify: `server/services/ai-agent.ts` — update if uses UUID
- Modify: `lib/services/component-parser.ts` — remove data-uniq-id handling
- Modify: `lib/services/tree-adapter.test.ts` — update tests
- Modify: `lib/testing/types.ts` — remove UUID from test types
- Modify: `lib/testing/analyzers/interactive-detector.ts` — remove data-uniq-id detection

- [ ] **Step 1: Search for remaining references**

Run: `grep -r 'data-uniq-id\|uniqId\|dataset\.uniqId\|findElementByUuid\|data_uniq_id' --include='*.ts' --include='*.tsx' client/ shared/ server/ lib/ vscode-extension/`

- [ ] **Step 2: Fix each remaining reference**

- [ ] **Step 3: Run full test suite**

Run: `bun run test`

- [ ] **Step 4: Commit**

```
refactor(element-tracing): final data-uniq-id sweep — remove all remaining references (HYP-268)
```

---

### Task 20: Migration script — strip data-uniq-id from project files

Create a one-time migration script for existing projects that have `data-uniq-id` in their source files.

**Files:**
- Create: `scripts/strip-data-uniq-ids.ts`

- [ ] **Step 1: Write script**

```typescript
#!/usr/bin/env bun
/**
 * Strip data-uniq-id attributes from all JSX/TSX files in a directory.
 * Usage: bun scripts/strip-data-uniq-ids.ts <directory>
 */

import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { parse } from '@babel/parser';
import _traverse from '@babel/traverse';
import _generate from '@babel/generator';
import * as t from '@babel/types';

const traverse = _traverse.default || _traverse;
const generate = _generate.default || _generate;

async function processFile(filePath: string): Promise<boolean> {
  const source = await readFile(filePath, 'utf-8');
  if (!source.includes('data-uniq-id')) return false;

  const ast = parse(source, {
    sourceType: 'module',
    plugins: ['jsx', 'typescript'],
  });

  let modified = false;

  traverse(ast, {
    JSXAttribute(path) {
      if (
        t.isJSXIdentifier(path.node.name) &&
        path.node.name.name === 'data-uniq-id'
      ) {
        path.remove();
        modified = true;
      }
    },
  });

  if (modified) {
    const output = generate(ast, { retainLines: true }, source);
    await writeFile(filePath, output.code);
  }

  return modified;
}

// ... recursive directory walk + main() ...
```

- [ ] **Step 2: Test on sample project**

Run against a test project from `ext-test-projects/`.

- [ ] **Step 3: Commit**

```
chore(element-tracing): add migration script to strip data-uniq-id from projects (HYP-268)
```

---

### ~~Task 20b: Comment anchoring DB migration~~ → DEFERRED

Comment anchoring migration is deferred to spec Phase 2e (`data-comment-id`).
Phase 2 removes `data-uniq-id` but leaves `comments.element_id` column as-is.
Existing UUID-anchored comments will lose their visual anchor until Phase 2e lands.
Create a separate Linear ticket for this.

---

### Task 21: Lint, type-check, full test suite

Final verification pass.

- [ ] **Step 1: Run type checker**

Run: `bunx tsc --noEmit`
Fix all errors.

- [ ] **Step 2: Run linter**

Run: `bun lint`
Fix all warnings.

- [ ] **Step 3: Run unused exports check**

Run: `bunx knip`
Fix unused exports/dependencies.

- [ ] **Step 4: Run full test suite**

Run: `bun run test`
All tests must pass.

- [ ] **Step 5: Final grep sweep**

Run: `grep -r 'data-uniq-id\|findElementByUuid\|injectUniqueIds\|addDataUniqIds\|updateAllChildUuids' --include='*.ts' --include='*.tsx' client/ shared/ server/ lib/ vscode-extension/ | grep -v node_modules | grep -v '.test.'`

Should return zero results (except test fixtures if any).

- [ ] **Step 6: Commit**

```
chore(element-tracing): Phase 2 final verification — lint, types, tests green (HYP-268)
```

---

## Dependency Graph

```
Phase 2a (Client):
  Task 1 (resolveClickLocal + public delegates)  ─┐
  Task 2 (fiber-element-query)                    ─┤
                                                    ├─→ Task 3 (click-handler) ─→ Task 4 (useElementInteraction)
  Task 5 (overlay rendering)                      ─┘         │
  Task 6 (keyboard handler)                       ──────────→ │
  Task 7 (empty containers)                       ──────────→ │
                                                              ↓
                                            Task 8 (IframeCanvas wiring) ─→ Task 9 (async resolution)
                                                              │                    │
                                            Task 9b (useHotkeysSetup) ←────────────┘
                                            Task 9c (overlayMapCondHighlight)
                                            Task 9d (PostMessage StateHub wiring)
                                            Task 9e (selection remapping)

Phase 2b (Server):
  Task 10 (findElementByPosition) ─→ Task 11 (mutation-tracing) ─→ Task 12 (simple routes + batch)
                                                                 ─→ Task 13 (complex routes)
  Task 14 (file change detection)
  Task 14b (initial NodeMap population)
  Task 15 (extension AstService) — depends on Tasks 3, 10

Phase 2c (Removal):
  Task 16 (remove injection)     ─┐
  Task 17 (remove client)        ─┤
  Task 18 (remove extension)     ─┼─→ Task 19 (final sweep) ──→ Task 21 (lint/types/tests verification)
  Task 20 (migration script)     ─┘
  Task 20b (comment DB migration) ── DEFERRED to Phase 2e
```

**Parallel execution opportunities:**
- Tasks 1, 2, 5, 6, 7 can run in parallel (independent client utilities)
- Tasks 9b, 9c, 9d, 9e can run in parallel (independent client updates after Task 8)
- Tasks 10, 14, 14b can run in parallel with Phase 2a
- Task 15 depends on both Task 3 (click-handler changes shared code) and Task 10
- Tasks 16, 17, 18, 20 can run in parallel (independent cleanup)

---

## Known Limitations

1. **React 19 / Next.js not supported in Phase 2** — `_debugSource` removed in React 19.
   `_debugStack` (the replacement) provides V8 Error stacks with **compiled positions** from
   bundler chunks, not source positions. For Next.js + Turbopack this means:
   - Server components (RSC): `_debugStack` has server-side `.next/…/ssr/*.js` paths —
     not accessible from the browser at all. No source location possible without server-side
     source map resolution.
   - Client components: `_next/static/chunks/*.js` frames — source maps available via HTTP
     but require VLQ decoding which is not yet implemented.

   `parseDebugStack` already filters these paths (returns `null`) so there are no errors —
   the inspector simply shows nothing for these elements. Proper fix tracked in spec section
   "Next.js / React 19 Support Constraints" — three candidate approaches documented there.

   **IMPORTANT: `data-uniq-id` is NOT a fallback option for this.** data-uniq-id is being
   removed entirely in Phase 2c. There is no going back. Source map resolution is the only
   valid path forward for Next.js support.

2. **SWC column format unverified** — Vite+SWC and standalone SWC may report different `columnNumber` format. Needs E2E testing with `ext-test-projects/` repos that use SWC.

3. **Board mode instance scoping** — `data-canvas-instance-id` stays on DOM (it's about layout/routing, not element identity). For fiber-based queries scoped to an instance: find the instance container DOM element → get its fiber → walk only that subtree. See Decision D8 for implementation approach. `FrameworkAdapter.findDOMElement()` needs an optional `rootElement` parameter to restrict the walk scope.

4. **No fallback for unsupported builds** — If `_debugSource` / `_debugStack` are missing (production build, unsupported bundler), element selection is completely disabled. Clear error message via `ReactAdapter.detect()` returning false. No UUID-based fallback exists or will be added.

5. **`findDOMElementsBySource` iteration** — The `for (i = 0; i < 1000; i++)` loop to find all elements at a source location works but is wasteful (each call walks the full fiber tree). Post-Phase-2 optimization: add `findAllDOMElements(source): HTMLElement[]` to the adapter for single-pass fiber tree walk.

6. **Concurrent mutations** — Two mutation routes running simultaneously on the same file could race on re-parse. `afterMutation` does read-parse-broadcast sequentially. Consider a per-file mutex if this becomes a real issue (unlikely in practice — mutations are user-initiated).
