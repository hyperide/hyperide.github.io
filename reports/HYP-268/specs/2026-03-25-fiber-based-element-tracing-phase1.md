# Fiber-Based Element Tracing — Phase 1: Infrastructure

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build all infrastructure for fiber-based element tracing without changing existing behavior — types, fiber utils, ReactAdapter, NodeMap builder/service, transport abstractions, sync state machine, WS channel, and PostMessage transport.

**Architecture:** Client-side `ElementTracer` uses `ReactAdapter` to walk React fiber tree and extract `_debugSource` locations from DOM nodes. Server-side `NodeMapService` parses JSX files into `NodeMap` (position-keyed entries with stable `nodeRef` identifiers). Communication via `TracingTransport` abstraction — `WSTracingTransport` for SaaS (dedicated Bun WS channel), `PostMessageTracingTransport` for VS Code extension (via StateHub). Sync state machine on client queues interactions during HMR/map-update race windows.

**Tech Stack:** TypeScript, Babel AST (`@babel/parser` + `@babel/traverse`), React Fiber internals (`__reactFiber$`, `_debugSource`), Bun WebSocket (`server.upgrade`), `bun:test`

**Spec:** `docs/specs/2026-03-24-fiber-based-element-tracing.md`

---

## File Structure

### New files

```
shared/element-tracing/
  types.ts                          # SourceLocation, NodeMapEntry, protocol messages, FrameworkAdapter interface

lib/element-tracing/
  node-map-builder.ts               # Pure: Babel AST → NodeMapEntry[]
  node-map-builder.test.ts          # TDD tests
  stability.ts                      # Composite key generation + old→new nodeRef mapping
  stability.test.ts                 # TDD tests
  node-map-service.ts               # Orchestrator: parse → build → track → emit
  node-map-service.test.ts          # TDD tests

client/lib/element-tracing/
  fiber-utils.ts                    # getFiberFromDOM, findNearestDebugSource, traceToRoot, etc.
  fiber-utils.test.ts               # TDD tests with mock fiber objects
  react-adapter.ts                  # ReactAdapter: FrameworkAdapter impl using fiber utils
  react-adapter.test.ts             # TDD tests
  tracing-transport.ts              # TracingTransport interface (re-exported from shared/types)
  ws-tracing-transport.ts           # WSTracingTransport: WebSocket impl for SaaS
  ws-tracing-transport.test.ts      # TDD tests
  sync-state-machine.ts             # TracingSyncState: synced/awaiting-both/awaiting-hmr/awaiting-map
  sync-state-machine.test.ts        # TDD tests
  element-tracer.ts                 # Client orchestrator: adapter + transport + state machine
  element-tracer.test.ts            # TDD tests
  index.ts                          # Re-exports

server/services/
  element-tracing-channel.ts        # WS channel handler: upgrade, message dispatch, broadcast

vscode-extension/hypercanvas-preview/src/services/element-tracing/
  post-message-tracing-transport.ts # PostMessageTracingTransport for extension
```

### Modified files

```
server/proxy/shared.ts              # Extend WSData with isElementTracing flag
server/main.ts                      # Add element-tracing WS handler branch
vscode-extension/hypercanvas-preview/src/StateHub.ts  # Add element-tracing message forwarding
```

---

## Task 1: Shared Types

**Files:**
- Create: `shared/element-tracing/types.ts`

- [ ] **Step 1: Create types file**

```typescript
/**
 * @file Shared types for fiber-based element tracing system
 *
 * Accessed via: Internal module — consumed by client (ElementTracer), server (NodeMapService),
 * and VS Code extension (PostMessageTracingTransport)
 */

/* ─── Core identifiers ───────────────────────────────────────────── */

/** Source location in a JSX file. Universal element identifier. */
export interface SourceLocation {
  /** Absolute or project-relative file path */
  fileName: string;
  /** 1-based line number */
  line: number;
  /** 0-based column number (matches Babel AST and _debugSource.columnNumber) */
  column: number;
}

/**
 * Server-assigned session-scoped identifier for an AST node.
 * Format: `"<filePath>:<traversalIndex>"` — opaque string, clients must NOT parse it.
 */
export type NodeRef = string;

/* ─── Node map ───────────────────────────────────────────────────── */

export interface NodeMapEntry {
  nodeRef: NodeRef;
  tag: string;
  loc: SourceLocation;
  endLoc: SourceLocation;
  parentRef: NodeRef | null;
  children: NodeRef[];
  isComponent: boolean;
  componentName?: string;
}

/* ─── Protocol messages ──────────────────────────────────────────── */

/** Server → Client: pushed after every file parse */
export interface NodeMapUpdate {
  type: 'node-map-update';
  filePath: string;
  fileHash: string;
  version: number;
  nodes: NodeMapEntry[];
  refMapping?: Record<NodeRef, NodeRef>;
  mutatedNodeRef?: NodeRef;
}

/** Server → Client: pushed when a file is deleted or renamed */
export interface NodeMapInvalidate {
  type: 'node-map-invalidate';
  filePath: string;
}

/** Client → Server: resolve DOM click to nodeRef */
export interface ResolveElement {
  type: 'resolve-element';
  requestId: string;
  source: SourceLocation;
  itemIndex: number;
}

/** Server → Client: response to resolve-element */
export interface ResolveElementResponse {
  type: 'resolve-element-response';
  requestId: string;
  nodeRef: NodeRef | null;
  entry: NodeMapEntry | null;
}

export type TracingClientMessage = ResolveElement;
export type TracingServerMessage = NodeMapUpdate | NodeMapInvalidate | ResolveElementResponse;

/* ─── Framework adapter ──────────────────────────────────────────── */

export interface ComponentInfo {
  name: string;
  source: SourceLocation | null;
  definitionSource?: SourceLocation;
  props: Record<string, string>;
  isLibrary: boolean;
}

export interface ComponentTreeNode {
  name: string;
  source: SourceLocation | null;
  children: ComponentTreeNode[];
  /** DOM element reference — null for non-host components.
   *  FrameworkAdapter interface uses HTMLElement; this shared type uses
   *  HTMLElement which is available via lib.dom in all three tsconfigs. */
  domElement: HTMLElement | null;
  fiberTag?: number;
}

export interface FrameworkAdapter {
  readonly name: string;
  detect(doc: Document): boolean;
  getSourceLocation(element: HTMLElement): SourceLocation | null;
  getComponentChain(element: HTMLElement): ComponentInfo[];
  getItemIndex(element: HTMLElement): number;
  walkComponentTree(rootElement: HTMLElement): ComponentTreeNode[];
  findDOMElement(source: SourceLocation, itemIndex: number): HTMLElement | null;
}

/* ─── Transport ──────────────────────────────────────────────────── */

export interface TracingTransport {
  send(msg: TracingClientMessage): void;
  onMessage(handler: (msg: TracingServerMessage) => void): () => void;
  readonly connected: boolean;
  onConnectionChange(handler: (connected: boolean) => void): () => void;
}

/* ─── Mutation response extension ────────────────────────────────── */

export interface MutationResponse {
  success: boolean;
  nodeRef?: NodeRef;
  newLoc?: SourceLocation;
  error?: string;
}

/* ─── Sync state ─────────────────────────────────────────────────── */

export type SyncState = 'synced' | 'awaiting-both' | 'awaiting-hmr' | 'awaiting-map';
```

- [ ] **Step 2: Verify file compiles**

Run: `bunx tsc --noEmit shared/element-tracing/types.ts`
Expected: no errors (may need to run from project root with tsconfig)

Alternative: `npx tsc --noEmit --strict --moduleResolution bundler --target esnext shared/element-tracing/types.ts`

- [ ] **Step 3: Commit**

```bash
git add shared/element-tracing/types.ts
git commit -m "feat(element-tracing): add shared types for fiber-based tracing (HYP-268)"
```

---

## Task 2: NodeMap Builder

Pure function: Babel AST → `NodeMapEntry[]`. No I/O, no state.

**Files:**
- Create: `lib/element-tracing/node-map-builder.ts`
- Create: `lib/element-tracing/node-map-builder.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
/**
 * @file Tests for NodeMap builder — AST to NodeMapEntry[] conversion
 */

import { describe, expect, it } from 'bun:test';
import { parse } from '@babel/parser';
import type * as t from '@babel/types';
import { buildNodeMap } from './node-map-builder';

function parseJSX(code: string): t.File {
  return parse(code, {
    sourceType: 'module',
    plugins: ['typescript', 'jsx'],
  });
}

describe('buildNodeMap', () => {
  it('should build entries for simple JSX', () => {
    const ast = parseJSX(`const App = () => <div><span>hello</span></div>;`);
    const entries = buildNodeMap(ast, 'src/App.tsx');

    expect(entries.length).toBe(2); // div + span
    expect(entries[0].tag).toBe('div');
    expect(entries[0].loc.fileName).toBe('src/App.tsx');
    expect(entries[0].loc.line).toBeGreaterThan(0);
    expect(entries[0].isComponent).toBe(false);
    expect(entries[0].children.length).toBe(1);
    expect(entries[0].parentRef).toBeNull(); // top-level JSX

    expect(entries[1].tag).toBe('span');
    expect(entries[1].parentRef).toBe(entries[0].nodeRef);
    expect(entries[1].children.length).toBe(0); // text child is not JSX
  });

  it('should detect component elements (uppercase tag)', () => {
    const ast = parseJSX(`const Page = () => <div><Card title="x" /><Button /></div>;`);
    const entries = buildNodeMap(ast, 'src/Page.tsx');

    const card = entries.find(e => e.tag === 'Card');
    const button = entries.find(e => e.tag === 'Button');

    expect(card).toBeDefined();
    expect(card!.isComponent).toBe(true);
    expect(card!.componentName).toBe('Card');

    expect(button).toBeDefined();
    expect(button!.isComponent).toBe(true);
  });

  it('should handle nested components', () => {
    const ast = parseJSX(`
      const App = () => (
        <Layout>
          <Header />
          <main>
            <Card />
          </main>
        </Layout>
      );
    `);
    const entries = buildNodeMap(ast, 'src/App.tsx');

    const layout = entries.find(e => e.tag === 'Layout');
    const main = entries.find(e => e.tag === 'main');

    expect(layout).toBeDefined();
    expect(layout!.children.length).toBe(2); // Header + main

    expect(main).toBeDefined();
    expect(main!.parentRef).toBe(layout!.nodeRef);
    expect(main!.children.length).toBe(1); // Card
  });

  it('should generate stable nodeRef format', () => {
    const ast = parseJSX(`const A = () => <div><span /></div>;`);
    const entries = buildNodeMap(ast, 'src/A.tsx');

    for (const entry of entries) {
      expect(entry.nodeRef).toMatch(/^src\/A\.tsx:\d+$/);
    }
  });

  it('should set endLoc correctly', () => {
    const ast = parseJSX(`const A = () => <div>text</div>;`);
    const entries = buildNodeMap(ast, 'src/A.tsx');

    expect(entries[0].endLoc.line).toBeGreaterThanOrEqual(entries[0].loc.line);
  });

  it('should handle JSX member expressions (Dialog.Portal)', () => {
    const ast = parseJSX(`const A = () => <Dialog.Portal><div /></Dialog.Portal>;`);
    const entries = buildNodeMap(ast, 'src/A.tsx');

    expect(entries[0].tag).toBe('Dialog.Portal');
    expect(entries[0].isComponent).toBe(true);
    expect(entries[0].componentName).toBe('Dialog.Portal');
  });

  it('should handle fragments', () => {
    const ast = parseJSX(`const A = () => <><div /><span /></>;`);
    const entries = buildNodeMap(ast, 'src/A.tsx');

    const fragment = entries.find(e => e.tag === 'Fragment');
    expect(fragment).toBeDefined();
    expect(fragment!.children.length).toBe(2);
  });

  it('should handle empty file (no JSX)', () => {
    const ast = parseJSX(`const x = 42;`);
    const entries = buildNodeMap(ast, 'src/utils.ts');

    expect(entries.length).toBe(0);
  });

  it('should handle conditional JSX (ternary)', () => {
    const ast = parseJSX(`const A = () => condition ? <div /> : <span />;`);
    const entries = buildNodeMap(ast, 'src/A.tsx');

    // Both branches should be in the map
    expect(entries.find(e => e.tag === 'div')).toBeDefined();
    expect(entries.find(e => e.tag === 'span')).toBeDefined();
  });

  it('should handle .map() JSX', () => {
    const ast = parseJSX(`
      const A = () => (
        <ul>
          {items.map(item => <li key={item.id}>{item.name}</li>)}
        </ul>
      );
    `);
    const entries = buildNodeMap(ast, 'src/A.tsx');

    const ul = entries.find(e => e.tag === 'ul');
    const li = entries.find(e => e.tag === 'li');

    expect(ul).toBeDefined();
    expect(li).toBeDefined();
    // li is inside .map() callback, not a direct JSX child of ul
    // but traversal still finds it
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `bun run test lib/element-tracing/node-map-builder.test.ts`
Expected: FAIL — `Cannot find module './node-map-builder'`

- [ ] **Step 3: Implement NodeMap builder**

```typescript
/**
 * @file Builds NodeMap entries from a Babel AST
 *
 * Accessed via: Internal module — consumed by NodeMapService
 * Assumptions: AST has source locations (parsed with { tokens: true } or standard config)
 */

