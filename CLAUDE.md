# Hardware Pipeline V2 — Project Memory

## Current Status
- **FastAPI backend** running on `localhost:8000` — serves API + React frontend
- **React v5 frontend** built and deployed at `http://localhost:8000/app`
- **Streamlit** (`app.py`) kept as legacy fallback — not used actively
- Gold theme (Streamlit) tagged as `theme-gold`
- **11 pipeline phases** functional in backend (P1, P2, P3, P4, P5, P6, P7, P7a, P8a, P8b, P8c)
- **design_scope is advisory only (v23, 2026-04-20)** — scope is still a DB column and still steers the P1 wizard (architecture picker / spec questions), but `PHASE_APPLICABLE_SCOPES` now maps every phase to every scope, so every project runs all 11 phases. The execute-gate + pipeline-run gate code paths are still in place but never fire.

---

## Completed Work

### React Frontend Rebuild (v5 Design) — DONE ✅
- Built at `hardware-pipeline-v5-react/`, deployed as `frontend/bundle.html`
- Full three-column layout: LeftPanel (248px) + Center content + FlowPanel (300px right)
- All 10 phases in sidebar with lock logic, color coding, ✓ marks
- FlowPanel: animated sub-steps, progress bars, completion summary, Run button
- ChatView (P1): typewriter effect, no auto-greet, no QuickReply popup cards, history restored on F5
- CreateProjectModal: name only (no description field)
- Phase completion toasts on status flip (via `prevStatusesRef` in App.tsx)
- DocumentsView: DOCX download with "Converting…" loading state (async blob fetch)
- DocumentsView: inline Mermaid rendering via mermaid.js CDN

