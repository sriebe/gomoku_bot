## 2026-02-10
*Initial commit with code review fixes for security, input validation, and robustness.*
- Fixed bounds check order in `server.py` `make_move()` — was accessing board before validating indices
- Added integer type validation for `row`/`col` in WebSocket `make_move` handler
- Fixed URL injection in both bot launchers — username now properly encoded via `params=`
- Fixed `find_threats()` open-end detection in easy bot — forward/backward counts tracked separately
- Replaced bare `except:` with `except Exception:` in server (4 occurrences)
- Fixed bot reconnect loop — `reset_game_state()` called at start of `run()` in both bots
- Migrated deprecated `@app.on_event("startup")` to `lifespan` context manager
