# Live-data snapshot (2026-10-09)

Reports from a bounded **live** backfill against the public DIBBS site during the build:
23 posting dates of RFQ index files for the default FSCs, procurement history from 400 RFQ PDFs
(one per NSN; 5,860 award rows), 100 award-listing rows and 4 NSN award searches, then `make fsc-report`
and `make train` on that real data only (no synthetic rows).

Commands (resumable; at 1 request/second per host):

    make backfill FSC=5305,5306,5310,5330,5340,4730,6145 START=2023-01-01 END=today MODE=live  # with --max-* limits
    make fsc-report
    make train

`../latest_model.md` is whatever the most recent `make train` produced (the demo seed trains on synthetic data).
