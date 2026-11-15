[![make.fun](https://make.fun/badge/btc-race-24h-20261115-0828.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828)
[![rank](https://make.fun/badge/btc-race-24h-20261115-0828/unot58/btc-race-bot-0828/rank.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828)
[![reward](https://make.fun/badge/btc-race-24h-20261115-0828/unot58/btc-race-bot-0828/reward.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828)

# BTC Race Bot — Setup Tipe 3: engulfing at supply/demand zones

Entry for the [make.fun bounty "Race a BTC trading bot live for 24 hours"](https://make.fun/bounty/btc-race-24h-20261115-0828) (btc-race-24h-20261115-0828).

A Python 3.13 bot (stdlib only) that trades cbBTC minute bars with the
owner's own Setup Tipe 3: 5-minute supply/demand zones as context, 1-minute
bullish engulfing candles as the entry confirmation, and the next opposing
zone as the target. No RSI, no EMA, no Bollinger, no momentum indicators.

## How it trades

- **Context:** supply/demand zones from 5m bars (swing + ≥100 bps
  displacement, body-based bounds).
- **Entry:** 1m bullish engulfing (body ≥12 bps) closing in/near a fresh
  demand zone, confirmed by next-bar follow-through → BUY full size.
- **Target:** the next supply zone above the entry (else +150 bps fixed).
- **Stop:** the demand zone low −10 bps; a stop-out invalidates that zone.
- **Exits:** bearish engulfing at supply, target hit, or a 360-bar time stop.

## Dev gate (kit replay engine)

- Net PnL **+$6.80 (+0.68%)** on the frozen 24h dev window
- 3 round trips, all winners; max drawdown 1.35%; 0 rejects
- Deterministic: two runs byte-identical

## Files

- `main.py` — the bot (reads JSONL bars on stdin, writes orders on stdout)
- `vendor/makefun_bars.py` — make.fun SDK, vendored verbatim from the kit

## License

MIT — see [LICENSE](LICENSE).
