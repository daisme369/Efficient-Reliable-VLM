# Thorax AI design

The clinical workspace follows the Figma sketch's patient strip, three-column organization and dark image stage. The frontend specification defines layout dimensions, review stages and workflow terminology.

## Visual system

- A cool `#F4F7FB` canvas supports white work surfaces separated by thin rules. `#0F172A` text, `#475569` secondary text and `#0E7490` controls follow the clinical specification.
- The viewer uses `#0B1220` chrome with `#E2E8F0` labels and a centered grayscale image. Amber dashed AI regions and solid cyan doctor regions also carry text labels and a contrasting outline.
- Segoe UI keeps labels, headings and dense data in one type family. Tabular numerals distinguish identifiers, dates, and coordinates.
- Status colors carry explicit labels. Amber marks pending or stale work and green marks completed approvals. A 3 px focus ring and native form semantics make actions keyboard-visible.

## Study workspace

Operate mode. Keep the patient strip and review tabs visible while the viewer, context and review regions scroll. The left rail explains study history and reading progress. The image occupies the flexible center column. Each review panel explains its current state and presents the next available action. At 1280–1439 px, study context opens in a separate drawer. Below 1280 px, show the supported-width notice.
