# 0011. Sales and downloads from Sales and Trends; engagement analytics deferred

Status: accepted

## Context
Release managers ask "how many downloads?" and "how many page views?". Apple serves these from two different places: Sales and Trends (daily gzip tab-separated reports, one request per day, needs the account's vendor number and a Sales-capable key role) and the Analytics Reports API (an ongoing report request must first be created per app, then reports, instances and segments are downloaded as CSV).

## Decision
1. **Downloads and proceeds now.** `SalesReader` implements the existing `SalesReports` port. Rows are classified by Apple's product type (first-time downloads, updates, in-app purchases); re-downloads and unknown types are left out of the totals and counted in the log, never guessed into a category. Proceeds are units times Apple's per-unit amount, kept per currency.
2. **Safe by construction.** Reports are decompressed with a size ceiling, required columns are checked and a changed format fails loudly, a day without a report is "not published", settled days (three days old) are cached, a request spans at most 70 days, and parallelism is bounded to protect the hourly quota.
3. **Opt-in.** Without a vendor number the assistant says sales are not supported yet and names the setting, instead of failing at startup.
4. **Engagement analytics are deferred, not guessed.** Creating the ongoing report request changes the account, and the CSV layouts are not in Apple's OpenAPI specification. Writing a parser from memory would put unverified code in the repository. They are built from real files after a read-only look at what the account's reports contain.

## Consequences
- Downloads from Sales and Trends and from Analytics differ by time zone and rules; the assistant labels sources when both exist.
- Figures are estimates; settled payouts (Finance reports) remain a separate future source.
- The product type table follows Apple's documentation and should be confirmed against a real account with `make live-asc`.
