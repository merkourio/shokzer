## What and why

<!-- What this changes and why. Link the issue it fixes, if there is one. -->

## Checklist

- [ ] `ruff check`, `ruff format --check`, `mypy` and `pytest` pass locally
- [ ] New behaviour has offline tests (against `tests/sim_headset.py` if it involves the headset)
- [ ] User-facing changes are listed in `CHANGELOG.md` under `## Unreleased`

### Only if this changes what's sent to the headset

- [ ] Every new byte has a source reference or capture in `docs/protocol-analysis.md`
- [ ] Writes read the current value first, send once, and are confirmed only by reading back
- [ ] Tested on a real headset with its owner's approval, at low volume, with the original setting restored
- [ ] The result, with model and firmware version, is in `docs/test-results.md`
- [ ] No real device addresses, names or raw captures are committed
