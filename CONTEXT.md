# Domain Context & Glossary: FDA 510(k) & Medical Device Lineage

## 1. Regulatory Background: The 510(k) Pathway

Under Section 510(k) of the Food, Drug and Cosmetic Act (FD&C Act), medical device manufacturers must submit a Premarket Notification prior to commercial distribution, demonstrating that the new device is **Substantially Equivalent (SE)** to a legally marketed predicate device (21 CFR 807 Subpart E).

### Key Concepts & Definitions
- **Predicate Device:** A legally marketed device cleared via 510(k), granted De Novo classification, or commercialized pre-amendment (before May 28, 1976), against which substantial equivalence is claimed.
- **Reference Device:** A legally marketed device cited within a 510(k) summary not as the primary predicate, but to support specific technological characteristics, performance testing methodologies, or standard materials. *Critical Data Engineering Distinction:* Reference devices must not be confused with true ancestry predicates.
- **Multiple Predicates / Split Predicates:** Submissions citing more than one predicate. FDA guidance historically discouraged "split predicates" (combining intended use from Device A and technological characteristics from Device B), but dual predicates (e.g., predicate for hardware + predicate for software accessory) occur frequently.
- **510(k) Summary vs. 510(k) Statement:** 
  - *Summary (21 CFR 807.92):* Public disclosure detailing intended use, technological comparison, and predicate identification. Stored as public PDFs on CDRH servers.
  - *Statement (21 CFR 807.93):* Applicant's certification that safety and effectiveness data will be provided upon request. Contains *no* predicate information. (Tracked via openFDA field `statement_or_summary`).

---

## 2. Identifier Formats & Encodings

| Prefix | Pathway | Format Regex | Notes |
|---|---|---|---|
| `K` | 510(k) Clearance | `K\d{6}` | Digits 1-2 indicate the 2-digit fiscal year of filing (e.g., `K210758` = FY2021). Range: 1976–present. |
| `BK` | CBER 510(k) | `BK\d{6}` | Blood and biologic device clearances managed by CBER. |
| `DEN` | De Novo Classification | `DEN\d{6}` | Low-to-moderate risk novel devices without an existing predicate. Often serve as lineage root nodes. |
| `P` | Premarket Approval (PMA) | `P\d{6}` | Class III high-risk devices. Some 510(k) devices cite PMAs as reference roots. |

---

## 3. FDA PDF Storage Conventions (CDRH)

FDA CDRH publishes 510(k) summary PDFs at predictable URL endpoints:
- **Modern / Standard URL Format (since ~1996):**
  `https://www.accessdata.fda.gov/cdrh_docs/pdf{YY}/{K_NUMBER}.pdf`
  - Example: `https://www.accessdata.fda.gov/cdrh_docs/pdf21/K210758.pdf` (for `K210758`)
  - Submissions prior to 2000 use 2-digit years `pdf96`, `pdf97`, `pdf98`, `pdf99`.
- **Legacy URL Format:**
  Some older documents reside under `https://www.accessdata.fda.gov/cdrh_docs/pdf/{K_NUMBER}.pdf`.
- **Era Stratification for Extraction Difficulty:**
  1. **Era 1 (1996–2002):** Scanned paper documents converted to raster PDFs. Minimal or corrupted digital text layers. Requires Tesseract OCR, deskewing, and character repair (e.g. `O` vs `0`, `l` vs `1`).
  2. **Era 2 (2003–2009):** Transition era. Mixed digital PDFs and scanned exhibits.
  3. **Era 3 (2010–2017):** Native digital PDFs; variable header layouts; unstructured tables.
  4. **Era 4 (2018–2023):** Standardized CDRH summary templates. Predicate tables standard.
  5. **Era 5 (2024–Present):** Structured eSTAR electronic submissions format. High digital fidelity.

---

## 4. Recall Classifications & Data Structure (openFDA)

Under 21 CFR Part 7, FDA classifies recalls into three risk tiers:
- **Class I:** Dangerous or defective products that predictably cause serious adverse health consequences or death.
- **Class II:** Products that might cause temporary or medically reversible adverse health consequences, or where serious harm is remote. (Crucial subset: *design-related* Class II recalls).
- **Class III:** Products that violate FDA law but are unlikely to cause adverse health consequences.

### openFDA Recall Fields
- `res_event_number`: Unique identifier for the recall *event*. A single event can span dozens of product lines and multiple K-numbers. Deduplication at the event level is essential.
- `recall_number`: Specific product/lot recall action identifier (e.g. `Z-1234-2023`).
- `k_numbers`: Array of associated 510(k) clearance numbers (fill rate varies; requires entity normalization).
- `root_cause_description`: Standardized FDA cause categorization (e.g. *Component design*, *Software design*, *Labeling false/misleading*).

