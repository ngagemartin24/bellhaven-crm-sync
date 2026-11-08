# Writeup

A daily run reads Bellhaven's website and the CRM, matches each of the website's 35 communities to CRM
accounts, and turns every difference into a proposal that carries its evidence. Nothing reaches the CRM until
a person approves the proposal in the review app. Every decision is stored per change, so later runs never
ask about the same change twice.

## Matching approach

**Reading the website.** The scraper reads the home page, the About page and every directory page, and collects
every community link it finds. Bellhaven of Findlay is linked from the home page but missing from the
directory, so a directory-only crawl would have missed it. The home page says "35 communities"; when the count
found differs, or a page fails, the run is marked incomplete and proposes nothing that depends on a location
being absent.

**Cleaning before comparing.** Streets are compared with abbreviations expanded ("Pk" = "Pike", "Rd" =
"Road"), phones by digits, names without filler words, and the website's care offerings are mapped to the
CRM's care types.

**Matching rules.** A pair is compared only when the state matches and something local is shared (city,
ZIP, phone or administrator). Same town alone is never a match. Then:

| Band | When | What happens |
|---|---|---|
| High | same street at the same city or ZIP | linked |
| Medium | same place plus the administrator or the phone, where the street differs or can't be compared (Ashtabula's billing street is a PO Box) | linked, evidence shown |
| Low | same place and a similar name, nothing else | shown as a possible match only; never acted on by itself |

Points (street 50, administrator 25, phone 10, ZIP 5, city 5, name up to 10) order the candidates and appear as
evidence; the band decides. Anything ambiguous goes to a person: two medium matches and no
strong one, strong matches at different addresses, or a look-alike under another owner whose link would need
the billing route. Several strong matches at the same full address with a compatible care type are
duplicates. The survivor is chosen in this order: billing history, already under Bellhaven, has contacts,
closest to the website, came from Harborview (which Bellhaven bought in 2025), lowest id.

The bands, weights, billing rule, survivor order, synced fields and note format live in
`src/bellhaven_sync/rules.py`, each pinned by a test, so changing one of them is a one-file edit. Text cleaning
(street abbreviations, the care-label mapping) is in `normalize.py`.

**The billing rule** runs before every parent change and again right before the write. With revenue history
and outstanding AR, the old account is left exactly as it is except `chow_current_account`, and a new account
is created under Bellhaven. Otherwise the account moves directly.

## The judgment calls

| Location or account | What the data shows | What I did |
|---|---|---|
| Tiffin, Marietta | Under Cedar Trail; $84,000 revenue / $12,400 AR and $51,250 / $3,800 | Billing rule: new Bellhaven account with the website's administrator; old account untouched except its pointer |
| Lima | Under Harborview; $47,000 revenue, $0 AR | Not both, so moved directly |
| Findlay | No parent; $22,000 revenue, $0 AR | Parent set directly |
| Zanesville | Under Cedar Trail as "Cedar Trail of Zanesville"; $0 | Moved directly and renamed |
| Kettering | Three copies at 3313 Wilmington Pike, all $0, no contacts, none under Bellhaven | Kept Harborview's copy (they tie until the Harborview tie-break), moved and renamed it; the other two marked duplicates |
| Owosso | Two copies; the retired one holds the only contact, Tricia Lindqvist | Retired the copy and moved her to the survivor in the same change |
| Monroe, Port Clinton, Erie | Copies left over from Harborview and Cedar Trail | Duplicates of the survivor, Inactive |
| Sandusky | Off the website; $130,000 / $5,200; Millstone Care of Sandusky already exists at 2715 Columbus Ave, Skilled Nursing | The one exception to the rule's wording. An account already existed under Millstone at the same full address and care type. I used it as the successor rather than create a duplicate, and set only the old account's pointer |
| Alliance, Coldwater | Off the website, $0, nothing else at the address | Needs Review with a dated note; parent unchanged, since a closure or sale isn't confirmed |
| Bellhaven at Union Square | 118 Union Square Dr; the CRM's Union Square Senior Living is at 240 Market St with Dale Croft, where the website lists Phil Holloway | Shown as a possible match and rejected; a new account created |
| Amberly Manor | Hudson, OH; the CRM's Amberly Manor is in Colorado Springs, CO | Different state, never compared; new account |
| Carlisle, PA | The CRM has Bellhaven of New Carlisle, OH, which matches the website's New Carlisle | Carlisle is a new account; New Carlisle unchanged |
| Ashtabula | Billing street PO Box 517; phone and administrator match | Medium match; the PO Box billing street is kept |
| Portsmouth | CRM ZIP 45626, website 45662 | ZIP corrected |
| 15 accounts | Phone differs from the website | Phone set to the website's |
| 7 more accounts | Old or slightly different names (Riverbend Manor Care Center is now Chagrin Falls) | Renamed to the website's exact name (9 renames in all, with Kettering and Zanesville) |
| Street spellings | "Ln" vs "Lane" and similar on otherwise identical addresses | Left alone: rewriting them adds noise and no information |
| Administrators | Four accounts list a different administrator; five list none | Shown as evidence, not written (see next) |
| Harborview's parent account | All its facilities now sit under Bellhaven | Left alone: the brief is about facilities |

