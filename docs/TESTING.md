# Testing

Every check below ran against a test copy of the app: the saved website and CRM snapshot behind an in-memory fake CRM.
Nothing here wrote to the real CRM. The one exception is the live run itself, where the real CRM found a bug the
fake could not (see "Found in real use").

## Summary

| Tool | What it checked | Before | After |
|---|---|---|---|
| pytest | matching, billing rule, proposals, decision memory, writes, retries, the app, the CLI | 170 tests | 203 tests pass |
| Playwright (browser test) | approve, reject, re-run in a real browser; page fits the window | 1 pass | 1 pass |
| axe-core 4.10.3 | WCAG 2.2 A/AA on all 42 cards, Locations and History, at 1280x800 and 390x844 | 11 serious | 0 |
| Manual WCAG 2.2 audit | keyboard, focus, reflow at 320px and 400% zoom, contrast, screen-reader semantics | 15 findings | all 15 fixed |
| Lighthouse 12 | four pages, desktop and mobile | 100 / 100 / 75-76 / 90 | 100 / 100 / 79 / 100 |
| Security review and probe | request forgery, host checks, where the CRM key can appear, escaping, headers | 2 medium, 4 low | fixed, one low accepted by design |
| gitleaks, trivy, semgrep | secrets in history and files, dependency CVEs, Python code patterns | clean | clean |
| Independent review (GPT-6 Astra; Fable 5.1 for the final pass) | design (4 rounds), code (6 rounds), then the whole submission | | code rounds 8-10 PASS; final review: CRM correct, findings fixed |
| impeccable audit | accessibility, performance, theming, responsive, implementation integrity | | 18 / 20 |

Lighthouse columns are Performance / Accessibility / Best Practices / SEO. The Best Practices points still missing
are HTTPS checks: the app is meant to run on a private network over plain HTTP.

## Found in real use

The first approvals on the real CRM showed that it answers an update with the record's id only, while the fake CRM
answered with the whole record. Every update was therefore reported "may have landed", and each Retry got one write
further. Reading the accounts back showed every write had landed. The fix (17bdde1) confirms an update by reading the
record back when the reply does not echo the fields; a failed read-back still counts as "may have landed". The fake
CRM now has a switch that answers the way the real one does, and three tests pin the behaviour.

## Accessibility

Full report: [testing/accessibility-audit.md](testing/accessibility-audit.md). The fixes (402f177):

- On phones and short windows the Approve/Reject bar sits at the end of the card instead of sticking over the
  evidence and under the bottom navigation bar; focus scrolls clear of that bar.
- The side-by-side table scrolls by keyboard and says in text which values differ from the website.
- Phone tables keep their column labels; long names and card tags wrap.
- Field borders at 3.45:1; the selected row no longer hides under the filter bar; history rows show a chevron.
- A skip link to the open proposal; tab titles name the open proposal; error pages name the problem; the result of a
  decision takes focus; unknown pages answer with an HTML page.

Not tested: real screen readers (NVDA, VoiceOver), Windows forced colours, Firefox and Safari.

## Security

Full report: [testing/security-review.md](testing/security-review.md). The fixes (e4ade52):

| Finding | Fix |
|---|---|
| Medium: no Host, Origin or Referer check | the app answers only to the address it was started on and refuses a POST from another site |
| Medium: the CRM key was in page HTML (the CRM's own page links carry it) | CRM links go through `/crm/<id>`, which redirects on click; the key never reaches a page |
| Low: the form token was not bound to the server | signed with a per-process secret |
| Low: no security headers | frame, content-type, referrer and content-security policies; pages are not cached |
| Low: decision database world-readable | created owner-only |
| Low: no login | accepted: one reviewer on a private network, as specified |

## Lighthouse

Before: Performance 100, Accessibility 100, Best Practices 75-76 (a console 404 for the tab icon, no compression, no
cache lifetime on static files), SEO 90 (no page description). After 84a7941 and d061efa: 100 / 100 / 79 / 100 on
all eight runs, largest paint 0.2 s on desktop and 0.8 s on mobile, layout shift under 0.01.

## Code review

GPT-6 Astra reviewed the design four times (rounds 1-4) and the code six times (rounds 5-10). Rounds 5-7 failed and
each finding got a failing test before its fix; rounds 8-10 passed with no findings. Round 9 covered the hardening,
the live-bug fix and the tab icon ("Read-back failures preserve uncertainty and audit evidence; Retry and Discard
remain safe"). A final review of the whole submission (round 11), by Astra and separately by Fable 5.1, re-derived
the corrected CRM from the data and agreed with it everywhere. It found a related-account check that ran only
before the first write, an evidence row that claimed a match it had not compared, a missing single-process test and
several documentation errors; all are fixed (DESIGN Appendix B9).

## impeccable audit

| Dimension | Score | Evidence |
|---|---|---|
| Accessibility | 3 | axe 0 violations and all 15 manual findings fixed; screen readers not tested |
| Performance | 4 | Lighthouse Performance 100 everywhere; no JavaScript; one stylesheet and two variable fonts (Geist, Geist Mono) |
| Theming | 3 | every colour is a token (0 hard-coded colours outside `:root`, 0 inline styles); light theme only |
| Responsive | 4 | checked at 1440, 1280, tablet, 390 and 320 px and at 200% and 400% zoom; targets pass WCAG 2.5.8 |
| Implementation integrity | 4 | every element serves a review decision; impeccable's detector script is not installed here, so this is by hand |
| **Total** | **18 / 20** | Excellent |

## Walkthroughs

Recorded with Playwright on a test copy, captions and a visible cursor added, no editing:

1. [Demo: approve a change of ownership, reject a look-alike](walkthroughs/01-demo-approve-and-reject.mp4) (82 s)
2. [Tour of the eight kinds of change](walkthroughs/02-tour-every-kind-of-change.mp4) (77 s)
3. [Locations: every verdict](walkthroughs/03-locations-every-verdict.mp4) (24 s)
4. [When the CRM fails: refused, lost answer, changed record, double click](walkthroughs/04-when-the-crm-fails.mp4) (74 s)
5. [Phone layout](walkthroughs/05-phone-layout.mp4) (23 s)
6. [Clearing the whole queue, then the end-state check](walkthroughs/06-clearing-the-whole-queue.mp4) (66 s)
7. [Keyboard only](walkthroughs/07-keyboard-only.mp4) (27 s)

## Run the checks yourself

```
uv run pytest -q          # 203 tests, about 25 s
uv run pytest -m e2e -q   # the browser test (needs `uv run playwright install chromium` once)
```
