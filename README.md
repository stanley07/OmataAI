# Voice Ledger — Milestone 9

Start with START-HERE.md. Existing users keep ledger.sqlite3 and replace the code files, then restart the dashboard.

Run: `python app.py` (Python standard library for text and ledger features).
Open: http://127.0.0.1:8080
Optional inbound receiver: see WHATSAPP-SETUP.md, then `python whatsapp.py`.
Optional local speech dependencies remain separate in requirements-voice.txt; no installation is needed for text intake.

Verification: `python -m unittest discover`

This release adds inbound text review, not automatic bookkeeping. Receipt never changes stock, sales or debt. One inbox note is linked atomically to one draft. Confirmation and reversal rules remain in force. Local simulations are labelled. Live Meta delivery and visual browser testing were not performed in the build environment.
