"""Live distributor data and purchasing: OUT OF SCOPE for sparky.

What exists: catalogue snapshot prices/stock/LCSC ids from the open jlcparts data (picker), the BOM, a shopping list with product/search links,
and an LCSC cart CSV. Prices are snapshots, not quotes, and are labelled as such in every output.

How live sourcing could be done (not implemented, not planned):
  * Distributor APIs (Digi-Key, Mouser, LCSC/JLCPCB, Octopart/Nexar) return live stock and price breaks per MPN; they need per-user API keys
    (store them with the environment's secret mechanism, never in the repo) and have rate limits and terms of use.
  * An alternative is the user's own BOM-upload flow at their chosen distributor using the CSVs this tool writes.
Automatic purchasing (agents filling carts or checking out) is explicitly not wanted: the user buys.
