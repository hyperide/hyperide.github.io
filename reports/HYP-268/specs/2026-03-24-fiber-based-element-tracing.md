# Fiber-Based Element Tracing — Replace data-uniq-id

**Linear:** HYP-268
**Status:** Draft
**Date:** 2026-03-24

## Problem

`data-uniq-id` is the sole bridge between DOM elements and AST nodes. It has fundamental flaws:

1. **Pollutes user source code** — every JSX element gets `data-uniq-id="uuid"` written to disk,
   cluttering diffs and confusing developers
2. **Library components are invisible** — can't inject attributes into `node_modules`;
   `<Button>` from shadcn renders `<button>` without `data-uniq-id`
3. **Prop pass-through required** — components that don't spread props to DOM lose the attribute;
   `<Card data-uniq-id="...">` renders `<div>` without it if Card doesn't do `{...props}`
4. **Cross-file blind spot** — `<Card>` from `Card.tsx` used in `Page.tsx`:
   clicks in Elements Tree and Canvas don't work because IDs only exist in the file being edited
5. **Deduplication overhead** — copy-paste, AI generation, manual editing create duplicate IDs
   (HYP-184), requiring constant dedup passes
6. **Injection lifecycle complexity** — `injectUniqueIdsIntoAST()` + file write-back + re-parse +
   `addDataUniqIds()` runtime heuristic matching — multiple fragile sync points

## Solution

Replace `data-uniq-id` with **React Fiber-based tracing** on the client and **AST position maps**
synced via WebSocket on the server.

Every DOM node rendered by React has a `__reactFiber$` internal property pointing to its Fiber node.
In dev mode, Fiber nodes carry `_debugSource: { fileName, lineNumber, columnNumber }` — the exact
source location where the JSX was written. This gives us a direct DOM→source mapping without
injecting anything into user code.

### Why this works without losses

| Concern | data-uniq-id | Fiber + WS position sync |
|---------|-------------|--------------------------|
| DOM → source mapping | Read `dataset.uniqId` from DOM | Read `_debugSource` from fiber |
| Source → DOM mapping | `querySelector('[data-uniq-id]')` | Match source position against node map |
| Server mutations | `findElementByUuid(ast, uuid)` | `findElementAtPosition(ast, line, col)` (already exists) |
| Survives file edits | UUID persists in source | Server re-parses → pushes updated positions via WS |
| Library components | ❌ Can't inject | ✅ Fiber exists on every DOM node |
| Cross-file components | ❌ Single-file only | ✅ Fiber chain: `div(Card.tsx:15) → Card(Page.tsx:7)` |
| Map items (.map()) | Same UUID + itemIndex | Same source position + fiber sibling index |
| Multi-instance (board) | `data-canvas-instance-id` scoping | Fiber tree per instance root |

### Framework support

`_debugSource` is React-specific. The architecture uses an **adapter pattern** so Vue, Svelte,
Solid.js can be supported later via their own source-mapping mechanisms.

If `_debugSource` is absent (production build, missing babel plugin), the project is **unsupported** —
no fallback, clear error message. This is acceptable because HyperCanvas always works with dev builds.

### Supported build configurations

`_debugSource` is added by `@babel/plugin-transform-react-jsx-source` (included in `@babel/preset-react`
in dev mode) or equivalent SWC transform. Verified/expected behavior per bundler:

| Build tool | Plugin | `_debugSource` format | `columnNumber` reliable? |
|-----------|--------|----------------------|--------------------------|
| Vite + `@vitejs/plugin-react` (Babel) | `@babel/preset-react` | `{ fileName, lineNumber, columnNumber }` | Yes, **1-based** (verified) |
| Vite + `@vitejs/plugin-react-swc` | SWC built-in | `{ fileName, lineNumber, columnNumber }` | Verify in Phase 2 |
| Next.js (SWC compiler) | SWC built-in | `{ fileName, lineNumber, columnNumber }` | Verify in Phase 2 |
| CRA (react-scripts) | `@babel/preset-react` | `{ fileName, lineNumber, columnNumber }` | Yes |
| Custom Webpack + Babel | Must include preset-react | Same as Babel | Depends on config |

**Phase 1 deliverable:** `ReactAdapter.detect()` must validate both presence AND format of
`_debugSource`. If `columnNumber` is missing or 0, fall back to line-only matching
(accept ambiguity for elements on the same line — rare in practice).

### Column convention

All `SourceLocation.column` values are **0-based** throughout the system. This matches
Babel AST (`node.loc.start.column`). **`_debugSource.columnNumber` is 1-based** (verified
empirically on Vite+Babel) — `ReactAdapter.debugSourceToLocation()` subtracts 1.

Conversion points:

- VS Code extension: `position.character` is 0-based — no conversion needed
- VS Code API `Position` constructor: 0-based — no conversion needed
- Display to user: convert to 1-based

### VS Code Extension impact

The extension has its own `AstService` (local Babel parsing), communicates with the preview
iframe via `postMessage` (not WS), and uses `SyncPositionService` for bidirectional
code↔cursor sync. There is no server WS channel.

