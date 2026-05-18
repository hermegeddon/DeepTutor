---
type: ["ux", "git"]
skills: []
negative_tests: 2
---

# Block 23: Final polish — loading feedback, module selector, git cleanup

## Issue 1: Loading UX — no feedback while waiting for LLM

**File**: `web/app/(workspace)/learning/[bookId]/page.tsx`

**Problem**: Lines 344-349 — after WS connects (`connecting=false`), content area shows "Ready to start learning" even though the LLM hasn't responded yet. User sees static text for 5-15s with no progress indicator. The Waiting state is invisible.

**Fix**: Add `waitingForLLM` state. Set `true` in `ws.onopen` after sending `start_turn`. Set `false` when first `content` or `stage_start` event arrives. Show spinner + "Generating diagnostic questions..." text instead of "Ready".

**Changes** (in order):
1. Add state: `const [waitingForLLM, setWaitingForLLM] = useState(false);` after line 32
2. In `ws.onopen` (line 101-108), after `ws.send(...)` add `setWaitingForLLM(true);`
3. In `handleStreamEvent`, in `stage_start` handler (line 150), add `setWaitingForLLM(false);` at top
4. In `handleStreamEvent`, in `content` handler (line 179), add `setWaitingForLLM(false);` at top
5. Change lines 346-349 from:
   ```
   {connecting ? <Loader2 ... /> : t("guidedLearning.ready")}
   ```
   to:
   ```
   {connecting ? <Loader2 ... /> : waitingForLLM ? (
     <div className="flex flex-col items-center gap-2">
       <Loader2 className="w-8 h-8 animate-spin" />
       <p className="text-sm">Generating diagnostic questions...</p>
     </div>
   ) : t("guidedLearning.ready")}
   ```

## Issue 2: Module selector — initial module selection before auto-start

**File**: `web/app/(workspace)/learning/[bookId]/page.tsx`

**Problem**: Page auto-connects WS and starts `guided_learning` immediately. With 3 modules (线性代数/微积分/概率论), user never gets to choose. ModuleTree is clickable but only AFTER connection.

**Fix**: When `modules.length > 1` AND no `currentModuleId`, show a module picker screen BEFORE connecting WS. User picks a module → `handleModuleClick` → connect starts.

**Changes**:
1. Add `const [moduleSelected, setModuleSelected] = useState(false);` (~line 51)
2. In `connect()`, guard: if `modules.length > 1 && !moduleSelected && !currentModuleId`, skip WS connect
3. In `handleModuleClick`, add `setModuleSelected(true);` after sending change_module
4. In `connect()` useEffect (line 270), add dependency on `moduleSelected` (or call connect inside handleModuleClick)
5. Add module picker UI before connect: show module cards when `!moduleSelected && modules.length > 1 && !connecting`

**Module picker UI** (insert before line 269 `useEffect` or in JSX before connecting state):
```tsx
// Show module picker when multiple modules + none selected
{!moduleSelected && modules.length > 1 && !connecting && (
  <div className="flex items-center justify-center h-full">
    <div className="text-center max-w-md">
      <h2 className="text-xl font-bold mb-2">Select a Module</h2>
      <p className="text-sm text-[var(--muted-foreground)] mb-6">Choose a module to start guided learning</p>
      <div className="flex flex-col gap-3">
        {modules.map(m => (
          <button
            key={m.id}
            onClick={() => { handleModuleClick(m.id); setModuleSelected(true); }}
            className="px-6 py-4 rounded-xl border border-[var(--border)] hover:border-[var(--primary)] text-left transition-colors"
          >
            <div className="font-medium">{m.name}</div>
            <div className="text-xs text-[var(--muted-foreground)] mt-1">{m.knowledge_points.length} knowledge points</div>
          </button>
        ))}
      </div>
    </div>
  </div>
)}
```

## Issue 3: Git cleanup — proxy.ts + middleware.ts

**Files**: git index only

**Problem**: 
- `web/proxy.ts` is untracked (needed by Next.js 16)
- `web/middleware.ts` deleted from disk but still tracked by git

**Fix**:
```bash
git rm web/middleware.ts
git add web/proxy.ts
```

## Negative tests (2)

1. Load `/learning/[bookId]` with 1 module → auto-connects (no picker), shows spinner during LLM wait
2. Load `/learning/[bookId]` with 0 modules → shows "Ready to start learning" (no picker, no endless spinner)

## Acceptance criteria

1. Loading state shows spinner with descriptive text while waiting for LLM
2. Module picker appears when 2+ modules exist and none selected
3. Single-module books auto-start (no picker)
4. `proxy.ts` committed, `middleware.ts` removed from git
5. 92/92 tests still pass
6. Webbridge verify: visit /learning/default → see loading spinner → see modules/stages

## Verification

```bash
python -m pytest deeptutor/learning/tests/ -v
git status web/proxy.ts web/middleware.ts
```
