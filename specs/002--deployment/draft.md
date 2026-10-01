---
title: Draft instruction for the deployment
description: |
  This document provides brief instructions for the deployment of my quantitative trading system. It's not a complete instruction, but rather a kind of bootstrapping instruction to plan the implementation. Agent will be responsible for generating the complete instruction for the implementation. 
---

We'll deploy this system on GCP, single e2-micro VM, with 16GB of storage, with external static IP assigned. The application will be containerized using Docker, pushed on GCP Artifact Registry (also need to provision).

Use terraform to provision the VM and the Artifact Registry. And then use Ansible to configure the VM, install Docker, and deploy the application. The deployment will be done in a single region, but we can consider multi-region deployment in the future.

We also need some kind of rolling update command (e.g. `just update`) if we want to update the application. The update command should be able to pull the latest image from Artifact Registry and restart the application. But this command should reject the update if the market is open (e.g. 9:00 AM to 3:30 PM KST for KRX). The update command should also be able to rollback to the previous version if the update fails.

Notes:
- `@` is alias of `/Users/hwanghyeongyu/Documents/projects/me/quantitative-trading/system`
- If you need any permissions for GCP cloud resources, use `ctx use quantitative-trading` to get the permission.
- IF YOU HAVE ANY AMBIGUITY OR UNCERTAINTY, PLEASE ASK ME FOR CLARIFICATION. DO NOT MAKE ASSUMPTIONS.