**Strategy:** The extension embeds a **local `NodeMapService`** (it already has `AstService`
and Babel — `NodeMapService` is a thin wrapper that builds the position map from the AST).
The `ElementTracer` and `ReactAdapter` run inside the iframe via the injected interaction script.
Communication uses `PostMessageTracingTransport` (see [TracingTransport](#tracingtransport))
instead of WS — postMessage integrates with StateHub for multi-panel broadcast, WS would add
a second transport layer with no benefit.

Extension-specific changes per phase:

- **Phase 1:** `NodeMapService` implemented as a shared module (works in both server and extension).
  `PostMessageTracingTransport` created. Fiber utils created as client modules.
- **Phase 2:** Switch directly — no dual mode (no users to migrate).
  `iframe-interaction.ts` sends fiber-based source location via postMessage.
  `AstService` methods switch from `{ uuid }` to `{ loc: SourceLocation }`.
  `SyncPositionService` uses source positions natively (it already deals in line:column).
  Remove `data-uniq-id` code from `AstService` and `iframe-interaction.ts`.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Client (iframe)                         │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐   ┌─────────────┐     │
│  │ FrameworkAdapter │←──│ ReactAdapter │   │ (future)    │     │
│  │   (interface)    │    │  - fiber walk │   │ VueAdapter  │     │
│  │                  │    │  - _debugSource│  │ SvelteAdapter│     │
│  └───────┬──────┘    └──────────────┘   └─────────────┘     │
│          │                                                   │
│  ┌───────▼──────────────────────────────────────────────┐    │
│  │              ElementTracer (client core)               │    │
│  │  - DOM click → SourceLocation via adapter             │    │
│  │  - Matches SourceLocation against NodeMap             │    │
│  │  - Manages selection state (nodeRef + itemIndex)      │    │
│  │  - Hover/highlight overlays                           │    │
│  └───────┬───────────────────────────────────────────────┘    │
│          │                                                    │
│  ┌───────▼───────────────────────────────────────────────┐    │
│  │           TracingTransport (interface)                  │    │
│  │  SaaS: WSTracingTransport (WS channel)                 │    │
│  │  Extension: PostMessageTracingTransport (postMessage)  │    │
│  └───────┬────────────────────────────────────────────────┘    │
└──────────┼────────────────────────────────────────────────────┘
           │
┌──────────▼────────────────────────────────────────────────────┐
│            Host (Server or Extension Host)                     │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐   │
│  │                NodeMapService (shared module)           │   │
│  │  - Parses file → builds NodeMap (nodeRef → position)   │   │
│  │  - Pushes NodeMap to client via transport on change     │   │
│  │  - Tracks nodeRef stability across re-parses           │   │
│  │  - fs watcher triggers re-parse on external edits      │   │
│  └──────────┬─────────────────────────────────────────────┘   │
│             │                                                 │
│  ┌──────────▼─────────────────────────────────────────────┐   │
│  │              Mutation Routes (REST / AstService)         │   │
│  │  SaaS: REST routes, { nodeRef } input                   │   │
│  │  Extension: AstService methods, { loc } input           │   │
│  │  - NodeMapService resolves nodeRef → AST position       │   │
│  │  - After mutation: re-parse, push updated NodeMap       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                               │
│  Extension-only:                                              │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  StateHub — broadcasts selection to all 5 webview panels│  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

## Core Concepts

### SourceLocation

The universal element identifier — where the JSX element is written in source:

```typescript
interface SourceLocation {
  fileName: string;     // absolute or project-relative path
  line: number;         // 1-based
  column: number;       // 0-based
}
```

### NodeRef

Server-assigned session-scoped identifier for an AST node. Stable within a single parse
of a file, re-mapped on re-parse via structural matching.

**Format:** `"<filePath>:<traversalIndex>"` — opaque string, clients must not parse it.

**Stability algorithm (re-parse mapping):**

When a file is re-parsed, `NodeMapService` must map old nodeRefs to new ones. Two strategies
depending on who caused the re-parse:

**Server-initiated mutation** (we know exactly what changed):
- Server holds the Babel `NodePath` of the mutated node during the mutation
- After `writeAST()` + re-parse, server re-traverses and finds the same node
  by tracking it through the mutation (it was just modified, we know its new position)
- All other nodes: matched by composite key `(tag, parentTag, indexAmongSameTagSiblings)`
- Example: `<Button>` is child-0-of-type-Button under `<div>` → this identity survives
  sibling insertions (unlike raw child index which breaks)

**External edit** (fs watcher, unknown changes):
- Build composite keys for all nodes in both old and new AST
- Key: `(tag, depth, parentTag, indexAmongSameTagSiblings)` — more resilient than raw path
- Match old keys to new keys
- Unmatched nodes: nodeRef invalidated, client selection cleared if it pointed there
- This handles the common case (insert/delete sibling) correctly:
  `<Button>` stays `(Button, 2, div, 0)` even when a `<Header>` is inserted above it

```typescript
interface NodeMapEntry {
  nodeRef: string;
  tag: string;               // "div", "Card", "Fragment"
  loc: SourceLocation;
  endLoc: SourceLocation;
  parentRef: string | null;
  children: string[];         // child nodeRefs
  isComponent: boolean;       // true for user components, false for host elements
  componentName?: string;     // "Card", "Button" — for component elements
}

type NodeMap = Map<string, NodeMapEntry>;
```

### FrameworkAdapter

```typescript
interface FrameworkAdapter {
  readonly name: string;  // "react", "vue", "svelte"

  /** Check if this adapter can handle the current page */
  detect(doc: Document): boolean;

  /** Get source location from a DOM element */
  getSourceLocation(element: HTMLElement): SourceLocation | null;

  /** Get component ancestry chain from DOM element to root */
  getComponentChain(element: HTMLElement): ComponentInfo[];

  /** Get item index for list-rendered elements (e.g. .map()) */
  getItemIndex(element: HTMLElement): number;

  /** Walk entire component tree from a root element */
  walkComponentTree(rootElement: HTMLElement): ComponentTreeNode[];

  /** Find DOM element by source location (reverse mapping for highlights/overlays) */
  findDOMElement(source: SourceLocation, itemIndex: number): HTMLElement | null;
}

interface ComponentInfo {
  name: string;
  source: SourceLocation | null;  // where this component is USED (not defined)
  definitionSource?: SourceLocation; // where component function is DEFINED
  /** Serializable prop summary — values truncated for transport */
  props: Record<string, string>;
  isLibrary: boolean;  // true if defined in node_modules
}

interface ComponentTreeNode {
  name: string;
  source: SourceLocation | null;
  children: ComponentTreeNode[];
  domElement: HTMLElement | null;  // null for non-host components
  fiberTag?: number;
}
```

### ReactAdapter implementation

Uses fiber internals from the Claude Desktop research demo.

**Edge case: `React.memo` and `React.forwardRef`** create wrapper fibers (tags 14, 15, 11).
When walking up the fiber tree to find `_debugSource`, the wrapper may or may not carry it.
The adapter must check fiber tags and unwrap: for memo → `fiber.type.type`,
for forwardRef → `fiber.type.render`.

```typescript
class ReactAdapter implements FrameworkAdapter {
  readonly name = "react";

  detect(doc: Document): boolean {
    const root = this.findReactRoot(doc);
    if (!root) return false;
    // Validate _debugSource format (not just presence)
    const fiber = this.getFiberFromDOM(root);
    const source = this.findNearestDebugSource(fiber);
    if (!source) return false;
    return typeof source.fileName === "string" && typeof source.lineNumber === "number";
  }

  getSourceLocation(element: HTMLElement): SourceLocation | null {
    const fiber = this.getFiberFromDOM(element);
    if (!fiber) return null;
    const source = this.findNearestDebugSource(fiber);
    if (!source) return null;
    return {
      fileName: source.fileName,
      line: source.lineNumber,
      column: source.columnNumber ?? 0,
    };
  }

  getComponentChain(element: HTMLElement): ComponentInfo[] {
    const fiber = this.getFiberFromDOM(element);
    if (!fiber) return [];
    return traceToRoot(fiber)
      .filter(isUserComponent)
      .map(fiberToComponentInfo);
  }

  getItemIndex(element: HTMLElement): number {
    const fiber = this.getFiberFromDOM(element);
    if (!fiber) return 0;
    // Count preceding siblings with same source location
    let index = 0;
    let sibling = fiber.return?.child;
    const mySource = this.findNearestDebugSource(fiber);
    while (sibling && sibling !== fiber) {
      if (sameSourceLocation(this.findNearestDebugSource(sibling), mySource)) {
        index++;
      }
      sibling = sibling.sibling;
    }
    return index;
  }

  findDOMElement(source: SourceLocation, itemIndex: number): HTMLElement | null {
    // Walk the fiber tree from root, find all fibers matching source location,
    // return the stateNode of the one at itemIndex
    const root = this.findReactRoot(document);
    if (!root) return null;
    const rootFiber = this.getFiberFromDOM(root);
    const matches: HTMLElement[] = [];
    this.walkFibers(rootFiber, (fiber) => {
      const ds = fiber._debugSource;
      if (ds && ds.fileName === source.fileName &&
          ds.lineNumber === source.line &&
          (source.column === 0 || (ds.columnNumber ?? 0) === source.column)) {
        // Find nearest host fiber (actual DOM element)
        const host = this.findHostFiber(fiber);
        if (host?.stateNode instanceof HTMLElement) {
          matches.push(host.stateNode);
        }
      }
    });
    return matches[itemIndex] ?? null;
  }

  private findNearestDebugSource(fiber: Fiber | null): DebugSource | null {
    let current = fiber;
    while (current) {
      if (current._debugSource) return current._debugSource;
      // Unwrap memo/forwardRef wrappers
      if ((current.tag === 14 || current.tag === 15) && current.type?.type?._debugSource) {
        return current.type.type._debugSource;
      }
      current = current.return;
    }
    return null;
  }

  private getFiberFromDOM(el: HTMLElement): Fiber | null {
    const key = Object.keys(el).find(
      k => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$")
    );
    return key ? (el as any)[key] : null;
  }
}
```

## TracingTransport

`ElementTracer` communicates with `NodeMapService` through a transport abstraction.
Client-side code (fiber walking, source location extraction, selection state) is 100% shared;
only the "last mile" differs per platform.

```typescript
/** Platform-agnostic transport between ElementTracer (client) and NodeMapService (host) */
interface TracingTransport {
  /** Send a message from client to host (resolve-element, etc.) */
  send(msg: TracingClientMessage): void;

  /** Subscribe to messages from host to client (node-map-update, etc.) */
  onMessage(handler: (msg: TracingServerMessage) => void): () => void;

  /** Connection state — client disables selection when disconnected */
  readonly connected: boolean;
  onConnectionChange(handler: (connected: boolean) => void): () => void;
}

type TracingClientMessage = ResolveElement;
type TracingServerMessage = NodeMapUpdate | NodeMapInvalidate | ResolveElementResponse;
```

### Implementations

| Platform | Class | Transport | Why |
|----------|-------|-----------|-----|
| SaaS | `WSTracingTransport` | Dedicated WS channel `element-tracing` | Direct browser↔server, lowest latency |
| VS Code | `PostMessageTracingTransport` | iframe → postMessage → webview → extension host | Multi-panel broadcast via StateHub for free; WS adds second transport with zero benefit (see below) |

**Why not WS in the extension:**

1. **Multi-panel broadcast** — selection state must reach preview (overlays), left panel (tree
   highlight), right panel (props/styles). StateHub broadcasts postMessage to all 5 panels.
   WS would only reach the iframe, requiring a second relay via postMessage anyway.
2. **Negligible latency delta** — extra postMessage hop through webview adds <1ms;
   AST parsing is the bottleneck, not transport.
3. **Infrastructure cost** — WS in extension host needs port allocation, token auth, CSP
   config, reconnect logic. postMessage is guaranteed by VS Code — zero setup.
4. **iframe-interaction.ts is already platform-specific** — injected differently in SaaS
   (proxy-path-bridge) vs extension (PreviewProxy IIFE). Transport divergence is natural.

## Tracing Protocol

### Channel: `element-tracing` (SaaS: WS channel, Extension: postMessage type prefix)

All messages use the types below regardless of transport.

### Server → Client messages

```typescript
/** Pushed after every file parse (initial load, mutation, external edit) */
interface NodeMapUpdate {
  type: "node-map-update";
  filePath: string;
  fileHash: string;       // detect stale maps
  version: number;        // monotonic counter per file, for ordering
  nodes: NodeMapEntry[];
  /** Old nodeRef → new nodeRef mapping for selection persistence */
  refMapping?: Record<string, string>;
  /** If a mutation just occurred, which new nodeRef was the target */
  mutatedNodeRef?: string;
}

/** Pushed when a file is deleted or renamed */
interface NodeMapInvalidate {
  type: "node-map-invalidate";
  filePath: string;
}
```

### Client → Server messages

```typescript
/** Client resolves a DOM click to a source location, asks server for nodeRef */
interface ResolveElement {
  type: "resolve-element";
  requestId: string;
  source: SourceLocation;
  itemIndex: number;
}

/** Server responds with the matched nodeRef */
interface ResolveElementResponse {
  type: "resolve-element-response";
  requestId: string;
  nodeRef: string | null;
  entry: NodeMapEntry | null;
}
```

### REST mutation routes

All mutation routes change input from `{ selectedId/elementId }` to `{ nodeRef }`.
Server resolves `nodeRef` → AST position via `NodeMapService`.

Response extended with:

```typescript
interface MutationResponse {
  success: boolean;
  /** The nodeRef of the mutated element in the NEW parse (post-mutation) */
  nodeRef?: string;
  /** Updated source location (for client to re-select) */
  newLoc?: SourceLocation;
  error?: string;
}
```

## Selection Persistence

### Scenario 1: User mutates selected element (e.g., update props)

1. Client has `selectedNodeRef = "abc123"` + `selectedLoc = { file, line: 10, col: 4 }`
2. Client sends mutation request with `{ nodeRef: "abc123" }`
3. Server finds AST node by nodeRef → position lookup
4. Server mutates AST → writes file → re-parses
5. Server finds the mutated node in new AST (it just modified it, knows the path)
6. Server returns `{ success: true, nodeRef: "def456", newLoc: { line: 12, col: 4 } }`
7. Server pushes `NodeMapUpdate` with full new map
8. Client updates selection: `selectedNodeRef = "def456"`, `selectedLoc = newLoc`
9. HMR reloads → fiber tree updates → client re-highlights at new position

### Scenario 2: User mutates a DIFFERENT element (positions shift)

1. Client has `selectedNodeRef = "abc123"` at line 10
2. User inserts element at line 5 → everything below shifts
3. Server mutates → re-parses → runs stability algorithm (server-initiated)
4. Server pushes `NodeMapUpdate` with `refMapping: { "abc123": "xyz789" }`
5. Client updates: `selectedNodeRef = "xyz789"`, reads new position from map
6. HMR reloads → client re-highlights at new position

### Scenario 3: External edit (VS Code, git pull, AI)

1. fs watcher detects file change
2. Server re-parses file → runs stability algorithm (external edit mode)
3. Server pushes `NodeMapUpdate` with `refMapping` (best-effort composite key matching)
4. Client receives new map:
   - If old `selectedNodeRef` is in `refMapping` → update selection to new ref
   - If not in mapping → clear selection (structural identity lost)
5. HMR reloads preview → fiber tree updates

### Scenario 4: Undo/Redo

1. Undo restores previous file content → exact positions restored
2. Server re-parses → NodeMap matches the undo state
3. Selection persistence works via source location match (positions revert to known state)

### Scenario 5: Map items (.map() rendering)

1. User clicks 3rd `<Card>` in a list of 5
2. Fiber: all 5 Cards share same `_debugSource` → same source location
3. `ReactAdapter.getItemIndex()` returns 2 (0-based)
4. Client sends `{ source: { file, line, col }, itemIndex: 2 }`
5. Server nodeRef points to the `.map()` callback body element
6. Selection state: `{ nodeRef, itemIndex: 2 }`
7. After mutation: server returns updated nodeRef, client preserves itemIndex

## Sync State Machine

Client maintains a sync state to handle the race between HMR and NodeMap updates:

```
         ┌─────────┐
         │ synced   │ ← element selection enabled
         └────┬─────┘
     file changed (mutation or external)
              │
         ┌────▼──────────┐
         │ awaiting-both  │ ← selection DISABLED (brief loading indicator)
         └──┬──────┬──────┘
   map      │      │  HMR
   arrives  │      │  completes
       ┌────▼───┐ ┌▼──────────┐
       │awaiting│ │ awaiting   │
       │  -hmr  │ │   -map     │
       └───┬────┘ └──┬────────┘
           │  HMR    │  map
           │completes│  arrives
           └────┬────┘
           ┌────▼─────┐
           │  synced   │ ← re-map selection, enable
           └───────────┘
```

When not in `synced` state, clicks are queued (not dropped) and replayed once synced.
Timeout: if either event doesn't arrive within 3s, force-sync with whatever is available.

## Migration Plan

### Phase 1: Infrastructure (no behavior change) ✅

- [x] `FrameworkAdapter` interface + `ReactAdapter` in `client/lib/element-tracing/`
- [x] `NodeMapService` on server (shared module, works in both server and extension)
- [x] `TracingTransport` interface + `WSTracingTransport` (SaaS) + `PostMessageTracingTransport` (extension)
- [x] WS channel `element-tracing` for `node-map-update` messages (SaaS transport)
- [x] Extension: `PostMessageTracingTransport` created (StateHub wiring deferred to Phase 2)
- [x] Fiber utility functions in `client/lib/element-tracing/fiber-utils.ts` (client-only, not shared/)
- [x] `findDOMElement()` reverse mapping implementation
- [x] Sync state machine on client (transport-agnostic, uses `TracingTransport.onMessage`)
- [x] Integration tests: fiber → source location → node map match
- [ ] Validate `_debugSource` format for Vite+Babel, Vite+SWC, Next.js

**Phase 1 additions (from CF review):**

- [ ] **CF-1: 3-tier stability cascade** — replace single composite key in `stability.ts`
  with tiered matching (structural key → ancestry path → position proximity) + content
  fingerprint. Must land before Phase 2 makes stability user-facing. See CF-1 for design.
- [ ] **CF-3: `FiberSourceIndex`** — reverse index `Map<sourceKey, HTMLElement[]>` rebuilt
  on `onCommitFiberRoot`. Replaces O(N) `walkFibers` in `findDOMElement()` with O(1) lookup.
  See CF-3 for design.
- [ ] **CF-5: `getItemIndex` WeakMap cache** — `WeakMap<Fiber, Map<sourceKey, Map<Fiber, number>>>`.
  O(1) amortized lookup, auto-invalidation via GC. See CF-5.

### Phase 2: Switch to fiber + remove data-uniq-id

No dual mode — no existing users, switch directly.

**2a. Wire fiber into click handler**

- [ ] Modify `shared/canvas-interaction/click-handler.ts`: replace `closest('[data-uniq-id]')` with `ReactAdapter.getSourceLocation()` + `ElementTracer.resolveClick()`
- [ ] Extension: `iframe-interaction.ts` sends fiber source location via postMessage
- [ ] Wire `PostMessageTracingTransport` through PanelRouter + StateHub
- [ ] Update `useElementInteraction.ts` — replace `dataset.uniqId` reads with fiber resolution
- [ ] Update `useHotkeysSetup.ts` — replace `parent.dataset.uniqId` walk
- [ ] Update `useOverlayMapCondHighlightComponents.ts` — replace querySelector patterns
- [ ] Update `client/pages/Editor/utils/mapElementQuery.ts` — replace querySelectorAll patterns
- [ ] Update `client/lib/dom-utils.ts` — replace `querySelector('[data-uniq-id]')` helpers

**2b. Switch mutation routes to nodeRef**

All mutation routes: replace `selectedId`/`elementId` (UUID) input with `nodeRef`.
Server resolves `nodeRef` → AST position via `NodeMapService`.
Extend responses with `MutationResponse` (nodeRef + newLoc).

**Complete mutation route inventory:**

| Route file | Input field | Notes |
|-----------|------------|-------|
| `updateComponentStyles.ts` | `selectedId` | Most frequent operation |
| `updateComponentProps.ts` | `selectedId` | |
| `updateComponentPropsBatch.ts` | `selectedId` | |
| `deleteElement.ts` | `elementId` | Clears selection after |
| `deleteElements.ts` | `elementId` (array) | Batch delete |
| `duplicateElement.ts` | `elementId` | Returns new element ref |
| `renameComponent.ts` | `selectedId` | |
| `wrapElement.ts` | `elementId` | Manual UUID matching |
| `editMap.ts` | `selectedId` | Manual data-uniq-id matching |
| `editCondition.ts` | `selectedId` | |
| `insertElement.ts` | parent UUID | Parent/sibling targeting |
| `pasteElement.ts` | parent UUID | Parent/sibling targeting |
| `copyElementTsx.ts` | `elementId` | Read-only (copy to clipboard) |
| `updateElementText.ts` | `selectedId` | |

**Read-only routes that also use UUID-based identification:**

| Route file | Input field | Notes |
|-----------|------------|-------|
| `getElementLocation.ts` | `uniqId` query param | Returns source position by UUID |
| `findElementAtPosition.ts` | returns `uniqId` | Searches by `data-uniq-id` |
| `comments.ts` | `elementId` | Switches to `data-comment-id` anchoring (see Phase 2e) |
| `ide.ts` | `uniqId` (internal) | IDE integration (open-in-editor, sync cursor) |

Extension `AstService` re-implements several of these locally — must be updated in parallel.

**2c. Remove data-uniq-id**

- [ ] Remove `injectUniqueIdsIntoAST()` and `injectIdsIntoSource()` from `lib/ast/`
- [ ] Remove `addDataUniqIds()` runtime heuristic in `CanvasRenderer.tsx`
- [ ] Remove `server/routes/injectUniqueIds.ts` route
- [ ] Remove UUID generation/management from `lib/ast/uuid.ts`
- [ ] Remove `findElementByUuid()` from `lib/ast/traverser.ts`
- [ ] Remove `closest('[data-uniq-id]')` from `shared/canvas-interaction/click-handler.ts`
- [ ] Remove `dataset.uniqId` reads from `shared/canvas-interaction/empty-container-placeholders.ts`
- [ ] Extension: remove UUID code from `AstService`, `iframe-interaction.ts`
- [ ] Migration script: strip `data-uniq-id` attributes from existing project files
- [ ] Update all tests (lib/ast, server/routes, client interaction)
- [ ] Final grep sweep: `grep -r 'data-uniq-id\|uniqId\|dataset.uniqId' client/ shared/ server/ lib/`
  to catch any remaining references (~25 client files expected)

**2d. React 19 `_debugStack` support**

React 19 removed `_debugSource` entirely. `ReactAdapter` must support both React 18
(`_debugSource`) and React 19 (`_debugStack` Error object parsing). See CF-7 for details.

- [ ] Add `_debugStack: Error | null` to `Fiber` interface
- [ ] Implement `parseDebugStack()` — parse V8 Error stack string, extract URL + line + col
- [ ] Add `urlToSourcePath()` — strip dev server origin from stack URLs
- [ ] Update `ReactAdapter.detect()` to recognize React 19 fibers (no `_debugSource`,
  has `_debugStack`)
- [ ] Dual-path in `getSourceLocation()`: `_debugSource` (React 18) → `_debugStack` (React 19)
- [ ] Update `getItemIndex()` and `findDOMElement()` for React 19 fibers
- [ ] Update `findNearestDebugSource()` → `findNearestDebugInfo()` (try `_debugSource`
  then `_debugStack`)
- [ ] Test with React 19 project from `ext-test-projects/` (e.g., `nextjs-tw-sample`)
- [ ] Verify `_debugStack` rate limit (`ownerStackLimit`) — if hit, document limitation

**Known limitations (React 19):**
- Stack URLs in Next.js/webpack are chunk URLs — need source map resolution (deferred,
  Vite projects work immediately)
- Column accuracy: `jsxDEV()` call column may differ from original JSX column
- `_debugStack` rate limit may cause missing source info on some fibers

**2e. `data-comment-id` — persistent comment anchoring**

Replace UUID-based comment anchoring with `data-comment-id` attribute on commented elements.
See CF-2 for full design.

- [ ] Add `data-comment-id` to AST mutator as protected attribute (never removed during
  mutations except explicit comment deletion)
- [ ] On comment create: AST mutation adds `data-comment-id={commentId}` to target element
- [ ] On comment delete: AST mutation removes `data-comment-id` attribute
- [ ] Migrate `comments.ts` routes from `elementId` (UUID) to `commentId` (data-comment-id)
- [ ] DB migration: `comments.comment_id` as UUID, drop old `element_id` column if present
- [ ] Orphan detection: on file parse, find `data-comment-id` values not in DB → clean up;
  find DB comments whose `comment_id` not in any parsed file → mark orphaned
- [ ] Extension: show inline decorations (gutter icons) on lines with `data-comment-id`
- [ ] Extension: CodeLens or hover to show comment text, click opens comments panel

## Bonus Features Unlocked by Fiber

These are NOT in scope for initial implementation but become possible:

1. **Cross-file component navigation** — click rendered `<Card>` → jump to `Card.tsx` definition,
   or jump to `<Card>` usage in `Page.tsx`. Fiber chain provides both.

2. **Library component inspection** — see props passed to `<Button>` from shadcn,
   even though it's from `node_modules`. Fiber has `memoizedProps`.

3. **Component tree panel** — real component hierarchy (not just JSX elements),
   including Providers, Memo wrappers, Suspense boundaries.

4. **Re-render highlighting** — `onCommitFiberRoot` hook tells us exactly which
   components re-rendered. Highlight them in real-time on canvas.

5. **Performance profiling** — `actualDuration` on fiber nodes shows render cost.
   Show slow components with heat-map overlay.

6. **State/hooks inspection** — `memoizedState` linked list gives hook values.
   Inspector panel can show `useState` values, `useEffect` deps.

7. **Prop change tracking** — `fiber.alternate` comparison shows exactly which
   props changed between renders. Surface this in a "Changes" panel.

## Performance

**Target:** DOM click → nodeRef resolution < 5ms.

Current `data-uniq-id` is O(1) via `dataset.uniqId`. New system:
- Fiber walk to `_debugSource`: O(depth), typically 5-15 nodes → < 1ms
- NodeMap lookup by `fileName:line:col`: `Map<string, NodeMapEntry>` keyed by
  `${fileName}:${line}:${col}` → O(1) lookup

NodeMap payload: ~100 bytes per node × 1000 nodes = ~100KB. With gzip: ~10KB.
Start with full maps + gzip. Add delta updates only if profiling shows issues.

## Container Path Mapping

**Problem:** `_debugSource.fileName` contains the path as seen by the build tool inside the
Docker container: `/app/src/Card.tsx`. The server runs on the host and knows the project at
`/Users/ultra/work/project/src/Card.tsx`. The proxy rewrites URLs but `_debugSource` is
embedded at compile time — it can't be rewritten by the proxy.

**Solution:** `NodeMapService` maintains a `containerPrefix → hostPrefix` mapping, derived from
the project configuration (container mount path is already known from the Docker setup).
`ReactAdapter` sends raw `_debugSource.fileName`; the server normalizes it before NodeMap lookup.

Normalization rules:
1. If `fileName` starts with container prefix (`/app/`) → replace with host project path
2. If `fileName` is already a host path → use as-is (local dev without Docker)
3. If `fileName` is relative → resolve against project root

This mapping is configured once per project (container mount path is in project settings)
and applied transparently in `NodeMapService.resolveSourceLocation()`.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `_debugSource` absent | No tracing | Detect in `ReactAdapter.detect()` + clear error; dev mode only |
| `_debugSource` format differs (SWC vs Babel) | Wrong positions | Validate format in Phase 1; adapter normalizes field names |
| `columnNumber` missing (0) | Can't distinguish elements on same line | Accept ambiguity for single-line JSX; rare in practice |
| Container paths in `_debugSource.fileName` | NodeMap lookup miss | Path mapping layer in NodeMapService (see above) |
| **React 19 removed `_debugSource`** | No tracing on React 19 | React 19 replaces `_debugSource` with `_debugOwner` (different structure: `{ name, env, stack, debugStack }` — no fileName/lineNumber). **ReactAdapter must support both**: `_debugSource` (React 18) and `_debugOwner.stack` frame parsing (React 19). See verification results below. |
| Fiber internals change in future React | Adapter breaks | Adapter abstraction isolates; `__reactFiber$` stable since React 16 |
| memo/forwardRef wrapper fibers | `_debugSource` on wrong node | `findNearestDebugSource()` unwraps by checking fiber tags |
| WS disconnect during mutation | Stale node map | Client re-requests full map on reconnect; mutations work via REST |
| External edit between click and mutation | nodeRef stale | Server validates nodeRef version; returns error if stale, client retries |
| Race: HMR vs WS map timing | DOM/map mismatch | Sync state machine (see above); queue clicks, replay when synced |
| Large files (1000+ nodes) | Map payload size | Gzip; delta updates if needed (deferred) |
| Pre-hydration clicks (SSR) | No fiber yet | Guard: disable selection until `ReactAdapter.detect()` returns true |
| `dangerouslySetInnerHTML` content | No fiber | Known limitation — static HTML without React reconciler is not selectable |
| Portals | DOM/fiber tree mismatch | Overlay positioning uses DOM coords (getBoundingClientRect), not fiber tree |

## Out of Scope

- Vue/Svelte/Solid adapters (architecture supports them, implementation deferred)
- Production build support (dev mode only)
- Source maps for minified code
- Fiber-based editing (direct fiber manipulation instead of AST)
- `onCommit` / re-render tracking (bonus feature, add to adapter when needed — YAGNI)
- React Server Components (RSC) — layout/data-fetching wrappers, not visual components;
  interactive elements are always client components with fibers

## Verification Results (Phase 1)

### Vite + Babel (React 18) — `test-repo` via HyperCanvas preview

- `__reactFiber$<hash>` present on all DOM elements
- `_debugSource` present on all fibers (host + function components)
- Format: `{ fileName: string, lineNumber: number, columnNumber: number }`
- `fileName`: container path (`/app/src/components/Card.tsx`)
- `lineNumber`: 1-based (matches Babel AST `node.loc.start.line`)
- **`columnNumber`: 1-based** (NOT 0-based as originally assumed in spec)
  - Babel AST `node.loc.start.column` is 0-based → **ReactAdapter subtracts 1**
- Fix applied: `ReactAdapter.debugSourceToLocation()` does `columnNumber - 1`

### Next.js 16 + SWC (React 19.1) — `nextjs-tw-sample`

- `__reactFiber$<hash>` present on all DOM elements
- **`_debugSource` is NULL on all fibers** — React 19 removed it entirely
- `_debugOwner` present — completely different structure:
  ```
  {
    name: "Home",           // component name (no file path)
    env: "Server",          // RSC environment
    stack: [[...]],         // call stack frames (array of tuples)
    debugStack: ...,        // Error stack object
    debugTask: { run }      // async task tracking
  }
  ```
- `_debugOwner.stack` contains call frame tuples — may be parseable for source location
  but format is undocumented and likely differs from `_debugSource`

**Impact:** ReactAdapter must be updated to support React 19's `_debugOwner.stack`
before Next.js / React 19 projects work. Current implementation only works with React 18.
This is a Phase 2 task — requires research into React 19 debug info format.

### Next.js / React 19 Support Constraints (2026-03-27)

Research during Phase 1 hardening revealed fundamental limitations for Next.js + React 19
that make source location resolution non-trivial:

**Root cause:** React 19's `_debugStack` captures an `Error` at the JSX call site
(`jsxDEV()`). The stack frames contain **compiled positions**, not source positions.
For Next.js + Turbopack, these are:
- Client components: `_next/static/chunks/<hash>.js:1:N` — bundle chunk, source map available via HTTP
- Server components (RSC): `Server/file:///abs-path/.next/dev/server/chunks/ssr/<hash>.js` —
  server-side path, not browser-accessible at all

**What was fixed (Phase 1):** `parseDebugStack` now correctly filters all these paths via
`REACT_INTERNAL_PATTERNS` — no more "Failed to open file" errors. The function returns `null`
for these frames, which silently disables selection for the element.

**What is NOT fixed:** When all stack frames are compiled chunks, `parseDebugStack` returns `null`
and the inspector shows nothing. This affects:
- All Next.js Server Components (RSC with `env: "Server"`)
- Next.js client components when Turbopack doesn't provide per-file source frames

**Explicitly rejected approach: `data-uniq-id` fallback.** Falling back to UUID-based DOM
attributes when fiber resolution fails is not an option — data-uniq-id is being removed entirely
(Phase 2c). There is no path back.

**Candidate approaches for Next.js source resolution (future Phase 2d or separate ticket):**

1. **Source map resolution via extension proxy** — extension host (Node.js) fetches
   `.next/**/*.js.map`, decodes VLQ, maps compiled position → source position.
   Client sends compiled `(url, line, col)` to extension via postMessage RPC;
   extension resolves and returns `SourceLocation`. Works for both client and server chunks.
   Pro: accurate, no browser limitations. Con: requires extension host involvement for SaaS too.

2. **Async HTTP source map fetch (client components only)** — browser fetches
   `_next/static/chunks/<hash>.js.map` directly, parses VLQ. Works only for client components
   (server chunks are not browser-accessible). Pro: pure client-side. Con: doesn't help RSC.

3. **`_debugOwner.stack` array parsing** — `_debugOwner.stack` in React 19 RSC fibers
   contains call frame tuples (format: `[[name, file?, line?, col?], ...]` — undocumented).
   May contain source paths rather than compiled paths. Pro: could work for RSC without source maps.
   Con: format is internal React DevTools protocol, likely to change without notice.

**Decision:** None of the above is implemented yet. Until one of these is in place,
Next.js + React 19 projects will silently return no source location for elements where
`_debugStack` only has compiled frames. The inspector remains disabled for those elements —
no error, no crash, no fallback.

## Critical Findings & Known Risks (Review 2026-03-25)

### CF-1: Composite key stability — silent wrong match risk

The stability algorithm for external edits uses `(tag, depth, parentTag, indexAmongSameTagSiblings)`.
This key is fragile:

- **Component rename** (`Card` → `ProductCard`): tag changes → nodeRef lost → selection cleared.
  Acceptable — user explicitly renamed, losing selection is expected.
- **Wrap/unwrap in container**: depth changes for ALL children → all keys invalidated.
  Frequent operation in visual editors — unacceptable loss rate.
- **Swap same-tag siblings**: two `<div>` swapped → `indexAmongSameTagSiblings` swaps →
  **SILENT WRONG MATCH**. System maps selection to the other div without any indication.
  This is the worst failure mode — user mutates the wrong element.

**Solution: 3-tier matching cascade with content fingerprint.**

Replace single composite key with a tiered fallback:

**Tier 1 — Structural Key (exact):** `parentTag/tag#siblingIndexByTag~fingerprint`
Drop absolute depth (breaks on wrapping), add fingerprint. Example:
`div/Card#0~a3f2` (parentTag/tag#sibIndex~fingerprint).

**Tier 2 — Ancestry Path (fuzzy):** `last-3-ancestor-tags/tag~fingerprint`
With subsequence matching — if 2 of 3 ancestor segments match + fingerprint → accept.
Handles wrapping (new div in ancestor chain) and distant ancestor renames.

**Tier 3 — Position Proximity (last resort):** Nearest unmatched node of same tag
within ±5 lines. Only if exactly 1 candidate (no ambiguity). Handles rename + wrap.

**Fingerprint:** hash of `(sorted prop/attribute names, JSX element children count,
subtree height)`. Excludes text content and prop values (too brittle). Computed during
existing AST traversal at O(N*K) where K = average props per node (3-5).

**Edge case: identical siblings** (two `<li>` with no distinguishing props):
No algorithm can tell them apart without semantic info. System reports ambiguity
instead of silent wrong match — the only honest answer.

**Performance:** O(N*K) with ~3-4x constant factor over current. For 500 nodes: microseconds.

**References:** GumTree tree diff algorithm (top-down hash + bottom-up propagation),
React Fast Refresh (component families by moduleId + name).

### CF-2: nodeRef is session-scoped — cannot anchor persistent data

`nodeRef` format `filePath:counter` changes on every re-parse. It MUST NOT be used as a
persistent identifier in the database (e.g., comment anchors in `comments.ts`).

**Solution: `data-comment-id` attribute — targeted injection only on commented elements.**

Unlike `data-uniq-id` (injected on ALL elements → hundreds of attributes → diff noise),
`data-comment-id` is injected only on elements that have comments (~1-5 per file).
The trade-off is fundamentally different:

| | `data-uniq-id` (removing) | `data-comment-id` (proposed) |
|--|--|--|
| Scale | Every JSX element (hundreds) | Only commented elements (1-5/file) |
| Diff noise | Massive | Negligible |
| When injected | Always, on project open | On comment creation only |
| Library components | Needed but impossible | Not needed — comments target user code |

**Lifecycle:**

1. **Create comment:** Write comment to DB with `commentId` (UUID). AST mutation adds
   `data-comment-id={commentId}` to the target element. Commit to source.
2. **Find element:** `querySelector('[data-comment-id="..."]')` at AST level — O(1).
   On client: fiber tree walk or DOM query. No heuristic resolution needed.
3. **Delete comment:** AST mutation removes `data-comment-id` attribute. DB soft-delete.
4. **Move element:** Attribute moves with the JSX element in source — survives cut/paste,
   reorder, wrap/unwrap, indentation changes.

**What it survives:**
- Our AST mutations — attribute on the element, preserved through mutation pipeline
- External edits (VS Code) — in source code, IDE undo/redo/cut/paste carries it
- Git branch switch — committed to source
- Server restart — in the file, no runtime state needed
- AI edits via AST routes — mutation pipeline preserves protected attributes

**Protected attribute:** `data-comment-id` is a "protected attribute" in the AST mutator —
never removed or modified during mutations except explicit comment deletion. AI edits
through AST routes automatically preserve it.

**Edge cases:**
- AI rewrites element as raw text (not through AST) → may lose attribute → detect on
  next parse, mark comment as orphaned, show re-attach UI
- User manually removes attribute → comment orphaned, expected behavior
- `.map()` elements → `data-comment-id` on the template element; `itemIndex` stored
  in DB alongside `commentId` to identify which instance

**VS Code integration:** Extension shows inline decorations (gutter icons) on lines
with `data-comment-id`. CodeLens or hover shows comment text. Click opens comments panel.
Extension already parses AST — finding these attributes is trivial.

**DB schema:** `comments.comment_id` (UUID) — same value as the `data-comment-id`
attribute in source. No anchor columns needed — the attribute IS the anchor.

**Rejected alternatives:**
- SourceLocation + content hash in DB: content hash breaks on prop/text edits (the
  exact scenario when comments matter — you comment on elements you're editing).
  Multi-tier heuristic resolution is fragile, degrades to guessing.
- Redis mapping: ephemeral (restart = lost mapping), adds operational complexity.
- UUID mapping table: reinvents `data-uniq-id` with extra indirection.

**Effort:** Low — one migration, ~50 lines resolution logic wrapping existing `NodeMapService`.

### CF-3: `findDOMElement()` full fiber tree walk — O(N) per call

`walkFibers(rootFiber, callback)` visits every fiber on every call. Called for overlay
highlighting in RAF loop. With 5 selected elements on a 2000-element page: 300 full tree
walks/second (4000-8000 fiber nodes each). Far exceeds 5ms budget.

**Solution: `FiberSourceIndex` — lazy source-keyed Map, rebuilt on React commit.**

```typescript
class FiberSourceIndex {
  private index: Map<string, HTMLElement[]> | null = null;

  // Called from __REACT_DEVTOOLS_GLOBAL_HOOK__.onCommitFiberRoot
  invalidate(): void { this.index = null; }

  findDOMElement(source: SourceLocation, itemIndex: number): HTMLElement | null {
    this.ensureBuilt();  // lazy O(N) rebuild if invalidated
    const key = `${source.fileName}:${source.line}:${source.column}`;
    const matches = this.index!.get(key);
    if (!matches) return null;
    const live = matches.filter(el => document.contains(el)); // Suspense safety
    return live[itemIndex] ?? null;
  }
}
```

**Performance:**

| Operation | Cost |
|-----------|------|
| Lookup | O(1) Map.get + O(K) filter live elements |
| Full rebuild (worst) | O(N), ~0.5-1ms for 8000 fibers |
| Incremental (`subtreeFlags` optimization) | O(changed), ~0.01-0.1ms |
| Invalidation frequency | 1-5x per user interaction |
| Memory | ~50-110KB for 2000-5000 nodes |

**`subtreeFlags` optimization:** React fibers have `subtreeFlags: Flags` — propagated
child work flags. If `subtreeFlags === 0`, skip entire subtree (no descendants changed).
Same technique React itself uses. Applied opportunistically with fallback to full walk.

**WeakRef not needed:** Fibers hold strong refs to stateNode. Regular Map + rebuild on
commit. No manual invalidation — `onCommitFiberRoot` fires unconditionally on every commit.

**New Fiber interface fields:** `alternate: Fiber | null`, `flags: number`,
`subtreeFlags: number`.

**WASM / SharedArrayBuffer rejected:** (a) SharedArrayBuffer unavailable in VS Code
webviews (issue #116715), (b) WASM FFI overhead dominates at N<10K — OpenUI proved
TypeScript beats Rust WASM 2.2-4.6x for small, frequent, object-heavy operations,
(c) V8 `Map` lookup ~50ns at N=5000 — plain JS wins.

### CF-4: Sync state machine 3s timeout — stale data on slow HMR

Force-sync after 3s means: new NodeMap + old DOM = position mismatches. Queued clicks
replay against wrong fiber tree. Real-world HMR times: Vite 100ms-2s, Next.js 2-10s,
CRA/Webpack 3-15s. Compilation errors → HMR never arrives.

**Solution: Server hints + Vite event listener + progressive timeout.**

1. **Server hints in `NodeMapUpdate`:**
   ```typescript
   interface NodeMapUpdate {
     // ... existing fields
     hmrExpected: boolean; // false for comment-only, type-only, whitespace changes
   }
   ```
   Server knows what it mutated → can predict if HMR fires. If `false` → client goes
   to `synced` immediately on NodeMap receipt. External edits → always `true`.

2. **Vite HMR event listener:** Listen to Vite WS (`vite-hmr` protocol) for:
   - `{ type: 'update' }` → HMR successful → transition to synced
   - `{ type: 'error' }` → compilation failed → error state (don't force-sync)
   - `{ type: 'full-reload' }` → wait for page reload + re-detect fibers

3. **Progressive timeout** (only as fallback for lost WS connection):
   - 0-500ms: normal, no UI
   - 500ms-3s: subtle spinner ("syncing...")
   - 3s-10s: warning ("HMR is slow, selection may be stale")
   - 10s: force-sync + warning toast

4. **Remove 3s hard cutoff** — replaced by Vite event listener for 80% of cases
   (external edits) and server hints for 20% (our mutations).

### CF-5: `getItemIndex()` linear sibling scan — O(siblings)

`let sibling = fiber.return?.child; while (sibling !== fiber)` — O(K) where K = sibling
count. For `.map()` with 500+ items, this adds latency.

**The real hot path is `findDOMElement` (CF-3), not `getItemIndex`.**
`getItemIndex` is called once per click — O(siblings) is acceptable for clicks.
For RAF-frequency calls, `FiberSourceIndex` (CF-3) eliminates the need entirely.

**Optimization for `getItemIndex`:** `WeakMap<Fiber, Map<sourceKey, Map<Fiber, number>>>`.
Cache per parent fiber. First call: O(siblings) to build. Subsequent calls: O(1) lookup.
WeakMap handles invalidation automatically — when React replaces a parent fiber (alternate
during reconciliation), old entry gets GC'd. No manual cache management.

**No libraries needed.** mnemonist adds nothing over native `Map` for this use case.
`TreeWalker` API is DOM-only, irrelevant for fiber tree. Typed arrays can't key by objects.

### CF-6: `findNearestDebugSource` walks up fiber tree — known UX ambiguity

When a clicked DOM element's fiber lacks `_debugSource` (e.g., host elements inside
minified library components), the function walks UP the tree and returns the nearest
**parent** component's source location. The user thinks they selected a specific `<span>`,
but selection is anchored to the parent `<Button>` from the library.

**Status:** Known behavior, not a bug. Acceptable for Phase 2 — library component
internals are not editable anyway. Document in user-facing help: "Clicking inside a
library component selects the nearest user-authored parent component."

**Future improvement:** Show a visual indicator (different overlay color or "library
component" badge) when selection was resolved via parent walk, not direct match.

### CF-7: React 19 removed `_debugSource` — strategic platform risk

React 19 replaced `_debugSource` with `_debugStack` (Error object captured inside
`jsxDEV()`). This affects ALL React 19 projects including Next.js 16+.

**React 19 fiber debug properties:**

| Property | Type | Content |
|----------|------|---------|
| `_debugStack` | `Error` | Error object with stack to JSX call site |
| `_debugTask` | `console.Task` | async task context |
| `_debugOwner` | `Fiber \| null` | parent component |
| `_debugSource` | — | **does not exist** (removed from fiber type) |

**How React DevTools solves it:** V8 `Error.prepareStackTrace` + structured `CallSite`
objects → `getScriptNameOrSourceURL()`, `getLineNumber()`, `getColumnNumber()`.
Fallback: regex parse `Error.stack` string (Firefox/Safari).

**Key nuance:** Stack returns URLs (`http://localhost:5173/src/App.tsx:7:12`), not
filesystem paths. In Vite dev mode, URL path ≈ source path (ESM, per-file serving).
In Next.js/webpack, chunk URLs require source map resolution.

**Solution: Parse `_debugStack` with dual-path in `ReactAdapter`:**

```typescript
getSourceLocation(element: HTMLElement): SourceLocation | null {
  const fiber = getFiberFromDOM(element);
  if (!fiber) return null;
  // React 18: direct property
  if (fiber._debugSource) return debugSourceToLocation(fiber._debugSource);
  // React 19: parse Error stack
  if (fiber._debugStack) return parseDebugStack(fiber._debugStack);
  return null;
}
```

Stack parsing: skip header + `jsxDEV` frame, parse 2nd frame with Chrome regex:
`/^\s*at .+\((.+):(\d+):(\d+)\)$/` → extract URL, strip dev server origin → fileName.

**Open risks:**
- `_debugStack` may have rate limit (`trackActualOwner` + `ownerStackLimit`) — needs
  runtime verification
- Next.js/webpack: chunk URLs need source map resolution (separate research)
- Column accuracy: transpiled `jsxDEV()` may differ from original JSX column

**Rejected alternatives:**
- Custom Babel/SWC plugin (5-7 days, needs per-project setup)
- `data-inspector-*` attributes (Babel-only, no SWC, adds DOM noise)

**Effort:** Medium (2-3 days for Vite support). Next.js source map resolution is
a follow-up task.

**References:**
- [React Issue #31981](https://github.com/facebook/react/issues/31981) — Reintroduce debugSource
- [React Issue #32574](https://github.com/facebook/react/issues/32574) — Bring back _debugSource
- [React PR #28265](https://github.com/facebook/react/pull/28265) — Remove __self and __source
- [React PR #33143](https://github.com/facebook/react/pull/33143) — Structured callsite extraction
- [V8 Stack Trace API](https://v8.dev/docs/stack-trace-api)
