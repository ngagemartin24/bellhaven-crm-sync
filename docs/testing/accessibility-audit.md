> Audit of commit 3b6b0d1, run on a test copy (saved data, fake CRM). Every finding below was fixed in 402f177 and re-measured: axe-core now reports 0 violations on all 42 cards at both sizes. See ../TESTING.md.

# Accessibility audit: Bellhaven CRM sync review app (WCAG 2.2 AA)

Target: test copy at 127.0.0.1:8774 only. Date: 2026-11-08. Nothing in the app's code was changed.
Method: axe-core 4.10.3 (tags wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa) in Playwright Chromium at 1280x800 and 390x844, plus scripted keyboard, geometry, contrast and reflow measurements. The audit's scripts, raw axe JSON and screenshots were kept outside the repo.

## Summary

**axe-core: 0 critical, 11 serious (2 rules), 0 moderate, 0 minor across 55 page-states (desktop + mobile).** The two rules are `target-size` (9 page-states) and `scrollable-region-focusable` (2 page-states; a sweep of all 32 open cards at both sizes finds it on all 5 Duplicates cards at 390px only, 0 on desktop). 790 axe "needs review" color-contrast nodes were all resolved by my own computation (0 failures, lowest 4.59:1).
**Manual findings: 15 total: 3 High, 8 Medium, 4 Low, 0 Critical.** The worst problems are all at narrow widths (phones, 320px, 400% zoom): the fixed bottom nav bar covers the sticky Approve/Reject bar and focused links. Desktop at 1280 and at 200% zoom is clean apart from A11Y-008, 009, 010, 011 and the Low items.

States covered: `/` (no card), one or more cards in each of the 8 sections (all 32 open cards swept), `/locations` (+ one verdict filter), `/history` (collapsed and open), after Approve, after Reject, FAILED ("Stopped", Retry + Discard), uncertain ("Check again"), and the 403 and 409 message pages.
Deviation from the brief: `fault?method=POST&mode=fail&status=500` on a new-account approve gave the uncertain "Check again" state (the app treats a 500 on a create as "may have landed"), not Retry. I reached the Retry/Discard "Stopped" state with `method=PATCH&mode=fail&status=500` on a Details card. Both states were audited.

## Findings