---

## 5. Medical Device Review Panels (Product Code Prefixes)

510(k) records are categorized into 19 medical specialty panels (identified by 3-letter product codes, e.g. `MRN`, `DQA`):
- `AN`: Anesthesiology
- `CV`: Cardiovascular
- `CH`: Clinical Chemistry
- `TX`: Clinical Toxicology
- `DE`: Dental
- `EN`: Ear, Nose, and Throat
- `GU`: Gastroenterology & Urology
- `HO`: General Hospital
- `HE`: Hematology
- `IM`: Immunology
- `MI`: Microbiology
- `NE`: Neurological
- `OB`: Obstetrics & Gynecology
- `OP`: Ophthalmic
- `OR`: Orthopedic
- `PA`: Pathology
- `PM`: Physical Medicine
- `RA`: Radiology
- `SU`: General & Plastic Surgery

---

## 6. Project Terms

- **Stage**: A roadmap phase (0–7, with Stage 2 split into 2a and 2b) whose start is gated on the previous stage's testable exit criteria, not on a date. See `docs/ROADMAP.md` and ADR-009.
- **As-of query**: A question answered using only what the raw store had recorded by a given date: records, recalls and graph edges whose snapshot date is on or before it. It is what makes a backtest free of look-ahead (Stage 5).
- **Snapshot citation**: A reference to one frozen release (its snapshot ID and DOI) that lets anyone reproduce a published number exactly.
- **Point-in-time vs reconstructed**: A point-in-time value comes from a weekly snapshot taken at that date. A reconstructed value comes from today's openFDA data using event dates, so later amendments may have leaked in. Every metric says which it is.
- **Recall-prediction benchmark**: A fixed, published task (outcome, horizon, date-based splits, metrics) for predicting device recalls, scored on test periods whose labels are frozen in advance (Stage 6).
- **Predicate simulator**: A tool that shows how devices citing a candidate predicate fared over time (recalls, review times, lineage age and depth), computed as of each date and cited to source documents, against a matched baseline. It reports facts about lineages, not a verdict on any device (Stage 7).

---

## 7. Storage Terms (ADR-010)

- **Raw snapshot**: The set of raw objects (openFDA bulk exports, CDRH PDFs) that one ingestion run used, as listed in its run manifest. Snapshots are never edited.
- **Run manifest**: `runs/<YYYY-MM-DD>_<run-id>/manifest.json`. It lists every object in a run with its key, SHA-256, size and record count, plus openFDA `export_date` and `last_updated`, commit SHA and run ID. Every run lists all current objects, including unchanged ones, so one manifest describes the whole snapshot. Schema: `RunManifest` in `packages/core/src/stemma/core/schemas/run_manifest.py` (`schema_version` 1). It is a shared data contract.
- **`export_date`**: openFDA's own version date for a bulk export (from `download.json`). Raw JSON is partitioned by it, not by our run date.
- **Write-once**: Once an object exists in the raw store, it is never overwritten. New content always gets a new key.
- **Raw-store dates are UTC**: Every date and timestamp the pipeline writes into the raw store or a run manifest (run dates, `started_at`, marker dates) is in UTC, whatever machine or time zone runs it. A clock without a time zone is refused. openFDA's own `export_date` and `last_updated` are stored exactly as openFDA publishes them.
- **Missing marker**: `cdrh/missing/<K-number>/<YYYY-MM-DD>_<url-fingerprint>.json`, written when a K-number's summary PDF returns 404 at every candidate CDRH URL. It means "not posted at these URLs as of that UTC date", not "never exists". The fingerprint (first 12 hex characters of the SHA-256 of the URLs tried) limits the marker to those URLs. When the crawler's URL patterns change, the K-number is checked again. A server error, rate limit, or network failure never writes one. Those are retried on the next crawl.
- **`latest.json` pointer**: The only mutable object in the raw store. It points to the newest complete run manifest and is updated last.
- **`RawStore`**: The storage interface (`put_once`, `get`, `exists`, `list`). `LocalRawStore` writes under `data/raw/`; `R2RawStore` writes to the `stemma-raw` bucket using the same keys. `STEMMA_RAW_STORE` picks one (`local` by default).
- **Canonical writer**: A process allowed to write to the `stemma-raw` bucket: the scheduled ingestion workflow, or the backfill job on the Oracle VM, both running code from `main`.
