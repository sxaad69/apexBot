# Zero-to-Hero Contributor Guide: Apex Hunter V14

Welcome to the Apex Hunter team. This guide will take you from a fresh clone to your first verified code change.

## Project Philosophy
We build high-performance trading software with a "Safety-First" mindset. We prefer **no trade** over a **bad trade**.

---

## Prerequisites
- Python 3.11+
- SQLite3
- An AWS EC2 Instance (for production deployment)
- API Keys for Binance/KuCoin (Testnet recommended)

---

## Environment Setup
1. **Clone the Repo**: `git clone <repo-url>`
2. **Initialize Venv**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements-minimal.txt
   ```
3. **Configure Secrets**:
   Copy `.env.example` to `.env` and populate your API keys.

---

## Project Structure
- `core/`: The "Brain" (Trading Engine, Spot Engine).
- `risk/`: The "Shield" (11 layers of risk management).
- `strategies/`: The "Sword" (A1-A6 trading logic).
- `database/`: Persistence layers (SQLite/MongoDB).
- `scripts/`: Production utilities (Setup, Deployment, Analysis).

---

## Your First Task: Verify the Hardening
Run the comprehensive verification suite to ensure the system is stable:
```bash
python3 tests/comprehensive_system_check.py
```
**Expected Output**: `✅ COMPREHENSIVE SYSTEM VERIFICATION PASSED.`

---

## Development Workflow
1. **Branching**: `feat/` for new features, `fix/` for bugs.
2. **Commits**: Use `/commit` to generate Sentry-compliant messages.
3. **Testing**: Never push code without running `test_trailing.py`.

---

## Common Pitfalls
- **Indentation Errors**: We use 4 spaces. Strict linting is enforced.
- **SQLite Locking**: Long-running transactions will block the bot. Always close connections in `finally` blocks.
- **API Rate Limits**: Don't increase the scan interval below 60s unless using WebSocket strategies (A6).
