# OMMAX Case Study

Submission for the OMMAX Data Science / GenAI case study (`2026_OMMAX_DAI_CaseStudy_DataScience_GenAI.pdf`), split into two independent deliverables.

## Part A — Passenger Forecasting

[`partA_passenger_forecasting/`](partA_passenger_forecasting/)

A point-in-time-safe, hourly batch forecasting package for Munich station passenger volumes. Predicts the next seven complete service dates (07:00-12:00) for ten stations, with a remote training pipeline, evaluation evidence, and an offline HTML presentation of the results.

## Part B — Agentic Workflow

[`partB_agentic_workflow/`](partB_agentic_workflow/)

A controlled email-to-ERP order workflow: an LLM proposes structured order data from inbound email, but a deterministic policy layer owns the write boundary. Includes the Python package, an executable notebook walkthrough, and a 14-scene offline HTML presentation.

Each part has its own `README.md` with setup and run instructions.
