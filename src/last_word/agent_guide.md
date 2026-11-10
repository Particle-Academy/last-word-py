# Agent guide: professional documents with last-word

<!--
  The agent guide for last-word: what makes a document professional, and the
  schema idioms that produce it. Canonical copy: the PHP package's
  agent-guide/professional-documents.md; the Node and Python packages carry the same
  bytes (a test in each pins the hash). Distilled from rubric-judged builds in
  the Fancy document lab -- every rule here moved a measured score.
-->

## How to use this guide

It is written for an agent and works with any agent loop. Give it to the model
as part of its instructions (a system prompt, a skill, a rules file), together
with the package's JSON schema (`Agent::jsonSchema()` / `Agent.jsonSchema()` / `json_schema()`). Then the procedure is:

1. Read the schema once and plan a document in full: structure, every figure,
   every derived number, computed and checked before anything is written.
2. Build the whole JSON in one go, following the recipes below.
3. Check it: `Agent::validate` (PHP), `Agent.validate` (Node) or `validate` (Python) returns errors with a hint each.
   `Agent::validateAndRepair($doc)` (`validate_and_repair`) also applies safe repairs and returns
   `{ok, schema, errors}`: bare strings become runs, heading levels clamp to 1-6, a `colSpan`
   over 1000 clamps, a page key written straight on a break moves into `page`. Write the
   repaired `schema` when `ok` is true; when it is false, fix only the paths `errors` names.
   In PHP, check the length too: `Agent::layout($doc)` returns the page count and the block
   each page starts with, as the last-word-ui viewer draws them (PHP only for now).
4. Write it (`Agent::write` / `Agent.toBytes` / `to_bytes`). If you can render the file and look at it, do,
   but repair only what you can see is wrong: one-shot "improvements" made
   blind regressed more documents than they fixed.

You are writing a document a demanding executive would send to their board or a
client UNCHANGED. Meeting the brief is the floor. Follow the conventions of the
genre, lead with what the reader must know or decide, and analyse rather than restate.

## The bar (every item is required)

1. **Genre conventions.** A memo opens with a stacked header block (To / From / Date /
   Subject) and a rule beneath it. A report opens with a title, a date and an
   executive summary.
2. **Lead with the conclusion or the ask.** The first paragraph says what the reader
   must know or decide, with the key figures. Not "this memo covers...".
3. **Analysis, not repetition.** State each figure once in prose where it matters, and
   put the full set in ONE table. Prose explains causes, implications and decisions.
4. **Tables are finished.** A caption above ("Table 1. Q3 revenue by region"), a
   header row with a fill, numbers right-aligned, a total row when values add up, a
   source line below.
5. **Pages are balanced.** No page that is mostly empty, no heading stranded at the
   bottom of a page. Size the content to the length the brief asks for; never pad
   it with sections the brief did not ask for, and never force a page break.
6. **Page furniture.** Every multi-page document carries a running footer with the
   document's name and "Page N of M" (see `page.footer` below).
7. **Consistent hierarchy.** One heading colour and font, sizes stepping down by level,
   the same spacing before and after every heading.

## Never ship

- A placeholder ("[Insert date]", "TBD", "[Name]"). If the brief gives no date, use the
  reporting period ("Q3 2026 results, October 2026"); if no author, use the role
  ("Office of the CFO").
- A paragraph split so that one or two lines sit alone at the top of a page. Keep
  regional paragraphs short, and put `keepNext:true` on a paragraph that must stay
  with the next one.

## Schema idioms that work

- Page: `page:{size:"letter", margins:{top:72, bottom:72, left:72, right:72},
  footer:{left:"Q3 2026 Board Memo | Confidential", right:"Page {page} of {pages}"}}`.
  `{page}` and `{pages}` are live page fields. `page.header` works the same way (use it
  for a classification or audience line on long reports). `margins` also takes `header`
  and `footer`: points from the page edge to the header and to the footer (36 by default).
- First page apart: `page:{firstHeader:{blocks:[{type:"paragraph", runs:[{text:"ACME LTD",
  bold:true}]}, {type:"paragraph", runs:[{text:"1 Example Street, Exampleton"}]}]},
  header:{left:"Acme | Proposal 42", right:"Page {page}"}}`. `firstHeader` / `firstFooter`
  replace `header` / `footer` on page one only; without a `firstFooter` the footer is on page
  one too, and `firstFooter:{blocks:[]}` leaves page one without it. `evenHeader` /
  `evenFooter` do the same for even pages of a two-sided document. To keep a house
  letterhead template on page one only, write with the option `templateFirstPageOnly: true`.