### Backend Fixes — DONE ✅
- All 7 AI agents scrub TBD/TBC/TBA from output
- `flag_modified` added to all JSON column writes (fixes P4 "Pending" status bug)
- Parallel Mermaid diagram rendering via `ThreadPoolExecutor` in `main.py`
- P4 netlist agent always completes (skeleton fallback if LLM doesn't call tool)

### V21 Deterministic Wizard (P1 Chat) — DONE ✅
- Pre-chat flow replaced with a 6-stage deterministic wizard (Scope → App → Arch → Specs → Details → Confirm)
- Data module: `src/data/rfArchitect.ts` — holds SCOPE_DESC, APPLICATIONS, ALL_ARCHITECTURES (scope-filtered + linear/detector split), ALL_SPECS, DEEP_DIVES, APP_QUESTIONS, AUTO_SUGGESTIONS, CASCADE_RULES, derivedMDS, archRationale
- Scope-first branching (`full` / `front-end` / `downconversion` / `dsp`) drives which architectures, specs, and deep-dive questions appear
- Per-project persistence: `localStorage["hp-v21-wizard-${projectId}"]` — survives F5 mid-flow, cleared on Generate
- Architect auto-suggestions: keyed on question-id × value; fire inline chips + confirm-stage notes
- Cascade sanity checks: Friis-derived MDS, gain stability, subsampling filter, freq-plan image, direct-RF clock, zero-IF, BW-vs-ADC Nyquist, radar/EW arch-fit
- "Other" free-text fallback on every chip row
- Structured payload stringified to the existing `/chat` endpoint — no backend changes
- `WizardFrame` component in `ChatView.tsx` owns the rendering; `ChatView` owns `wizardStage` + `wizard` state
- Build tag: `BUILD v21 (deterministic 7-stage wizard · architect intelligence + cascade sanity · scope-first branching)`

### V22 Backend-Authoritative Design Scope — DONE ✅
- `ProjectDB.design_scope` (String, default `'full'`) is the source of truth; SQLite migration `003_design_scope.sql` adds the column idempotently
- `POST /api/v1/projects` accepts `design_scope`; `PATCH /api/v1/projects/{id}/design-scope` updates it at any time
- `POST /phases/{id}/execute` calls `is_phase_applicable()` and returns **HTTP 409** if the phase is not applicable to the project's scope — frontend can no longer bypass the sidebar grey-out
- `run_pipeline()` skips out-of-scope phases (logs `pipeline.phase_skipped_out_of_scope`) so a full-pipeline click never executes an inapplicable phase
- `GET /api/v1/projects/{id}/status` returns `design_scope` + `applicable_phase_ids` (computed from `services/phase_scopes.PHASE_APPLICABLE_SCOPES`)
- Frontend (`App.tsx → refreshStatuses`) reconciles to the backend scope on every poll; localStorage is kept only as a transient cache that the backend always overrides
- `PhaseHeader` suppresses the Execute and Re-run buttons when `!isPhaseApplicable(phase, scope)` and shows a `NOT APPLICABLE` pill
- Build tag: `BUILD v22 (backend-authoritative design_scope · /status returns applicable_phase_ids · execute-gate 409 on out-of-scope phase · 11 phases)`

---

## Target Layout (v2 rebuild)

### Overall Structure

Two modes:

**1. Landing page** (no project loaded)
- Full-screen, v5 style: dark grid/checkerboard background, glowing teal orb
- Centered: Hardware Pipeline logo + tagline
- Two buttons: `+ Create New Project` | `Load Existing`
- Subline: `DATA PATTERNS INDIA · GREAT AI HACK-A-THON 2026`

**2. Pipeline view** (project loaded)
Three-column layout, full viewport height:

```
+------------------+---------------------------+----------------------+
|  LEFT PANEL      |  CENTER CONTENT           |  RIGHT PANEL         |
|  248px fixed     |  flex-1 scrollable        |  340px fixed         |
|  sticky          |                           |  sticky              |
|                  |  Sticky mini-topbar:      |                      |
|  Logo / branding |  project name + prod ID   |  Step-by-Step        |
|                  |  phase progress dots      |  Execution Flow      |
|  Phase list:     |                           |                      |
|  P1 [teal]       |  Phase header:            |  Sub-steps for       |
|  P2 [blue]       |  icon + code + title      |  selected phase,     |
|  P3 [amber]      |  badge + tagline          |  animated on run,    |
|  P4 [purple]     |                           |  each with label,    |
|  P5 [slate/lock] |  Sub-tabs:                |  time, detail,       |
|  P6 [teal]       |  Chat | Details | Metrics |  progress bar        |
|  P7 [slate/lock] |  Documents                |                      |
|  P8 [teal]       |  (Chat only on P1)        |  Run button          |
|                  |                           |  (phase color)       |
+------------------+---------------------------+----------------------+
```

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  REACT FRONTEND — frontend/bundle.html                             │
│  • Served by FastAPI at http://localhost:8000/app                  │
│  • All JS/CSS inlined — single self-contained HTML file            │
│  • Makes live HTTP calls to FastAPI at localhost:8000              │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ HTTP fetch() calls (same-origin)
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│  FASTAPI BACKEND — main.py on port 8000                            │
│  • Runs AI agents, reads SQLite DB, generates output files         │
│  • Swagger docs at http://localhost:8000/docs                      │
│  • Serves React bundle at GET /app                                 │
└────────────────────────────────────────────────────────────────────┘
```

---

## Design System (v5)

| Token            | Value                          |
|------------------|--------------------------------|
| Background       | `#070b14` (deep navy)          |
| Panel BG         | `#1a2235`                      |
| Panel Alt        | `#2a3a50`                      |
| Accent (primary) | `#00c6a7` (teal)               |
| Accent glow      | `rgba(0,198,167,0.25)`         |
| Text primary     | `#e2e8f0`                      |
| Text muted       | `#94a3b8`                      |
| Text dim         | `#64748b`                      |
| Border           | `rgba(42,58,80,0.8)`           |
| Error            | `#dc2626`                      |
| Warning          | `#f59e0b`                      |
| Blue accent      | `#3b82f6`                      |
| Display font     | **Syne** (Google Fonts)        |
| UI labels font   | **DM Mono** (Google Fonts)     |
| Code font        | **JetBrains Mono**             |
| Border radius    | 6px cards / 4px inputs         |
| Teal glow shadow | `0 0 28px rgba(0,198,167,0.25)`|

### Per-Phase Colors
Each phase has its own accent color used for icons, borders, progress, sub-step highlights:

| Phase | Color   | Hex       |
|-------|---------|-----------|
| P1    | Teal    | `#00c6a7` |
| P2    | Blue    | `#3b82f6` |
| P3    | Amber   | `#f59e0b` |
| P4    | Purple  | `#8b5cf6` |
| P5    | Slate   | `#475569` |
| P6    | Teal    | `#00c6a7` |
| P7    | Slate   | `#475569` |
| P8a   | Teal    | `#00c6a7` |
| P8b   | Blue    | `#3b82f6` |
| P8c   | Purple  | `#8b5cf6` |

CSS variables in `src/index.css`: `--navy`, `--panel`, `--panel2`, `--panel3`, `--teal`, `--teal-border`, `--teal-glow`, `--text`, `--text2`, `--text3`, `--text4`, `--border`, `--border2`, `--danger`, `--warning`, `--blue`, `--green`

---

## Views / Pages

### Left Panel (248px, sticky)
- Top: "DATA PATTERNS · CODE KNIGHTS" label (teal, small caps) + "Hardware Pipeline" logo
- Phase list: one button per phase, full width, with:
  - Circle icon (phase number, or ✓ if complete, or lock if manual/locked)
  - Phase title + `⚡ AUTO` or `MANUAL` tag
  - Active phase highlighted with that phase's color border + tinted bg
  - Locked phases (P5, P7, and phases after current) shown at 32% opacity
  - Clicking a locked manual phase shows a toast: "Completed externally in Altium/Vivado"
  - Clicking a locked AI phase shows a toast: "Complete P{n-1} first"

### Center Content (flex-1)

**Sticky mini-topbar** (inside pipeline view, ~40px):
- Project name (left)
- Product ID chip in teal (if set)
- Phase progress dots right-aligned: one dot per phase, colored when complete

**Phase header** (below topbar):
- Large circle icon (phase color, number or ✓)
- Phase code (e.g. P01), AUTO/MANUAL badge, time estimate
- Phase title (Syne font, bold)
- Tagline (muted)
- Manual phase lock note if applicable

**Sub-tabs** (below header, tab bar):
- `⬡ Flow` — NOT here (moved to right panel)
- `⚡ Chat` — only for P1; same functionality as gold theme Streamlit chat, colors adapted to v5
- `◈ Details` — inputs list, outputs list, tools list
- `◎ Metrics` — TIME SAVED, ERROR REDUCTION, CONFIDENCE %, ANNUAL COST IMPACT
- `📄 Documents` — all generated outputs for this phase: .md/.docx inline renderer, Mermaid diagrams, JSON viewer, component table, netlist viewer, code review results (as applicable per phase)

Default tab: `◈ Details` (since Flow moved to right panel)

### Right Panel (340px, sticky)

**Step-by-Step Execution Flow** for the currently selected phase:
- Header: "Step-by-Step Execution Flow" (Syne bold) + sub-step count + total time
- Run button (phase color): `▶ Run Simulation` / `Running…` / `↺ Replay`
- Sub-steps list (vertical, connected by lines):
  - Each sub-step: circle (phase color when active/complete, dim when pending)
  - Connector line between steps
  - Step label + time chip
  - Detail text (12px, muted) shown when step is active/complete
  - Animated progress bar per step (phase color glow when active)
- Completed summary card: TOTAL TIME + SUB-STEPS count
- Animation: steps complete sequentially, each with fill bar animation ~22ms interval

This is **always visible** while you use the center sub-tabs.

---

## Phase Reference Data

| ID  | Name                  | Type     | Color   | Time      | Description |
|-----|-----------------------|----------|---------|-----------|-------------|
| P1  | Design & Requirements | AI       | teal    | ~4 min    | Block diagram + requirements capture via chat |
| P2  | HRS Document          | AI       | blue    | ~4 min    | IEEE 29148 Hardware Requirements Specification |
| P3  | Compliance Check      | AI       | amber   | ~4 min    | RoHS/REACH/FCC/MIL-STD rules engine |
| P4  | Netlist Generation    | AI       | purple  | ~4 min    | Component connectivity graph with DRC |
| P5  | PCB Layout            | Manual   | slate   | Days-Wks  | Altium/KiCad/OrCAD — manual, external tool |
| P6  | GLR Specification     | AI       | teal    | ~4 min    | Glue Logic Requirements for FPGA/CPLD |
| P7  | FPGA Design           | Manual   | slate   | Days-Wks  | Vivado/Quartus — manual, external tool |
| P7a | Register Map          | AI       | blue    | ~4 min    | Register map / memory layout for the FPGA / DSP |
| P8a | SRS Document          | AI       | teal    | ~4 min    | Software Requirements Specification |
| P8b | SDD Document          | AI       | blue    | ~4 min    | Software Design Document |
| P8c | Code Review           | AI       | purple  | ~4 min    | MISRA-C + Clang-Tidy static analysis |

### Sub-Steps Per Phase (for Right Panel Flow)

**P1 — Requirements & Component Selection** (~4 min)
1. Parse natural language input — 12s
2. Identify hardware domain — 5s
3. Query component database — 48s
4. Rank & select components — 20s
5. Generate BOM with alternates — 15s
6. Block diagram verification — 30s
7. Requirement finalization loop — 50s

**P2 — HRS Document Generation** (~4 min)
1. Load requirements from P1 — 3s
2. Select domain template — 5s
3. Calculate power budget — 18s
4. Generate interface tables — 22s
5. Write specification sections — 120s
6. Insert diagrams — 30s
7. Export .docx / .pdf — 12s

**P3 — Compliance Validation** (~4 min)
1. Load HRS + BOM from P1/P2 — 4s
2. RoHS / REACH substance check — 35s
3. EMC pre-compliance check — 45s
4. Safety standard mapping — 30s
5. Generate compliance matrix — 20s
6. Cost impact estimation — 15s
7. Compliance report export — 10s

**P4 — Logical Netlist Generation** (~4 min)
1. Parse block diagram from P1 — 8s
2. Map components to pinouts — 22s
3. Build connectivity graph — 30s
4. Assign net classes — 15s
5. Run electrical rules check — 35s
6. Export KiCad netlist (.net) — 8s
7. Pre-PCB DRC report — 10s

**P5 — PCB Layout** (Manual / External)
1. Import validated netlist (P4) — 5 min
2. Define layer stackup — 2 hrs
3. Component placement — 1-2 days
4. Route critical signals — 2-3 days
5. DRC / ERC check — 2 hrs
6. Gerber export — 30 min

**P6 — GLR Specification** (~4 min)
1. Load netlist from P4 — 5s
2. Identify FPGA/CPLD boundaries — 20s
3. Map glue logic requirements — 35s
4. Generate RTL constraints — 40s
5. Write GLR document — 80s
6. Export specification — 10s

**P7 — FPGA Design** (Manual / External)
1. Import GLR specification — 30 min
2. RTL coding (VHDL/Verilog) — 2-5 days
3. Simulation & verification — 1-2 days
4. Synthesis & place-and-route — 4 hrs
5. Timing closure — 2-4 hrs
6. Bitstream generation — 1 hr

**P7a — Register Map** (~4 min)
1. Load FPGA / DSP interface list from P6 / P7 — 5s
2. Assign base addresses per peripheral — 20s
3. Layout control / status / data registers — 60s
4. Encode bitfields + reset values — 45s
5. Generate Markdown register map — 25s
6. Export register map document — 10s

**P8a — SRS Document** (~4 min)
1. Load hardware spec from P1-P4 — 5s
2. Define software interfaces — 25s
3. Write functional requirements — 90s
4. Write non-functional requirements — 40s
5. Generate traceability matrix — 20s
6. Export SRS document — 10s

**P8b — SDD Document** (~4 min)
1. Load SRS from P8a — 5s
2. Design software architecture — 60s
3. Define module interfaces — 35s
4. Write design descriptions — 80s
5. Generate architecture diagrams — 25s
6. Export SDD document — 10s

**P8c — Code Review** (~4 min)
1. Load firmware source files — 8s
2. Run MISRA-C static analysis — 45s
3. Run Clang-Tidy checks — 40s
4. Classify issues by severity — 15s
5. Generate fix suggestions — 50s
6. Export review report — 10s

---

## API Integration

**Base URL:** `http://localhost:8000`
**API prefix:** `/api/v1/`
**Swagger docs:** `http://localhost:8000/docs`
**CORS:** Configured in FastAPI backend

| Method | Endpoint | Used For |
|--------|----------|----------|
| GET | `/api/v1/projects` | List all projects |
| POST | `/api/v1/projects` | Create new project |
| GET | `/api/v1/projects/{id}` | Project detail |
| GET | `/api/v1/projects/{id}/status` | All phase statuses — returns `{ phase_statuses: {...} }` |
| POST | `/api/v1/projects/{id}/pipeline/run` | Start full pipeline |
| POST | `/api/v1/projects/{id}/phases/{phase_id}/execute` | Run a single phase |
| POST | `/api/v1/projects/{id}/chat` | P1 design chat — returns full JSON (not a stream) |

**Create Project payload:** `{ name, description, design_type }` — do NOT include `product_id`, backend ignores/rejects it.

**Chat response:** Backend returns `{ response: "..." }` as a complete JSON object, not a streaming response. Display the full text at once.

**Status polling:** Phase status auto-refreshes every 3 seconds when a phase is running.

**Phase status values:** `pending`, `in_progress`, `completed`, `failed`, `draft_pending`

---

## Interactive Components

### Create Project Modal
- Fields: PROJECT NAME only, Design Type (RF / Digital) — description textarea REMOVED
- Subtitle: "Give your project a name — describe your design in the chat"
- NO product_id field — backend rejects it
- NO description field in UI (backend still accepts empty string for description)
- Actions: `Cancel` | `CREATE & START →`
- On submit: POST `/api/v1/projects` → load into pipeline view, select P1

### Load Project Modal
- Lists existing projects from GET `/api/v1/projects`
- On select: load project, auto-select first incomplete AI phase

### Phase Actions (in Right Panel)
- `▶ Run Simulation` — triggers `POST /api/v1/projects/{id}/phases/{phase_id}/execute`
- Running state: `Running…` (disabled button, phase color dimmed)
- `↺ Replay` — re-runs animation after completion
- Sub-steps animate sequentially as phase executes

### Chat (P1 — Center tab)
- POST `/api/v1/projects/{id}/chat` with `{ message }`
- Response is full JSON `{ response: "..." }` — display all at once
- Animated typewriter effect on the response text (like v5 HTML chat)
- NO QuickReply suggestion chip popups — user types answers freely
- NO auto-greet on load — user must send first message
- Message history restored on F5 via `api.getConversationHistory` in `handleLoadProject`
- Colors: teal accent (P1 color), dark panel background

---

## Frontend Source Structure

```
hardware-pipeline-v5-react/
├── index.html
├── vite.config.ts
├── tsconfig.app.json          # noUnusedLocals: false
├── src/
│   ├── main.tsx
│   ├── App.tsx                # mode switch: landing | pipeline
│   ├── index.css              # CSS vars + font imports
│   ├── api.ts                 # all fetch() calls
│   ├── types.ts               # Project, PhaseStatus, DesignScope, etc.
│   ├── data/
│   │   ├── phases.ts          # phase metadata + sub-steps (static data)
│   │   └── rfArchitect.ts     # v21 deterministic wizard data + helpers
│   ├── components/
│   │   ├── LandingPage.tsx    # full-screen landing
│   │   ├── LeftPanel.tsx      # phase list sidebar
│   │   ├── MiniTopbar.tsx     # sticky project name + progress dots
│   │   ├── PhaseHeader.tsx    # phase icon + title + badges
│   │   ├── FlowPanel.tsx      # right panel: sub-steps execution flow
│   │   ├── CreateProjectModal.tsx
│   │   ├── LoadProjectModal.tsx
│   │   └── Toast.tsx
│   └── views/
│       ├── ChatView.tsx       # P1 chat — v21 wizard (WizardFrame) + free-form chat
│       ├── DetailsView.tsx    # inputs / outputs / tools
│       ├── MetricsView.tsx    # 4 metric cards
│       └── DocumentsView.tsx  # all phase outputs consolidated
```

---

## Build & Deploy

**Build command:** `cd hardware-pipeline-v5-react && npx vite build`

### Bundle Script (run after build)
```python
# bundle_and_escape.py
import re, pathlib

dist = pathlib.Path("hardware-pipeline-v5-react/dist")
src_html = (dist / "index.html").read_text(encoding="utf-8")

for css_file in dist.glob("assets/*.css"):
    tag = f'<link rel="stylesheet" crossorigin href="/assets/{css_file.name}">'
    src_html = src_html.replace(tag, f"<style>{css_file.read_text('utf-8')}</style>")

for js_file in dist.glob("assets/*.js"):
    tag = f'<script type="module" crossorigin src="/assets/{js_file.name}"></script>'
    src_html = src_html.replace(tag, f'<script type="module">{js_file.read_text("utf-8")}</script>')

def escape_script(m):
    content = m.group(1)
    escaped = re.sub(r'[^\x00-\x7F]', lambda c: f'\\u{ord(c.group()):04X}', content)
    return f'<script type="module">{escaped}</script>'

src_html = re.sub(r'<script type="module">([\s\S]*?)</script>', escape_script, src_html)

out = pathlib.Path("frontend/bundle.html")
out.parent.mkdir(exist_ok=True)
out.write_text(src_html, encoding="ascii")
print(f"Bundle written: {out} ({out.stat().st_size // 1024} KB)")
```

### FastAPI /app Route
```python
@app.get("/app", response_class=HTMLResponse, tags=["ops"])
async def serve_frontend():
    import pathlib
    p = pathlib.Path(__file__).parent / "frontend" / "bundle.html"
    if p.exists():
        return HTMLResponse(content=p.read_text(encoding="utf-8", errors="replace"), status_code=200)
    return HTMLResponse(content="<h1>Frontend not built yet.</h1>", status_code=404)
```

---

## Branding

- Logo: "Hardware Pipeline" — "Pipeline" in teal `#00c6a7` (Syne font)
- Sub-brand: `DATA PATTERNS · CODE KNIGHTS` (teal, 10px, letter-spaced)
- Hackathon line: `DATA PATTERNS INDIA · GREAT AI HACK-A-THON 2026`
- Team credits: **NOT included**
- Logo icon: use ASCII `[lightning]` in source code to avoid encoding issues

---

## Backend

- **FastAPI**: `main.py` on `localhost:8000`
- **Streamlit** (legacy/fallback): `app.py` on `localhost:8501`
- **DB**: `hardware_pipeline.db` (SQLite)
- **AI Engine**: `agents/orchestrator.py`

---

## Git Tags

- `theme-gold` — Streamlit gold theme, all 8 phases working

---

## Known Issues / Bug Backlog

| # | Bug | Status | File(s) |
|---|-----|--------|---------|
| B1 | **Optional Requirements Card** — After AI asks questions in P1 chat, inject a final card: "ANY SPECIFIC REQUIREMENTS? (optional)" so user can add free-form constraints before generation | ✅ DONE | `ChatView.tsx` |
| B2 | **Power Budget Table Jumbled** — Split into two sub-tables: "5V & 3.3V Rails" and "2.5V & 1.8V Rails" | ✅ DONE | `requirements_agent.py` `_build_power_calc_md()` |
| B3 | **DOCX Download Broken** — Fixed `clickDownload()` (append anchor to body), fixed hardcoded cairosvg path in `main.py`, added inline error toast in UI | ✅ DONE | `DocumentsView.tsx`, `main.py` |
| B4 | **Elapsed Timer Resets on Phase Switch** — Elapsed state lifted out of `GeneratingState` into `DocumentsView` using `phaseStartTsRef` keyed by phase ID | ✅ DONE | `DocumentsView.tsx` |
| B5 | **Preview Slow** — Fixed stale closure in prefetch via `contentsRef`; Preview button shows ✓ when cached; loading spinner on in-flight fetch | ✅ DONE | `DocumentsView.tsx` |
| B6 | **Mermaid Parse Error in All Phases** — Both sanitizers completely rewritten: strips `%%` frontmatter, fixes `==>` arrows, removes `"` `#` `|` from labels, handles multi-line labels, aligns ChatView + DocumentsView sanitizers. System prompt updated | ✅ DONE | `ChatView.tsx`, `DocumentsView.tsx`, `requirements_agent.py` |
| B7 | **Bad Datasheet Links** — VPT manufacturer banned in system prompt + URL validator strips VPT URLs at build time. Agent instructed to use product-page URLs (not fabricated PDF paths) | ✅ DONE | `requirements_agent.py` |
| B8 | **GLR Missing RF Specs** — Tool schema extended with `input_return_loss_db`, `output_return_loss_db`, `harmonic_rejection`, `power_vs_frequency`, `power_vs_input`, `cable_loss`. All rendered conditionally when data present | ✅ DONE | `requirements_agent.py` |
| B9 | **HTTP 500 on first chat message** — `SYSTEM_PROMPT` contained `%%{{ init }}%%` escaped as `{ init }` causing `KeyError: ' init '` when `.format()` was called. Fixed by escaping as `%%{{init}}%%` | ✅ DONE | `requirements_agent.py` |
| B10 | **Optional card in wrong place / not shown** — `specificReqs` card was inside `QuickReplyPanel` (not rendered). Moved to `preStage === 'clarifying'` section with `clarifySpecificReqs` state. Removed pre-existing requirements field from `CreateProjectModal` | ✅ DONE | `ChatView.tsx`, `CreateProjectModal.tsx` |

---

## Planned Features (Roadmap)

### Tier 1 — High value / Low effort
| # | Feature | Status |
|---|---------|--------|
| 1 | **"Re-run all stale phases" button** in MiniTopbar — appears when `stalePhaseIds.length > 0`, one-click re-runs the full pipeline to refresh all outdated documents | TODO |
| 2 | **Chat history reload on F5** — reload `conversation_history` from DB on `handleLoadProject` so P1 chat isn't blank after browser refresh | ✅ DONE |
| 3 | **Phase completion toast** — when any phase flips to `completed` during polling, show "P02 — HRS Document complete ✓" toast | ✅ DONE |

### Tier 2 — Medium effort / High demo impact
| # | Feature | Status |
|---|---------|--------|
| 4 | **Inline Mermaid rendering** — detect ` ```mermaid ` blocks in DocumentsView markdown and render as live diagrams via mermaid.js CDN | TODO |
| 5 | **Export all as ZIP** — backend endpoint `GET /api/v1/projects/{id}/export` that zips the project output dir; frontend "Download All" button in DocumentsView | TODO |

### Tier 3 — Bigger features
| # | Feature | Status |
|---|---------|--------|
| 6 | **Requirement version history** — snapshot `requirements.md` on every P1 approval, store as `requirements_v1.md`, `v2.md`, etc.; viewable in a "History" drawer with diffs between versions | TODO |
| 7 | **Real-time log streaming** — stream AI internal reasoning (tool calls, search steps) to a collapsible "Live Log" panel in the right panel while phase is running | TODO |
| 8 | **Dependency graph view** — visual DAG: P1→P2→P3→P4→P5, P4→P6→P7, P1-P4→P8a→P8b→P8c; rendered as interactive SVG on the landing page or a dedicated "Pipeline Map" view | TODO |

---

## Critical Gotchas

**1. Windows cp1252 encoding crash**
bundle.html must have ALL non-ASCII chars escaped as `\uXXXX`. The bundle script handles this.

**2. Do NOT open bundle.html as file://**
`type="module"` inline scripts block via `file://`. Always access via `http://localhost:8000/app`.

**3. FastAPI runs on port 8000, not 8001**
API prefix is `/api/v1/` — check `http://localhost:8000/docs` for exact signatures.

**4. Chat is not streaming**
`/api/v1/projects/{id}/chat` returns complete JSON. Use typewriter animation client-side.

**5. No product_id in create project**
Backend `POST /api/v1/projects` only accepts `name`, `description`, `design_type`.

**6. Default exports required**
All component and view files must use `export default`. Named-only exports break bundling.

**7. uvicorn requires manual restart**
New routes in `main.py` need a manual server restart if not running with `--reload`.

**8. SQLAlchemy JSON column mutation tracking**
`phase_statuses`, `conversation_history`, `design_parameters` are `Column(JSON)`. SQLAlchemy does NOT auto-detect in-place or reassignment mutations for JSON columns. Always call `flag_modified(p, 'field_name')` after any assignment. Already done in `services/project_service.py`.

**9. TBD/TBC/TBA banned in all agent output**
All 7 agents scrub these words via `re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', ...)` before saving output. System prompts also explicitly forbid them. Server restart required for agent changes to take effect.

**10. Standards in use**
- HRS: ISO/IEC/IEEE 29148:2018
- SRS: IEEE 830-1998 / ISO/IEC/IEEE 29148:2018
- SDD: IEEE 1016-2009
- Compliance: RoHS EU 2011/65/EU, REACH, FCC Part 15, CE Marking, IEC 60601, ISO 26262, MIL-STD

**11. design_scope is backend-authoritative**
- `ProjectDB.design_scope` is the single source of truth; frontend localStorage is a transient cache that is overwritten on every `/status` poll via `App.tsx → refreshStatuses`
- `services/phase_scopes.PHASE_APPLICABLE_SCOPES` mirrors `src/data/phases.ts` `applicableScopes` — **keep these two maps in sync** when adding or retargeting a phase
- `POST /phases/{id}/execute` returns **HTTP 409 Conflict** with `{ detail: "Phase {id} is not applicable for this project's scope ({scope})" }` when violated; frontend displays a friendly toast in this case
- `run_pipeline()` skips out-of-scope phases silently (logs `pipeline.phase_skipped_out_of_scope`) — safe to click "Approve & Run" on any scope
- `GET /api/v1/projects/{id}/status` returns `design_scope` and `applicable_phase_ids: string[]` — prefer `api.getFullStatus()` over `getStatus`/`getStatusRaw` when scope info is needed
- Migration `003_design_scope.sql` is idempotent (column-exists check in `migrations/__init__.py::_apply_003`) — safe to re-run

---

## P1 Anti-Hallucination Design (4-Round Requirement Elicitation)

### Problem Statement
Demo feedback: "AI is hallucinating to the core." The P1 agent was assuming critical specs, fabricating component part numbers, and generating designs without proper requirement gathering.

### Solution: Strict 4-Round Elicitation Before Any Design Generation

#### Round 1 — Mandatory RF/Hardware Specifications (16 Tier-1 questions, always asked)

**Group A — RF Performance (core):**
1. Frequency range / band of operation
2. Bandwidth (instantaneous BW + tuning BW)
3. Target noise figure (NF) in dB
4. Gain requirements (total system gain in dB)
5. Sensitivity / MDS (minimum detectable signal in dBm)
6. Spurious-Free Dynamic Range (SFDR in dB)

**Group B — Linearity & Power Handling:**
7. Linearity (IIP3, P1dB in dBm)
8. Maximum input power / survivability (max safe input without damage + recovery time)
9. AGC requirement (range in dB, attack/release time)

**Group C — Selectivity & Rejection:**
10. Selectivity (adjacent channel rejection, alternate channel rejection in dBc)
11. Image rejection requirement (dB)
12. Spurious response rejection (dBc)
13. Input return loss / VSWR

**Group D — System Constraints:**
14. Power consumption budget (total W)
15. Supply voltage (available rails)
16. Output format (analog IF, digital I/Q, detected power, demodulated baseband)

**Group E — Application & Environment:**
- Application type (radar, comms, EW, SIGINT, satcom, T&M)
- Environmental (temperature range, vibration, altitude, IP rating)
- Physical constraints (form factor, size envelope, weight, cooling method)
- Compliance (MIL-STD, ITAR, RoHS, TEMPEST)

#### Round 1.5 — Application-Adaptive Questions (Tier 2, 3-6 questions based on application)

**If Military/EW/SIGINT:**
- Signal type (CW, pulsed, frequency-hopped, spread spectrum)
- Pulse handling (min pulse width, PRI range, POI, TOA accuracy)
- Direction finding / AOA requirement (angular accuracy, technique)
- Number of simultaneous channels
- BIT / self-test capability
- TEMPEST / emissions security
- Co-site interference environment
- Warm-up time requirement

**If Communications:**
- Modulation type (AM, FM, QAM, OFDM, etc.)
- Channel count (single tuned vs multi-channel)
- Tuning speed / switching time
- Frequency reference accuracy (ppm), TCXO vs OCXO
- Phase noise / blocking requirement (dBc/Hz at offset)

**If Radar:**
- Pulse handling (PW, PRI, duty cycle)
- Coherent processing required?
- Range resolution / Doppler requirements
- MTI / pulse compression

**If Satcom:**
- G/T requirement
- Link budget parameters
- Tracking requirement (auto-track, step-track, monopulse)

**Additional conditional questions:**
- Antenna interface (single/array, impedance, polarization, bias-tee)
- Frequency reference / clock (internal TCXO/OCXO, external ref, GPS-disciplined)
- Calibration / BIT requirement
- Redundancy / MTBF
- Data interface protocol (VITA 49, STANAG, Ethernet/UDP, PCIe, custom FPGA)
- Testability / production test points
- Power sequencing / inrush constraints

#### Round 2 — Architecture Selection (MANDATORY before component selection)

Present structured architecture options:

**Analog-Output Architectures:**
1. RF Front-End Only (LNA + Filter, no downconversion)
2. Superheterodyne Receiver (Single / Double / Multi-IF)
3. Direct Conversion (Zero-IF / Homodyne)
4. Low-IF Receiver
5. Image-Reject Receiver (Hartley / Weaver)
6. Analog IF Receiver (with analog demodulation)
7. Crystal Video Receiver (detector only, no LO)
8. Tuned RF (TRF) Receiver

**Digital-Output Architectures:**
9. SDR / Digital IF Receiver (RF → IF → ADC → DSP)
10. Direct RF Sampling Receiver (RF → ADC directly)
11. Subsampling / Undersampling Receiver
12. Dual-Conversion with Digital IF (analog front + digital backend)

**Specialized Architectures:**
13. Channelized Receiver (parallel filter bank, SIGINT/EW)
14. Compressive / Microscan Receiver (dispersive delay, radar warning)

15. "Not sure — recommend based on my specs"

**Adaptive Logic After Architecture Selection:**
- **Architecture has mixer** (superhet, direct conversion, image-reject, low-IF) → Ask: IF frequency, LO phase noise, tuning speed, step size, single vs multi-LO
- **Architecture has ADC at IF** (digital IF, dual-conversion+digital, low-IF digital) → Ask: ADC ENOB at IF, anti-alias filter, IF bandwidth, FPGA interface
- **Architecture has ADC at RF** (direct RF sampling, subsampling, SDR) → Ask: ADC sampling rate (≥2× RF), SFDR, aperture jitter, clock phase noise
- **Purely analog output** (RF front-end, analog IF, crystal video) → Ask: IF output specs (impedance, level, connector)
- **No mixer** (RF front-end, direct RF sampling, crystal video) → Skip LO questions
- **User selects "Not sure"** → AI recommends based on frequency, bandwidth, dynamic range, application

#### Round 3 — Architecture-Adaptive Follow-ups (3-5 questions)

Depends on Round 2 selection. Examples:
- Superhet → IF frequency choice, number of conversions, image rejection method, LO synthesizer specs, IF filter type (SAW, crystal, LC)
- Direct conversion → I/Q balance tolerance, DC offset handling, baseband filter bandwidth, flicker noise corner
- Digital IF / SDR → ADC resolution (bits), sample rate, FPGA family/size, DSP algorithm requirements, data throughput
- Direct RF sampling → ADC SFDR requirement, Nyquist zone, clock jitter budget (fs), anti-alias filter
- Channelized → number of channels, channel bandwidth, filter bank implementation (analog/digital/polyphase)

#### Round 4 — Requirement Validation & Cascade Analysis

Before generating any design:
1. Show complete requirements summary table (ALL specs from Rounds 1-3)
2. Show preliminary cascade analysis: "Based on your specs, system NF < 3 dB → LNA must have NF < 1.5 dB with gain > 15 dB (Friis). SFDR of 65 dB → mixer IIP3 > -5 dBm."
3. Flag any impossible/contradictory specs: "NF < 1 dB at 18 GHz is extremely challenging — confirm or relax?"
4. Ask: "Please confirm these requirements. I will NOT proceed until confirmed."
5. ONLY after explicit confirmation → call generate_requirements with REAL components

### Anti-Hallucination Rules
- NEVER assume missing specs — if not stated, ASK
- NEVER fabricate part numbers — if unsure, use manufacturer family + specs (e.g., "ADI HMC-series LNA, 2-18 GHz, 2 dB NF")
- NEVER skip architecture selection — it determines the entire signal chain
- NEVER proceed to component selection before all 4 rounds complete
- Every spec value must come from confirmed requirement or real datasheet
- All further questions MUST adapt based on chosen architecture
- Show cascade/link budget BEFORE generating BOM to catch impossible specs early
