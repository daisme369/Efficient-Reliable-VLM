# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Next.js 15 App Router, React, TypeScript, Tailwind and TanStack Query, as specified in the frontend architecture.

## Users

Doctors review assigned chest X-ray studies and approve their own readings. Administrators manage demo accounts, assignments and allowlisted fixtures.

## Product Purpose

Thorax AI is an academic demonstration of chest X-ray reading assistance. A reading records the source image, model suggestions, doctor corrections, reviewed evidence and two explicit approvals before becoming read-only.

## Capabilities and Constraints

The MVP uses English and supports desktop browser widths of 1280 CSS pixels or more. Images are immutable PNG or JPEG assets. The browser uses the same-origin REST contract. The local demo uses synthetic fixtures. The supplied real inference artifacts and backend are separate integration inputs.

## Evidence on Hand

The product, frontend and REST contracts are in `docs/spec.md`, `docs/frontend_des.md` and `docs/implementation_plan.md`. The Thorax AI PACS Figma workspace provides layout inspiration.

## Product Principles

- Keep patient, study and image identity visible during review.
- Keep model suggestions, doctor-authored changes and approvals distinct.
- Preserve original images and evidence lineage through every state change.
- Make blocked or unavailable actions explain their recovery.