- Mixed page shapes, for a table too wide for portrait: `{type:"pageBreak",
  page:{orientation:"landscape", margins:{left:28.8, right:28.8}}}`. The pages after it keep
  those settings until another break names a change, so turn back with
  `page:{orientation:"portrait"}`. `margins` merge side by side and every other key is
  replaced whole. `firstHeader` / `firstFooter` are never inherited: on a break they mean the
  first page after it. `page:{}` starts a new stretch with nothing changed. Only on a
  top-level break, and never `background`. Header and footer tab stops follow each page's
  width on their own. `{section}` counts the stretches and `{sectionpages}` is the page
  count of the current one.
- Header and footer text also takes `{date}`, `{date:d MMMM yyyy}`, `{time}`, `{author}`,
  `{title}`, `{filename}`, `{section}` and `{sectionpages}`: live Word fields, not typed
  values. `{title}` shows the document `title`; `{author}` shows `meta:{author:"..."}`. In
  the body they are plain text.
- Footnotes: a run with `text:""` and a `footnote`, placed right after the words it
  annotates: `runs:[{text:"Revenue grew 18%"}, {text:"", footnote:{blocks:[{type:"paragraph",
  runs:[{text:"Audited figures, FY2026; restated for the Q2 disposal."}]}]}}, {text:" on the prior year."}]`.
  Word numbers the marks and sets each note at the foot of its page; never type the number.
  Use them for sources, bases and caveats that would clutter the sentence. Not in a header
  or footer, and never one inside another.
  `defaultFont` (a clean sans such as "Calibri" or the brand body font), `defaultSize` 11.
- The brief's explicit form wins: if it asks for a "short list", write a list, not a
  table; if it asks for two pages, write two pages.
- Headings: `{type:"heading", level:1, runs:[{text:"Summary", color:"#1F3A5F"}], spaceBefore:18, spaceAfter:6, keepNext:true}`.
  Use the SAME colour and spacing on every heading of a level.
- Memo header block: a borderless 2-column table, `widths:[18,82]`,
  `borders:{top:{style:"none"},bottom:{style:"none"},left:{style:"none"},right:{style:"none"},insideH:{style:"none"},insideV:{style:"none"}}`,
  one row per field, label cell bold muted ("TO", "FROM", "DATE", "SUBJECT").
  Then a paragraph with `borders:{bottom:{style:"single", width:1, color:"#1F3A5F"}}` as the rule.
- Emphasis inside prose: runs with `bold:true` for the key figure; colour only for
  direction (green up, red down), sparingly.
- Callout for the decision being asked: a paragraph with `shading:"#EEF2F7"`,
  `borders:{left:{style:"single", width:4, color:"#1F3A5F"}}`, `indentLeft:8`.
- Table: `widths` as relative weights, `rows[0].header:true` with cell `shading`
  "#1F3A5F" and white bold runs; numeric cells hold a paragraph with `align:"right"`;
  `cellPadding:{top:4, bottom:4, left:6, right:6}`; borders: a bottom rule per row
  (`insideH`) and no vertical lines.
- Lists: `{type:"list", ordered:true, items:[{runs:[...]}]}` with `spaceAfter:4` on items.
- Charts are native Word charts, editable in Word:
  `{type:"chart", option:{xAxis:{type:"category", data:["Q1","Q2","Q3"]}, yAxis:{type:"value", name:"USD (thousands)"}, series:[{type:"bar", name:"Revenue", data:[120,135,150]}]}, numberFormat:"#,##0"}`.
  `option` is an Apache ECharts option, the same one a dark-slide chart takes. Column chart:
  a category `xAxis`. Horizontal bars: swap the axes (category `yAxis`, value `xAxis`). Line:
  `type:"line"`. Pie: `series:[{type:"pie", data:[{name:"Payroll", value:61}, {name:"Cloud", value:22}]}]`
  and no axes. Several series: add `legend:{show:true}`; one series needs none. Highlight the
  finding by colouring one point, `{value:57, itemStyle:{color:"#DC2626"}}`, over a muted
  series colour. Only bar, line and pie draw. The chart spans the text column unless
  `width` (a percentage) or `widthPx`/`heightPx` say otherwise.
  Caption it like a table: "Figure 1. ..." in a paragraph above with `keepNext:true`, and
  a source line below. A chart illustrates the table's figures; it does not replace them.
- Keep a heading with its content: `keepNext:true` on every heading.

## Recipes

- **Board memo:** header block + rule; "Summary" (the conclusion, the key figures, the
  decision asked) and the decision in a callout; one section of analysis per theme
  (not one per data row); one finished table; the actions in the form the brief asks
  for (a list stays a list; give each item an owner and a date inline); a closing line
  on what happens next; the running footer with "Page {page} of {pages}".
- **Report:** title, subtitle and date; executive summary (3-5 sentences); sections
  with numbered headings; tables and figures captioned and sourced; conclusion or
  recommendations.

## Working from an existing document