| ID | Severity | WCAG criterion | Page/state | Element (selector) | What fails | Evidence | Suggested fix |
|---|---|---|---|---|---|---|---|
| A11Y-001 | High | 2.4.11 Focus Not Obscured (Minimum) (AA) | Any card awaiting a decision, viewport <=640px (390, 320, 640) | `footer.actions button` (Approve, Reject) under `.rail` | The fixed bottom nav bar (60px, z-index 5) sits on top of the sticky decision bar's bottom 60px. Approve and Reject are fully hidden at every scroll position except the very end of the page. Focus moved onto them (as screen-reader or switch navigation does) is invisible. In my Tab sweep these two buttons were not flagged as covered (I did not establish why; the page is probably already scrolled to its end by the note input stop), so the barrier is for focus that arrives another way. | 390x844: Approve spans y=794-830, rail top y=784; `elementFromPoint` at the button centre returns a rail link at scrollY 0, 400 and 900; same at 320x568, 320x256, 640x400. `shots/mobile-card-chow-scrolled-300.png` | At <=640px set `.actions{bottom:60px}` so it sticks above the bar |
| A11Y-002 | High | 1.4.10 Reflow (AA) | Any card awaiting a decision at 400% zoom (1280x800 at 400% = 320x200) | `footer.actions` + `.rail` | The stuck decision bar (161px) plus the fixed rail (60px) is 221px, more than the 200px viewport, so no card content is visible until the page end. At 320x256 only 35px is left for reading. | Measured 320x200: actionsH=161, railH=60, vh=200. `shots/400pct-320x200-card.png`, `shots/320x256-card-chow-scroll600.png` | Do not make `.actions` sticky below ~700px height or width <=640px (`position:static`) |
| A11Y-003 | High | 2.4.11 Focus Not Obscured (Minimum) (AA) | `/locations` at 390px (page of 96 Tab stops) | `table.table td a` (verdict/proposal links, "View in CRM") under `.rail` | Tabbing scrolls the focused link to the viewport bottom, under the fixed rail. 14 of 96 stops (e.g. #22, 27, 28, 41, 47, 69, 70, 71, 80, 81, 88, 93, 94, 95) have 0% visible area, so keyboard users cannot see where focus is. | Tab sweep `json/keyboard-tabs.json` plus debug run: link top=789-805, rail top=784, covering element is a rail link. `shots/mobile-locations-focus-obscured.png` | At <=640px add `html{scroll-padding-bottom:72px}` so focus scrolls clear of the bar |
| A11Y-004 | Medium | 2.1.1 Keyboard (A) | Duplicates cards (all 5) at 390px | `table.compare` (display:block; overflow-x:auto) | Table is wider than its box (451px content in 308px) and scrolls sideways, but has no focusable content and no tabindex, so a keyboard-only user cannot reveal the other CRM columns. Touch and mouse can. | axe `scrollable-region-focusable` (serious): `json/axe_card-duplicates_mobile.json`, `axe_card-duplicates-kettering_mobile.json`, `axe_sweep_all-cards_mobile.json`; scrollWidth 451 vs clientWidth 308, tabindex None | Wrap the table in `<div role="region" aria-label="Website and CRM side by side" tabindex="0" style="overflow-x:auto">` |
| A11Y-005 | Medium | 1.3.1 Info and Relationships (A) | `/locations` and open `/history` entries at <=640px | `.table thead` (display:none) | Mobile CSS hides the header row. Column headers drop out of the accessibility tree (0 columnheaders vs 4 and 5 on desktop) and sighted users lose the labels too: on Locations the verdict "Gone from the website" and the proposal link "Gone from the website" look identical; history steps lose Step/Request/Before/Sent/Result. | AX tree at 390: Locations columnheaders=0 (desktop 4); history table columnheaders=0 (desktop 5). `shots/mobile-history-entry-open.png` | Keep `thead` visually-hidden (not display:none) and print a `data-label` via `td::before` on mobile |
| A11Y-006 | Medium | 1.4.10 Reflow (AA) | `/locations` at 320 CSS px | `.who` (white-space:nowrap in table cell) | Long names force the table to 367px, so the page scrolls horizontally in two dimensions. Also +71px at 390 under text-spacing. | scrollWidth 367 vs 320; with `.who{white-space:normal}` injected scrollWidth is 320 | `.table .who{white-space:normal;overflow-wrap:anywhere}` |
| A11Y-007 | Medium | 1.4.10 Reflow (AA) | Change of ownership cards (Marietta, Tiffin) at 320px | `.tags` (flex, nowrap) and its `.state` span | The three chips (category, confidence, "Waiting for review") do not wrap and overflow the card by 29px, so the page scrolls sideways. Both Change-of-ownership cards that have a confidence band do this. | scrollWidth 332 vs 320 on 2 of 32 cards; `.state` right edge 332; with `.tags{flex-wrap:wrap}` it is 320. `shots/320-card-chow-top.png` | `.tags{flex-wrap:wrap}` |
| A11Y-008 | Medium | 1.4.11 Non-text Contrast (AA) | Every card page (note field), and the queue filter | `.decide input[type=text]`, `#location` (select) | The only visible edge of each field is a `#d4d4d4` border on white (1.48:1; 3:1 required). The fill is white on a white panel, so the fields are hard to see for low-vision users. (Secondary buttons have a 1.26:1 fill, but their text is 15.7:1, so I did not count them.) | Computed border `#d4d4d4` vs `#ffffff` = 1.48:1 | `--control:#8a8a8a` (3.45:1) or darker |
| A11Y-009 | Medium | 1.3.1 Info and Relationships (A) | Cards with a side-by-side table (Duplicates, Wrong parent, Possible matches, Change of ownership) | `.compare td.differs`, `td.same` | Which values differ from the website is shown only by ink colour/weight (CSS classes). There is no text or ARIA, and the help line itself says "full ink" / "grey". Screen-reader users get no cue. (Colour difference is 3.7:1, so 1.4.1 is met for sighted users.) | Markup in `_card.html`; `td.same` #6b6b6b vs `td.differs` #0a0a0a = 3.7:1; AX tree has no differs/same text | Add visually-hidden "differs" / "matches" text in each cell |
| A11Y-010 | Medium | 2.5.8 Target Size (Minimum) (AA), per axe; selection indicator also hidden | `/` with a card open after any row click or any decision (desktop and 390px) | `.row.selected` in `.list` (sticky `.toolbar`) | Row links and the post-decision redirect carry `#card-<id>`. The browser scrolls that row to the list top, under the sticky filter toolbar, leaving 1px of a 58px row visible. The "which proposal is open" cue (shade and 2px bar) is invisible and the link is mostly covered. | axe `target-size` (serious) on 9 page-states; measured row 93-151 vs toolbar bottom 150; `shots/after-row-click-selected-hidden.png` | `.list{scroll-padding-top:64px}` (or `li{scroll-margin-top:64px}`) |
| A11Y-011 | Medium | No SC fails outright (discoverability; cf. 1.3.1) | `/history` | `details.entry > summary` (display:grid) | The grid summary shows no disclosure triangle. Rows look static, and the whole audit trail (requests, CRM answers, old values) is hidden behind clicks with no visible cue. Keyboard and AT are fine. | Computed `list-style-type: disclosure-closed` but nothing is drawn; `shots/history-collapsed.png` | Add a visible chevron (`summary::after`) |
| A11Y-012 | Low | 2.4.1 Bypass Blocks (A) (met via landmarks; keyboard-only gap) | All pages with a card, desktop | Page start (no skip link) | No skip link. A keyboard-only user passes 45 Tab stops (rail, filter, every list row, the card's links) to reach Approve. Landmarks (nav, main) satisfy the criterion for AT users. | Counted: 45 Tabs to Approve; 47 in `json/keyboard-tabs.json` on other cards | Add "Skip to proposal" link to the `article` |
| A11Y-013 | Low | 2.4.2 Page Titled (A) | 403 and 409 message pages; any `/?card=...` | `<title>` | Message pages are titled "409 · Bellhaven CRM sync" (code only; heading says "Not applied"). All cards share "Review queue · Bellhaven CRM sync", so opening a proposal does not change the title. Titles do describe the page type. | Titles read from the DOM | `{{ heading }}` in the message title; add card title to the queue title |
| A11Y-014 | Low | 4.1.3 Status Messages (AA) (borderline; no screen reader run) | After Approve/Reject | `p.flash[role=status]` | The result ("Approved: 1 of 1 writes done") is part of the freshly loaded page after redirect, where screen readers usually do not announce a status node present at load. Focus resets to the page start and a different proposal (with its own Approve button in the same place) is now open. | After Enter on Approve: url becomes another card, `document.activeElement`=BODY, flash role=status present | Fragment-link to the flash with `tabindex="-1"` so focus moves to it |
| A11Y-015 | Low | 1.4.4 Resize Text (AA) (information remains available) | `/` list | `.who`, `.line2`, `.band` (ellipsis) | Row titles and summaries are cut with "..." (29 truncated rows at 1280, 43 at 390, 10 at 200% zoom) with no tooltip. Full text is in the link's accessible name and in the open pane. | Count of `scrollWidth > clientWidth` | Allow two-line wrap or add `title` |

## WCAG 2.2 new criteria

2.4.11 Focus Not Obscured (AA): FAIL on narrow widths (A11Y-001, 003); desktop passes (0 fully hidden of ~310 stops on 6 pages).
2.4.12 / 2.4.13 (AAA): not tested (out of AA scope); 2px `#1d5fbf` outline is 6.1:1.
2.5.7 Dragging: pass (no drag operations).
2.5.8 Target Size: pass except A11Y-010; buttons 36px, rail targets 40-72x40-48.
3.2.6 Consistent Help: not applicable (no help mechanism).
3.3.7 Redundant Entry, 3.3.8 / 3.3.9 Accessible Authentication: not applicable (no login; one optional note field).

## Passed checks

- Language: `<html lang="en">` on every page, including the 403/409 message pages.
- Page titles: present and distinct for `/`, `/locations`, `/history` (see A11Y-013 for the caveats).
- Headings: one h1 per page; h1, h2, h3 order with no skipped levels on every card.
- Landmarks: one `nav` (labelled), one `main`, labelled `region`s and `article`; no duplicate unlabelled landmarks.
- Labels: note input has a programmatically associated visible label; the location select has a visually-hidden label; no placeholder-as-label.
- Button names: Approve, Reject, Retry, Discard, Check again, Filter are distinct and exact. Only one Approve/Reject pair exists per page (one open card at a time), inside `article` labelled by the proposal title (not several identical Approve buttons).
- Rail links: icons are `aria-hidden`; aria-labels contain the visible text (Label in Name); `aria-current` set.
- Text contrast: 66 distinct foreground/background pairs across all states, 0 below 4.5:1 (lowest 4.59:1). Approve 18.4:1, Reject 15.7:1; badges: Check 6.84, Stopped/failed 8.63, Approved 14.77, neutral 9.36.
- Focus indicator: `:focus-visible` 2px `#1d5fbf`, offset 2px, 6.1:1 on white and 5.3:1 on grey; no `outline:none`; every one of ~600 Tab stops had an outline.
- Keyboard: every control reachable and operable; Approve by Enter and Reject by Space both work; Tab order follows reading order; no keyboard trap (Tab cycles out at the end).
- details/summary: native; Enter and Space toggle on `/history` and on the card's "API calls" disclosure; the open content has no stray focus stops.
- Reduced motion: no CSS animations or transitions; `scroll-behavior:auto`; 0 running animations under `prefers-reduced-motion: reduce`.
- Zoom 200% (640x400 at 2x): no horizontal scroll on 8 states.
- Text spacing (1.4.12): no clipped or overlapping text on 5 states at both sizes.
- Viewport meta allows zoom (no `user-scalable=no`).
- Failed ("Stopped") and uncertain ("Checking whether a create landed") states are stated in text and notice copy, not by colour alone; yes/no signal marks have `role=img` labels.
- No time limits, no auto-updating content, filter select does not submit on change (3.2.2).
- Consistent navigation and identification across pages (3.2.3, 3.2.4).
- 403 "Form expired" and 409 "Not applied" pages: axe clean.
- axe: 0 violations on `/`, `/locations`, `/history` and on the other 7 card types on desktop; 0 desktop violations on any of 32 cards.

## Untested, and why

- Screen readers (NVDA, VoiceOver, TalkBack, JAWS): none on this box. Announcement behaviour (A11Y-014) is judged from markup and the Chromium accessibility tree only.
- "Check" badge cards, "Needs attention"/Acknowledge cards, "Held" cards and the incomplete-run notice: not present in this copy's data. Their colours were computed from the CSS tokens (Check 6.84:1) but the states were not rendered.
- Forced-colors / Windows High Contrast and `prefers-contrast: more`: not run. Note the selected-row bar and current-page highlight rely on background colour, so check these first.
- Text-only resize / browser default font size: not exercised; all sizes are in px (page zoom works).
- Real phones and other browsers: mobile was an emulated 390x844 and 320-wide Chromium viewport; Firefox and Safari not run.
- Unknown routes (`/nope`) return a raw JSON 404, not an HTML page; no a11y measurement possible.
- Linked PDFs and documents: none exist.