import _traverse, { type NodePath } from '@babel/traverse';
import * as t from '@babel/types';
import type { NodeMapEntry, NodeRef, SourceLocation } from '../../shared/element-tracing/types';

const traverse = (_traverse as { default?: typeof _traverse }).default ?? _traverse;

/** Build a dotted name from JSXMemberExpression (e.g., Dialog.Portal) */
function buildTagName(name: t.JSXIdentifier | t.JSXMemberExpression | t.JSXNamespacedName): string {
  if (t.isJSXIdentifier(name)) return name.name;
  if (t.isJSXMemberExpression(name)) {
    return `${buildTagName(name.object)}.${buildTagName(name.property)}`;
  }
  if (t.isJSXNamespacedName(name)) return `${name.namespace.name}:${name.name.name}`;
  return 'Unknown';
}

/** Check if a tag name represents a user component (starts with uppercase or contains dot) */
function isComponentTag(tag: string): boolean {
  return /^[A-Z]/.test(tag) || tag.includes('.');
}

function toSourceLocation(loc: t.SourceLocation['start'], fileName: string): SourceLocation {
  return { fileName, line: loc.line, column: loc.column };
}

/**
 * Build NodeMapEntry[] from a parsed Babel AST.
 * Pure function — no I/O, no side effects.
 *
 * @param ast - Parsed Babel AST (must have source locations)
 * @param filePath - Project-relative file path (used in nodeRef and SourceLocation.fileName)
 * @returns Array of NodeMapEntry for every JSXElement in the file
 */
export function buildNodeMap(ast: t.File, filePath: string): NodeMapEntry[] {
  const entries: NodeMapEntry[] = [];
  /** Map from Babel NodePath to its assigned nodeRef (for parent/child linking) */
  const pathToRef = new Map<NodePath<t.JSXElement>, NodeRef>();
  let traversalIndex = 0;

  traverse(ast, {
    JSXElement(path) {
      const { node } = path;
      if (!node.loc) return;

      const tag = node.openingElement.name.type === 'JSXFragment'
        ? 'Fragment'
        : buildTagName(node.openingElement.name as t.JSXIdentifier | t.JSXMemberExpression | t.JSXNamespacedName);

      const nodeRef: NodeRef = `${filePath}:${traversalIndex}`;
      traversalIndex++;

      pathToRef.set(path, nodeRef);

      // Find parent JSXElement (if any)
      let parentRef: NodeRef | null = null;
      let parentPath = path.parentPath;
      while (parentPath) {
        if (parentPath.isJSXElement()) {
          parentRef = pathToRef.get(parentPath as NodePath<t.JSXElement>) ?? null;
          break;
        }
        parentPath = parentPath.parentPath;
      }

      const isComp = isComponentTag(tag);

      entries.push({
        nodeRef,
        tag,
        loc: toSourceLocation(node.loc.start, filePath),
        endLoc: toSourceLocation(node.loc.end, filePath),
        parentRef,
        children: [], // filled in second pass
        isComponent: isComp,
        componentName: isComp ? tag : undefined,
      });
    },

    JSXFragment(path) {
      const { node } = path;
      if (!node.loc) return;

      const nodeRef: NodeRef = `${filePath}:${traversalIndex}`;
      traversalIndex++;

      // Store on parent map using a unique key approach
      // Fragments don't have a JSXElement path, handle parent lookup separately
      let parentRef: NodeRef | null = null;
      let parentPath = path.parentPath;
      while (parentPath) {
        if (parentPath.isJSXElement()) {
          parentRef = pathToRef.get(parentPath as NodePath<t.JSXElement>) ?? null;
          break;
        }
        parentPath = parentPath.parentPath;
      }

      entries.push({
        nodeRef,
        tag: 'Fragment',
        loc: toSourceLocation(node.loc.start, filePath),
        endLoc: toSourceLocation(node.loc.end, filePath),
        parentRef,
        children: [],
        isComponent: false,
      });

      // Store fragment ref for child lookup (using a side channel)
      (path.node as t.JSXFragment & { __nodeRef?: string }).__nodeRef = nodeRef;
    },
  });

  // Second pass: populate children arrays
  const refToEntry = new Map(entries.map(e => [e.nodeRef, e]));
  for (const entry of entries) {
    if (entry.parentRef) {
      const parent = refToEntry.get(entry.parentRef);
      if (parent) {
        parent.children.push(entry.nodeRef);
      }
    }
  }

  return entries;
}
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `bun run test lib/element-tracing/node-map-builder.test.ts`
Expected: all tests PASS

- [ ] **Step 5: Fix any failing tests**

Iterate until green. Common issues: fragment handling, parent detection through
non-JSX intermediaries (expression containers, arrow functions in .map()).

- [ ] **Step 6: Commit**

```bash
git add shared/element-tracing/types.ts lib/element-tracing/node-map-builder.ts lib/element-tracing/node-map-builder.test.ts
git commit -m "feat(element-tracing): NodeMap builder — AST to NodeMapEntry[] (HYP-268)"
```

---

## Task 3: NodeRef Stability Algorithm

Composite key matching to map old nodeRefs to new nodeRefs after re-parse.

**Files:**
- Create: `lib/element-tracing/stability.ts`
- Create: `lib/element-tracing/stability.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
/**
 * @file Tests for nodeRef stability algorithm — old→new mapping across re-parses
 */

import { describe, expect, it } from 'bun:test';
import type { NodeMapEntry } from '../../shared/element-tracing/types';
import { buildCompositeKey, mapNodeRefs } from './stability';

function entry(overrides: Partial<NodeMapEntry> & Pick<NodeMapEntry, 'nodeRef' | 'tag'>): NodeMapEntry {
  return {
    loc: { fileName: 'f.tsx', line: 1, column: 0 },
    endLoc: { fileName: 'f.tsx', line: 1, column: 10 },
    parentRef: null,
    children: [],
    isComponent: false,
    ...overrides,
  };
}

describe('buildCompositeKey', () => {
  it('should generate key from tag, depth, parentTag, siblingIndex', () => {
    const entries: NodeMapEntry[] = [
      entry({ nodeRef: 'f:0', tag: 'div', parentRef: null }),
      entry({ nodeRef: 'f:1', tag: 'span', parentRef: 'f:0' }),
      entry({ nodeRef: 'f:2', tag: 'span', parentRef: 'f:0' }),
    ];
    entries[0].children = ['f:1', 'f:2'];

    const refToEntry = new Map(entries.map(e => [e.nodeRef, e]));

    const key0 = buildCompositeKey(entries[0], refToEntry);
    const key1 = buildCompositeKey(entries[1], refToEntry);
    const key2 = buildCompositeKey(entries[2], refToEntry);

    expect(key0).toBe('div|0|ROOT|0');
    expect(key1).toBe('span|1|div|0');  // first span under div
    expect(key2).toBe('span|1|div|1');  // second span under div
  });
});

describe('mapNodeRefs', () => {
  it('should map identical structures 1:1', () => {
    const oldEntries: NodeMapEntry[] = [
      entry({ nodeRef: 'f:0', tag: 'div' }),
      entry({ nodeRef: 'f:1', tag: 'span', parentRef: 'f:0' }),
    ];
    oldEntries[0].children = ['f:1'];

    const newEntries: NodeMapEntry[] = [
      entry({ nodeRef: 'f:0', tag: 'div' }),
      entry({ nodeRef: 'f:1', tag: 'span', parentRef: 'f:0' }),
    ];
    newEntries[0].children = ['f:1'];

    const mapping = mapNodeRefs(oldEntries, newEntries);
    expect(mapping).toEqual({ 'f:0': 'f:0', 'f:1': 'f:1' });
  });

  it('should handle sibling insertion (shift)', () => {
    // Before: div > [span]
    const oldEntries: NodeMapEntry[] = [
      entry({ nodeRef: 'f:0', tag: 'div' }),
      entry({ nodeRef: 'f:1', tag: 'span', parentRef: 'f:0' }),
    ];
    oldEntries[0].children = ['f:1'];

    // After: div > [p, span] — p inserted before span, traversal indices shift
    const newEntries: NodeMapEntry[] = [
      entry({ nodeRef: 'f:0', tag: 'div' }),
      entry({ nodeRef: 'f:1', tag: 'p', parentRef: 'f:0' }),
      entry({ nodeRef: 'f:2', tag: 'span', parentRef: 'f:0' }),
    ];
    newEntries[0].children = ['f:1', 'f:2'];

    const mapping = mapNodeRefs(oldEntries, newEntries);
    // div maps to div (same key), span maps to new index
    expect(mapping['f:0']).toBe('f:0');
    expect(mapping['f:1']).toBe('f:2'); // old span → new span
  });

  it('should handle element deletion', () => {
    const oldEntries: NodeMapEntry[] = [
      entry({ nodeRef: 'f:0', tag: 'div' }),
      entry({ nodeRef: 'f:1', tag: 'span', parentRef: 'f:0' }),
      entry({ nodeRef: 'f:2', tag: 'p', parentRef: 'f:0' }),
    ];
    oldEntries[0].children = ['f:1', 'f:2'];

    // span deleted
    const newEntries: NodeMapEntry[] = [
      entry({ nodeRef: 'f:0', tag: 'div' }),
      entry({ nodeRef: 'f:1', tag: 'p', parentRef: 'f:0' }),
    ];
    newEntries[0].children = ['f:1'];

    const mapping = mapNodeRefs(oldEntries, newEntries);
    expect(mapping['f:0']).toBe('f:0');
    expect(mapping['f:2']).toBe('f:1'); // old p → new p
    expect(mapping['f:1']).toBeUndefined(); // old span → gone
  });

  it('should return empty mapping for completely different structures', () => {
    const oldEntries: NodeMapEntry[] = [
      entry({ nodeRef: 'f:0', tag: 'div' }),
    ];
    const newEntries: NodeMapEntry[] = [
      entry({ nodeRef: 'f:0', tag: 'section' }),
    ];

    const mapping = mapNodeRefs(oldEntries, newEntries);
    expect(mapping['f:0']).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `bun run test lib/element-tracing/stability.test.ts`
Expected: FAIL — `Cannot find module './stability'`

- [ ] **Step 3: Implement stability algorithm**

```typescript
/**
 * @file NodeRef stability algorithm — maps old nodeRefs to new nodeRefs across re-parses
 *
 * Accessed via: Internal module — consumed by NodeMapService during re-parse
 * Assumptions: NodeMapEntry arrays are built by buildNodeMap() with consistent traversal order
 */

import type { NodeMapEntry, NodeRef } from '../../shared/element-tracing/types';

/**
 * Build a composite key for structural identity of a node.
 * Key: `tag|depth|parentTag|indexAmongSameTagSiblings`
 *
 * This identity survives sibling insertions/deletions because it counts
 * only same-tag siblings, not raw child index.
 */
export function buildCompositeKey(
  entry: NodeMapEntry,
  refToEntry: Map<NodeRef, NodeMapEntry>,
): string {
  const parentEntry = entry.parentRef ? refToEntry.get(entry.parentRef) : null;
  const parentTag = parentEntry ? parentEntry.tag : 'ROOT';

  // Calculate depth
  let depth = 0;
  let current = entry;
  while (current.parentRef) {
    depth++;
    const parent = refToEntry.get(current.parentRef);
    if (!parent) break;
    current = parent;
  }

  // Calculate index among same-tag siblings under the same parent
  let sameTagIndex = 0;
  if (parentEntry) {
    for (const childRef of parentEntry.children) {
      if (childRef === entry.nodeRef) break;
      const sibling = refToEntry.get(childRef);
      if (sibling && sibling.tag === entry.tag) {
        sameTagIndex++;
      }
    }
  }

  return `${entry.tag}|${depth}|${parentTag}|${sameTagIndex}`;
}

/**
 * Map old nodeRefs to new nodeRefs by matching composite keys.
 *
 * @param oldEntries - NodeMap entries from previous parse
 * @param newEntries - NodeMap entries from current parse
 * @returns Mapping from old nodeRef to new nodeRef. Missing keys = node was deleted/not matched.
 */