- Read: `Agent::read($bytesOrPath)` (Node `Agent.read(bytes)`) takes .docx, .doc, .odt or .rtf,
  told apart by content, and returns the same JSON you write; `fromBytes` is its alias. A file
  with several sections reads with the first section's `page` on top and each later section as
  a `pageBreak` whose `page` holds only what differs. What the JSON cannot hold (text columns, a
  page-number restart, a paper size other than letter, legal or A4) is listed in `unrepresented`
  with kind `layout-not-preserved`: it was in the source, not a fault of yours.
- Summarise before you dump: `Agent::describe($doc)` is a plain-text summary (title, block
  counts, word count, page stretches), far cheaper to read than the JSON.
- Edit, never regenerate: change only what was asked as ops. `Agent::opSchema()` is the JSON
  Schema for one op (`{op:"blocks.replace", path:"/blocks", index:3, block:{...}}`; also
  `insert`, `remove`, `move`, and the same for `items`, `rows`, `cells`, plus `doc.set`).
  `Agent::reduce($doc, $ops)` applies one op or a list; an op that does not resolve is skipped.
  `Agent::diff($a, $b)` gives the ops from `$a` to `$b` (store `diff($new, $old)` to keep a
  version), and `Agent::equivalent($a, $b)` says whether two documents write the same file.
- Markdown: `Agent::toMarkdown($doc)` and `Agent::fromMarkdown($markdown)`.
  `toMarkdownWithReport` (PHP) adds what markdown could not carry, by kind and location.
- Templates: `Agent::convertTemplate($bytesOrPath, $to)` turns a .dotx / .ott into its .docx /
  .odt twin or back (`$to` is `document`, `template` or null to flip). Macro-enabled files are
  refused.
- Content from another Fancy package (PHP): put `{type:"portable", content:...}` in `blocks`
  and `validateAndRepair` expands it, or convert first with `Agent::fromPortable($content)`;
  `Agent::validateContent($content)` checks it without converting. The other way,
  `Agent::catalog($input)` indexes the headings and tables with a selector each,
  `Agent::extract($input, $selector)` grabs one as portable content and
  `Agent::toPortable($doc, $selector)` does the same for a document you hold.
  `Agent::export($input, $target, $options, $policy)` (markdown, text, html, docx) is off
  unless the host's policy enables the target; `Agent::exportToolDefinition($policy)` is the
  tool that lists only those.

## Failures we have seen (do not repeat)

- To/From/Re run together on one line with no date. -> The stacked header block.
- A heading and a two-line paragraph per region, restating the summary and the table.
  -> One analysis section; figures once.
- Page 2 holding only a four-item list, 80% blank. -> Move material so both pages
  carry analysis; never pad with sections the brief did not ask for.
- A manual page break to "fix" layout, which left a blank page. -> Never insert
  `pageBreak` blocks in a memo.
- No page numbers on a multi-page memo. -> `page.footer`.
- "Change against the prior comparison period", a vague basis. -> Name the basis.
- Summary that opens with the total rather than the decision. -> Lead with the ask.
- A heading per data row ("North: $1,250,000.50, up 18.4%") repeating the table.
  -> Headings name themes ("Growth is concentrated in EMEA"); the table holds the rows.
- Page 1 dense, page 2 one-third empty. -> Estimate: a letter page at 11pt holds about
  45 lines of body text; balance sections across the pages the brief asks for.
- "A two-page memo" that the reader sees as three pages. A word count is a poor
  estimate: headings, lists, tables and a letterhead take room words do not show. ->
  In PHP, measure it: `$l = Agent::layout($doc, $options)` (pass the same `template`
  options you write with) gives `$l['pages']`, `$l['pageStarts']` (the block index each
  page starts with) and `$l['pageList'][$i]['fill']` (0..1). Too long: tighten prose or
  merge sections, then measure again. `$l['approximate']` lists anything only estimated
  (a font other than Calibri, code, a date field); empty means exact.

- A last page holding one stranded paragraph and a short section, 70% blank. ->
  Estimate the length first (about 45 lines of 11pt body per letter page, a table row
  ~1.5 lines, a heading ~3) and size the content so the last page is at least half
  full: tighten prose, or move the closing section up; never pad.
- A two-row tail of a table on the next page. -> Put `keepNext:true` on the caption
  and on the paragraph before a short table so it starts on the page that can hold it.
- "Action 3" in prose when the table lists it as action 2. -> After drafting, check
  every cross-reference (table numbers, action numbers, section numbers) against
  the thing it names.
- Section numbers typed into headings ("1. Eligibility") and repeated in a contents
  table. -> Fine for a policy, but then every reference must match; re-check them.
- Signature lines as bare rules with tiny captions. -> A two-column table: "Signed",
  "Name", "Title", "Date" rows with space to sign, 11pt labels.
- The headline figure only in prose (a cap or maximum fee). -> Put it in the table
  as its own labelled row.

## Before you write the document, check

Header block and date present; first paragraph states the conclusion and the ask;
every figure appears once in prose and once in the table, consistently; table has a
caption, aligned numbers and a source; every heading has keepNext; no page will be
mostly empty. Then write once.
