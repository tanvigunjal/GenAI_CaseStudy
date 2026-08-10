export interface SceneMeta {
  id: string;
  eyebrow: string;
  title: string;
  timing: string;
  notes: string[];
  sources: string[];
}

export const scenes: SceneMeta[] = [
  {
    id: "scene-1",
    eyebrow: "OMMAX · Data Science / GenAI case study",
    title: "A controlled path from inbox to ERP, not an autonomous shortcut.",
    timing: "0:55",
    notes: [
      "Open with the decision: this is a control system with an LLM inside it, not an agent with an ERP credential.",
      "Frame the prototype as an offline, synthetic-data vertical slice. Do not call it production-ready.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · locked scope and Milestone 7"],
  },
  {
    id: "scene-2",
    eyebrow: "The operating tension",
    title: "The hard problem is knowing when not to write.",
    timing: "1:05",
    notes: [
      "A plausible extraction is not the same as an authorized order.",
      "The design optimizes for safe decisions across incomplete, conflicting, or hostile inputs, not maximum straight-through processing.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · deterministic order policy"],
  },
  {
    id: "scene-3",
    eyebrow: "Acceptance contract",
    title: "Acceptance contract has three outcomes and no silent failure.",
    timing: "1:15",
    notes: [
      "Use the tabs to make the contract concrete: approve, review, or block.",
      "Every path persists a stable trace and explicit reason codes. Unsupported or partial parsing never becomes empty text and silently continues.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · system flow and acceptance gates"],
  },
  {
    id: "scene-4",
    eyebrow: "Tested scope",
    title: "Tested scope covers English, German, and all three attachment formats.",
    timing: "1:00",
    notes: [
      "This is deliberately narrow: one order per email, body plus PDF/PPTX/XLSX, English and German.",
      "Scanned documents, legacy Office formats, split shipments, and other languages route out of auto-write scope.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · locked scope and deliberate exclusions"],
  },
  {
    id: "scene-5",
    eyebrow: "Decision boundary",
    title: "The model proposes; deterministic policy owns the write boundary.",
    timing: "1:25",
    notes: [
      "Walk left to right. The model may normalize language and propose structured fields; it never creates a write command.",
      "Only strict deterministic validation can mint the immutable command accepted by the writer.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · controlled agent and deterministic policy"],
  },
  {
    id: "scene-6",
    eyebrow: "Inspectable capability",
    title: "Inspectable boundaries in code and data access.",
    timing: "1:15",
    notes: [
      "The model sees exactly three read-only operations. Purchase history is implicitly scoped to the authenticated customer.",
      "There is no customer search and no create, update, or delete capability in the tool registry.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · extraction tool allowlist"],
  },
  {
    id: "scene-7",
    eyebrow: "Exactly-once control",
    title: "A clean order creates one traceable order, even after replay.",
    timing: "1:35",
    notes: [
      "Move the trace scrubber. The write occurs only after validation and persisted audit intent.",
      "A duplicate delivery resolves the stable idempotency key to the original order; it does not decrement stock again.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · exactly-once write acceptance contract"],
  },
  {
    id: "scene-8",
    eyebrow: "Human control",
    title: "Ambiguity creates a reviewed revision, not a model guess.",
    timing: "1:20",
    notes: [
      "The reviewer corrects the business field and attaches evidence. The system retains the before/after revision.",
      "Approval is impossible until the corrected order is revalidated against current ERP data.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · durable human review state machine"],
  },
  {
    id: "scene-9",
    eyebrow: "Containment",
    title: "Malicious content is contained before it can gain useful capability.",
    timing: "1:20",
    notes: [
      "Transport authentication and deterministic security checks run before model extraction.",
      "Even advisory screening cannot expose unrelated customer data because the tool surface is sender-scoped and read-only.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · safe inbox boundary and threat controls"],
  },
  {
    id: "scene-10",
    eyebrow: "Prototype findings",
    title: "Three prototype findings changed my design.",
    timing: "1:35",
    notes: [
      "First, mailbox retries make idempotency a core domain concern. Second, read-only tools can still leak data if scope is broad.",
      "Third, a small deterministic corpus demonstrates controls but cannot estimate a production defect rate.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · baseline gaps and promotion gate"],
  },
  {
    id: "scene-11",
    eyebrow: "Production posture",
    title: "The prototype proves the control path; these gates remain before production.",
    timing: "1:30",
    notes: [
      "Separate engineering completion from operating permission. The latter needs shadow evidence, integration hardening, ownership, and privacy/legal decisions.",
      "The zero-false-approval sample is a proposed gate, not proof already achieved.",
    ],
    sources: [
      "TASK_B_IMPLEMENTATION_PLAN.md · production promotion gate",
      "European Commission · GDPR processing principles",
      "Regulation (EU) 2024/1689",
    ],
  },
  {
    id: "scene-12",
    eyebrow: "Editable value hypothesis",
    title: "The value case is an adjustable hypothesis, not invented ROI.",
    timing: "1:40",
    notes: [
      "Edit assumptions live. The model separates released capacity from cash benefit, because time saved is not automatically cash saved.",
      "Net benefit and payback remain hidden until a cash-realization percentage is supplied.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · evidence and financial rules"],
  },
  {
    id: "scene-13",
    eyebrow: "Measured autonomy",
    title: "Earn autonomy through a measured shadow pilot.",
    timing: "1:25",
    notes: [
      "The selected gate asks for zero false auto-approvals across at least 299 reviewed auto-eligible shadow orders.",
      "The one-sided 95% upper bound is 1 − 0.05^(1/n); at n=299 it falls below 1%. Change the controls to test other gates.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · user-locked proposed promotion gate"],
  },
  {
    id: "scene-14",
    eyebrow: "Recommendation",
    title: "Start in shadow. Promote one bounded path only after the evidence holds.",
    timing: "1:05",
    notes: [
      "Close on three decisions, not a generic thank-you: pilot ownership, risk gate, and value baseline.",
      "Recommend keeping the kill switch, review path, and sender-scoped capability unchanged during the pilot.",
    ],
    sources: ["TASK_B_IMPLEMENTATION_PLAN.md · recommendation and locked scope"],
  },
];

export const totalRehearsalSeconds = scenes.reduce((sum, scene) => {
  const [minutes, seconds] = scene.timing.split(":").map(Number);
  return sum + minutes * 60 + seconds;
}, 0);
