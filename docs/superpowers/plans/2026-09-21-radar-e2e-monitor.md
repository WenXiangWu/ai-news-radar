# Radar E2E Monitor Recovery Plan

## Scope

Make the Radar-to-Way synchronization observable and bounded from execution
through static report publication and consumer rendering.

## Tasks

1. Add a bounded operation runner that honors each registered operation's
   runtime limit, records timeout/blocked results, and emits progress logs.
2. Make `radar_run.py` write a failure report for unexpected execution errors,
   so the workflow can publish a report even when a connector fails.
3. Add end-to-end artifact tests for the Radar report bundle and monitor URL
   paths.
4. Make the monitor page distinguish unavailable data from a real zero-valued
   snapshot.
5. Add a read-only Radar result panel to Way's scheduler settings. It will
   consume mirrored JSON reports and link back to Radar for full monitoring.
6. Run focused tests, full repository tests, local HTTP artifact checks, and
   verify the GitHub Actions workflow can publish the resulting files.