export function mapNodeRefs(
  oldEntries: NodeMapEntry[],
  newEntries: NodeMapEntry[],
): Record<NodeRef, NodeRef> {
  const oldRefToEntry = new Map(oldEntries.map(e => [e.nodeRef, e]));
  const newRefToEntry = new Map(newEntries.map(e => [e.nodeRef, e]));

  // Build composite keys for both sets
  const oldKeyToRef = new Map<string, NodeRef>();
  for (const entry of oldEntries) {
    const key = buildCompositeKey(entry, oldRefToEntry);
    // If duplicate keys exist, first one wins (ambiguous match = skip)
    if (!oldKeyToRef.has(key)) {
      oldKeyToRef.set(key, entry.nodeRef);
    }
  }

  const newKeyToRef = new Map<string, NodeRef>();
  for (const entry of newEntries) {
    const key = buildCompositeKey(entry, newRefToEntry);
    if (!newKeyToRef.has(key)) {
      newKeyToRef.set(key, entry.nodeRef);
    }
  }

  // Match old → new by composite key
  const mapping: Record<NodeRef, NodeRef> = {};
  for (const [key, oldRef] of oldKeyToRef) {
    const newRef = newKeyToRef.get(key);
    if (newRef) {
      mapping[oldRef] = newRef;
    }
  }

  return mapping;
}
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `bun run test lib/element-tracing/stability.test.ts`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/element-tracing/stability.ts lib/element-tracing/stability.test.ts
git commit -m "feat(element-tracing): nodeRef stability algorithm — composite key matching (HYP-268)"
```

---

## Task 4: NodeMapService

Orchestrator that parses files, builds NodeMap, tracks nodeRef stability across re-parses, and emits updates. Uses `FileIO` abstraction to work in both server and extension.

**Files:**
- Create: `lib/element-tracing/node-map-service.ts`
- Create: `lib/element-tracing/node-map-service.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
/**
 * @file Tests for NodeMapService — parse, build, track, emit
 */

import { describe, expect, it, beforeEach, mock } from 'bun:test';
import type { NodeMapEntry, NodeMapUpdate, SourceLocation } from '../../shared/element-tracing/types';
import { NodeMapService } from './node-map-service';