That is 42 proposals: 41 approved, 1 rejected (the Union Square look-alike). The CRM ends with 127 accounts
(6 created) and 73 contacts (6 created, 1 moved).

After the last approval was written (2026-11-08 15:37 UTC), `scripts/verify.py` against the real CRM printed
`PASS: the CRM matches the expected end state (127 accounts and 73 contacts checked)`, and a second run read
35 of 35 locations and 127 accounts and proposed 0 changes.

## Safety and re-runs

The daily run cannot write: its CRM client refuses writes, and a test proves it. Approve writes a proposal's
steps in order, and before each write the app reads the record again and writes only if it still holds what
the reviewer saw. Otherwise it stops and says what changed (for example "phone is now (330) 555-0123, not (517) 409-8156
as reviewed"). A step log makes Retry safe. A create whose answer never arrived is looked for, never sent
twice. A second run proposes nothing already approved or rejected. A sale or a "gone from the website" change
waits whenever the latest website read was incomplete.

## How I used AI tools

I set the requirements and made every judgment call above in a structured interview with Claude Code, which
then wrote the design, the plan, the code and the tests to that spec. A second model, GPT-6 Astra, reviewed
the design four times and the code six times, independently; the last three code reviews passed with no
findings. A final review of the whole submission, by Astra and separately by Fable 5.1, confirmed the
corrected CRM and found one more write-path gap and a mislabelled evidence row; both are fixed and tested. The reviews found real problems, mostly in the write path: retries, lost answers, a run racing a
reviewer. Each one got a failing test before its fix. For the answers themselves, a fresh Astra session that
never saw my design classified every location from the same website and CRM data. It reached the same result
for every location and leftover account except Kettering's survivor, where the three copies tie on everything
but their history, and I kept Harborview's copy.

The first real approvals showed one thing the fake CRM had hidden: the real CRM answers an update with only the
record's id, so every update read as unconfirmed. The writes had all landed; the app now confirms an update by
reading the record back. I made the first two decisions in the app myself (Marietta and Kettering). The other
40 follow the answer key above: a browser script clicked them through the app's own Approve and Reject buttons
on my instruction, and History records each one. The demo video was recorded the same way and narrated with an
ElevenLabs clone of my own voice. This runs on my own agent setup, which keeps the decisions, reviews and test runs on
record. The time I report is my own time directing and checking; the agents ran for many more hours on their
own, building, testing and reviewing.

## What I'd build next

1. **Medicare's public ownership data.** CMS publishes every skilled-nursing change of ownership since 2016,
   with buyer and seller, and a file naming each facility's owners. Matching those against the CRM would catch
   a sale before the operator updates its website, for every operator in the CRM. Assisted living is
   licensed by each state, so it would need state sources.
2. **Every operator.** One settings row per parent company (website plus CRM account). The page parsing,
   the Harborview tie-break and the note wording would need adapting; the matching and the write path would not.
3. **Administrators.** Propose adding the website's administrator where none is listed and retiring the old
   one where they differ, as its own reviewed card.
4. **Learning from decisions.** Every approve and reject is a labelled example. Measure which signals predicted
   a rejection, tune the bands, and later auto-approve routine high-confidence phone fixes.
