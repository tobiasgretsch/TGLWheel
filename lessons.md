# Lessons Learned

| Date | Category | What went wrong | Rule derived |
|------|----------|----------------|--------------|
| 2026-04-16 | Refactoring | When extracting handler functions from a Flask route, the `@app.route` decorator was left above the first extracted function (`_handle_spin`) instead of being moved to the actual view function (`send_command`). Flask silently registered `_handle_spin` as the endpoint handler. | When refactoring code below a decorator, always verify the decorator stays attached to the correct function. Check the decorator-to-function binding after any insertion between them. |
