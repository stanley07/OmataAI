# Milestone 9 — Incoming text notes

## Upgrade your existing app

Your completed backup is the recovery copy. Keep it private.

1. Stop the dashboard with Ctrl+C in its terminal.
2. Extract this ZIP into a separate folder.
3. Copy all files from its voice-ledger-m9 folder into your existing app folder, replacing the code files. Keep your existing ledger.sqlite3 in that original folder. This ZIP contains no database.
4. In the original app folder run `python app.py`.
5. Open http://127.0.0.1:8080 and press Ctrl+F5. Check the heading says Milestone 9.

The database adds inbox tables automatically. Existing sales, customers, stock and shop records remain in the same database.

## First test — no Meta account needed

1. Select a test shop (or create a separate shop named Inbox Test).
2. Open **Incoming notes → Try a local simulation**.
3. Click **Add simulated note**. It is clearly marked SIMULATION. Sales, debt and inventory have not changed.
4. Click **Review this note**, then choose **Sale** and **Prepare fields for review**.
5. Check total 135000 and received 115000. Select the correct customer. For inventory tracking, select the rice product and quantity 3. Create a customer/product first if needed; confirm sufficient opening stock before confirming the sale.
6. Create the draft. Confirm only after checking every field. The sale adds 20000 debt; a linked rice sale deducts 3 bags on confirmation.
7. Return to Incoming notes and refresh. The note is marked as already drafted. Refreshing or receiving the same provider message again cannot create a second linked draft.

You can instead test stock intake with: `Stock received. Quantity ten.` Select Stock received, prepare fields, select the product and review quantity before drafting and confirming.

## What this milestone includes

- Persistent text inbox separated by selected shop, plus labelled local simulations.
- Separate signed inbound webhook receiver in whatsapp.py.
- Explicit mapping of sender and receiving phone-number ID to an existing shop.
- Duplicate delivery protection and atomic linking from one inbox note to one sales/payment or stock draft.
- Existing confirmation, inventory checks, reversals and backups.
- Inbox notes and draft links are included in the full database backup.

A note stays linked after its draft is cancelled or reversed. To correct it, cancel/reverse the original and enter a new note manually, referencing the old transaction. A note containing multiple transactions must be split manually after checking for duplicates.

## Limits

This is a local, single-operator pilot, not a production multi-user service. Shop selection is not authentication. No automatic outgoing WhatsApp messages, customer reminders, audio processing, media downloads or payments are implemented by the receiver. Only text of 1–2000 characters from configured senders is accepted; other messages are ignored, without a reply or inbox entry. Tell pilot traders to send text and check receipt with the operator.

The dashboard does not poll automatically: press Refresh inbox after receiving a message. Inbox times are receipt times in UTC; financial reporting remains based on confirmation time in Nigeria. Notes do not backdate ledger entries.

The receiver is not live merely because the dashboard is running. See WHATSAPP-SETUP.md for the separate configuration. Do not expose port 8080 to the internet.
