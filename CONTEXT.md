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
- `product_res_number`: Identifier of one recalled product within a recall event (e.g. `Z-0001-04`); the key of `RecallRecord` (ADR-012). Present on every device/recall record and never repeated (all 59,705 records in the 2026-11-13 export).
- `res_event_number`: Unique identifier for the recall *event*. A single event can span dozens of product lines and multiple K-numbers. Deduplication at the event level is essential.
- `cfres_id`: FDA's internal recall database ID; not carried by `RecallRecord`.
- `recall_number`: The device/enforcement name for `product_res_number`. device/recall has no field by this name. Blank on 1 of 40,535 enforcement records (2026-11-16 export, event 94520); the weekly diff keys such records by `event_id` instead.
- `classification` (Class I/II/III): Not in device/recall. It comes from device/enforcement, joined on `product_res_number` (#129). `None` when no enforcement record matches or FDA lists it as "Not Yet Classified". openFDA's enforcement export only holds reports from 2012 on, so recalls initiated before 2012 almost never get a class from it (2026-11-16 exports: 67.9% of all recalls classified; 8.4% of 2011, 100% from 2013). FDA's Medical Device Recalls database (cfRES) does list a class for older recalls: all 34 sampled from 2002–2011 had one, and all 42 sampled from 2012 on matched enforcement (checked 2026-11-17).
- `pma_numbers`: PMA approvals a recalled product names, usually with a supplement suffix (`P850020S011`). `RecallRecord` keeps the base P-number (`P850020`), which matches FDA's PMA export. A value without one is kept as written.
- `openfda.k_number` (on recall records): every 510(k) under the recall's product code, a median of 288, not the recalled device's K-number. Never use it as a link or label.
- `recall_status`: The recall's state in this snapshot: `Open, Classified`, `Completed` or `Terminated`. It changes after posting, despite openFDA's note that it doesn't (44,691 of 59,705 products were `Terminated` on 2026-11-18), and openFDA keeps no history. It is never a label or a target (ADR-017); #91 measures how it changes across snapshots.
- `event_date_posted` / `event_date_created`: When FDA posted the recall. openFDA uses one name or the other per record, never both: `event_date_created` on 13,575 records (mostly 2015–2022), `event_date_posted` on the rest. Some values are back-fills, entered in bulk long after the recall ended (e.g. 693 on 2004-07-20, 89 on 2015-06-25/26). Label code reads the **availability date** (ADR-013), not these fields.
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

- **Filiation**: The project's name (ADR-019; formerly Stemma, then Recension). In textual criticism, filiation is the line of descent between manuscripts, which copy was made from which; this project traces the same thing for 510(k) devices, which clearance descends from which predicate, one versioned snapshot at a time.
- **Stage**: A roadmap phase (0–7, with Stage 2 split into 2a and 2b) whose start is gated on the previous stage's testable exit criteria, not on a date. See `docs/ROADMAP.md` and ADR-009.
- **Availability date**: The date a recall is treated as publicly known, for labels and backtests (ADR-013). It is the device/recall posting date when valid (`event_date_posted`, or `event_date_created`, openFDA's name for it on 13,575 records; one entered more than 30 days after termination is a back-fill: it loses to a valid enforcement date, and otherwise is kept as an upper bound flagged `availability_upper_bound`, which recall-velocity features skip); otherwise the later of the valid enforcement `report_date` and `center_classification_date`; otherwise empty, never the initiation date. Dates after their snapshot's export date or before 1976-05-28 are invalid. Each recall records which field supplied it (`availability_source`), and date flags (`century_repaired`, `initiation_conflict`, `initiation_invalid`, `posted_before_initiated`); every flag but `century_repaired` excludes the recall from time-to-event durations.
- **Recall event**: One FDA recall action, identified by `res_event_number`. It can cover many recalled products (`product_res_number`) and many devices. Every product of an event shares its posting and initiation dates, but class is set per product.
- **Recall label**: The outcome the models and backtests use (ADR-017): one recall event as it touches one device application, stored as a `DeviceRecallEvent` keyed on (`pathway`, `application_number`, `res_event_number`). The pathway is `510k` (a K-number) or `pma` (a base P-number), and only 510(k) labels join the predicate graph. Its class is the most severe among the event's products that name the application. It counts as known only from its availability date. It comes only from openFDA device/recall, with the class and fallback dates joined from device/enforcement. Part 806 reports and other safety signals may be features, never the label.
- **As-of query**: A question answered using only what the raw store had recorded by a given date: records, recalls and graph edges whose snapshot date is on or before it. It is what makes a backtest free of look-ahead (Stage 5).
- **Snapshot citation**: A reference to one frozen release (its snapshot ID and DOI) that lets anyone reproduce a published number exactly.
- **Point-in-time vs reconstructed**: A point-in-time value comes from a weekly snapshot taken at that date. A reconstructed value comes from today's openFDA data using event dates, so later amendments may have leaked in. Every metric says which it is.
- **Recall-prediction benchmark**: A fixed, published task (outcome, horizon, date-based splits, metrics) for predicting device recalls, scored on test periods whose labels are frozen in advance (Stage 6).
- **Predicate simulator**: A tool that shows how devices citing a candidate predicate fared over time (recalls, review times, lineage age and depth), computed as of each date and cited to source documents, against a matched baseline. It reports facts about lineages, not a verdict on any device (Stage 7).

---

## 7. Storage Terms (ADR-010)

- **Raw snapshot**: The set of raw objects (openFDA bulk exports, CDRH PDFs) that one ingestion run used, as listed in its run manifest. Snapshots are never edited.
- **Run manifest**: `runs/<YYYY-MM-DD>_<run-id>/manifest.json`. It lists every object in a run with its key, SHA-256, size and record count, plus openFDA `export_date` and `last_updated`, commit SHA and run ID. Every ingestion run lists all current objects, including unchanged ones, so one manifest describes the whole snapshot. Schema: `RunManifest` in `packages/core/src/filiation/core/schemas/run_manifest.py` (`schema_version` 4 since ADR-021: v2 added completeness and checks (ADR-016), v3 change lists (ADR-018), v4 cosmetic changes marked in those lists; v1 to v3 manifests still load). It is a shared data contract.
- **Audit manifest**: The run manifest of a PDF backfill (`crawl-pdfs --from-latest`). Its single `cdrh` source lists only the objects that run wrote, and `targets_from` names the snapshot manifest its targets came from. It records provenance, not a snapshot, so `latest.json` never points at it.
- **`export_date`**: openFDA's own version date for a bulk export (from `download.json`). Raw JSON is partitioned by it, not by our run date.
- **Write-once**: Once an object exists in the raw store, it is never overwritten. New content always gets a new key.
- **Raw-store dates are UTC**: Every date and timestamp the pipeline writes into the raw store or a run manifest (run dates, `started_at`, marker dates) is in UTC, whatever machine or time zone runs it. A clock without a time zone is refused. openFDA's own `export_date` and `last_updated` are stored exactly as openFDA publishes them.
- **Missing marker**: `cdrh/missing/<K-number>/<YYYY-MM-DD>_<url-fingerprint>.json`, written when a K-number's summary PDF returns 404 at every candidate CDRH URL. It means "not posted at these URLs as of that UTC date", not "never exists". The fingerprint (first 12 hex characters of the SHA-256 of the URLs tried) limits the marker to those URLs. When the crawler's URL patterns change, the K-number is checked again. A server error, rate limit, or network failure never writes one. Those are retried on the next crawl.
- **`latest.json` pointer**: The only mutable object in the raw store. It points to the newest complete run manifest and is updated last.
- **Snapshot diff**: How a source's export in one run differs from the snapshot `latest.json` named when the run started. Records are matched by key (`k_number` for 510(k), `product_res_number` for recall). They are **added**, **removed**, or **amended**. Amended means the same key with different content, not counting key order. The diff counts how many amended records changed each top-level field. It is stored as `SourceDiff` on the source's entry in the run manifest. `null` means the previous snapshot didn't include that source. Most weekly amendments are openFDA re-enriching its `openfda` block: in the 2026-11-18 510(k) export, 171,357 records changed there, in the `registration_number` and `fei_number` lists, and only 6 field changes fell outside it. FDA's own amendments must be counted apart from these.
- **Change list**: The keys behind a snapshot diff, stored once per changed source at `runs/<run_id>/diffs/<source>.jsonl.gz` (ADR-018). It holds one JSON line per added, removed or amended key, sorted by key; an amended line names the changed top-level fields and, from v4, which of them changed only cosmetically. It is linked from `SourceDiff.changes` with its SHA-256. No list is stored when nothing changed.
- **Cosmetic change**: A field change that is only whitespace inside text (runs collapsed, ends trimmed) or the order of list entries, at any depth (ADR-021). A cosmetic change is still an amendment in the change list, but it is never counted as FDA amending a record.
- **FDA edit**: An amended key with at least one non-cosmetic change outside `openfda`. It is the numerator of R01's amendment rate. A change confined to `openfda` is openFDA re-enrichment, reported separately (#91).
- **Field completeness** (`fields_present`): For one source in one run, how many records hold a non-empty value at each JSON-pointer field path (`/openfda/device_class`). Empty means null, a blank or whitespace-only string, `[]` or `{}`; `0` and `false` are values. A path seen but always empty counts 0 (ADR-016, #92).
- **Check**: A named ingestion test with a stable dotted id (`export_counts.total`, `dates.decision_before_mda`), a **severity**, and the bias-register factors it controls. `fail` stops the run before anything is stored; `report` is recorded in the run manifest and the job summary, and the run continues. Each source lists the checks that ran (`checks`).
- **Finding**: One check's result for one source or partition, with a message that gives counts and up to 5 example identifiers. A written manifest only holds `report` findings. `checks`/`findings` of `null` mean the source was **not checked** (all v1 manifests); an empty list means checked and clean.
- **`RawStore`**: The storage interface (`put_once`, `get`, `exists`, `list`). `LocalRawStore` writes under `data/raw/`; `R2RawStore` writes to the `filiation-raw` bucket using the same keys. `FILIATION_RAW_STORE` picks one (`local` by default).
- **Canonical writer**: A process allowed to write to the `filiation-raw` bucket: the scheduled ingestion workflow, or the backfill job on the Oracle VM, both running code from `main`.