describe('NodeMapService', () => {
  let service: NodeMapService;

  beforeEach(() => {
    service = new NodeMapService();
  });

  const simpleJSX = `const App = () => <div><span>hello</span></div>;`;

  it('should parse file and build node map', () => {
    const entries = service.parseAndBuild(simpleJSX, 'src/App.tsx');

    expect(entries.length).toBe(2);
    expect(entries[0].tag).toBe('div');
    expect(entries[1].tag).toBe('span');
  });

  it('should store parsed map and return it by filePath', () => {
    service.parseAndBuild(simpleJSX, 'src/App.tsx');

    const map = service.getNodeMap('src/App.tsx');
    expect(map).toBeDefined();
    expect(map!.length).toBe(2);
  });

  it('should resolve source location to nodeRef', () => {
    const entries = service.parseAndBuild(simpleJSX, 'src/App.tsx');
    const divEntry = entries[0];

    const resolved = service.resolveSourceLocation(divEntry.loc);
    expect(resolved).not.toBeNull();
    expect(resolved!.nodeRef).toBe(divEntry.nodeRef);
  });

  it('should return refMapping on re-parse', () => {
    service.parseAndBuild(simpleJSX, 'src/App.tsx');
    const oldEntries = service.getNodeMap('src/App.tsx')!;
    const oldDivRef = oldEntries[0].nodeRef;

    // Re-parse with modified content (inserted element)
    const modifiedJSX = `const App = () => <div><p>new</p><span>hello</span></div>;`;
    const result = service.reparseAndUpdate(modifiedJSX, 'src/App.tsx');

    expect(result.refMapping).toBeDefined();
    expect(result.refMapping![oldDivRef]).toBeDefined(); // div still exists
    expect(result.version).toBe(2);
  });

  it('should increment version on each re-parse', () => {
    service.parseAndBuild(simpleJSX, 'src/App.tsx');
    const r1 = service.reparseAndUpdate(simpleJSX, 'src/App.tsx');
    const r2 = service.reparseAndUpdate(simpleJSX, 'src/App.tsx');

    expect(r1.version).toBe(2);
    expect(r2.version).toBe(3);
  });

  it('should normalize container paths', () => {
    service.setPathMapping('/app/', '/Users/dev/project/');

    const entries = service.parseAndBuild(simpleJSX, 'src/App.tsx');

    // Resolve with container path
    const containerLoc: SourceLocation = {
      fileName: '/app/src/App.tsx',
      line: entries[0].loc.line,
      column: entries[0].loc.column,
    };

    const resolved = service.resolveSourceLocation(containerLoc);
    expect(resolved).not.toBeNull();
  });

  it('should invalidate file on remove', () => {
    service.parseAndBuild(simpleJSX, 'src/App.tsx');
    service.removeFile('src/App.tsx');

    expect(service.getNodeMap('src/App.tsx')).toBeNull();
  });

  it('should resolve nodeRef to entry', () => {
    const entries = service.parseAndBuild(simpleJSX, 'src/App.tsx');
    const resolved = service.resolveNodeRef(entries[0].nodeRef);

    expect(resolved).not.toBeNull();
    expect(resolved!.tag).toBe('div');
  });

  it('should compute file hash', () => {
    service.parseAndBuild(simpleJSX, 'src/App.tsx');
    const hash = service.getFileHash('src/App.tsx');

    expect(hash).toBeDefined();
    expect(typeof hash).toBe('string');
    expect(hash!.length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `bun run test lib/element-tracing/node-map-service.test.ts`
Expected: FAIL — `Cannot find module './node-map-service'`

- [ ] **Step 3: Implement NodeMapService**

```typescript
/**
 * @file NodeMapService — parses JSX files, builds NodeMap, tracks stability across re-parses
 *
 * Accessed via: Internal module — consumed by server routes and extension AstService
 * Assumptions: Files are valid JSX/TSX parseable by Babel
 */

import { createHash } from 'node:crypto';
import { parse } from '@babel/parser';
import type * as t from '@babel/types';
import type { NodeMapEntry, NodeMapUpdate, NodeRef, SourceLocation } from '../../shared/element-tracing/types';
import { buildNodeMap } from './node-map-builder';
import { mapNodeRefs } from './stability';

interface FileState {
  entries: NodeMapEntry[];
  version: number;
  hash: string;
  /** Lookup: `fileName:line:column` → NodeMapEntry */
  locIndex: Map<string, NodeMapEntry>;
  /** Lookup: nodeRef → NodeMapEntry */
  refIndex: Map<NodeRef, NodeMapEntry>;
}

function locKey(loc: SourceLocation): string {
  return `${loc.fileName}:${loc.line}:${loc.column}`;
}

function hashContent(content: string): string {
  return createHash('sha256').update(content).digest('hex').slice(0, 16);
}

export class NodeMapService {
  private files = new Map<string, FileState>();
  private containerPrefix: string | null = null;
  private hostPrefix: string | null = null;

  /**
   * Configure container → host path mapping.
   * @param container - Path prefix inside container (e.g., "/app/")
   * @param host - Corresponding host path (e.g., "/Users/dev/project/")
   */
  setPathMapping(container: string, host: string): void {
    this.containerPrefix = container;
    this.hostPrefix = host;
  }

  /**
   * Normalize a fileName from `_debugSource` to match project-relative paths.
   */
  normalizeFileName(fileName: string): string {
    if (this.containerPrefix && this.hostPrefix && fileName.startsWith(this.containerPrefix)) {
      return fileName.replace(this.containerPrefix, this.hostPrefix);
    }
    return fileName;
  }

  /**
   * Parse source code and build initial NodeMap for a file.
   * @returns NodeMapEntry[] for the file
   */
  parseAndBuild(sourceCode: string, filePath: string): NodeMapEntry[] {
    const ast = parse(sourceCode, {
      sourceType: 'module',
      plugins: ['typescript', 'jsx'],
    });

    const entries = buildNodeMap(ast, filePath);
    const hash = hashContent(sourceCode);

    const locIndex = new Map<string, NodeMapEntry>();
    const refIndex = new Map<NodeRef, NodeMapEntry>();
    for (const entry of entries) {
      locIndex.set(locKey(entry.loc), entry);
      refIndex.set(entry.nodeRef, entry);
    }

    this.files.set(filePath, {
      entries,
      version: 1,
      hash,
      locIndex,
      refIndex,
    });

    return entries;
  }

  /**
   * Re-parse a file after modification. Computes refMapping for stability.
   */
  reparseAndUpdate(sourceCode: string, filePath: string): NodeMapUpdate {
    const oldState = this.files.get(filePath);
    const oldEntries = oldState?.entries ?? [];

    const ast = parse(sourceCode, {
      sourceType: 'module',
      plugins: ['typescript', 'jsx'],
    });

    const newEntries = buildNodeMap(ast, filePath);
    const hash = hashContent(sourceCode);
    const version = (oldState?.version ?? 0) + 1;

    const refMapping = mapNodeRefs(oldEntries, newEntries);

    const locIndex = new Map<string, NodeMapEntry>();
    const refIndex = new Map<NodeRef, NodeMapEntry>();
    for (const entry of newEntries) {
      locIndex.set(locKey(entry.loc), entry);
      refIndex.set(entry.nodeRef, entry);
    }

    this.files.set(filePath, {
      entries: newEntries,
      version,
      hash,
      locIndex,
      refIndex,
    });

    return {
      type: 'node-map-update',
      filePath,
      fileHash: hash,
      version,
      nodes: newEntries,
      refMapping: Object.keys(refMapping).length > 0 ? refMapping : undefined,
    };
  }

  /** Get current NodeMap entries for a file */
  getNodeMap(filePath: string): NodeMapEntry[] | null {
    return this.files.get(filePath)?.entries ?? null;
  }

  /** Get file hash */
  getFileHash(filePath: string): string | null {
    return this.files.get(filePath)?.hash ?? null;
  }

  /** Resolve a source location (possibly from container) to a NodeMapEntry */
  resolveSourceLocation(source: SourceLocation): NodeMapEntry | null {
    const normalized = this.normalizeFileName(source.fileName);

    // Try exact match first
    for (const [, state] of this.files) {
      const key = locKey({ ...source, fileName: normalized });
      const exact = state.locIndex.get(key);
      if (exact) return exact;

      // Try with fileName matching the file's own entries
      for (const entry of state.entries) {
        if (
          entry.loc.fileName === normalized &&
          entry.loc.line === source.line &&
          (source.column === 0 || entry.loc.column === source.column)
        ) {
          return entry;
        }
      }
    }

    return null;
  }

  /** Resolve a nodeRef to its NodeMapEntry */
  resolveNodeRef(nodeRef: NodeRef): NodeMapEntry | null {
    for (const [, state] of this.files) {
      const entry = state.refIndex.get(nodeRef);
      if (entry) return entry;
    }
    return null;
  }

  /** Remove a file from tracking */
  removeFile(filePath: string): void {
    this.files.delete(filePath);
  }

  /** Get list of tracked file paths */
  getTrackedFiles(): string[] {
    return Array.from(this.files.keys());
  }

  /** Build a NodeMapUpdate message for the current state of a file */
  buildUpdateMessage(filePath: string): NodeMapUpdate | null {
    const state = this.files.get(filePath);
    if (!state) return null;

    return {
      type: 'node-map-update',
      filePath,
      fileHash: state.hash,
      version: state.version,
      nodes: state.entries,
    };
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `bun run test lib/element-tracing/node-map-service.test.ts`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/element-tracing/node-map-service.ts lib/element-tracing/node-map-service.test.ts
git commit -m "feat(element-tracing): NodeMapService — parse, build, track, resolve (HYP-268)"
```

---

## Task 5: Fiber Utilities

Low-level functions for working with React fiber internals. Client-side only (runs in iframe).

**Files:**
- Create: `client/lib/element-tracing/fiber-utils.ts`
- Create: `client/lib/element-tracing/fiber-utils.test.ts`

- [ ] **Step 1: Write failing tests**

Tests use mock fiber objects (no real React DOM needed):

```typescript
/**
 * @file Tests for fiber utilities — mock fiber objects, no real React DOM
 */

import { describe, expect, it } from 'bun:test';
import {
  getFiberFromDOM,
  findNearestDebugSource,
  traceToRoot,
  isUserComponent,
  findHostFiber,
  type Fiber,
  type DebugSource,
} from './fiber-utils';

/** Helper to create mock fiber */
function mockFiber(overrides: Partial<Fiber> = {}): Fiber {
  return {
    tag: 5, // HostComponent
    type: 'div',
    stateNode: null,
    return: null,
    child: null,
    sibling: null,
    memoizedProps: {},
    _debugSource: null,
    _debugOwner: null,
    ...overrides,
  };
}

function mockDebugSource(overrides: Partial<DebugSource> = {}): DebugSource {
  return {
    fileName: '/app/src/App.tsx',
    lineNumber: 10,
    columnNumber: 4,
    ...overrides,
  };
}

describe('getFiberFromDOM', () => {
  it('should extract fiber from __reactFiber$ property', () => {
    const fiber = mockFiber();
    const el = { '__reactFiber$abc123': fiber } as unknown as HTMLElement;
    // Need to make Object.keys work
    const result = getFiberFromDOM(el);
    expect(result).toBe(fiber);
  });

  it('should extract fiber from __reactInternalInstance$ (older React)', () => {
    const fiber = mockFiber();
    const el = { '__reactInternalInstance$xyz': fiber } as unknown as HTMLElement;
    const result = getFiberFromDOM(el);
    expect(result).toBe(fiber);
  });

  it('should return null for non-React element', () => {
    const el = {} as HTMLElement;
    const result = getFiberFromDOM(el);
    expect(result).toBeNull();
  });
});

describe('findNearestDebugSource', () => {
  it('should return _debugSource from the fiber itself', () => {
    const source = mockDebugSource();
    const fiber = mockFiber({ _debugSource: source });
    expect(findNearestDebugSource(fiber)).toBe(source);
  });

  it('should walk up to find _debugSource on parent', () => {
    const source = mockDebugSource();
    const parent = mockFiber({ _debugSource: source });
    const child = mockFiber({ return: parent });
    expect(findNearestDebugSource(child)).toBe(source);
  });

  it('should return null if no fiber has _debugSource', () => {
    const parent = mockFiber();
    const child = mockFiber({ return: parent });
    expect(findNearestDebugSource(child)).toBeNull();
  });

  it('should unwrap React.memo wrapper (tag 14)', () => {
    const source = mockDebugSource();
    const wrappedType = { type: { _debugSource: source } };
    const memoFiber = mockFiber({ tag: 14, type: wrappedType as unknown });
    expect(findNearestDebugSource(memoFiber)).toBe(source);
  });

  it('should unwrap React.forwardRef wrapper (tag 11)', () => {
    const source = mockDebugSource();
    const wrappedType = { render: { _debugSource: source } };
    const forwardRefFiber = mockFiber({ tag: 11, type: wrappedType as unknown });
    // forwardRef is handled by walking up, not by type.type
    // Actually per spec: "for memo → fiber.type.type, for forwardRef → fiber.type.render"
    // The findNearestDebugSource checks tag 14/15 with type.type
    // For forwardRef, it should check type.render
    expect(findNearestDebugSource(forwardRefFiber)).toBe(source);
  });
});

describe('traceToRoot', () => {
  it('should collect all fibers from target to root', () => {
    const root = mockFiber({ type: 'div' });
    const mid = mockFiber({ type: 'App', return: root });
    const leaf = mockFiber({ type: 'span', return: mid });

    const chain = traceToRoot(leaf);
    expect(chain.length).toBe(3);
    expect(chain[0]).toBe(leaf);
    expect(chain[2]).toBe(root);
  });

  it('should handle single fiber (root)', () => {
    const root = mockFiber();
    const chain = traceToRoot(root);
    expect(chain.length).toBe(1);
  });
});

describe('isUserComponent', () => {
  it('should return true for function component (tag 0)', () => {
    expect(isUserComponent(mockFiber({ tag: 0 }))).toBe(true);
  });

  it('should return true for class component (tag 1)', () => {
    expect(isUserComponent(mockFiber({ tag: 1 }))).toBe(true);
  });

  it('should return false for host component (tag 5)', () => {
    expect(isUserComponent(mockFiber({ tag: 5 }))).toBe(false);
  });

  it('should return false for host root (tag 3)', () => {
    expect(isUserComponent(mockFiber({ tag: 3 }))).toBe(false);
  });
});

describe('findHostFiber', () => {
  it('should return same fiber if already a host component', () => {
    const fiber = mockFiber({ tag: 5, stateNode: {} as HTMLElement });
    expect(findHostFiber(fiber)).toBe(fiber);
  });

  it('should walk down to find first host child', () => {
    const hostChild = mockFiber({ tag: 5, stateNode: {} as HTMLElement });
    const component = mockFiber({ tag: 0, child: hostChild });
    expect(findHostFiber(component)).toBe(hostChild);
  });

  it('should return null if no host fiber found', () => {
    const component = mockFiber({ tag: 0 });
    expect(findHostFiber(component)).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `bun run test client/lib/element-tracing/fiber-utils.test.ts`
Expected: FAIL — `Cannot find module './fiber-utils'`

- [ ] **Step 3: Implement fiber utilities**

```typescript
/**
 * @file Low-level React fiber traversal utilities
 *
 * Accessed via: Internal module — consumed by ReactAdapter inside iframe
 * Assumptions: React dev mode with __reactFiber$ on DOM nodes and _debugSource on fibers.
 * These are React internals stable since React 16 but not part of public API.
 */

/* ─── Fiber types (minimal, from React internals) ────────────────── */

export interface DebugSource {
  fileName: string;
  lineNumber: number;
  columnNumber?: number;
}

export interface Fiber {
  tag: number;
  type: unknown;
  stateNode: HTMLElement | null;
  return: Fiber | null;
  child: Fiber | null;
  sibling: Fiber | null;
  memoizedProps: Record<string, unknown>;
  _debugSource: DebugSource | null;
  _debugOwner: Fiber | null;
}

/**
 * React fiber tag constants (subset we care about).
 * Full list: https://github.com/facebook/react/blob/main/packages/react-reconciler/src/ReactWorkTags.js
 */
export const FiberTag = {
  FunctionComponent: 0,
  ClassComponent: 1,
  HostRoot: 3,
  HostComponent: 5,
  HostText: 6,
  ForwardRef: 11,
  MemoComponent: 14,
  SimpleMemoComponent: 15,
} as const;

/** Extract React fiber from a DOM element */
export function getFiberFromDOM(el: HTMLElement): Fiber | null {
  const key = Object.keys(el).find(
    k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'),
  );
  return key ? (el as Record<string, Fiber>)[key] : null;
}

/**
 * Find the nearest _debugSource walking up the fiber tree.
 * Handles memo/forwardRef wrappers that may carry source on their wrapped type.
 */
export function findNearestDebugSource(fiber: Fiber | null): DebugSource | null {
  let current = fiber;
  while (current) {
    // Direct _debugSource on the fiber
    if (current._debugSource) return current._debugSource;

    // Unwrap React.memo (tag 14, 15): source may be on fiber.type.type
    if (
      (current.tag === FiberTag.MemoComponent || current.tag === FiberTag.SimpleMemoComponent) &&
      current.type &&
      typeof current.type === 'object'
    ) {
      const wrapped = (current.type as { type?: { _debugSource?: DebugSource } }).type;
      if (wrapped?._debugSource) return wrapped._debugSource;
    }

    // Unwrap React.forwardRef (tag 11): source may be on fiber.type.render
    if (current.tag === FiberTag.ForwardRef && current.type && typeof current.type === 'object') {
      const render = (current.type as { render?: { _debugSource?: DebugSource } }).render;
      if (render?._debugSource) return render._debugSource;
    }

    current = current.return;
  }
  return null;
}

/** Collect all fibers from target to root */
export function traceToRoot(fiber: Fiber): Fiber[] {
  const chain: Fiber[] = [];
  let current: Fiber | null = fiber;
  while (current) {
    chain.push(current);
    current = current.return;
  }
  return chain;
}

/** Check if a fiber represents a user-defined component (function or class) */
export function isUserComponent(fiber: Fiber): boolean {
  return fiber.tag === FiberTag.FunctionComponent || fiber.tag === FiberTag.ClassComponent;
}

/**
 * Find the nearest host fiber (actual DOM element) by walking down.
 * Used to get the stateNode (HTMLElement) from a component fiber.
 */
export function findHostFiber(fiber: Fiber): Fiber | null {
  if (fiber.tag === FiberTag.HostComponent && fiber.stateNode) {
    return fiber;
  }

  let child = fiber.child;
  while (child) {
    const found = findHostFiber(child);
    if (found) return found;
    child = child.sibling;
  }

  return null;
}

/** Compare two DebugSource locations for equality */
export function sameDebugSource(a: DebugSource | null, b: DebugSource | null): boolean {
  if (!a || !b) return false;
  return (
    a.fileName === b.fileName &&
    a.lineNumber === b.lineNumber &&
    (a.columnNumber ?? 0) === (b.columnNumber ?? 0)
  );
}

/**
 * Walk all fibers in a tree, calling visitor for each.
 * DFS traversal: child → sibling.
 */
export function walkFibers(root: Fiber | null, visitor: (fiber: Fiber) => void): void {
  if (!root) return;

  const stack: Fiber[] = [root];
  while (stack.length > 0) {
    const fiber = stack.pop()!;
    visitor(fiber);

    if (fiber.sibling) stack.push(fiber.sibling);
    if (fiber.child) stack.push(fiber.child);
  }
}

/**
 * Get component display name from a fiber.
 * Handles string types (host elements), function types, and wrapped types.
 */
export function getFiberDisplayName(fiber: Fiber): string {
  const { type } = fiber;
  if (typeof type === 'string') return type;
  if (typeof type === 'function') return (type as { displayName?: string; name?: string }).displayName || (type as { name?: string }).name || 'Anonymous';
  if (typeof type === 'object' && type !== null) {
    // memo/forwardRef wrapper
    const inner = (type as { type?: unknown; render?: unknown }).type ?? (type as { render?: unknown }).render;
    if (typeof inner === 'function') {
      return (inner as { displayName?: string; name?: string }).displayName || (inner as { name?: string }).name || 'Anonymous';
    }
  }
  return 'Unknown';
}
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `bun run test client/lib/element-tracing/fiber-utils.test.ts`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add client/lib/element-tracing/fiber-utils.ts client/lib/element-tracing/fiber-utils.test.ts
git commit -m "feat(element-tracing): fiber utilities — getFiber, debugSource, traceToRoot (HYP-268)"
```

---

## Task 6: ReactAdapter

Implements `FrameworkAdapter` interface using fiber utilities.

**Files:**
- Create: `client/lib/element-tracing/react-adapter.ts`
- Create: `client/lib/element-tracing/react-adapter.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
/**
 * @file Tests for ReactAdapter — uses mock fiber trees, no real React
 */

import { describe, expect, it, beforeEach } from 'bun:test';
import { ReactAdapter } from './react-adapter';
import type { Fiber, DebugSource } from './fiber-utils';
import type { SourceLocation } from '../../../shared/element-tracing/types';

/** Build a minimal mock DOM tree with fiber references */
function createMockDOM(): {
  root: HTMLElement;
  divEl: HTMLElement;
  spanEl: HTMLElement;
  divFiber: Fiber;
  spanFiber: Fiber;
  rootFiber: Fiber;
} {
  const divEl = Object.create(HTMLElement.prototype) as HTMLElement & Record<string, unknown>;
  const spanEl = Object.create(HTMLElement.prototype) as HTMLElement & Record<string, unknown>;
  const root = Object.create(HTMLElement.prototype) as HTMLElement & Record<string, unknown>;

  const appSource: DebugSource = { fileName: '/app/src/App.tsx', lineNumber: 5, columnNumber: 4 };
  const spanSource: DebugSource = { fileName: '/app/src/App.tsx', lineNumber: 6, columnNumber: 6 };

  const spanFiber: Fiber = {
    tag: 5,
    type: 'span',
    stateNode: spanEl as HTMLElement,
    return: null, // set below
    child: null,
    sibling: null,
    memoizedProps: { className: 'text' },
    _debugSource: spanSource,
    _debugOwner: null,
  };

  const divFiber: Fiber = {
    tag: 5,
    type: 'div',
    stateNode: divEl as HTMLElement,
    return: null, // set below
    child: spanFiber,
    sibling: null,
    memoizedProps: {},
    _debugSource: appSource,
    _debugOwner: null,
  };
  spanFiber.return = divFiber;

  // App component fiber (function component, no stateNode)
  const appFiber: Fiber = {
    tag: 0,
    type: function App() {},
    stateNode: null,
    return: null,
    child: divFiber,
    sibling: null,
    memoizedProps: {},
    _debugSource: { fileName: '/app/src/index.tsx', lineNumber: 8, columnNumber: 2 },
    _debugOwner: null,
  };
  divFiber.return = appFiber;

  // Root fiber
  const rootFiber: Fiber = {
    tag: 3,
    type: null,
    stateNode: root as HTMLElement,
    return: null,
    child: appFiber,
    sibling: null,
    memoizedProps: {},
    _debugSource: null,
    _debugOwner: null,
  };
  appFiber.return = rootFiber;

  // Attach fibers to DOM elements
  (divEl as Record<string, unknown>)['__reactFiber$test'] = divFiber;
  (spanEl as Record<string, unknown>)['__reactFiber$test'] = spanFiber;
  (root as Record<string, unknown>)['__reactFiber$test'] = rootFiber;

  return { root: root as HTMLElement, divEl: divEl as HTMLElement, spanEl: spanEl as HTMLElement, divFiber, spanFiber, rootFiber };
}

describe('ReactAdapter', () => {
  let adapter: ReactAdapter;

  beforeEach(() => {
    adapter = new ReactAdapter();
  });

  describe('getSourceLocation', () => {
    it('should extract source location from element with fiber', () => {
      const { divEl } = createMockDOM();
      const loc = adapter.getSourceLocation(divEl);

      expect(loc).not.toBeNull();
      expect(loc!.fileName).toBe('/app/src/App.tsx');
      expect(loc!.line).toBe(5);
      expect(loc!.column).toBe(4);
    });

    it('should return null for element without fiber', () => {
      const el = Object.create(HTMLElement.prototype) as HTMLElement;
      expect(adapter.getSourceLocation(el)).toBeNull();
    });
  });

  describe('getComponentChain', () => {
    it('should return component ancestry', () => {
      const { spanEl } = createMockDOM();
      const chain = adapter.getComponentChain(spanEl);

      // Should include App (function component) but not host elements
      const appInfo = chain.find(c => c.name === 'App');
      expect(appInfo).toBeDefined();
      expect(appInfo!.source).not.toBeNull();
    });
  });

  describe('getItemIndex', () => {
    it('should return 0 for single element', () => {
      const { spanEl } = createMockDOM();
      expect(adapter.getItemIndex(spanEl)).toBe(0);
    });

    it('should count siblings with same source location', () => {
      // Build fiber tree with 3 siblings sharing same _debugSource (like .map())
      const source: DebugSource = { fileName: 'f.tsx', lineNumber: 10, columnNumber: 8 };
      const parentFiber: Fiber = {
        tag: 5, type: 'ul', stateNode: null, return: null, child: null,
        sibling: null, memoizedProps: {}, _debugSource: null, _debugOwner: null,
      };

      const li1: Fiber = {
        tag: 5, type: 'li',
        stateNode: Object.create(HTMLElement.prototype),
        return: parentFiber, child: null, sibling: null,
        memoizedProps: {}, _debugSource: source, _debugOwner: null,
      };

      const li2: Fiber = {
        tag: 5, type: 'li',
        stateNode: Object.create(HTMLElement.prototype),
        return: parentFiber, child: null, sibling: null,
        memoizedProps: {}, _debugSource: source, _debugOwner: null,
      };

      const li3: Fiber = {
        tag: 5, type: 'li',
        stateNode: Object.create(HTMLElement.prototype),
        return: parentFiber, child: null, sibling: null,
        memoizedProps: {}, _debugSource: source, _debugOwner: null,
      };

      parentFiber.child = li1;
      li1.sibling = li2;
      li2.sibling = li3;

      (li2.stateNode as Record<string, unknown>)['__reactFiber$test'] = li2;

      expect(adapter.getItemIndex(li2.stateNode as HTMLElement)).toBe(1);
    });
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `bun run test client/lib/element-tracing/react-adapter.test.ts`
Expected: FAIL — `Cannot find module './react-adapter'`

- [ ] **Step 3: Implement ReactAdapter**

```typescript
/**
 * @file ReactAdapter — FrameworkAdapter implementation for React
 *
 * Accessed via: ElementTracer inside iframe preview
 * Assumptions: React dev mode with _debugSource on fiber nodes.
 * __reactFiber$ property exists on all React-rendered DOM elements since React 16.
 */

import type { ComponentInfo, ComponentTreeNode, FrameworkAdapter, SourceLocation } from '../../../shared/element-tracing/types';
import {
  type DebugSource,
  type Fiber,
  FiberTag,
  findHostFiber,
  findNearestDebugSource,
  getFiberDisplayName,
  getFiberFromDOM,
  isUserComponent,
  sameDebugSource,
  traceToRoot,
  walkFibers,
} from './fiber-utils';

function debugSourceToLocation(ds: DebugSource): SourceLocation {
  return {
    fileName: ds.fileName,
    line: ds.lineNumber,
    column: ds.columnNumber ?? 0,
  };
}

function fiberToComponentInfo(fiber: Fiber): ComponentInfo {
  const name = getFiberDisplayName(fiber);
  const source = fiber._debugSource ? debugSourceToLocation(fiber._debugSource) : null;
  const isLibrary = typeof fiber.type === 'function' &&
    (fiber._debugSource?.fileName?.includes('node_modules') ?? false);

  // Serialize props (truncate values for transport)
  const props: Record<string, string> = {};
  if (fiber.memoizedProps && typeof fiber.memoizedProps === 'object') {
    for (const [key, val] of Object.entries(fiber.memoizedProps)) {
      if (key === 'children') continue;
      const str = typeof val === 'string' ? val :
                  typeof val === 'number' || typeof val === 'boolean' ? String(val) :
                  typeof val === 'function' ? '[fn]' :
                  '[object]';
      props[key] = str.length > 50 ? `${str.slice(0, 47)}...` : str;
    }
  }

  return { name, source, props, isLibrary };
}

export class ReactAdapter implements FrameworkAdapter {
  readonly name = 'react';

  detect(doc: Document): boolean {
    const root = this.findReactRoot(doc);
    if (!root) return false;

    const fiber = getFiberFromDOM(root);
    if (!fiber) return false;

    // Validate _debugSource exists somewhere in the tree
    const source = findNearestDebugSource(fiber);
    if (!source) return false;

    return typeof source.fileName === 'string' && typeof source.lineNumber === 'number';
  }

  getSourceLocation(element: HTMLElement): SourceLocation | null {
    const fiber = getFiberFromDOM(element);
    if (!fiber) return null;

    const source = findNearestDebugSource(fiber);
    if (!source) return null;

    return debugSourceToLocation(source);
  }

  getComponentChain(element: HTMLElement): ComponentInfo[] {
    const fiber = getFiberFromDOM(element);
    if (!fiber) return [];

    return traceToRoot(fiber)
      .filter(isUserComponent)
      .map(fiberToComponentInfo);
  }

  getItemIndex(element: HTMLElement): number {
    const fiber = getFiberFromDOM(element);
    if (!fiber) return 0;

    const mySource = findNearestDebugSource(fiber);
    if (!mySource || !fiber.return?.child) return 0;

    let index = 0;
    let sibling: Fiber | null = fiber.return.child;
    while (sibling && sibling !== fiber) {
      const sibSource = findNearestDebugSource(sibling);
      if (sameDebugSource(sibSource, mySource)) {
        index++;
      }
      sibling = sibling.sibling;
    }

    return index;
  }

  walkComponentTree(rootElement: HTMLElement): ComponentTreeNode[] {
    const rootFiber = getFiberFromDOM(rootElement);
    if (!rootFiber) return [];

    return this.buildTreeFromFiber(rootFiber);
  }

  findDOMElement(source: SourceLocation, itemIndex: number): HTMLElement | null {
    const root = this.findReactRoot(typeof document !== 'undefined' ? document : null!);
    if (!root) return null;

    const rootFiber = getFiberFromDOM(root);
    if (!rootFiber) return null;

    const matches: HTMLElement[] = [];
    walkFibers(rootFiber, (fiber) => {
      const ds = fiber._debugSource;
      if (
        ds &&
        ds.fileName === source.fileName &&
        ds.lineNumber === source.line &&
        (source.column === 0 || (ds.columnNumber ?? 0) === source.column)
      ) {
        const host = findHostFiber(fiber);
        if (host?.stateNode instanceof HTMLElement) {
          matches.push(host.stateNode);
        }
      }
    });

    return matches[itemIndex] ?? null;
  }

  /** Find the React root container element */
  private findReactRoot(doc: Document | null): HTMLElement | null {
    if (!doc) return null;

    // Common React root selectors
    const candidates = [
      doc.getElementById('root'),
      doc.getElementById('__next'),
      doc.getElementById('app'),
      doc.querySelector('[data-reactroot]'),
    ];

    for (const el of candidates) {
      if (el && getFiberFromDOM(el as HTMLElement)) {
        return el as HTMLElement;
      }
    }

    // Fallback: find any element with a React fiber
    const body = doc.body;
    if (!body) return null;

    for (const child of Array.from(body.children)) {
      if (getFiberFromDOM(child as HTMLElement)) {
        return child as HTMLElement;
      }
    }

    return null;
  }

  private buildTreeFromFiber(fiber: Fiber): ComponentTreeNode[] {
    const nodes: ComponentTreeNode[] = [];
    let child = fiber.child;

    while (child) {
      if (isUserComponent(child)) {
        const source = child._debugSource ? debugSourceToLocation(child._debugSource) : null;
        const host = findHostFiber(child);

        nodes.push({
          name: getFiberDisplayName(child),
          source,
          children: this.buildTreeFromFiber(child),
          domElement: host?.stateNode ?? null,
          fiberTag: child.tag,
        });
      } else if (child.tag === FiberTag.HostComponent) {
        // Host element — recurse into children to find nested components
        nodes.push(...this.buildTreeFromFiber(child));
      }
      child = child.sibling;
    }

    return nodes;
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `bun run test client/lib/element-tracing/react-adapter.test.ts`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add client/lib/element-tracing/react-adapter.ts client/lib/element-tracing/react-adapter.test.ts
git commit -m "feat(element-tracing): ReactAdapter — fiber-based FrameworkAdapter impl (HYP-268)"
```

---

## Task 7: Sync State Machine

Client-side state machine that handles the race between HMR reload and NodeMap update delivery.

**Files:**
- Create: `client/lib/element-tracing/sync-state-machine.ts`
- Create: `client/lib/element-tracing/sync-state-machine.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
/**
 * @file Tests for TracingSyncStateMachine
 */

import { describe, expect, it, beforeEach, mock } from 'bun:test';
import { TracingSyncStateMachine } from './sync-state-machine';
import type { SyncState } from '../../../shared/element-tracing/types';

describe('TracingSyncStateMachine', () => {
  let machine: TracingSyncStateMachine;
  let stateChanges: SyncState[];

  beforeEach(() => {
    stateChanges = [];
    machine = new TracingSyncStateMachine({
      onStateChange: (state) => stateChanges.push(state),
      timeoutMs: 100,
    });
  });

  it('should start in synced state', () => {
    expect(machine.state).toBe('synced');
  });

  it('should transition to awaiting-both on fileChanged', () => {
    machine.fileChanged();
    expect(machine.state).toBe('awaiting-both');
  });

  it('should transition through awaiting-hmr when map arrives first', () => {
    machine.fileChanged();
    machine.mapReceived();
    expect(machine.state).toBe('awaiting-hmr');
  });

  it('should transition through awaiting-map when HMR arrives first', () => {
    machine.fileChanged();
    machine.hmrCompleted();
    expect(machine.state).toBe('awaiting-map');
  });

  it('should return to synced when both arrive (map first)', () => {
    machine.fileChanged();
    machine.mapReceived();
    machine.hmrCompleted();
    expect(machine.state).toBe('synced');
  });

  it('should return to synced when both arrive (HMR first)', () => {
    machine.fileChanged();
    machine.hmrCompleted();
    machine.mapReceived();
    expect(machine.state).toBe('synced');
  });

  it('should queue clicks while not synced', () => {
    const clickHandler = mock(() => {});
    machine.fileChanged();

    machine.queueClick({ handler: clickHandler, args: ['arg1'] });
    expect(clickHandler).not.toHaveBeenCalled();

    machine.mapReceived();
    machine.hmrCompleted();

    // Queued click should replay
    expect(clickHandler).toHaveBeenCalledTimes(1);
    expect(clickHandler).toHaveBeenCalledWith('arg1');
  });

  it('should not queue clicks when synced', () => {
    const clickHandler = mock(() => {});
    const queued = machine.queueClick({ handler: clickHandler, args: ['arg1'] });

    // In synced state, click should NOT be queued — return false
    expect(queued).toBe(false);
  });

  it('should notify on state changes', () => {
    machine.fileChanged();
    machine.mapReceived();
    machine.hmrCompleted();

    expect(stateChanges).toEqual(['awaiting-both', 'awaiting-hmr', 'synced']);
  });

  it('should force-sync after timeout', async () => {
    machine.fileChanged();
    expect(machine.state).toBe('awaiting-both');

    // Wait for timeout
    await new Promise(resolve => setTimeout(resolve, 150));

    expect(machine.state).toBe('synced');
  });

  it('should handle rapid fileChanged calls (reset to awaiting-both)', () => {
    machine.fileChanged();
    machine.mapReceived();
    expect(machine.state).toBe('awaiting-hmr');

    // Another file change while waiting for HMR
    machine.fileChanged();
    expect(machine.state).toBe('awaiting-both');
  });

  it('should clear queue on dispose', () => {
    const clickHandler = mock(() => {});
    machine.fileChanged();
    machine.queueClick({ handler: clickHandler, args: [] });

    machine.dispose();
    // Queued clicks should NOT replay
    expect(clickHandler).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `bun run test client/lib/element-tracing/sync-state-machine.test.ts`
Expected: FAIL — `Cannot find module './sync-state-machine'`

- [ ] **Step 3: Implement sync state machine**

```typescript
/**
 * @file Client-side sync state machine for element tracing
 *
 * Accessed via: ElementTracer — manages click availability during HMR/map-update race
 * Assumptions: Both HMR completion and map update arrive within timeoutMs (3000 default)
 */

import type { SyncState } from '../../../shared/element-tracing/types';

interface QueuedClick {
  handler: (...args: unknown[]) => void;
  args: unknown[];
}

interface TracingSyncOptions {
  onStateChange?: (state: SyncState) => void;
  /** Timeout in ms before force-syncing (default 3000) */
  timeoutMs?: number;
}

export class TracingSyncStateMachine {
  private _state: SyncState = 'synced';
  private _queue: QueuedClick[] = [];
  private _timer: ReturnType<typeof setTimeout> | null = null;
  private _options: Required<TracingSyncOptions>;

  constructor(options: TracingSyncOptions = {}) {
    this._options = {
      onStateChange: options.onStateChange ?? (() => {}),
      timeoutMs: options.timeoutMs ?? 3000,
    };
  }

  get state(): SyncState {
    return this._state;
  }

  /** A file was changed (mutation or external edit) — expect both map update and HMR */
  fileChanged(): void {
    this.clearTimer();
    this.setState('awaiting-both');
    this.startTimer();
  }

  /** NodeMap update received from server */
  mapReceived(): void {
    if (this._state === 'awaiting-both') {
      this.setState('awaiting-hmr');
    } else if (this._state === 'awaiting-map') {
      this.syncCompleted();
    }
    // Ignore if already synced or awaiting-hmr
  }

  /** HMR reload completed (DOM updated) */
  hmrCompleted(): void {
    if (this._state === 'awaiting-both') {
      this.setState('awaiting-map');
    } else if (this._state === 'awaiting-hmr') {
      this.syncCompleted();
    }
    // Ignore if already synced or awaiting-map
  }

  /**
   * Queue a click interaction while not synced.
   * @returns true if click was queued, false if not needed (already synced)
   */
  queueClick(click: QueuedClick): boolean {
    if (this._state === 'synced') return false;
    this._queue.push(click);
    return true;
  }

  /** Clean up timers and queued clicks */
  dispose(): void {
    this.clearTimer();
    this._queue = [];
  }

  private setState(state: SyncState): void {
    if (this._state === state) return;
    this._state = state;
    this._options.onStateChange(state);
  }

  private syncCompleted(): void {
    this.clearTimer();
    this.setState('synced');
    this.replayQueue();
  }

  private replayQueue(): void {
    const queue = this._queue;
    this._queue = [];
    for (const click of queue) {
      click.handler(...click.args);
    }
  }

  private startTimer(): void {
    this._timer = setTimeout(() => {
      // Force-sync: proceed with whatever we have
      this.syncCompleted();
    }, this._options.timeoutMs);
  }

  private clearTimer(): void {
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `bun run test client/lib/element-tracing/sync-state-machine.test.ts`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add client/lib/element-tracing/sync-state-machine.ts client/lib/element-tracing/sync-state-machine.test.ts
git commit -m "feat(element-tracing): sync state machine — HMR/map-update race handling (HYP-268)"
```

---

## Task 8: TracingTransport + WSTracingTransport

Transport interface is in shared types. `WSTracingTransport` is a WebSocket-based client for SaaS.

**Files:**
- Create: `client/lib/element-tracing/ws-tracing-transport.ts`
- Create: `client/lib/element-tracing/ws-tracing-transport.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
/**
 * @file Tests for WSTracingTransport — uses mock WebSocket
 */

import { describe, expect, it, beforeEach, mock } from 'bun:test';
import { WSTracingTransport } from './ws-tracing-transport';
import type { TracingServerMessage, ResolveElement } from '../../../shared/element-tracing/types';

/** Minimal mock WebSocket */
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;

  sentMessages: string[] = [];

  constructor(public url: string) {
    // Simulate async open
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN;
      this.onopen?.();
    }, 0);
  }

  send(data: string): void {
    this.sentMessages.push(data);
  }

  close(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  simulateMessage(msg: TracingServerMessage): void {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

describe('WSTracingTransport', () => {
  let transport: WSTracingTransport;
  let mockWs: MockWebSocket;

  beforeEach(async () => {
    mockWs = new MockWebSocket('ws://test/element-tracing'); // nosemgrep: detect-insecure-websocket -- test mock
    transport = new WSTracingTransport(() => mockWs as unknown as WebSocket);

    // Wait for connection
    await new Promise(resolve => setTimeout(resolve, 10));
  });

  it('should report connected after WebSocket opens', () => {
    expect(transport.connected).toBe(true);
  });

  it('should send messages as JSON', () => {
    const msg: ResolveElement = {
      type: 'resolve-element',
      requestId: 'req-1',
      source: { fileName: 'App.tsx', line: 10, column: 4 },
      itemIndex: 0,
    };

    transport.send(msg);

    expect(mockWs.sentMessages.length).toBe(1);
    expect(JSON.parse(mockWs.sentMessages[0])).toEqual(msg);
  });

  it('should dispatch received messages to handlers', () => {
    const handler = mock(() => {});
    transport.onMessage(handler);

    const serverMsg: TracingServerMessage = {
      type: 'node-map-update',
      filePath: 'src/App.tsx',
      fileHash: 'abc123',
      version: 1,
      nodes: [],
    };

    mockWs.simulateMessage(serverMsg);

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith(serverMsg);
  });

  it('should support unsubscribe from onMessage', () => {
    const handler = mock(() => {});
    const unsub = transport.onMessage(handler);
    unsub();

    mockWs.simulateMessage({
      type: 'node-map-invalidate',
      filePath: 'src/App.tsx',
    });

    expect(handler).not.toHaveBeenCalled();
  });

  it('should notify on connection change', () => {
    const handler = mock(() => {});
    transport.onConnectionChange(handler);

    mockWs.close();

    expect(handler).toHaveBeenCalledWith(false);
  });

  it('should report disconnected after close', () => {
    mockWs.close();
    expect(transport.connected).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `bun run test client/lib/element-tracing/ws-tracing-transport.test.ts`
Expected: FAIL — `Cannot find module './ws-tracing-transport'`

- [ ] **Step 3: Implement WSTracingTransport**

```typescript
/**
 * @file WebSocket-based TracingTransport for SaaS platform
 *
 * Accessed via: ElementTracer in iframe (SaaS deployment)
 * Assumptions: Server exposes WS endpoint at /api/element-tracing/:projectId
 */

import type {
  TracingClientMessage,
  TracingServerMessage,
  TracingTransport,
} from '../../../shared/element-tracing/types';

type MessageHandler = (msg: TracingServerMessage) => void;
type ConnectionHandler = (connected: boolean) => void;
type WebSocketFactory = () => WebSocket;

export class WSTracingTransport implements TracingTransport {
  private _ws: WebSocket;
  private _connected = false;
  private _messageHandlers = new Set<MessageHandler>();
  private _connectionHandlers = new Set<ConnectionHandler>();

  constructor(wsFactory: WebSocketFactory) {
    this._ws = wsFactory();
    this.wireEvents();
  }

  get connected(): boolean {
    return this._connected;
  }

  send(msg: TracingClientMessage): void {
    if (!this._connected) return;
    this._ws.send(JSON.stringify(msg));
  }

  onMessage(handler: MessageHandler): () => void {
    this._messageHandlers.add(handler);
    return () => this._messageHandlers.delete(handler);
  }

  onConnectionChange(handler: ConnectionHandler): () => void {
    this._connectionHandlers.add(handler);
    return () => this._connectionHandlers.delete(handler);
  }

  dispose(): void {
    this._messageHandlers.clear();
    this._connectionHandlers.clear();
    this._ws.close();
  }

  private wireEvents(): void {
    this._ws.onopen = () => {
      this._connected = true;
      this.notifyConnection(true);
    };

    this._ws.onclose = () => {
      this._connected = false;
      this.notifyConnection(false);
    };

    this._ws.onerror = () => {
      this._connected = false;
      this.notifyConnection(false);
    };

    this._ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string) as TracingServerMessage;
        for (const handler of this._messageHandlers) {
          handler(msg);
        }
      } catch {
        // Invalid JSON — ignore
      }
    };
  }

  private notifyConnection(connected: boolean): void {
    for (const handler of this._connectionHandlers) {
      handler(connected);
    }
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `bun run test client/lib/element-tracing/ws-tracing-transport.test.ts`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add client/lib/element-tracing/ws-tracing-transport.ts client/lib/element-tracing/ws-tracing-transport.test.ts
git commit -m "feat(element-tracing): WSTracingTransport — WebSocket client for SaaS (HYP-268)"
```

---

## Task 9: ElementTracer

Client orchestrator that wires adapter, transport, and sync state machine together.

**Files:**
- Create: `client/lib/element-tracing/element-tracer.ts`
- Create: `client/lib/element-tracing/element-tracer.test.ts`
- Create: `client/lib/element-tracing/index.ts`

- [ ] **Step 1: Write failing tests**

```typescript
/**
 * @file Tests for ElementTracer — client orchestrator
 */

import { describe, expect, it, beforeEach, mock } from 'bun:test';
import { ElementTracer } from './element-tracer';
import type {
  FrameworkAdapter,
  SourceLocation,
  TracingTransport,
  TracingClientMessage,
  TracingServerMessage,
  ComponentInfo,
  ComponentTreeNode,
  NodeMapUpdate,
} from '../../../shared/element-tracing/types';

/** Mock adapter */
function mockAdapter(overrides: Partial<FrameworkAdapter> = {}): FrameworkAdapter {
  return {
    name: 'react',
    detect: () => true,
    getSourceLocation: () => ({ fileName: 'App.tsx', line: 10, column: 4 }),
    getComponentChain: () => [],
    getItemIndex: () => 0,
    walkComponentTree: () => [],
    findDOMElement: () => null,
    ...overrides,
  };
}

/** Mock transport */
function mockTransport(): TracingTransport & {
  handlers: Set<(msg: TracingServerMessage) => void>;
  connHandlers: Set<(connected: boolean) => void>;
  sent: TracingClientMessage[];
  simulateMessage(msg: TracingServerMessage): void;
} {
  const handlers = new Set<(msg: TracingServerMessage) => void>();
  const connHandlers = new Set<(connected: boolean) => void>();
  const sent: TracingClientMessage[] = [];

  return {
    connected: true,
    handlers,
    connHandlers,
    sent,
    send(msg: TracingClientMessage) { sent.push(msg); },
    onMessage(handler) { handlers.add(handler); return () => handlers.delete(handler); },
    onConnectionChange(handler) { connHandlers.add(handler); return () => connHandlers.delete(handler); },
    simulateMessage(msg: TracingServerMessage) {
      for (const h of handlers) h(msg);
    },
  };
}

describe('ElementTracer', () => {
  let tracer: ElementTracer;
  let adapter: FrameworkAdapter;
  let transport: ReturnType<typeof mockTransport>;

  beforeEach(() => {
    adapter = mockAdapter();
    transport = mockTransport();
    tracer = new ElementTracer(adapter, transport);
  });

  it('should resolve click to source location via adapter', () => {
    const el = {} as HTMLElement;
    const result = tracer.resolveClick(el);

    expect(result).not.toBeNull();
    expect(result!.source.fileName).toBe('App.tsx');
    expect(result!.source.line).toBe(10);
    expect(result!.itemIndex).toBe(0);
  });

  it('should send resolve-element to transport on click', () => {
    const el = {} as HTMLElement;
    tracer.resolveClick(el);

    expect(transport.sent.length).toBe(1);
    expect(transport.sent[0].type).toBe('resolve-element');
  });

  it('should return null when adapter returns no source', () => {
    adapter = mockAdapter({ getSourceLocation: () => null });
    tracer = new ElementTracer(adapter, transport);

    const result = tracer.resolveClick({} as HTMLElement);
    expect(result).toBeNull();
  });

  it('should store received node maps', () => {
    const update: NodeMapUpdate = {
      type: 'node-map-update',
      filePath: 'src/App.tsx',
      fileHash: 'abc',
      version: 1,
      nodes: [
        {
          nodeRef: 'src/App.tsx:0',
          tag: 'div',
          loc: { fileName: 'src/App.tsx', line: 5, column: 4 },
          endLoc: { fileName: 'src/App.tsx', line: 10, column: 10 },
          parentRef: null,
          children: [],
          isComponent: false,
        },
      ],
    };

    transport.simulateMessage(update);
    expect(tracer.getNodeMap('src/App.tsx')).not.toBeNull();
    expect(tracer.getNodeMap('src/App.tsx')!.length).toBe(1);
  });

  it('should clear node map on invalidate', () => {
    transport.simulateMessage({
      type: 'node-map-update',
      filePath: 'src/App.tsx',
      fileHash: 'abc',
      version: 1,
      nodes: [],
    });

    transport.simulateMessage({
      type: 'node-map-invalidate',
      filePath: 'src/App.tsx',
    });

    expect(tracer.getNodeMap('src/App.tsx')).toBeNull();
  });

  it('should update selection on resolve-element-response', () => {
    const onSelect = mock(() => {});
    tracer.onSelectionResolved(onSelect);

    transport.simulateMessage({
      type: 'resolve-element-response',
      requestId: 'req-1',
      nodeRef: 'src/App.tsx:0',
      entry: {
        nodeRef: 'src/App.tsx:0',
        tag: 'div',
        loc: { fileName: 'src/App.tsx', line: 5, column: 4 },
        endLoc: { fileName: 'src/App.tsx', line: 5, column: 30 },
        parentRef: null,
        children: [],
        isComponent: false,
      },
    });

    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('should dispose cleanly', () => {
    tracer.dispose();
    // No errors expected
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `bun run test client/lib/element-tracing/element-tracer.test.ts`
Expected: FAIL — `Cannot find module './element-tracer'`

- [ ] **Step 3: Implement ElementTracer**

```typescript
/**
 * @file ElementTracer — client orchestrator for fiber-based element tracing
 *
 * Accessed via: Editor components inside iframe preview
 * Assumptions: Adapter and transport are injected; adapter matches the current framework
 */

import type {
  FrameworkAdapter,
  NodeMapEntry,
  NodeMapUpdate,
  ResolveElement,
  ResolveElementResponse,
  SourceLocation,
  TracingServerMessage,
  TracingTransport,
} from '../../../shared/element-tracing/types';

interface ClickResult {
  source: SourceLocation;
  itemIndex: number;
  requestId: string;
}

type SelectionHandler = (response: ResolveElementResponse) => void;

let requestCounter = 0;

export class ElementTracer {
  private _nodeMaps = new Map<string, NodeMapEntry[]>();
  private _selectionHandlers = new Set<SelectionHandler>();
  private _unsubMessage: (() => void) | null = null;

  constructor(
    private _adapter: FrameworkAdapter,
    private _transport: TracingTransport,
  ) {
    this._unsubMessage = this._transport.onMessage(this.handleMessage);
  }

  /** Resolve a DOM click to source location and send to server */
  resolveClick(element: HTMLElement): ClickResult | null {
    const source = this._adapter.getSourceLocation(element);
    if (!source) return null;

    const itemIndex = this._adapter.getItemIndex(element);
    const requestId = `et-${++requestCounter}`;

    const msg: ResolveElement = {
      type: 'resolve-element',
      requestId,
      source,
      itemIndex,
    };

    this._transport.send(msg);

    return { source, itemIndex, requestId };
  }

  /** Subscribe to resolved selections */
  onSelectionResolved(handler: SelectionHandler): () => void {
    this._selectionHandlers.add(handler);
    return () => this._selectionHandlers.delete(handler);
  }

  /** Get cached node map for a file */
  getNodeMap(filePath: string): NodeMapEntry[] | null {
    return this._nodeMaps.get(filePath) ?? null;
  }

  /** Find DOM element by source location (reverse mapping) */
  findDOMElement(source: SourceLocation, itemIndex: number): HTMLElement | null {
    return this._adapter.findDOMElement(source, itemIndex);
  }

  dispose(): void {
    this._unsubMessage?.();
    this._selectionHandlers.clear();
    this._nodeMaps.clear();
  }

  private handleMessage = (msg: TracingServerMessage): void => {
    switch (msg.type) {
      case 'node-map-update':
        this._nodeMaps.set(msg.filePath, msg.nodes);
        break;

      case 'node-map-invalidate':
        this._nodeMaps.delete(msg.filePath);
        break;

      case 'resolve-element-response':
        for (const handler of this._selectionHandlers) {
          handler(msg);
        }
        break;
    }
  };
}
```

- [ ] **Step 4: Create index.ts re-exports**

```typescript
/**
 * @file Public API for element tracing client module
 */

export { ElementTracer } from './element-tracer';
export { ReactAdapter } from './react-adapter';
export { WSTracingTransport } from './ws-tracing-transport';
export { TracingSyncStateMachine } from './sync-state-machine';
export * from './fiber-utils';
```

- [ ] **Step 5: Run tests — verify they pass**

Run: `bun run test client/lib/element-tracing/element-tracer.test.ts`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add client/lib/element-tracing/element-tracer.ts client/lib/element-tracing/element-tracer.test.ts client/lib/element-tracing/index.ts
git commit -m "feat(element-tracing): ElementTracer — client orchestrator (HYP-268)"
```

---

## Task 10: Server WS Channel

Server-side WebSocket handler for `element-tracing` channel. Integrates with Bun.serve's existing websocket handler.

**Files:**
- Create: `server/services/element-tracing-channel.ts`
- Modify: `server/proxy/shared.ts` (extend WSData)
- Modify: `server/main.ts` (add WS handler branch + route)

- [ ] **Step 1: Implement element-tracing channel service**

```typescript
/**
 * @file WS channel handler for element-tracing protocol
 *
 * Accessed via: Bun.serve websocket handler — routes element-tracing messages
 * Assumptions: WSData.isElementTracing is set by the upgrade handler in main.ts
 */

import type { Server, ServerWebSocket } from 'bun';
import type {
  NodeMapUpdate,
  NodeMapInvalidate,
  ResolveElement,
  ResolveElementResponse,
  TracingServerMessage,
} from '../../shared/element-tracing/types';
import type { WSData } from '../proxy/shared';
import { NodeMapService } from '../../lib/element-tracing/node-map-service';

/** Per-project tracing state */
interface ProjectTracingState {
  nodeMapService: NodeMapService;
  clients: Set<ServerWebSocket<WSData>>;
}

const projectStates = new Map<string, ProjectTracingState>();

function getOrCreateState(projectId: string): ProjectTracingState {
  let state = projectStates.get(projectId);
  if (!state) {
    state = {
      nodeMapService: new NodeMapService(),
      clients: new Set(),
    };
    projectStates.set(projectId, state);
  }
  return state;
}

/** Register a client WS connection for element tracing */
export function onTracingClientConnect(ws: ServerWebSocket<WSData>): void {
  const { projectId } = ws.data;
  const state = getOrCreateState(projectId);
  state.clients.add(ws);

  // Send current node maps to newly connected client
  for (const filePath of state.nodeMapService.getTrackedFiles()) {
    const update = state.nodeMapService.buildUpdateMessage(filePath);
    if (update) {
      ws.send(JSON.stringify(update));
    }
  }
}

/** Handle disconnect */
export function onTracingClientDisconnect(ws: ServerWebSocket<WSData>): void {
  const { projectId } = ws.data;
  const state = projectStates.get(projectId);
  if (state) {
    state.clients.delete(ws);
    // Clean up empty states
    if (state.clients.size === 0) {
      projectStates.delete(projectId);
    }
  }
}

/** Handle incoming message from client */
export function onTracingClientMessage(ws: ServerWebSocket<WSData>, data: string): void {
  try {
    const msg = JSON.parse(data);
    if (msg.type === 'resolve-element') {
      handleResolveElement(ws, msg as ResolveElement);
    }
  } catch {
    // Invalid JSON — ignore
  }
}

function handleResolveElement(ws: ServerWebSocket<WSData>, msg: ResolveElement): void {
  const { projectId } = ws.data;
  const state = projectStates.get(projectId);
  if (!state) {
    sendResponse(ws, {
      type: 'resolve-element-response',
      requestId: msg.requestId,
      nodeRef: null,
      entry: null,
    });
    return;
  }

  const entry = state.nodeMapService.resolveSourceLocation(msg.source);
  sendResponse(ws, {
    type: 'resolve-element-response',
    requestId: msg.requestId,
    nodeRef: entry?.nodeRef ?? null,
    entry: entry ?? null,
  });
}

/** Broadcast a message to all connected clients for a project */
export function broadcastToProject(projectId: string, msg: TracingServerMessage): void {
  const state = projectStates.get(projectId);
  if (!state) return;

  const data = JSON.stringify(msg);
  for (const client of state.clients) {
    try {
      client.send(data);
    } catch {
      state.clients.delete(client);
    }
  }
}

/** Get the NodeMapService for a project (for use in mutation routes) */
export function getNodeMapService(projectId: string): NodeMapService | null {
  return projectStates.get(projectId)?.nodeMapService ?? null;
}

function sendResponse(ws: ServerWebSocket<WSData>, msg: TracingServerMessage): void {
  ws.send(JSON.stringify(msg));
}
```

- [ ] **Step 2: Extend WSData in shared.ts**

In `server/proxy/shared.ts`, add `isElementTracing` to WSData:

```typescript
// Add to WSData interface:
isElementTracing?: boolean;
```

- [ ] **Step 3: Add WS upgrade route and handler branch in main.ts**

In `server/main.ts`, inside the `fetch` handler before the 404 catch-all, add:

```typescript
// Element tracing WebSocket upgrade
if (pathname.startsWith('/api/element-tracing/') && req.headers.get('upgrade') === 'websocket') {
  const projectIdMatch = pathname.match(/^\/api\/element-tracing\/([a-f0-9-]+)/);
  if (!projectIdMatch) {
    return new Response('Invalid project path', { status: 400 });
  }
  const upgraded = server.upgrade(req, {
    data: {
      projectId: projectIdMatch[1],
      targetHost: '',
      path: pathname,
      backendWs: null,
      isElementTracing: true,
    } satisfies WSData,
  });
  if (upgraded) return;
  return new Response('WebSocket upgrade failed', { status: 500 });
}
```

In the `websocket.open` handler, add early return for tracing connections:

```typescript
open(ws) {
  const data = ws.data as WSData;

  if (data.isElementTracing) {
    onTracingClientConnect(ws);
    return;
  }
  // ... existing proxy code
}
```

In `websocket.message`, add tracing branch:

```typescript
message(ws, message) {
  const data = ws.data as WSData;

  if (data.isElementTracing) {
    onTracingClientMessage(ws, typeof message === 'string' ? message : new TextDecoder().decode(message as ArrayBuffer));
    return;
  }
  // ... existing proxy code
}
```

In `websocket.close`, add tracing cleanup:

```typescript
close(ws) {
  const data = ws.data as WSData;

  if (data.isElementTracing) {
    onTracingClientDisconnect(ws);
    return;
  }
  // ... existing proxy code
}
```

- [ ] **Step 4: Verify compilation**

Run: `npx tsc --noEmit`
Expected: no new errors

- [ ] **Step 5: Commit**

```bash
git add server/services/element-tracing-channel.ts server/proxy/shared.ts server/main.ts
git commit -m "feat(element-tracing): server WS channel — upgrade, dispatch, broadcast (HYP-268)"
```

---

## Task 11: PostMessageTracingTransport (Extension)

Transport for VS Code extension that uses postMessage through webview panels and StateHub.

**Files:**
- Create: `vscode-extension/hypercanvas-preview/src/services/element-tracing/post-message-tracing-transport.ts`

- [ ] **Step 1: Implement PostMessageTracingTransport**

```typescript
/**
 * @file PostMessage-based TracingTransport for VS Code extension
 *
 * Accessed via: Extension iframe interaction layer
 * Assumptions: Messages flow through VS Code webview postMessage.
 * StateHub broadcasts to all panels — selection state reaches preview, left panel, right panel.
 */

import type {
  TracingClientMessage,
  TracingServerMessage,
  TracingTransport,
} from '../../../../../shared/element-tracing/types';

type MessageHandler = (msg: TracingServerMessage) => void;
type ConnectionHandler = (connected: boolean) => void;

const TRACING_PREFIX = 'element-tracing:';

/**
 * PostMessage transport for iframe ↔ extension host communication.
 *
 * In iframe context: sends to window.parent via postMessage, receives via message event.
 * In extension host context: receives from webview onDidReceiveMessage, sends via webview.postMessage.
 */
export class PostMessageTracingTransport implements TracingTransport {
  private _messageHandlers = new Set<MessageHandler>();
  private _connectionHandlers = new Set<ConnectionHandler>();
  private _connected = true; // postMessage is always "connected" (no network)
  private _listener: ((event: MessageEvent) => void) | null = null;

  /**
   * @param mode 'iframe' for client-side (sends to parent), 'host' for extension host
   */
  constructor(private readonly mode: 'iframe' | 'host') {
    if (mode === 'iframe' && typeof window !== 'undefined') {
      this._listener = (event: MessageEvent) => {
        const data = event.data;
        if (data && typeof data === 'object' && typeof data.type === 'string' && data.type.startsWith(TRACING_PREFIX)) {
          const innerType = data.type.slice(TRACING_PREFIX.length);
          const msg = { ...data.payload, type: innerType } as TracingServerMessage;
          for (const handler of this._messageHandlers) {
            handler(msg);
          }
        }
      };
      window.addEventListener('message', this._listener);
    }
  }

  get connected(): boolean {
    return this._connected;
  }

  send(msg: TracingClientMessage): void {
    if (this.mode === 'iframe' && typeof window !== 'undefined') {
      window.parent.postMessage({
        type: `${TRACING_PREFIX}${msg.type}`,
        payload: msg,
      }, '*');
    }
    // Host mode: handled externally via onWebviewMessage
  }

  onMessage(handler: MessageHandler): () => void {
    this._messageHandlers.add(handler);
    return () => this._messageHandlers.delete(handler);
  }

  onConnectionChange(handler: ConnectionHandler): () => void {
    this._connectionHandlers.add(handler);
    return () => this._connectionHandlers.delete(handler);
  }

  /**
   * For extension host: feed a message received from webview into the transport.
   * Called by PanelRouter when it receives element-tracing messages.
   */
  receiveFromWebview(msg: TracingClientMessage): void {
    // In host mode, client messages are forwarded to handlers
    // (which are the NodeMapService handlers on the extension host side)
    // This is the reverse direction — client → host
    // Not dispatched to _messageHandlers (those are server→client)
    // Instead, emit a separate event for the host-side handler
    this._onClientMessage?.(msg);
  }

  private _onClientMessage: ((msg: TracingClientMessage) => void) | null = null;

  /** Extension host: subscribe to messages from client (iframe) */
  onClientMessage(handler: (msg: TracingClientMessage) => void): () => void {
    this._onClientMessage = handler;
    return () => { this._onClientMessage = null; };
  }

  /**
   * For extension host: send a server message to the iframe via webview.
   * Called by NodeMapService when it has updates.
   */
  sendToClient(msg: TracingServerMessage): void {
    for (const handler of this._messageHandlers) {
      handler(msg);
    }
  }

  dispose(): void {
    if (this._listener && typeof window !== 'undefined') {
      window.removeEventListener('message', this._listener);
    }
    this._messageHandlers.clear();
    this._connectionHandlers.clear();
  }
}
```

- [ ] **Step 2: Verify compilation**

Run: `cd vscode-extension/hypercanvas-preview && npx tsc --noEmit` (or project-level tsc)
Expected: no new errors

- [ ] **Step 3: Commit**

```bash
git add vscode-extension/hypercanvas-preview/src/services/element-tracing/post-message-tracing-transport.ts
git commit -m "feat(element-tracing): PostMessageTracingTransport for VS Code extension (HYP-268)"
```

---

## Task 12: Integration Tests

End-to-end test: parse file → build node map → mock fiber with matching source → resolve element via ElementTracer. Also validate `_debugSource` column format assumptions.

**Files:**
- Create: `lib/element-tracing/integration.test.ts`

- [ ] **Step 1: Write integration tests**

```typescript
/**
 * @file Integration tests for the full element-tracing pipeline
 *
 * Tests the complete flow: parse JSX → build NodeMap → simulate fiber source →
 * resolve via NodeMapService → verify match.
 */

import { describe, expect, it } from 'bun:test';
import { parse } from '@babel/parser';
import { NodeMapService } from './node-map-service';
import { buildNodeMap } from './node-map-builder';
import { mapNodeRefs, buildCompositeKey } from './stability';
import type { SourceLocation } from '../../shared/element-tracing/types';

const FIXTURE = `
import { Card } from './Card';

export const Page = () => (
  <div className="container">
    <h1>Title</h1>
    <Card title="Hello">
      <p>Content</p>
    </Card>
    <ul>
      {items.map(item => (
        <li key={item.id}>{item.name}</li>
      ))}
    </ul>
  </div>
);
`;

describe('element-tracing integration', () => {
  it('should parse fixture and build valid node map', () => {
    const service = new NodeMapService();
    const entries = service.parseAndBuild(FIXTURE, 'src/Page.tsx');

    // Should find: div, h1, Card, p, ul, li (6 elements, no Fragment)
    expect(entries.length).toBeGreaterThanOrEqual(5);

    const div = entries.find(e => e.tag === 'div');
    const h1 = entries.find(e => e.tag === 'h1');
    const card = entries.find(e => e.tag === 'Card');
    const p = entries.find(e => e.tag === 'p');
    const ul = entries.find(e => e.tag === 'ul');
    const li = entries.find(e => e.tag === 'li');

    expect(div).toBeDefined();
    expect(h1).toBeDefined();
    expect(card).toBeDefined();
    expect(card!.isComponent).toBe(true);
    expect(card!.componentName).toBe('Card');
    expect(p).toBeDefined();
    expect(ul).toBeDefined();
    expect(li).toBeDefined();

    // Parent-child relationships
    expect(h1!.parentRef).toBe(div!.nodeRef);
    expect(card!.parentRef).toBe(div!.nodeRef);
    expect(p!.parentRef).toBe(card!.nodeRef);
    expect(ul!.parentRef).toBe(div!.nodeRef);

    // div should have h1, Card, ul as children
    expect(div!.children).toContain(h1!.nodeRef);
    expect(div!.children).toContain(card!.nodeRef);
    expect(div!.children).toContain(ul!.nodeRef);
  });

  it('should resolve source location to correct nodeRef', () => {
    const service = new NodeMapService();
    const entries = service.parseAndBuild(FIXTURE, 'src/Page.tsx');

    // Simulate _debugSource pointing at <Card> element
    const card = entries.find(e => e.tag === 'Card')!;
    const resolved = service.resolveSourceLocation(card.loc);

    expect(resolved).not.toBeNull();
    expect(resolved!.nodeRef).toBe(card.nodeRef);
    expect(resolved!.tag).toBe('Card');
  });

  it('should maintain nodeRef stability after sibling insertion', () => {
    const service = new NodeMapService();
    service.parseAndBuild(FIXTURE, 'src/Page.tsx');

    const oldEntries = service.getNodeMap('src/Page.tsx')!;
    const oldCard = oldEntries.find(e => e.tag === 'Card')!;

    // Simulate adding a <nav> element before Card
    const modifiedFixture = FIXTURE.replace(
      '<Card title="Hello">',
      '<nav>Nav</nav>\n    <Card title="Hello">',
    );

    const result = service.reparseAndUpdate(modifiedFixture, 'src/Page.tsx');
    expect(result.refMapping).toBeDefined();

    // Card should be mapped to its new nodeRef
    const newCardRef = result.refMapping![oldCard.nodeRef];
    expect(newCardRef).toBeDefined();

    const newCard = result.nodes.find(e => e.nodeRef === newCardRef);
    expect(newCard).toBeDefined();
    expect(newCard!.tag).toBe('Card');
  });

  it('should handle container path normalization', () => {
    const service = new NodeMapService();
    // Container paths: _debugSource reports /app/src/Page.tsx, entries use src/Page.tsx
    // Mapping: /app/ → '' (strip container prefix to get project-relative path)
    service.setPathMapping('/app/', '');
    service.parseAndBuild(FIXTURE, 'src/Page.tsx');

    const entries = service.getNodeMap('src/Page.tsx')!;
    const div = entries.find(e => e.tag === 'div')!;

    // Simulate _debugSource with container path
    const containerLoc: SourceLocation = {
      fileName: '/app/src/Page.tsx',
      line: div.loc.line,
      column: div.loc.column,
    };

    // After normalization: /app/src/Page.tsx → src/Page.tsx → matches entries
    const resolved = service.resolveSourceLocation(containerLoc);
    expect(resolved).not.toBeNull();
    expect(resolved!.nodeRef).toBe(div.nodeRef);
  });

  it('should handle re-parse with element deletion', () => {
    const service = new NodeMapService();
    service.parseAndBuild(FIXTURE, 'src/Page.tsx');
    const oldEntries = service.getNodeMap('src/Page.tsx')!;

    // Remove the <ul> block
    const withoutUl = FIXTURE.replace(
      /\s*<ul>[\s\S]*?<\/ul>/,
      '',
    );

    const result = service.reparseAndUpdate(withoutUl, 'src/Page.tsx');

    const oldUl = oldEntries.find(e => e.tag === 'ul')!;
    // ul should NOT be in the mapping (deleted)
    expect(result.refMapping?.[oldUl.nodeRef]).toBeUndefined();

    // But div and Card should still be mapped
    const oldDiv = oldEntries.find(e => e.tag === 'div')!;
    const oldCard = oldEntries.find(e => e.tag === 'Card')!;
    expect(result.refMapping?.[oldDiv.nodeRef]).toBeDefined();
    expect(result.refMapping?.[oldCard.nodeRef]).toBeDefined();
  });

  describe('_debugSource column format', () => {
    it('should match Babel AST column numbers (0-based)', () => {
      // Parse with standard Babel config (same as our parser)
      const ast = parse(FIXTURE, {
        sourceType: 'module',
        plugins: ['typescript', 'jsx'],
      });

      const entries = buildNodeMap(ast, 'src/Page.tsx');
      const div = entries.find(e => e.tag === 'div')!;

      // Babel columns are 0-based — this should match _debugSource.columnNumber
      expect(div.loc.column).toBeGreaterThanOrEqual(0);
      // The <div> in our fixture is indented 2 spaces inside the arrow function return
      // Exact value depends on parse behavior with parenthesized expression
    });
  });

  /**
   * Spec requirement: "Validate _debugSource format for Vite+Babel, Vite+SWC, Next.js"
   *
   * These validations require running real build tools — can't unit-test.
   * Manual verification steps:
   *
   * 1. **Vite + Babel** (react-vite-tw4-twitter test project):
   *    - Start dev server, open browser DevTools console
   *    - Run: document.querySelector('#root').__reactFiber$<tab>._debugSource
   *    - Expected: { fileName: '/app/src/App.tsx', lineNumber: N, columnNumber: N }
   *    - columnNumber should be 0-based and present
   *
   * 2. **Vite + SWC** (create a test with @vitejs/plugin-react-swc):
   *    - Same console check
   *    - Verify: columnNumber present? 0-based or 1-based?
   *    - If 1-based: ReactAdapter.getSourceLocation must subtract 1
   *    - If missing: ReactAdapter falls back to column=0 (line-only matching)
   *
   * 3. **Next.js** (nextjs-app-router test project):
   *    - Same console check (client components only — RSC has no fibers)
   *    - Verify: field names match (fileName vs file, lineNumber vs line)
   *    - SWC may use different field names than Babel
   *
   * Document results in docs/specs/ as amendment to the spec.
   * If SWC format differs, update ReactAdapter.getSourceLocation to normalize.
   */
});
```

- [ ] **Step 2: Run integration tests**

Run: `bun run test lib/element-tracing/integration.test.ts`
Expected: all tests PASS

- [ ] **Step 3: Run ALL element-tracing tests together**

Run: `bun run test lib/element-tracing/ client/lib/element-tracing/`
Expected: all tests PASS, no cross-file interference

- [ ] **Step 4: Run full test suite**

Run: `bun run test`
Expected: no regressions — all existing tests still pass

- [ ] **Step 5: Run lint**

Run: `bun lint`
Expected: no warnings or errors in new files

- [ ] **Step 6: Commit**

```bash
git add lib/element-tracing/integration.test.ts
git commit -m "test(element-tracing): integration tests — full parse→resolve pipeline (HYP-268)"
```

---

## Task 13: Final Cleanup

- [ ] **Step 1: Run `bunx knip`**

Check for unused exports/dependencies in new files.

Run: `bunx knip`
Expected: no new issues from element-tracing files (new files are infrastructure, may show as "unused exports" — these are consumed in Phase 2)

- [ ] **Step 2: Verify all tests pass**

Run: `bun run test`
Expected: green

- [ ] **Step 3: Verify lint passes**

Run: `bun lint`
Expected: clean

- [ ] **Step 4: Verify type checking passes**

Run: `npx tsc --noEmit`
Expected: no new errors

- [ ] **Step 5: Self-review diff**

Run: `git diff main --stat` and `git diff main` to review all changes.

Check:
- No `any` or `as any` in new code
- No commented-out code
- All files have proper file header comments
- All new types are in `shared/element-tracing/types.ts` (single source of truth)
- No duplicate type definitions

- [ ] **Step 6: Final commit if any cleanup needed**

```bash
git add -A
git commit -m "chore(element-tracing): Phase 1 cleanup — lint, types, exports (HYP-268)"
```
