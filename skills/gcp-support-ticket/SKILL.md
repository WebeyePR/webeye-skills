---
name: gcp-support-ticket
description: Draft, review, or refine Google Cloud Platform (GCP) technical support tickets. Use when a user needs help creating a GCP support case, optimizing a ticket for faster resolution, or wants to ensure their ticket follows GCP best practices.
---

# GCP Support Ticket Creator

This skill helps you draft high-quality Google Cloud support tickets to get faster resolutions from GCP engineers.

## Required Information for a Good Ticket

When drafting a ticket, ensure the following details are included:

1.  **Project ID**: The unique identifier for your GCP project.
2.  **Resource ID**: (e.g., Instance ID, Cluster Name, Dataset ID).
3.  **Error Message**: Paste the exact error from logs or console.
4.  **Time of Incident**: Precise timestamp (including timezone).
5.  **Steps to Reproduce**: How can an engineer trigger the same issue?
6.  **Expected vs. Actual Behavior**: What did you want to happen?

## Best Practices

-   **Severity Level**: Be honest. P1 is for production outages only.
-   **Component**: Specify if it's Compute Engine, GKE, BigQuery, etc.
-   **Logs**: If possible, provide a `gcloud` command or query to view relevant logs.

## Workflow

1.  Ask the user for the 6 required information pieces listed above if they haven't provided them.
2.  Format the data into a professional draft.
3.  Review the draft for clarity and conciseness.
4.  Present the finalized ticket content to the user to copy-paste into the GCP Console.
