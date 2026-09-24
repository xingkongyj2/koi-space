# Ask block — text-only questionnaire for human-in-the-loop

When you need the user to answer one or more text-only multiple-choice
questions to disambiguate or gather requirements before proceeding,
emit a fenced ` ```ask ` block. The renderer turns it into a list of
radio/checkbox questions with an automatic "Other…" text-input
affordance per question.

After emitting the fence, **stop calling tools** — your turn ends and
the agent process idles until the user submits answers. The answers
arrive as the next user message shaped like:

```
Answered:
- Form factor: M.2 NVMe (internal)
- Capacity: 2 TB
- Budget: Other: around $250
```

Read the answers, route them into your next steps. Compare with the
` ```options ` fence, which is for image-driven product/listing picks;
use `ask` whenever the choices are text-only.

## When to use it

- **Disambiguating intent before browsing.** "Buy me an SSD" → ask
  form-factor + capacity + budget BEFORE you start scraping Amazon.
- **Requirements gathering.** "Build me a side project" → ask language
  + framework + deployment target.
- **Configuration / preferences.** Authentication method, theme,
  notification frequency, region selection.
- NOT for visual product picks — use ` ```options ` instead. The cards
  there carry images, prices, and structured fields per option, which
  matter for shopping but waste space for text decisions.

## The schema

```
```ask
{
  "prompt": "Before I shop, a few quick questions:",
  "questions": [
    {
      "question": "What kind of SSD do you want?",
      "header": "Form factor",
      "multiSelect": false,
      "allowOther": true,
      "options": [
        { "label": "M.2 NVMe (internal)", "description": "Fast internal SSD, slots into a motherboard or laptop M.2 slot" },
        { "label": "2.5\" SATA (internal)", "description": "Older internal SSD format, drops into laptops/desktops with SATA" },
        { "label": "External / portable USB SSD", "description": "Plugs into USB-C, no install needed" }
      ]
    },
    {
      "question": "What capacity?",
      "header": "Capacity",
      "multiSelect": false,
      "options": [
        { "label": "1 TB", "description": "Standard size, ~$60-100" },
        { "label": "2 TB", "description": "Roomier, ~$120-180" },
        { "label": "4 TB", "description": "Large, ~$220-350" }
      ]
    },
    {
      "question": "Budget ceiling?",
      "header": "Budget",
      "multiSelect": false,
      "options": [
        { "label": "Under $100", "description": "Stay cheap" },
        { "label": "Under $200", "description": "Mid-range" },
        { "label": "Under $400", "description": "Premium" },
        { "label": "No limit — just get a good one", "description": "Prioritize reviews and brand" }
      ]
    }
  ]
}
```
```

| field                  | required | notes |
|------------------------|----------|-------|
| `prompt`               | no       | One-line context for the whole form. Skip if the questions are self-explanatory. |
| `questions[].question` | **yes**  | Full question text, ending with "?". Read by the user. |
| `questions[].header`   | no       | Short chip label (≤12 chars), e.g. "Capacity". Useful when the question text is long; appears as a small uppercase tag above the question. |
| `questions[].multiSelect` | no    | Default `false` (radio). `true` renders checkboxes; user can pick multiple. |
| `questions[].allowOther`  | no    | Default `true`. When `true`, an "Other…" affordance with a text input is appended automatically — the user can always type a custom answer. Set `false` only when the listed options are truly exhaustive. |
| `questions[].options[].label`       | **yes** | 1-5 word display. The clickable text. |
| `questions[].options[].description` | no      | Optional context: tradeoff, pricing hint, when-to-pick guidance. |

Questions without a `question` string or with zero valid options are
silently dropped. If zero questions survive, the block is rejected.

## Picking labels + descriptions

- **Labels** are the line the user reads first. 1-5 words. Concrete,
  not abstract ("M.2 NVMe" not "Newest format").
- **Descriptions** are short context lines — pricing hints, when-to-pick
  guidance, tradeoffs. Skip when the label is self-explanatory.
- 2-4 options per question. More than that and the user scrolls a wall
  of radios. If you need 5+ choices, the answer is usually a `options`
  picker (image cards) or a clarifying back-and-forth in prose.

## "Other" — always-on by default

Every question gets an "Other…" text input unless you set
`allowOther: false`. The user can write a free-form answer there
(answers come back as `Other: <their text>` in the bulleted reply).
Don't try to enumerate every possible answer; trust the "Other"
escape valve.

## Multi-question forms

Up to ~4 questions in one block. If you need more, split into
sequential `ask` blocks across turns — too many questions in one
block fatigues the user.

## The turn-ending rule

**Emit the fence, then stop.** No tool calls after the closing ``` ` ```
. Your turn is over. The user submits answers; the next user message
arrives with the structured `Answered:` reply.
