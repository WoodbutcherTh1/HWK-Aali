---
name: priority-erp
description: Priority ERP (Priority Software) — the warehouse/business-management system many users work with daily. What it is, how it works, its modules, who uses it, what it does and doesn't do, and how it compares to similar ERPs. Load before answering any ERP/warehouse/inventory question.
---

## What Priority is

Priority Software (historically "Priority ERP", originally built by Eshbel) is
an **ERP platform for small and mid-size companies**: ONE database covering
finance, supply chain, manufacturing, CRM, HR/payroll and project management.
Deployment: on-premise, private/private-cloud, or SaaS, with a Windows desktop
client, a full web interface (Priority Web), and mobile apps.

## How it works (core model)

- Everything is documents in a workflow: sales/purchase orders → pick/pack/ship
  → invoices → accounting entries. Each document has a form, statuses, and
  sub-forms (lines).
- Inventory is tracked by **warehouse → bin/location → part**, with lot/serial
  numbers, expiry dates, and units of measure. Every movement (receipt,
  issue, transfer, count) is a logged transaction.
- Strong customization layer: form/screen designers, report generators,
  triggers and an SDK — integrators adapt flows without touching core code.
- Typical users: warehouse operators (barcode scanning, picking), logistics,
  accounting, sales, and management dashboards.

## Warehouse management (what users ask about most)

Purchase receipts → put-away to bins; sales orders → picking routes → packing
→ shipping documents; transfers between warehouses; cycle counts and full
inventories; part cost methods (average/last/fifo); lot/serial traceability;
reorder warnings by part and warehouse.

## Who uses it / why

Thousands of companies (strongest in Israel, expanding internationally) —
retail, wholesale/distribution, light manufacturing, services. Chosen for
enterprise-grade depth (especially manufacturing + finance) with faster,
cheaper implementation than SAP.

## What it does NOT do

- Not a micro-business tool (that is Odoo/Zoho territory) — implementation
  still needs a partner.
- Not a standalone best-of-breed WMS; warehouse is strong INSIDE the ERP but
  giant automated DCs often add dedicated WMS on top.
- Localizations outside its core markets are weaker than SAP/NetSuite.

## Similar systems (know the differences)

| System | vs Priority |
|---|---|
| SAP Business One | bigger global brand; heavier/more expensive; less flexible customization |
| SAP S/4HANA | enterprise tier — much bigger budgets and timelines |
| Odoo | cheaper, very modular, huge app store; shallower finance/manufacturing depth |
| Oracle NetSuite | cloud-first, strong for multi-subsidiary; less localized here |
| Dynamics 365 BC | Microsoft ecosystem; strong finance; steeper partner-led customization |
| Acumatica / Sage X3 / IFS | comparable mid-market ERPs; different vertical strengths |

## Answering users

When asked "how do I do X in Priority", give the document-flow steps by module,
name the likely form/screen, warn about irreversible actions (posted invoices,
final shipments), and say plainly when something is a partner-customization
question rather than a core feature.
