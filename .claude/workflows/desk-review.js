export const meta = {
  name: 'desk-review',
  description: 'Daily head-trader review: attribution, verdicts due, risk, and the single most valuable next action',
  whenToUse: 'Once per daily gate cycle (career mode) — or on demand after a surprising day',
  phases: [
    { title: 'Review', detail: '3 desk lenses in parallel' },
    { title: 'Synthesize', detail: 'the daily decision memo' },
  ],
}

const API = 'https://gracious-inspiration-production.up.railway.app'

const CONTEXT = `
You are one lens of the DAILY DESK REVIEW for a memecoin trading operation run as a CAREER (AxiS: "the bills have to be paid"). Read REVENUE_PLAN.md and OPERATORS_MANUAL.md in the repo root FIRST — they carry the honesty rules (fidelity currency, big-number audit, attribution) and the go-live gates. Work from /c/Users/jcole/multichain-bot with python.

DATA: scratchpad/_gradebook.json (bar status, run "python scripts/gradebook.py" if stale >2h); "python scripts/revenue_check.py" (distance-to-revenue); ${API}/api/regime + /api/regime/history (tape + router); ${API}/api/rh-paper?bot=<id>&raw=1 (RH ledgers); ${API}/api/leaderboard (SOL daily); scratchpad/_dead_tokens.json (corpse set); scratchpad/_flip_sim.json.

NON-NEGOTIABLES: dead-token/phantom-corrected dollars only; benchmark vs the tape before judging anything; numbers you cannot attribute are numbers you do not report; no promotion talk below the bar (n>=30/5d/20tok/drop-top-2); only AxiS retires bots or arms live.
`

const LENS = {
  type: 'object',
  properties: {
    findings: { type: 'string', description: 'what this lens found today, numbers-first, fidelity-honest' },
    actions: { type: 'string', description: 'concrete actions this lens demands (or "none")' },
    flags_for_axis: { type: 'string', description: 'decisions only AxiS can make (or "none")' },
  },
  required: ['findings', 'actions', 'flags_for_axis'],
  additionalProperties: false,
}

phase('Review')
const lenses = await parallel([
  () => agent(`${CONTEXT}\nYOUR LENS — P&L ATTRIBUTION: decompose the last 24h on both chains into TAPE / INSTRUMENT / STRUCTURE. Big-number audit anything ±$50. Where did honest dollars actually move, and does it match the regime map's prediction (sick-window dips pay; pump windows commit corpses)?`,
    { label: 'desk:attribution', phase: 'Review', schema: LENS, model: 'fable' }),
  () => agent(`${CONTEXT}\nYOUR LENS — VERDICTS DUE: run the gradebook fresh, then list every experiment AT or NEAR its bar or kill line. For each: the pre-registered criterion, today's number, and the verdict (GRADE NOW / kill-line CROSSED / accruing, days left). Include the pro seat's population check (2-20 entries/day) and distance-to-revenue.`,
    { label: 'desk:verdicts', phase: 'Review', schema: LENS, model: 'fable' }),
  () => agent(`${CONTEXT}\nYOUR LENS — RISK & INSTRUMENTS: pipes alive (ledger upload age, fidelity_ts age, regime snapshot cadence, zero Tracebacks)? dead-token set fresh (<36h)? wallet still $40.71 and untouched? any bot whose paper and fidelity numbers are diverging fast (the illusion alarm)? any silent population change (a bot firing 5x more or less than yesterday)?`,
    { label: 'desk:risk', phase: 'Review', schema: LENS, model: 'fable' }),
])

phase('Synthesize')
const memo = await agent(
  `${CONTEXT}\nThree desk lenses reported:\n${JSON.stringify(lenses.filter(Boolean), null, 2)}\n\nWrite the DAILY DESK MEMO for AxiS (short, numbers-first): (1) the day in one honest paragraph benchmarked vs the tape; (2) verdicts due today with their pre-registered criteria; (3) DISTANCE TO REVENUE — which gates the go-live candidate passed/failed today and the realistic date; (4) flags only AxiS can decide; (5) THE ONE MOST VALUABLE ACTION for the next 24h and why it beats the alternatives. No filler, no hedging language that dodges a call.`,
  { label: 'desk:memo', phase: 'Synthesize', model: 'fable' }
)

return { memo, lenses: lenses.filter(Boolean) }
