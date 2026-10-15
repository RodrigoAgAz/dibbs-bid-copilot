# Data sources (observed formats)

Everything below was observed on **2026-10-09** against the live public sites, using
`backend/scripts/dibbs_discovery.py` (all samples are saved under `backend/tests/fixtures/`,
see the [fixture index](#fixture-index) at the end). Anything not directly observed is marked
**UNVERIFIED**.

Ground rules used during discovery (and required for the production connectors):

* `User-Agent: DIBBSBidCopilot/0.1 (+mailto:ops@copilot.example.com)` on every request
  (httpx and the Playwright browser context).
* ≤ 1 request/second/host, exponential backoff (2s, 4s, 8s, 16s + jitter) on 429/5xx/network errors.
* Public pages only. No login, no quote forms (`/RA/...`), no JCP/cFolders/TDMT technical data
  (`tdmt.dla.mil`, `pcf1x.bsm.dla.mil/cfolders`), no CAPTCHA solving, and no agreement accepted on
  any site other than the DIBBS DoD banner (owner-approved).

Contents

1. [DIBBS access mechanism (DoD banner)](#1-dibbs-access-mechanism-dod-notice-and-consent-banner)
2. [RFQ dates pages and daily files](#2-rfq-dates-pages-and-daily-files)
3. [RFQ index file `inYYMMDD.txt`](#3-rfq-index-file-inyymmddtxt-fixed-width)
4. [Batch-quote zip `bqYYMMDD.zip` (bq + as files)](#4-batch-quote-zip-bqyymmddzip)
5. [RFQ record page (Package View)](#5-rfq-record-page-package-view)
6. [RFQ solicitation PDF](#6-rfq-solicitation-pdf-sf-18)
7. [Public NSN page](#7-public-rfq-nsn-page)
8. [Awards (search, listings, package view, PDFs)](#8-awards)
9. [CAGE](#9-cage)
10. [SAM.gov Entity API (UNVERIFIED, docs only)](#10-samgov-entity-api-unverified)
11. [USAspending](#11-usaspending)
12. [Fixture index](#fixture-index)

---

## 1. DIBBS access mechanism (DoD Notice and Consent banner)

Two hosts are involved and **each has its own banner and its own cookies**:

| Host | Serves | Banner behaviour without cookie |
|---|---|---|
| `https://www.dibbs.bsm.dla.mil` | all HTML pages | `302 Location: /dodwarning.aspx?goto=/rfq/rfqdates.aspx?category=recent` (target lower-cased) |
| `https://dibbs2.bsm.dla.mil` | all downloads: index/zip files, RFQ PDFs, award PDFs | **`200 text/html`** banner page served *in place* at the file URL (no redirect!). A naive downloader saves a 9 KB HTML page named `in261008.txt`. |

The banner page is an ASP.NET WebForm `<form id="FrmBody" method="post" action="./dodwarning.aspx?goto=...">`
with hidden fields and one submit button `<input type="submit" name="butAgree" value="OK" id="butAgree">`.

* www host hidden fields: `__VIEWSTATEFIELDCOUNT` (=24), `__VIEWSTATE`, `__VIEWSTATE1` … `__VIEWSTATE23`
  (split viewstate), `__VIEWSTATEGENERATOR` (`0B8E87B5`), `__VIEWSTATEENCRYPTED` (empty), `__EVENTVALIDATION`.
* dibbs2 host hidden fields: `__VIEWSTATE` (single), `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`.

**Mechanism A – headless browser (primary, as specified):** Playwright Chromium, context
`user_agent=<project UA>`, `goto(target)`, click `#butAgree`, wait for navigation, then copy
`context.cookies()` into the httpx client. Do it once for `https://www.dibbs.bsm.dla.mil/RFQ/RfqDates.aspx?category=recent`
and once for `https://dibbs2.bsm.dla.mil/dodwarning.aspx?goto=%2f` (use the dodwarning URL on dibbs2
so the post-click navigation is not a file download).

**Mechanism B – plain HTTP form POST (tested, works, recommended for production):**
1. `GET` target (www: follow the 302 once; dibbs2: the 200 body *is* the banner).
2. Parse every `<input type="hidden">` inside `form#FrmBody`, add `butAgree=OK`.
3. `POST` (form-urlencoded) to `urljoin(banner_url, form.action)`.
   * www: response `302 Location: https://www.dibbs.bsm.dla.mil//rfq/rfqdates.aspx?category=recent` (note the double slash).
   * dibbs2: response `200` **and the body is already the requested file** (e.g. the 251 KB index text).
4. Server sets cookie **`dw`** (HttpOnly, Secure, SameSite=Strict, session cookie, host-only).

Cookies observed (all session cookies, `expires=-1`):

| Cookie | Host | Set when | Needed? |
|---|---|---|---|
| `dw` | www and dibbs2 (separate values) | on banner POST | **Yes – `dw` alone is sufficient** (tested: a fresh client with only `dw` per host got 200s on both hosts) |
| `SessionId` | www | first GET | no (ASP.NET session) |
| `di` | www | first GET (value rotates after OK) | no |
| `TS01523db3` / `TS01391f55` | `.www…` / `.dibbs2…` | every response | no (F5 load-balancer cookie) |

Banner detection rule for connectors: response has `Location` containing `dodwarning.aspx`, **or**
`content-type` is HTML and the body contains `butAgree` and `Warning and Consent`. When detected,
re-run the banner POST and retry once. Lifetime of `dw`: session-scoped; expiry time **UNVERIFIED**
(everything in a ~20 minute run worked with one acceptance). Evidence: `dibbs/rfq_dates/banner_access_trace.json`.

No blocking, CAPTCHA or rate-limit responses were seen at 1 req/s (~200 requests total).

---

## 2. RFQ dates pages and daily files

| Page | URL |
|---|---|
| Recent RFQs + daily downloads | `https://www.dibbs.bsm.dla.mil/RFQ/RfqDates.aspx?category=recent` |
| RFQs by issue date | `https://www.dibbs.bsm.dla.mil/Rfq/RfqDates.aspx?category=issue` |
| RFQs by return-by date | `https://www.dibbs.bsm.dla.mil/Rfq/RfqDates.aspx?category=close` |
| RFQs by FSC (search form, POST) | `https://www.dibbs.bsm.dla.mil/Rfq/RfqFsc.aspx` (666 DLA-managed FSCs; results not captured – **UNVERIFIED**) |
| File-format help | `https://www.dibbs.bsm.dla.mil/Rfq/RfqFileDefs.aspx` |
| Batch quote 121-column layout | `https://www.dibbs.bsm.dla.mil/Refs/help/Quoting/Batch/BatchFormat.aspx` |

`category=recent` lists ~22 posting dates (business days + some Sundays, e.g. 10-04-2026) for the last
~30 days. One row per post date with four links:

```
10-08-2026  -> https://www.dibbs.bsm.dla.mil/RFQ/RfqRecs.aspx?category=post&TypeSrch=dt&Value=10-08-2026
ca261008.zip -> https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/ca261008.zip
in261008.txt -> https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/in261008.txt
bq261008.zip -> https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/bq261008.zip
```
plus one undated `https://dibbs2.bsm.dla.mil/Downloads/RFQ/Archive/bqothers.zip` ("all other available RFQs in batch format").

File name pattern: `<prefix><YY><MM><DD>.<ext>`:

| Prefix | Content | Size observed |
|---|---|---|
| `in` | **Index**: fixed-width text, one line per RFQ line item (PR) | 196–438 KB/day (1,416–3,157 lines) |
| `bq` | **Batch quote** zip: `bqYYMMDD.txt` (121-col quoted CSV, one row per CLIN) + `asYYMMDD.txt` (approved sources) | 66–120 KB zipped (0.8–1.5 MB unzipped) |
| `ca` | "Contains All": every RFQ PDF posted that day | **416,538,826 bytes** for 261008 (probed with a streamed GET, body not downloaded) – too big for routine use |

Per the page: "Records are appended to the same file during normal business hours … files for the
current date will be posted one day later". So the newest file is yesterday's; treat a day's file as
final only once it shows up on this page. `HEAD` requests return **404** on dibbs2 (use GET).

`RfqDates.aspx?category=issue|close` link to `RfqRecs.aspx?category=issue|close&TypeSrch=dt&Value=MM-DD-YYYY`.
`RfqRecs.aspx` (fixture `rfqrecs_post_261008_page1.html`) is an ASP.NET GridView, 50 rows/page
("Records Found: 1840" for 10-08-2026), paging/sorting only via `__doPostBack('ctl00$cph1$grdRfqSearch','Page$N')`.
Row element ids `ctl00_cph1_grdRfqSearch_ctlNN_lbl{RowNum,Nsn,Nomenclature,TechnicalDocuments,Solicitation,Status,...}`.
It carries extra flags not in the index (e.g. red `Mil-Spec` tag under the NSN, `*Spec/Stnd Only`,
Fast Award icon) but the index + bq files are the better bulk source.

**Recommended parsing approach:** scrape `rfqdates_recent` for `in*.txt`/`bq*.zip` links (regex
`/Archive/(in|bq)(\d{6})\.(txt|zip)$`); download each date once (idempotent by file name + sha256);
never download `ca*.zip`; fetch individual PDFs instead.

---

## 3. RFQ index file `inYYMMDD.txt` (fixed width)

* Encoding: ASCII/ISO-8859-1 (`Content-Type: text/plain; charset=ISO-8859-1`), **CRLF** line endings.
* **Every record is exactly 140 characters** (11,341 of 11,341 lines in 5 files). No header, no trailer.
* One line per purchase request (PR) line. Multi-line solicitations repeat the solicitation number.

Layout (official field lengths from `RfqFileDefs.aspx`, verified on data; positions are 1-based inclusive,
Python slice in brackets):

| # | Field | Len | Cols | Slice | Notes / observed values |
|---|---|---|---|---|---|
| 1 | Solicitation number | 13 | 1–13 | `[0:13]` | no hyphens, e.g. `SPE4A627T0486` |
| 2 | NSN / part number | 46 | 14–59 | `[13:59]` | left-justified, space padded. 13-digit NSN (11,335 lines); see quirks |
| 3 | Purchase request # | 13 | 60–72 | `[59:72]` | **right-justified**: always 3 spaces + 10 digits |
| 4 | Return-by date | 8 | 73–80 | `[72:80]` | `MM/DD/YY` |
| 5 | File name | 19 | 81–99 | `[80:99]` | always `<solicitation>.pdf` + 2 spaces (lower-case `.pdf`; the real URL uses `.PDF`) |
| 6 | Quantity | 7 | 100–106 | `[99:106]` | zero-padded integer (sum of the PR's CLINs) |
| 7 | Unit of issue | 2 | 107–108 | `[106:108]` | `EA` 88%, `PG`, `FT`, `KT`, `BX`, `AY`, … |
| 8 | Nomenclature | 21 | 109–129 | `[108:129]` | **truncated at 21 chars**, may end mid-word (`STRAP,INVOLUNTARY,R`) |
| 9 | Buyer code | 5 | 130–134 | `[129:134]` | **first 5 chars of the PDF's buyer code** (`DDB02` ↔ PDF `DDB0227`); team codes like `PLCL6` |
| 10 | AMSC | 1 | 135 | `[134]` | `D G C Z R L Q T B P H U S M A N V` |
| 11 | Item type | 1 | 136 | `[135]` | `1` = NSN, `2` = part number |
| 12 | Set-aside | 1 | 137 | `[136]` | `N` unrestricted, `Y` small business, `H` HUBZone, `R` SDVOSB, `L` WOSB, `A` 8(a), `E` EDWOSB |
| 13 | Set-aside % | 3 | 138–140 | `[137:140]` | `000` (with N) or `100` (all others) |

Literal lines (trailing CRLF removed):

```
SPE4A627T04865305011209897                                    701841441110/13/26SPE4A627T0486.pdf  0001977EASCREW,MACHINE        DDB02G1L100
SPE4A627Q00615305010560169                                    701637508010/15/26SPE4A627Q0061.pdf  0003676PGSCREW,CLOSE TOLERANCEDMB02G1Y100
SPE7L127U00375340004290174                                    100024579310/21/26SPE7L127U0037.pdf  0000012EACASTER,SWIVEL        PLCLSB1Y100
SPE1C127T00168465014706253                                    701334723710/19/26SPE1C127T0016.pdf  0000001BGSTRAP,INVOLUNTARY,R  DJB04Z1N000
SPE1C127T00168465014706253                                    701344068610/19/26SPE1C127T0016.pdf  0000003BGSTRAP,INVOLUNTARY,R  DJB04Z1N000
SPEFA327Q00034730LLQJ07784                                    701863467110/18/26SPEFA327Q0003.pdf  0000001EAAE21397R  90 DEG SWIVDMV00L1N000
SPE7L127T01131501555-9155                                     701861889310/15/26SPE7L127T0113.pdf  0000001EANON-NSN TEAM         PLCL6H2N000
```
(the last line is an item-type-2 part number; the next-to-last is a local stock number with item type `1`.)

### Solicitation number anatomy and the 9th character

`SPE4A6-27-T-0486` → `SPE4A6` issuing office DoDAAC (6) + `27` fiscal year + `T` type + `0486` serial
(serial may be alphanumeric: `SPE4A626T36LQ`, `SPE2DS26T482H`). Hyphenated form appears on the record page and PDFs.

| 9th char | Meaning (observed) | bq col 2 "Solicitation Type Indicator" |
|---|---|---|
| `T` | automated simplified acquisition (auto-evaluated; may be fast-award) | `F` (Fast Auto Evaluation – micro-purchase quotes may be awarded before return date) or `P` (Auto Evaluation) |
| `U` | **AIDC** – Automated Indefinite Delivery Contract (one-year IDC, contract max $350,000) | `I` |
| `Q` | manually evaluated RFQ (buyer-evaluated; no procurement history in PDF) | blank |

### Volumes observed (5 files: 261004–261008; `dibbs/rfq_index/index_stats.json`)

* 11,341 lines, 10,047 unique solicitations; lines per solicitation: 1 → 9,577; 2 → 269; up to 50.
* By 9th char — lines: T 9,468 / U 1,497 / Q 376; solicitations: T 8,231 / U 1,497 / Q 319.
* Set-aside lines: N 9,249, Y 1,610, L 227, R 149, H 102, E 4.
* Item type: `1` 11,334, `2` 7.

Target FSCs (first 4 digits of NSN):

| FSC | Lines | Solicitations | Lines by type (T/U/Q) | Set-aside lines (non-N) |
|---|---|---|---|---|
| 5305 Screws | 304 | 265 | 299 / 4 / 1 | 21 |
| 5306 Bolts | 179 | 177 | 174 / 3 / 2 | 24 |
| 5310 Nuts & washers | 275 | 246 | 272 / 3 / 0 | 26 |
| 5330 Packing & gaskets | 384 | 372 | 325 / 49 / 10 | 54 |
| 5340 Hardware | 482 | 474 | 444 / 28 / 10 | 42 |
| 4730 Hose/pipe fittings | 407 | 382 | 398 / 4 / 5 | 41 |
| 6145 Wire & cable | 107 | 58 | 105 / 2 / 0 | 5 |

(Top FSCs overall: 6515 616, 5340 482, 4730 407, 4820 387, 5330 384, 5935 382, 5305 304, 4720 292, 5310 275.)

### Quirks

* Item-type-1 values are not always numeric NSNs: local stock numbers like `4730LLQJ07784`, `3120LN0035720`
  (letters in positions 5–6; 50 lines). Item-type-2 values are free-text part numbers (`1501555-9155`,
  `8120521919WCJ29`) usually with nomenclature `NON-NSN TEAM`.
* Same solicitation can re-appear in a later file (4 cases in 5 days) – e.g. `SPE3SE27T0010` posted 10-04
  with return 10/08/26 and again 10-08 with return 10/14/26 (amendment/extension). Upsert on
  `(solicitation, PR)` and let the newer post date win.
* Post date ≠ issue date: `SPE4A027T0162` is in `in261008.txt` but its record page says issue date 10-09-2026.
* Index quantity is per PR and can be the sum of several CLINs (`SPE4A627Q0061`: index 3676 = PDF CLINs 2,618 + 1,058).
* Return-by date has a 2-digit year.
* Line endings: the live file is CRLF. The repo's `.gitattributes` (`* text=auto eol=lf`) will normalise the
  fixture copies to LF on commit (and invalidate the sha256 values in `manifest.json`) unless
  `backend/tests/fixtures/** -text` is added. Parsers must accept both (`str.splitlines()`), and must not rely on
  the 140-char length including `\r`.

**Recommended parsing approach:** read bytes, decode latin-1, `splitlines()`, skip blank lines,
assert `len == 140`, slice with the table above, `.strip()` each field, `int(qty)`,
`datetime.strptime(ret, "%m/%d/%y")`; NSN = field 2 when item type `1` and `isdigit()`; derive
`fsc = nsn[:4]`, `niin = nsn[4:]`, `sol_type = sol[8]`. Key = `(solicitation, purchase_request)`.
Join with the bq file for line numbers, delivery days, FOB/inspection and type indicator.

---

## 4. Batch-quote zip `bqYYMMDD.zip`

Zip members (observed): `bq261008.txt` (835,328 B) + `as261008.txt` (77,301 B); `bq261007.txt` 1,476,597 B + `as261007.txt` 150,636 B.

### 4a. `bqYYMMDD.txt` — 121 columns, comma-delimited, every value double-quoted, CRLF

One row **per CLIN** (more granular than the index: `SPE4A627Q0061` has 2 rows, `SPE4A027T0162` has 18).
The file is the pre-filled upload template for DIBBS batch quoting, so the RFQ-requirement columns are
populated and the vendor-response columns are blank. 1,868 rows / 1,597 solicitations in bq261008 (index had 1,769 lines).

Populated columns (column numbers are 1-based as in `BatchFormat.aspx`; full 121-column list is in
fixture `dibbs/rfq_dates/batch_quote_format.html`):

| Col | Name | Observed values |
|---|---|---|
| 1 | Solicitation number | `SPE1C127T0014` |
| 2 | Solicitation type indicator | `F` fast auto-eval, `P` auto-eval, `I` AIDC, blank (Q-type) |
| 3 | Small business set-aside indicator | same codes as index |
| 4 | Additional clause fill-ins | `N` |
| 5 | Return-by date | `MM/DD/YYYY` (4-digit year) |
| 24 | Bid type (default) | `BI` |
| 25 | Prompt payment terms (default) | `1` (Net 30) |
| 27 | Days quote valid (default) | `90` |
| 29 | BOA/FSS/BPA | `NAP` |
| 32 | **FOB point required** | `O` origin (1,653) / `D` destination (215) |
| 36 | **Inspection point required** | `D` destination (1,463) / `O` origin (405) |
| 39 | (undocumented / reserved) | `N` |
| 44 | Solicitation line (CLIN) number | `0001` … |
| 46 | Purchase request number | `7018473486` |
| 47 | NSN / part number | `8305011311769` |
| 48 | Unit of issue | `EA` |
| 49 | Quantity | `3` (per CLIN, not zero-padded) |
| 51 | **Delivery days (required, ADO)** | `20`, `5`, `168`, `171` … |
| 52–55 | AIDC: guaranteed minimum, DO minimum, contract maximum, annual frequency of buys | only for type `I`: e.g. `1`, `1`, `350000`, `2` |
| 57 | HUBZone price-evaluation preference applies | `Y`/`N` |
| 62 | Trade Agreements indicator | `N`/`Y`/`I` |
| 68 | Buy American indicator | `N`/`Y`/`I` |
| 69 | Free Trade Agreements indicator | `N`/`Y`/`I`/`B` |
| 73 | Duty free entry requested | `N` |
| 77 | Price breaks solicited | `N`/`Y` (cols 78–95 then hold pre-set quantity ranges) |
| 96/97 | Quantity variance +/− | `0` |
| 98, 100 | Min-order qty code, immediate shipment | `N` |
| 105 | **Item description indicator** | `P` approved-source item (1,254), `D` spec/std/drawing (452), `S` (60, *not in the published code list*), `Q` QPL (53), `B` source controlled (49); `N` non-NSN documented |
| 117 | **Higher-level quality required** | `N`, `6` AS9003/ISO 9001 tailored, `7` ISO 9001:2015, `8` AS9100 |
| 120 | Child labor certification default | `N` |

Literal row:

```
"SPE1C127T0014","F","N","N","10/19/2026","","","","","","","","","","","","","","","","","","","BI","1","","90","","NAP","","","D","","","","D","","","N","","","","","0001","","7018473486","8305011311769","EA","3","","5","","","","","","Y","","","","","N","","","","","","N","N","","","","N","","","","N","","","","","","","","","","","","","","","","","","","0","0","N","","N","","","","","D","","","","","","","","","","","","N","","","N",""
```

### 4b. `asYYMMDD.txt` — approved sources

4 quoted columns: `NSN, approved-source CAGE, part number, company name`:
```
"1005012209398","62228","014496-1",""
"1005014851397","3S679","46032",""
```
Observed: the company-name column was **empty in all 1,888 rows** of as261008 (the help page shows it populated).
Company names are available from the RFQ PDF / NSN page / DIBBS CAGE page instead.

**Recommended parsing approach:** `zipfile` → `csv.reader(io.TextIOWrapper(..., encoding="latin-1"))`,
assert 121 columns, map by column number; key `(sol, CLIN col 44)`; take required delivery days,
FOB, inspection, type indicator, AIDC terms, HLQ and item-description indicator from here.
`as` file → `approved_sources(nsn, cage, part_number)`.

---

## 5. RFQ record page (Package View)

URL: `https://www.dibbs.bsm.dla.mil/rfq/rfqrec.aspx?sn=<SOLICITATION_NO_HYPHENS>` (any case; ~50–73 KB).
"Printer Friendly Version" link exists but was not captured.

Structure (`div#ctl00_cph1_pnlRfqRec`):

* Header table (`summary='Table contains RFQ Header data'`), spans:
  * `#ctl00_cph1_lblSolicitation` – `<strong>SPE4A0-27-T-0162</strong>` followed by **flag icons (`img@alt`)**:
    `Fast Award Candidate.  Micro-purchase quotes may be awarded prior to the solicitation return date. …`,
    `Automated Indefinite Delivery Contract (AIDC)`, set-aside icon (`Unrestricted/Not Set Aside`,
    `Small Business Set-Aside`, `Woman Owned Small Business (WOSB) Set-Aside`, …), `DLA E-Procurement`.
  * `#ctl00_cph1_lblRfqStatus` – `Open` (+ a "Quote" button linking to `/RA/Quote/QuoteFrm.aspx?sn=…`, login
    required – do not follow; its text renders as `uote`), `Awarded`, or empty (other closed states; **UNVERIFIED** values like Cancelled).
  * `#ctl00_cph1_lblIssue` – `10-09-2026`; `#ctl00_cph1_lblReturnBy` – `10-16-2026` (MM-DD-YYYY).
* `div#RfqDocsDisplay` – `Solicitation: <a href='https://dibbs2.bsm.dla.mil/Downloads/RFQ/2/SPE4A027T0162.PDF'>` and an
  attachments table (`No Attachments` in all 10 samples).
* Item table (`summary='Table contains RFQ PR data'`), columns `# | NSN/Part No. | Nomenclature | Technical Documents | Purchase Request QTY`:
  * NSN cell links to `https://www.dibbs.bsm.dla.mil/RFQ/RFQNsn.aspx?value=5310015432453&category=&Scope=` (hyphenated text `5310-01-543-2453`;
    part-number style `4730-LL-QJ0-7784`). Once awarded, the NSN is plain text (no link).
  * Technical Documents: `None` or `Tech Docs` → `https://tdmt.dla.mil/tdmt/Client/?StartItem=DSP_TDP_Acquisition_Doc%3AbySolicitation%3A<SOL>`
    (**do not fetch** – TDMT/cFolders). Presence of the link is a useful "has tech data" flag.
  * PR/qty cell: `7018621279 <br />Qty: 8`; first-article / testing CLINs show pseudo-NSNs
    `0001S00000052` (`GOVERNMENT FIRST ARTIC`), `0001S00000061` (`PRODUCTION LOT TESTING`) with `Qty - See Solicitation`.
  * **Awarded RFQs** get an extra row:
    `Awarded  Awardee: 1V630  Contract /Delv. Order: SPE7M027P0185  Date: 10-09-2026  Total value: $240.58`
    (CAGE links to `/Refs/cage.aspx?Cage=1V630`, contract links to `/Awards/AwdRec.aspx?contract=SPE7M027P0185&dlv=`).
    This is the cheapest way to label outcomes for our own solicitations (seen on `SPE7M027T0003`, a fast award made 7 days after issue).

**RFQ PDF URL pattern** (verified on 12 solicitations incl. alphanumeric serials):
`https://dibbs2.bsm.dla.mil/Downloads/RFQ/<last character of solicitation>/<SOLICITATION>.PDF`
e.g. `.../RFQ/2/SPE4A027T0162.PDF`, `.../RFQ/Q/SPE4A626T36LQ.PDF`, `.../RFQ/H/SPE2DS26T482H.PDF`.
The PDF can therefore be fetched straight from the index line without loading the record page.

There is **no public procurement-history page** on DIBBS for RFQs; history is only inside the RFQ PDF
(section 6) and, for awards, via the award search (section 8).

**Recommended parsing approach:** BeautifulSoup/lxml by element id; flags from `img[alt]` inside
`#ctl00_cph1_lblSolicitation`; item rows from the PR table (`td[headers=xrh0..xrh4]`); detect the
awarded row by `strong` text `Awarded` and parse with regex
`Awardee:\s*(\w{5}).*?Order:\s*(\w+).*?Date:\s*([\d-]+).*?Total value:\s*\$([\d,.]+)`.

---

## 6. RFQ solicitation PDF (SF-18)

10 samples (`dibbs/rfq_pdf/*.pdf` + `.txt`). 18–63 pages, 113–718 KB. Text extracted with
`pdfplumber.Page.extract_text()` (default settings), pages joined with `<<<PAGE n END>>>` markers.
pdfminer emits harmless `CropBox missing from /Page` warnings. All PDFs had a text layer (no OCR needed).

Two dialects:

* **Automated (T and U types)** – page 1 header `1 / PAGE OF PAGES / REQUEST FOR QUOTATIONS … / 1 63`.
* **Manual (Q type)** – page 1 header `REQUEST FOR QUOTATIONS THIS RFQ IS IS NOT A SMALL BUSINESS SET-ASIDE PAGE 1 OF 26`,
  block 11 contains the quoter fill-in form (catalog price, FOB point, best delivery…).

Continuation pages start with
`CONTINUATION SHEET REFERENCE NO. OF DOCUMENT BEING CONTINUED: PAGE 6 OF 63 PAGES` then the hyphenated solicitation number,
then `SECTION A|B|…`; page breaks inside a CLIN repeat `PR: 7018621323 PRLI: 0001 CONT’D` (automated) or
`SUPPLY/SERVICE: 5305-01-056-0169 CONT'D` (manual) and end with `CONTINUED ON NEXT PAGE`. Note the curly apostrophe in `CONT’D`.

Field landmarks (regexes are against the extracted text):

| Field | Where / how it looks | Example |
|---|---|---|
| Solicitation no., issue date, PR | page 1 line after `1. REQUEST NO. 2. DATE ISSUED 3. REQUISITION/PURCHASE REQUEST NO.`: `^(SP\w{4}-\d\d-[TUQ]-\w{4}) (\d{4} [A-Z]{3} \d\d) (\S+)` | `SPE4A6-27-T-0486 2026 OCT 06 7018414411 UNDER BDSA REG. 2 DO-C9` (multi-PR RFQs say `See Schedule`; trailing `DO-C9` = DPAS rating) |
| Return-by date | page 1, line after `be completed by the quoter.` (T/U) / `be completed by the quoter.` then date (Q): `^\d{4} [A-Z]{3} \d\d$` | `2026 OCT 16` |
| Required delivery (header) | page 1 line under `6. DELIVER BY (Date)`: `(\d+) DAYS ADO` | `DLA WEAPONS SUPPORT 154 DAYS ADO` |
| Issuing office | lines after `5. ISSUED BY` | `DLA WEAPONS SUPPORT` / `ASC COMMODITIES DIVISION` / `6090 STRATHMORE ROAD` / `RICHMOND VA 23237` |
| **Buyer name/code/phone** | T/U: `^Name: (.+?) Buyer Code:(\w+) Tel: (.+?)( Fax: .+)?$`; Q: `^Buyer: (.+) (\w{3}\d{4}) Tel: (.+)$` | `Name: Danita Davis Buyer Code:GPD0022 Tel: DSN(392)-737-2101`; `Name: Dov Sneward Buyer Code:PLCLSED Tel: 614-692-1455 Fax: 614-693-1566`; `Buyer: MICHAEL BLAIN DMB0289 Tel: (804) 807-4436` |
| **Buyer email** | `^Email: (\S+@\S+) a. NAME OF CONSIGNEE` | `Email: Danita.Davis@dla.mil a. NAME OF CONSIGNEE` (can be a team mailbox: `DLA.Land.Postaward.FLSEE@dla.mil`) |
| **NAICS** | Section A: `NORTH AMERICAN INDUSTRY CLASSIFICATION SYSTEM (\d{6})` (all T/U samples and Q0024: `… SYSTEM 332510 applies to this solicitation.`); fallback: clause 52.204-8 fill-in `NAICS) code for this acquisition is (\d{6})` (filled only in Q0058; T/U show `________`). Q0061 and QFA3 carry **no NAICS** – fall back to an FSC→NAICS map | `NORTH AMERICAN INDUSTRY CLASSIFICATION SYSTEM 332722` |
| **Set-aside** | **NOT recoverable from page 1 text**: `THIS RFQ IS IS NOT A SMALL BUSINESS SET-ASIDE` (checkbox marks are not in the text layer; both checkboxes are unfilled rects). Section A may contain `FAR 52.219-6 Notice of Total Small Business Set-Aside` or `FAR 52.219-30 Notice of Set-aside for … Women-Owned Small Business` (T/U only). Use the index/bq set-aside code instead. | |
| Fast award / AIDC | Section A: `THIS BUY MAY BE A CANDIDATE FOR AUTOMATED AWARD`, `MICRO-PURCHASE QUOTES MAY BE AWARDED PRIOR TO RETURN DATE.`; AIDC: `This solicitation may result in an Indefinite Delivery Contract (IDC)` | |
| **AIDC terms** (U) | Section A lines: `Guaranteed Contract Minimum Quantity: 1 EA`, `Contract Maximum Value: $350000.00`, `Estimated Annual Quantity: 12.000 EA`, `Estimated Number of orders: 1`, `Minimum Delivery Order Quantity: 6 EA` | SPE7L127U0037 |
| **Inspection point** | per CLIN: `INSPECTION POINT: (ORIGIN|DESTINATION)` (+ `ACCEPTANCE POINT: …`); Section A also states e.g. `DESTINATION INSPECTION REQUIRED - FAR 52.246-2 APPLIES.`; `RQ009: INSPECTION AND ACCEPTANCE AT ORIGIN` when origin | `INSPECTION POINT: DESTINATION` |
| **FOB** | per CLIN – T/U: `DELIVER FOB: (ORIGIN|DESTINATION)`; Q: `FOB: ORIGIN DELIVERY DATE: 120 DAYS ADO`. Page 1 always says `FOB DESTINATION (See Schedule)` – **ignore it** (FDT program: CLINs are mostly FOB ORIGIN with government-arranged transport, text `First Destination Transportation (FDT) program`) | |
| **Delivery days per CLIN** | T/U: `DELIVERY (IN DAYS):(\d{3,4})` (zero padded `0005`); Q: `DELIVERY DATE: (\d+) DAYS ADO` | |
| **CLIN line (quantity/UI)** | T/U header `CLIN PR PRLI UI QUANTITY UNIT PRICE TOTAL PRICE` then `^(\d{4}) (\d{10}) (\d{4}) ([A-Z]{2}) ([\d,]+\.\d{3}) \.$`, next lines `NSN/MATERIAL:(\w+)`; Q header `ITEM NO. SUPPLIES/SERVICES QUANTITY UNIT UNIT PRICE AMOUNT` then `^(\d{4}) ([\w-]+(?: - \w+)?) ([\d,]+\.\d{3}) ([A-Z]{2}) \$ _+ \$ _+ \.$` | `0001 7018621323 0001 EA 8.000 .`; `0001 5305-01-056-0169 2,618.000 PG $ ________________ $ ________________ .`; FAT CLIN `0002 0001 - S00000052 1.000 EA $ … .` |
| Item description | Section B after `ITEM DESCRIPTION` (T/U) / `ITEM DESCRIPTION:` (Q) – nomenclature repeated, then requirement codes `RA001`, `RP001`, `RQ011`, `RD003`, `RQ001: TAILORED HIGHER LEVEL CONTRACT QUALITY REQUIREMENTS`, `CRITICAL APPLICATION ITEM`, `THIS ITEM IS IDENTIFIED AS A COMMERCIAL OFF THE SHELF (COTS) ITEM`, `QUALIFIED PRODUCTS LIST ITEM …`, `THIS IS A SOURCE CONTROLLED ITEM …` | |
| **Approved sources** | Section B lines `^(.+?) ([0-9A-Z]{5}) P/N (\S.*)$` (company, CAGE, part no.), may be several | `MCMASTER-CARR SUPPLY COMPANY 2V507 P/N 90973A114`; `ALBION INDUSTRIES, INC. 96266 P/N 16-PD-08201-S-NSL` |
| Spec / drawing refs | `IAW BASIC DRAWING NR 10001 64A127D16`, `IAW BASIC NON GOVT STD NAS1580`, `IAW REFERENCE QAP 13873 QAP-EQ003`, `REVISION NR 16 DTD 04/30/2024`, `PART PIECE NUMBER: NAS1580A3T3` | |
| **Packaging** | per CLIN after `PREP FOR DELIVERY:`: either `PKGING DATA-QUP:001` + `WILL BE PACKAGED IN ACCORDANCE WITH ASTM D3951.` (commercial) or `PKGING DATA - MIL-STD-2073-1E` + `QUP:001 PRES MTHD:31 CLNG/DRY:1 PRESV MAT:00` / `WRAP MAT:00 CUSH/DUNN MAT:00 CUSH/DUNN THKNESS:0` / `UNIT CONT:BL OPI:M` / `INTRMDTE CONT:ED INTRMDTE CONT QTY:AAA` / `PACK CODE:U` (military); `MARKING SHALL BE IN ACCORDANCE WITH MIL-STD-129.` | |
| Qty variance | `QTY VARIANCE: PLUS 0% MINUS 0%` | |
| **First article** | `FIRST ARTICLE TESTING IS REQUIRED`; FAT CLIN `0001S00000052` (Government FAT) / `0001S00000061` (production lot testing); `PGN-09-05 Waiver – First Article Test`, waiver-eligible CAGEs: `eligible for waiver of first article testing requirements: CAGE 84256, CAGE 78286` | SPE4A627Q0058 |
| AMSC | **not printed** in the RFQ PDF text (text `AMSCBDOC` is part of a drawing number). Use the index (col 135) or the NSN page. | |
| Destinations | per CLIN `PARCEL POST ADDRESS:` / `FREIGHT SHIPPING ADDRESS:` / `MARKFOR` blocks with DoDAAC (`W91GUK`), plus `Original Required Delivery Date:10/01/2026` | |
| **Procurement history** | T/U only (Section A, just before the FDT paragraph). Q-type PDFs have none. Either `No History Available` or the table below | |

**Procurement history table** (T/U):

```
Procurement History for NSN/FSC:011209897/5305
CAGE Contract Number Quantity Unit Cost AWD Date Surplus Material
.
6M132 SPE4A626F470G 643.000 13.80000 20260917 N
6M132 SPE4A626F341FP00001 852.000 13.80000 20260817 N
0Q2H8 SPE4A626P8189 1369.000 7.00000 20251203 N
0FA68 SPE5E216M0161 3335.000 4.45000 20151027 N
```
* Header: `Procurement History for NSN/FSC:<NIIN 9>/<FSC 4>`; columns: awardee CAGE (5), contract number,
  quantity (3 decimals), **unit cost** (5 decimals), award date `YYYYMMDD`, surplus flag `N`/`Y`.
* Row regex: `^([0-9A-Z]{5}) (\S+) ([\d,]+\.\d{3}) ([\d,]+\.\d{5}) (\d{8}) ([YN])$`.
* Contract number can carry a delivery order and/or a modification suffix concatenated:
  `SPE4A626F341FP00001` (= order + mod `P00001`), `SPM50003D00570013` (= `SPM500-03-D-0057` + order `0013`).
* Rows are newest first, go back 20+ years (2001 in SPE7L127U0037). Up to ~20 rows observed.
* **Page breaks**: the header block (`Procurement History…`, column header, `.`) is **repeated** on the next page
  (SPE7L127U0037, SPE7M127U0029 have two blocks for one NSN) – concatenate blocks with the same NSN.
* Quirk: the line after the column header is a lone `.`.
* Multiple NSNs per PDF: all 10 samples had a single NSN (SPE4A027T0162 has 18 CLINs of the same NSN); multi-NSN
  solicitations exist in the index (up to 50 lines/solicitation) – expect one history block per NSN (**UNVERIFIED**).

**Recommended parsing approach:** pdfplumber text per page → strip the continuation header lines
(`CONTINUATION SHEET…`, solicitation number, `SECTION X`, `CONTINUED ON NEXT PAGE`, `* * *` rule) →
line-oriented regex state machine: (1) header fields from page 1, (2) Section A blocks (NAICS, AIDC terms,
history tables), (3) Section B split on CLIN header lines, collecting per-CLIN attributes until the next CLIN.
Do not trust page-1 FOB/set-aside text. Keep the raw text for re-parsing.

---

## 7. Public RFQ NSN page

URL: `https://www.dibbs.bsm.dla.mil/RFQ/RFQNsn.aspx?value=<NSN13 or part no>&category=&Scope=`
(linked from record and listing pages; ~45–75 KB). No element ids on the data; use the table `summary` attributes.

Content:
* `NSN: 5310-01-543-2453`, `Nomenclature: NUT,PLAIN,PLATE`, **`AMSC: Z`**.
* Optional "Specification, Drawing and Standard Data" (`I/A/W DRW: 1X4F3 AMSCBDOC (BASIC) DTD: 06-18-2026`, amend no., dates).
* **Approved Source Data** table (`summary="Table conatins Approved Source Data"` – sic): `CAGE | Part Number | Company Name`, e.g. `2V507 | 90973A114 | MCMASTER-CARR SUPPLY COMPANY`.
* Open solicitations table (`summary="Contains RFQ records for the NSN. "`): `Solicitation # | Technical Documents | PR # | QTY | Issue | Return By`.
* **No procurement history / past awards** on this page.

**Recommended parsing approach:** fetch only when the NSN is new or approved sources are needed;
parse the two tables by `summary`; AMSC by the `AMSC:` label.

---

## 8. Awards

### 8a. Entry points

| Page | URL |
|---|---|
| Award search form | `https://www.dibbs.bsm.dla.mil/Awards/` |
| Award dates by post date | `https://www.dibbs.bsm.dla.mil/Awards/AwdDates.aspx?category=post` |
| Award dates by award date | `https://www.dibbs.bsm.dla.mil/Awards/AwdDates.aspx?category=awddt` |
| Awards for one date | `https://www.dibbs.bsm.dla.mil/Awards/AwdRecs.aspx?Category=post|awddt&TypeSrch=cq&Value=MM-DD-YYYY` |
| **Search by GET** (no form needed) | `https://www.dibbs.bsm.dla.mil/Awards/AwdRecs.aspx?Category=<cat>&TypeSrch=cq&Value=<v1,v2,…>&Scope=<scope>` |
| Award package view | `https://www.dibbs.bsm.dla.mil/Awards/AwdRec.aspx?contract=<BASIC>&dlv=<ORDER or empty>&cnt=` |
| Award PDF | `https://dibbs2.bsm.dla.mil/Downloads/Awards/<DDMONYY of posting>/<BASIC>[<ORDER>].PDF` |

* `AwdDates` pages list ~45 days (08-25-2026 … 10-09-2026), i.e. the date pages only cover the last ~6 weeks.
* Search categories (`Category=`): `cage`, `contract`, `do`, `docnt`, `sol`, `pr`, **`nsn`**, `nomenclature`.
  Scopes (`Scope=`): `todays`, `recent` (past 15 days: "Recent Awards/Modifications since 09-22-2026"), `all`,
  `dtrange`/`dtrangeawd` (need form start/end dates; GET form **UNVERIFIED**). `Value` accepts a
  **comma-separated list** (we used 40 NSNs per request; the help says "MIN Search length: 5"). NSNs may be given with or without hyphens.
* The search form itself is a **cross-page postback**: POST all hidden fields + `ctl00$cph1$ddlCategory`,
  `ctl00$cph1$txtValue`, `ctl00$cph1$rblScope`, `ctl00$cph1$ddlSort`, `ctl00$cph1$butSubmit=Submit` to
  `/Awards/AwdRecs.aspx` (posting to `/Awards/` just re-renders the form). The GET form above is simpler and equivalent.
* No downloadable daily award files exist (the Downloads page only has RFQ files and SRVA FSC files).
* Retention: `Scope=all` NSN searches returned awards back to 2012/2017 (large IDCs) but "No Awards are on file"
  for several active NSNs (e.g. `5340014183431`, `5310015432453`) – DIBBS only shows awards within its document retention
  policy. **The RFQ PDF procurement history is the deeper history source.**

### 8b. Award listing (`AwdRecs.aspx`) — GridView `table#ctl00_cph1_grdAwardSearch`

`Records Found: 4069` for post date 10-08-2026; 50 rows/page. **Paging works without a browser**:
POST the page's hidden fields + `__EVENTTARGET=ctl00$cph1$grdAwardSearch`, `__EVENTARGUMENT=Page$2`
back to the same URL (fixture `awdrecs_post_10-08-2026_page2.html`, rows 51–100).

Columns (span ids `ctl00_cph1_grdAwardSearch_ctlNN_<id>`):

| Column | Span id | Example | Notes |
|---|---|---|---|
| # | `lblRowNum` | `1` | |
| Award/Basic Number | `lblAwardBasicNumber` | `SPE4A527P0501` | links basic PDF + `Award/Basic Package View` when no order |
| Delivery Order Number | `lblDeliveryOrder` | `SPE4A727F0066` | links order PDF `…/08OCT26/N0038323GAN01SPE4A727F0066.PDF` + `Delivery Order Package View` |
| Delivery Order Counter | `lblDeliveryOrderCounter` | `18`, `161033` | blank for non-orders |
| Last Mod Posting Date | `lblLastModPostingDate` | `10-08-2026` | blank if no mods |
| **Awardee CAGE** | `lblCage` | `U1604` | `onclick` opens `/Refs/cage.aspx?Cage=U1604` |
| **Total Contract Price** | `lblTotalContactPrice` (sic) | `$3,988.15` | leading spaces; can be `See Award Doc`; IDC basics show the max (`$350,000.00`, `$999,999,999.00`) |
| **Award Date** | `lblAwardDate` | `10-08-2026` | |
| Posted Date | `lblPostedDate` | `10-08-2026` | |
| NSN/Part Number | `lblNsn` | `1680992132229` | services use pseudo-NSNs `S206V00014217` |
| Nomenclature | `lblNomenclature` | `LANYARD ASSEMBLY, BE` | truncated |
| Purchase Request | `lblPurchaseRequest` | `7015537658` | can be blank |
| **Solicitation** | `lblSolicitation` | `SPE4A726T9419` | plain text (no link); blank for some |

* One row per award × line item (× PR); a contract with N lines appears N times (and twice when it has a mod) – dedupe by
  `(basic, order, PR, NSN)`.
* PIID 9th character seen in listings: `P` purchase order (manual or fast award), `V` automated PO, `F` delivery order, `D` IDC/AIDC basic, `C` contract, `G` BOA; older history rows also show `M`.

Literal row text (pipe-joined cells):
```
2 | SPE4A527P0501 | Award/Basic Package View | 22819 | $13,298.75 | 10-08-2026 | 10-08-2026 | 5330010735775 | SEAL, PLAIN ENCASED | 7018414496 | SPE4A526T473X
6 | SPE4A626D61KA | SPE4A626F459W | Delivery Order Package View | 3 | 09-17-2026 | 5UY76 | See Award Doc | 09-17-2026 | 09-17-2026 | 5306008391901 | BOLT, SHEAR | 7018389495
```

### 8c. Award package view (`AwdRec.aspx`)

Spans: `ctl00_cph1_lblAwardBasic`, `lblDeliveryOrder`, `lblDeliveryOrderCounter`, `lblAwarded` (award date),
`lblPrice` (`$1,792.70`), `lblOffices` (`SPE4A2 / SPE4A1 / SL4701` = issued by / admin by / payment),
`lblPosted`, `lblCageHyperlink` (`2N935`), `lblAwardee` (`BOEING DISTRIBUTION SERVICES X, INC`), `lblBasicDoc`
(PDF link), `lblModsNone` (`None`) / mod links, `lblRecs` (table `# | NSN/Part Number | Nomenclature | Purchase Request | Solicitation`).
This page gives the **awardee company name** without needing cage.dla.mil.

### 8d. Award PDFs (DD Form 1155 "ORDER FOR SUPPLIES OR SERVICES")

5 samples (`dibbs/award_pdf/`). Folder = **posting** date as `DDMONYY` (`08OCT26`, `30OCT25`); delivery-order file name =
basic + order concatenated. All had a text layer.

Page-1 landmarks:
* line after `1. CONTRACT/PURCH ORDER/AGREEMENT NO. 2. DELIVERY ORDER/CALL NO. 3. DATE OF ORDER/CALL 4. REQUISITION/PURCH REQUEST NO. 5. PRIORITY` + `(YYYYMMMDD)`:
  `SPE4A5-27-V-0074 2026 OCT 08 7013505573 DO-C9` or with an order `SPE4AX-20-D-9412 SPE4A2-26-F-1602 2026 JUN 11 7015475299 DO-C9`.
* `9. CONTRACTOR CODE 1V630 FACILITY …`, company name 2 lines later (`F & R SALES, INC.`). Small-business box shows as `X SMALL`.
* `12. DISCOUNT TERMS` value on the following lines (`Net 30 days`, `10 days .5% , net 30`).
* `Local Admin: Nina Gonter Tel: 6146922208 … Email: NINA.GONTER@DLA.MIL` (post-award admin, not the RFQ buyer).
* `25. TOTAL $240.58`; `Reference your Offer/Quote dated 2026 OCT 06` (quote date – useful for fast-award latency).
* Quirk: address blocks in boxes 6/7 are garbled by overlapping text (`P C O O L B U O M X B 3 U 9 S 9 0 O H 43218-3990`).

**Line-level quantity and unit price ARE recoverable** — three schedule formats:

| Award type | Header line | Line regex / example |
|---|---|---|
| Fast-award / manual PO (`P`) | `ITEM NO. SUPPLIES/SERVICES QUANTITY UNIT UNIT PRICE AMOUNT` | `0001 6145-01-224-9084 493.000 FT $ 0.48800 $ 240.58 .`; `0001 5330-01-723-4104 46.000 EA $ 9.10 $ 418.60 .` → `^(\d{4}) ([\d-]{16}\|\S+) ([\d,]+\.\d{3}) ([A-Z]{2}) \$ ([\d,.]+) \$ ([\d,.]+) \.$` |
| Automated PO (`V`) and AIDC basic (`D`) | `CLIN PR PRLI UI QUANTITY UNIT PRICE CURRENCY TOTAL PRICE` | `0001 7013505573 0001 EA 24.000 180.00000 USD 4320.00 .` then `NSN/MATERIAL:5305011635794` → `^(\d{4}) (\d{10}) (\d{4}) ([A-Z]{2}) ([\d,]+\.\d{3}) ([\d,]+\.\d+) USD ([\d,.]+) \.$`. AIDC basic: quantity `0.000`, unit price is the IDC price, total = contract max (`0001 1000213415 0001 EA 0.000 16.76000 USD 350000.00 .`) |
| Delivery order under IDC (`F`) | `CLIN BASIC QUANTITY UI UNIT PRICE DOLLAR VALUE DELIVERY` / `REF DATE` / `CLIN` | `0001 0228 455.000 EA 3.9400 1792.70 2026 SEP 09 .` (basic CLIN ref, qty, UI, unit price, value, delivery date); NSN appears earlier as `MFR. CAGE: 06848 P/N: 2651545` / nomenclature – NSN itself is in the package view |

Also per CLIN: `INSPECTION POINT:`, `ACCEPTANCE POINT:`, `FOB: ORIGIN DELIVERY DATE: 2027 MAR 08` or `DELIVERY (IN DAYS):190`,
`PRICING TERMS: Firm Fixed Price`, approved-source lines `FORD MOTOR CO 3LQV7 P/N FL3Z-8590-A`, and a trailing
`GOVT USE` table `0001 7018564183 0001 N/A N/A N/A N/A .` (CLIN, PR, PRLI).

**Recommended parsing approach:** discover awards via the GET search (`Category=nsn`, batches of ≤40 NSNs,
`Scope=recent` daily, `Scope=all` for backfill) and via `AwdRecs?Category=post&Value=<date>` with postback paging;
store rows; fetch the PDF only when line-level unit price is needed; parse the schedule with the three regexes
above, falling back to `Total Contract Price / quantity` for single-line awards. Treat `See Award Doc` as unknown.

---

## 9. CAGE

* **DIBBS CAGE pop-up (public, behind the DIBBS banner only):**
  `https://www.dibbs.bsm.dla.mil/Refs/cage.aspx?Cage=<CAGE>` → "SAM CAGE Information" with ids
  `lblCageCode`, `lblVendorName`, `lblDoingBusinessAs`, `lblVendorStreetAddress`, `lblVendorStreetAddress1`,
  `lblVendorCity`, `lblVendorState`, `lblVendorCountry`, `lblVendorZip`, `lblCCRAddedDate` (shown as "SAM Expiration", e.g. `2027-09-15`).
  Example: `2N935 | BOEING DISTRIBUTION SERVICES X, INC | 3760 W 108TH ST | HIALEAH | FL | US | 33018-1261 | 2027-09-15`.
  No UEI, no business size / socio-economic flags.
* **cage.dla.mil:** every page (`/`, `/Search/Results?q=<CAGE>&page=1`, `/Search/Details?id=<CAGE>`) returns
  `302 Location: /Home/UsageAgree`, a Terms-and-Conditions page with an anti-forgery token and an **"I Agree"** POST button.
  Per project rules we did **not** accept it, so nothing on cage.dla.mil is reachable. Search result/detail formats are **UNVERIFIED**.
  Saved: `cage/cage_dla_mil_usage_agree.html`, `cage/cage_dla_mil_probe.json`.

**Recommended parsing approach:** use the DIBBS CAGE pop-up (+ award package view awardee name) for name/address;
use SAM.gov Entity API (needs key) or USAspending recipient data for UEI and small-business categories.

---

## 10. SAM.gov Entity API (UNVERIFIED)

No API key was available, so **no request was made and no response fixture exists**. From the public docs
(https://open.gsa.gov/api/entity-api/):

* `GET https://api.sam.gov/entity-information/v3/entities?api_key=<KEY>&cageCode=<CAGE>` (also `ueiSAM=`,
  `legalBusinessName=`, `registrationStatus=A`, `includeSections=entityRegistration,coreData,assertions,repsAndCerts,pointsOfContact`,
  `page`, `size` ≤ 10). Older `v2`/`v1` paths also exist. Public (non-federal) keys see public data only; rate limits are per key/day.
* Response shape: `{"totalRecords": n, "entityData": [ { "entityRegistration": {"samRegistered", "ueiSAM", "cageCode",
  "legalBusinessName", "dbaName", "registrationStatus", "registrationExpirationDate", "purposeOfRegistrationDesc", …},
  "coreData": {"physicalAddress": {...}, "businessTypes": {"businessTypeList": [...], "sbaBusinessTypeList": [...]},
  "entityInformation": {...}}, "assertions": {"goodsAndServices": {"primaryNaics", "naicsList": [...], "pscList": [...]}},
  "pointsOfContact": {...} } ], "links": {...} }`.
* Exact field names/sections must be verified once a key exists.

---

## 11. USAspending

No key needed. `POST https://api.usaspending.gov/api/v2/search/spending_by_award/` (JSON), then
`GET https://api.usaspending.gov/api/v2/awards/<generated_internal_id>/`.

Request used (fixture `usaspending/spending_by_award_dla_psc.json` stores request + response):
```json
{"filters": {"agencies": [{"type": "awarding", "tier": "subtier", "name": "Defense Logistics Agency"}],
             "psc_codes": ["5305","5306","5310","5330","5340","4730","6145"],
             "award_type_codes": ["A","B","C","D"],
             "time_period": [{"start_date": "2025-10-09", "end_date": "2026-10-09"}]},
 "fields": ["Award ID","Recipient Name","Recipient UEI","recipient_id","Award Amount","Total Outlays","Description",
            "Start Date","End Date","Awarding Agency","Awarding Sub Agency","Funding Sub Agency","Contract Award Type",
            "NAICS","PSC","Place of Performance State Code","Last Modified Date","generated_internal_id"],
 "limit": 25, "page": 1, "sort": "Start Date", "order": "desc", "subawards": false}
```
Keyword search by PIID works: `"filters": {"keywords": ["SPE4A626PL037","SPE4A626PU582","SPE4A626PX272"], …}` → 3 results.

Result row (`results[]`): `internal_id`, `Award ID` (PIID without hyphens, e.g. `SPE4A626PX272`), `Recipient Name`,
`Recipient UEI`, `recipient_id`, `Award Amount` (float), `Start Date`, `End Date`, `Awarding Sub Agency`
(`Defense Logistics Agency`), `Funding Sub Agency`, `Contract Award Type` (`PURCHASE ORDER`, `DELIVERY ORDER`, …),
`NAICS {code, description}`, `PSC {code, description}`, `Description` (DLA format `8512226378!NUT,PLAIN,HEXAGON` –
an internal number + `!` + nomenclature), `generated_internal_id` (`CONT_AWD_SPE4A626PX272_9700_-NONE-_-NONE-`).
Paging: `page_metadata {page, hasNext, last_record_unique_id, last_record_sort_value}`. Every response carries a
`messages` notice about the 2007-10-01 earliest date.

Award detail (`usaspending/award_detail.json`) adds: `piid`, `type_description`, `total_obligation`, `date_signed`,
`parent_award` (IDV piid for orders), `recipient {recipient_name, recipient_uei, parent_recipient_*, business_categories
[ "Small Business", "HUBZone Firm", …], location}`, `period_of_performance`, `psc_hierarchy`, `naics_hierarchy`, and
`latest_transaction_contract_data` with **`solicitation_identifier`** (`SPE4A626T82N5` – joins back to the DIBBS RFQ),
**`number_of_offers_received`** (`"3"`), `extent_competed_description` (`COMPETED UNDER SAP`), `type_set_aside_description`,
`solicitation_procedures_description` (`SIMPLIFIED ACQUISITION`), `type_of_contract_pricing_description` (`FIRM FIXED PRICE`).

Quirks / volumes:
* **Publication lag ~90 days** (DoD FPDS delay): the newest DLA award returned on 2026-10-09 started 2026-07-13;
  a 10-08-2026 DIBBS award (`SPE4A527P0501`) returned 0 results.
* No line-level quantity/unit price — only award totals. Useful for recipient UEI/size, number of offers and the
  solicitation link, not for unit-price labels.

**Recommended parsing approach:** nightly keyword search on PIIDs from DIBBS awards older than ~90 days (batches of ~25),
store `number_of_offers_received`, `recipient_uei`, `business_categories`; respect 1 req/s.

---

## Fixture index

All paths relative to `backend/tests/fixtures/`. `manifest.json` (same directory) lists every file with its source URL,
size, sha256 and fetch time. Total ≈ 13 MB. Index files were small enough to keep **in full** (no truncation needed).

| File | Size | What it is |
|---|---|---|
| `manifest.json` | 28 KB | machine-readable list of all fixtures (url, bytes, sha256, fetched_at) |
| `cage/cage_dla_mil_probe.json` | 1 KB | cage.dla.mil redirects every search to /Home/UsageAgree; not accepted |
| `cage/cage_dla_mil_usage_agree.html` | 8 KB | cage.dla.mil usage agreement gate (NOT accepted) |
| `cage/dibbs_cage_01084.html` | 10 KB | DIBBS public CAGE pop-up (SAM CAGE info) |
| `cage/dibbs_cage_2N935.html` | 10 KB | DIBBS public CAGE pop-up (SAM CAGE info) |
| `cage/dibbs_cage_3WGD1.html` | 10 KB | DIBBS public CAGE pop-up (SAM CAGE info) |
| `dibbs/award_pdf/SPE4A527V0074.pdf` | 478 KB | automated PO (V), 5305, CLIN/PR/PRLI schedule format |
| `dibbs/award_pdf/SPE4A527V0074.txt` | 46 KB | pdfplumber text |
| `dibbs/award_pdf/SPE4AX20D9412SPE4A226F1602.pdf` | 257 KB | delivery order (F) under IDC SPE4AX20D9412, CLIN/BASIC schedule format |
| `dibbs/award_pdf/SPE4AX20D9412SPE4A226F1602.txt` | 5 KB | pdfplumber text |
| `dibbs/award_pdf/SPE7L126D60JX.pdf` | 520 KB | AIDC basic (D), 4730, qty 0 / unit price / $350k max |
| `dibbs/award_pdf/SPE7L126D60JX.txt` | 61 KB | pdfplumber text |
| `dibbs/award_pdf/SPE7L126P8608.pdf` | 51 KB | manual PO (P), 5330, ITEM NO. schedule format |
| `dibbs/award_pdf/SPE7L126P8608.txt` | 22 KB | pdfplumber text |
| `dibbs/award_pdf/SPE7M027P0185.pdf` | 101 KB | fast award of fixture RFQ SPE7M027T0003 (6145), $0.488/FT |
| `dibbs/award_pdf/SPE7M027P0185.txt` | 59 KB | pdfplumber text |
| `dibbs/awards/awards_search_page.html` | 71 KB | Award search form (cross-page postback to AwdRecs.aspx) |
| `dibbs/awards/awddates_awddt.html` | 72 KB | Award dates list, category=awddt |
| `dibbs/awards/awddates_post.html` | 71 KB | Award dates list, category=post |
| `dibbs/awards/awdrec_SPE4A527V0074.html` | 48 KB | Award Package View |
| `dibbs/awards/awdrec_SPE4AX20D9412SPE4A226F1602.html` | 47 KB | Award Package View (delivery order) |
| `dibbs/awards/awdrec_SPE7L126D60JX.html` | 48 KB | Award Package View (AIDC basic) |
| `dibbs/awards/awdrec_SPE7L126P8608.html` | 48 KB | Award Package View |
| `dibbs/awards/awdrec_SPE7M027P0185.html` | 48 KB | Award Package View (fast award) |
| `dibbs/awards/awdrecs_form_post_nsn.html` | 38 KB | NSN search via the form's cross-page POST ("No Awards are on file" example) |
| `dibbs/awards/awdrecs_nsn_search_1.html` | 240 KB | GET award search Category=nsn, 40 comma-separated target-FSC NSNs, Scope=all |
| `dibbs/awards/awdrecs_nsn_search_2.html` | 258 KB | same, next 40 NSNs |
| `dibbs/awards/awdrecs_post_10-07-2026_page1.html` | 458 KB | Awards posted 10-07-2026, GridView page 1 |
| `dibbs/awards/awdrecs_post_10-08-2026_page1.html` | 431 KB | Awards posted 10-08-2026, GridView page 1 (4,069 records) |
| `dibbs/awards/awdrecs_post_10-08-2026_page2.html` | 471 KB | page 2 via __doPostBack replay (Page$2) |
| `dibbs/nsn/rfqnsn_4730010435999.html` | 47 KB | Public RFQ NSN view |
| `dibbs/nsn/rfqnsn_4730LLQJ07784.html` | 45 KB | Public RFQ NSN view (local stock number) |
| `dibbs/nsn/rfqnsn_5305010560169.html` | 48 KB | Public RFQ NSN view |
| `dibbs/nsn/rfqnsn_5305011209897.html` | 47 KB | Public RFQ NSN view |
| `dibbs/nsn/rfqnsn_5306015642972.html` | 56 KB | Public RFQ NSN view (with spec/drawing data) |
| `dibbs/nsn/rfqnsn_5310015432453.html` | 75 KB | Public RFQ NSN view (18 open PRs) |
| `dibbs/nsn/rfqnsn_5330016630871.html` | 47 KB | Public RFQ NSN view |
| `dibbs/nsn/rfqnsn_5340004290174.html` | 49 KB | Public RFQ NSN view |
| `dibbs/nsn/rfqnsn_5340014073391.html` | 45 KB | Public RFQ NSN view |
| `dibbs/rfq_dates/banner_access_trace.json` | 6 KB | How the DoD banner was accepted; cookie names; form-POST fallback test |
| `dibbs/rfq_dates/batch_quote_format.html` | 450 KB | Batch quote (bq) 121-column layout (official) |
| `dibbs/rfq_dates/dodwarning_dibbs2.html` | 9 KB | DoD banner served in place (HTTP 200) by dibbs2 file host |
| `dibbs/rfq_dates/dodwarning_www.html` | 11 KB | DoD Notice and Consent banner form (www host) |
| `dibbs/rfq_dates/rfq_file_defs.html` | 44 KB | Official help page with in/ca/bq/as file layouts |
| `dibbs/rfq_dates/rfq_fsc.html` | 191 KB | RFQs by FSC search form |
| `dibbs/rfq_dates/rfqdates_close.html` | 74 KB | RFQ dates page category=close |
| `dibbs/rfq_dates/rfqdates_issue.html` | 60 KB | RFQ dates page category=issue |
| `dibbs/rfq_dates/rfqdates_recent.html` | 117 KB | Recent RFQs and Downloads (daily file links) |
| `dibbs/rfq_dates/rfqrecs_post_261008_page1.html` | 423 KB | RFQ listing for post date 10-08-2026, GridView page 1 |
| `dibbs/rfq_index/bq261007.zip` | 120 KB | batch quote zip (bq261007.txt + as261007.txt) |
| `dibbs/rfq_index/bq261008.zip` | 66 KB | batch quote zip (bq261008.txt + as261008.txt) |
| `dibbs/rfq_index/in261004.txt` | 196 KB | full index, 1,416 lines |
| `dibbs/rfq_index/in261005.txt` | 348 KB | full index, 2,506 lines |
| `dibbs/rfq_index/in261006.txt` | 346 KB | full index, 2,493 lines |
| `dibbs/rfq_index/in261007.txt` | 438 KB | full index, 3,157 lines |
| `dibbs/rfq_index/in261008.txt` | 245 KB | full index, 1,769 lines |
| `dibbs/rfq_index/index_stats.json` | 4 KB | counts by solicitation type (9th char), set-aside, AMSC, FSC across the 5 index files |
| `dibbs/rfq_pdf/SPE4A027T0162.pdf` / `.txt` | 702 / 163 KB | T, fast award, 5310, 18 CLINs same NSN, COTS source, "No History Available" |
| `dibbs/rfq_pdf/SPE4A627Q0058.pdf` / `.txt` | 281 / 278 KB | Q, 5306, source controlled, first article + production lot test CLINs |
| `dibbs/rfq_pdf/SPE4A627Q0061.pdf` / `.txt` | 148 / 107 KB | Q, 5305, SB set-aside, 2 CLINs (PG unit), no NAICS |
| `dibbs/rfq_pdf/SPE4A627T0486.pdf` / `.txt` | 404 / 64 KB | T auto-eval, 5305, WOSB set-aside, 20-row procurement history |
| `dibbs/rfq_pdf/SPE7L127Q0024.pdf` / `.txt` | 113 / 75 KB | Q, 5340 |
| `dibbs/rfq_pdf/SPE7L127T0501.pdf` / `.txt` | 414 / 68 KB | T auto-eval, 5330, SB set-aside, procurement history |
| `dibbs/rfq_pdf/SPE7L127U0037.pdf` / `.txt` | 424 / 74 KB | U (AIDC), 5340, SB set-aside, AIDC terms, history split across a page break |
| `dibbs/rfq_pdf/SPE7M027T0003.pdf` / `.txt` | 395 / 63 KB | T fast award, 6145 QPL item (since awarded: SPE7M027P0185) |
| `dibbs/rfq_pdf/SPE7M127U0029.pdf` / `.txt` | 420 / 72 KB | U (AIDC), 4730, history split across a page break |
| `dibbs/rfq_pdf/SPEFA327Q0003.pdf` / `.txt` | 124 / 88 KB | Q, 4730 local stock number `4730LLQJ07784` |
| `dibbs/rfq_record/rfqrec_SPE4A027T0162.html` | 72 KB | RFQ Package View (fast award flag, 18 lines) |
| `dibbs/rfq_record/rfqrec_SPE4A627Q0058.html` | 52 KB | RFQ Package View (FAT pseudo-NSN rows, Tech Docs link) |
| `dibbs/rfq_record/rfqrec_SPE4A627Q0061.html` | 50 KB | RFQ Package View |
| `dibbs/rfq_record/rfqrec_SPE4A627T0486.html` | 50 KB | RFQ Package View (WOSB icon) |
| `dibbs/rfq_record/rfqrec_SPE7L127Q0024.html` | 49 KB | RFQ Package View |
| `dibbs/rfq_record/rfqrec_SPE7L127T0501.html` | 50 KB | RFQ Package View |
| `dibbs/rfq_record/rfqrec_SPE7L127U0037.html` | 51 KB | RFQ Package View (AIDC icon) |
| `dibbs/rfq_record/rfqrec_SPE7M027T0003.html` | 51 KB | RFQ Package View, status **Awarded** with awardee row |
| `dibbs/rfq_record/rfqrec_SPE7M127U0029.html` | 51 KB | RFQ Package View (AIDC icon) |
| `dibbs/rfq_record/rfqrec_SPEFA327Q0003.html` | 49 KB | RFQ Package View (local stock number) |
| `usaspending/award_detail.json` | 6 KB | GET /api/v2/awards/CONT_AWD_SPE4A626PX272_9700_-NONE-_-NONE-/ |
| `usaspending/spending_by_award_dla_psc.json` | 27 KB | request + response: DLA awards in the 7 target PSCs, newest first |
| `usaspending/spending_by_award_piid_keyword.json` | 4 KB | request + response: keyword search for 3 DIBBS PIIDs |

Regenerate with (from the repo root; writes fixtures + `data/raw/dibbs_discovery/`):

```
backend/.venv/Scripts/python backend/scripts/dibbs_discovery.py \
    --award-dates 10-08-2026,10-07-2026 --award-contracts SPE7M027P0185 \
    --piids SPE4A626PL037,SPE4A626PU582,SPE4A626PX272
```
(`--steps`, `--dates`, `--fscs`, `--solicitations`, `--banner form|browser` etc.: see `--help`. Open RFQs and
retention windows change daily, so a re-run will capture different documents.)

---

## 13. FLIS / PUB LOG item characteristics (UNVERIFIED format)

DLA publishes the Federal Logistics Information System's public data ("PUB LOG") through its FLIS Data Electronic
Reading Room (https://www.dla.mil/Information-Operations/FLIS-Data-Electronic-Reading-Room/). It carries, per NSN, the
item's technical characteristics as Master Requirement Codes (MRC, e.g. MATT material, AAGR thread size) with clear-text
replies, plus reference numbers (manufacturer CAGE + part number), freight and packaging data. `dla.mil` refuses
non-browser downloads (HTTP 403), so the layout could not be captured in this repo and the loader
(`app/pipelines/enrichment.py`, `python -m app.cli import-characteristics <file>`) reads any delimited export with a
header row and columns named NSN (or NIIN + FSC), MRC (or code), value (or reply / clear text reply) and optionally
name (or requirement). To verify against the real product: export a few NSNs from PUB LOG, check that the MRC codes and
replies load (`item_characteristics` table), and record the exact column names here.

What the data is used for: same-name parts in the same supply class that agree on at least two characteristics become
price-evidence neighbours for new parts (`dibbs_ml.items`, reason "matching characteristics"). Their price signal is to
be measured with `ml/research/lab.py --cold` once data is loaded; until then the part-number-family evidence remains
the only measured kind.

## 14. Distributor price lists (per organization)

`python -m app.cli import-catalog <file> --org <id> --supplier <CAGE> [--manufacturer <CAGE>]` loads a distributor's
price list (columns matched by name: part number, price, optional manufacturer CAGE, UOM, per, MOQ, lead time, in stock)
into `catalog_prices`, tenant-owned. The cost model uses an imported price for a line's exact approved part number as
its "catalog" tier (after the tenant's own quotes for the NSN, before any ratio tier). Before integrating a feed,
`ml/research/coverage_lab.py --org <id>` reports how many open lines a price list actually matches, by FSC.
