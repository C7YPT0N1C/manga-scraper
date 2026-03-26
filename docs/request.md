Current search behaviour (observed from code)

- Server endpoint: `/search` implemented in `mangascraper/dashboard_utils/routes/scraper_routes.py` (`search_galleries`).
  - Accepts `query_type` and `query_value` in JSON payload. Allowed `query_type` values are the set in `_SEARCH_TYPES` ("homepage", "id_range", "ids", "cache_key", "search", "artist", "group", "tag", "character", "parody").
  - Special handling:
    - `query_type == "cache_key"`: uses `scraperapi.Cache.Load.cache(cache_key=...)` to resolve IDs.
    - `query_type == "id_range"`: expects `start_id` and optional `end_id`, returns range of IDs.
    - `query_type == "ids"`: normalises `ids` (comma-separated string or list) using `_normalise_ids`.
  - Other query types call `scraperapi.Fetch.gallery_ids(query_type, search_value, sort_value, start_page, end_page, ...)` to get `ids` and optional `cache_key`.
  - Server-side validation: before searching, `search_galleries` calls `_validate_prefixed_tokens(query_value)`.
    - `_validate_prefixed_tokens` uses the regex `(?:(?:^|\s))([+-])(?:[A-Za-z_]+:)?([^"'\s][^\s]*)` to detect any unquoted `+`/`-` prefixed bare token (optionally with a `field:` prefix). If such a token exists the validator returns `False` and `search_galleries` returns an empty result set (HTTP 200 JSON with no IDs/results).
  - Note: the helper `_parse_prefixed_query` still exists in the file (parses `field: value` for certain fields) but current `search_galleries` no longer uses it — the code treats `query_value` as free-text.
  - After obtaining IDs, the server hydrates metadata via `scraperapi.Fetch.all_galleries_metadata(ids, cache_key=None)` and returns rows built by `_queue_rows`.
    - `_queue_rows` maps metadata keys into each row; `parodies` is populated from `meta.get('parodies') or []` (so server returns whatever is in metadata: strings or numeric ids).

- Client-side search (front-end file: `mangascraper/dashboard_utils/templates/creators.html`):
  - Input: `#searchInput` captures free-text queries and updates `_searchTerm` on `input`.
  - Tokeniser: `parseSearchQuery(raw)` (character-driven parser):
    - Accepts free-text; respects quoted strings (single or double quotes) and supports escape `\\` inside quotes.
    - Supports an optional leading `+` or `-` prefix before a term.
    - DOES NOT treat `field:value` specially — colons are treated as part of terms.
    - If a token has `+` or `-` prefix, the term MUST be quoted; otherwise `parseSearchQuery` returns `null` to indicate an invalid query.
    - Returns an array of tokens each shaped `{ field: '', term: <lowercased>, include: <bool>, exclude: <bool> }` (note: `field` is always empty string under current code).
  - Invalid query handling: `getFilteredItems()` treats `tokens === null` as an invalid query and returns an empty result set (no client-side results shown).

- Client token matching (`getFilteredItems()`):
  - Items source: `_allCreators` when in creators mode, or `_allGalleries` for galleries mode.
  - For a non-empty (valid) tokens array, the code:
    - Builds `includeTokens` (tokens without `exclude`) and `excludeTokens` (tokens with `exclude`). All tokens are lowercased.
    - Builds `creatorHay` from `searchableParts` which includes the label/name and aggregated fields (languages, parodies, tags, status) joined and lowercased.
    - Builds `galleryTitles` from `item.gallery_titles` (lowercased) when present.
    - Excludes: if any `excludeToken` appears in `creatorHay` or in any `galleryTitle`, the item is rejected.
    - Include logic:
      - If there are no `includeTokens`, the item passes (subject only to excludes).
      - `creatorNameMatch`: true if `creatorHay` contains ALL `includeTokens`.
      - `galleryMatch`: true if AT LEAST ONE gallery title contains ALL `includeTokens`.
      - The item is accepted if `creatorNameMatch || galleryMatch`.
    - Observed implication: this logic implements "creator matches if its own aggregated fields match all include terms OR one of its gallery titles matches all include terms". For gallery-mode items (which usually lack `gallery_titles`) the include-match effectively reduces to matching aggregated fields (label, tags, parodies, languages, status).

- Pages filtering (client):
  - The previous pages-in-token parsing was removed.
  - UI now includes explicit numeric inputs for `Pages min` and `Pages max` (added to the filter bar) bound to `_activeFilters.min_pages` and `_activeFilters.max_pages`.
  - Filtering logic:
    - For `galleries` mode: compares `item.page_count` against min/max and rejects if outside the range.
    - For `creators` mode: compares the creator's `min_page_count`/`max_page_count` and rejects creators whose entire gallery range lies outside the requested min/max (i.e., rejects if creator's max < min filter or creator's min > max filter).

- Parody handling / legacy numeric IDs:
  - Server: new endpoint `GET /api/gallery/parody_map` (implemented at `gallery_bp.route('/parody_map')`) returns JSON `{ "parody_map": { id_str: name, ... } }` by selecting `id, name` from the `Parodies` table.
  - Client: lazy-loads the parody map into `_parodyMap` on demand via `loadParodyMap()`.
  - `resolveParodyList(arr)` maps numeric-only parody entries (strings that match `^\d+$`) to names using `_parodyMap`. If numeric entries are present and the map isn't loaded it triggers a background load and will re-render details once loaded.
  - When rendering gallery details the code uses `resolveParodyList(normaliseList(gallery.parodies))` so numeric parody IDs (if present) are displayed as names once the mapping is loaded.

- Other notable code facts:
  - `_parse_prefixed_query` remains in `scraper_routes.py` but is no longer used by `search_galleries` (key:value parsing removed from server-side main flow).
  - Server enforces the quoted `+`/`-` rule via `_validate_prefixed_tokens`. Client enforces the same rule by returning `null` from `parseSearchQuery` on unquoted prefixed tokens — both sides return/produce empty results for invalid queries.
  - Tokens that include a colon (`:`) are NOT interpreted as `field:value` anymore on the client.
  - `getFilteredItems()` also applies active select filters for `language`, `tag`, and `parody` before returning results (these checks compare normalized equality: item must include the selected value).

- Summary of user-visible behaviours to test:
  1. Unquoted `+term` or `-term` in search input => client shows no results; server search (if called directly) returns empty results.
  2. Quoted `+"phrase"` or `-"phrase"` => works as include/exclude respectively.
  3. Multiple space-separated tokens (quoted or bare) => include tokens are ANDed; excludes remove items containing the term in either creator fields or any gallery title.
  4. `field:value` typed into search input is treated as a plain token (colon included in the term) — no special field parsing.
  5. Pages filtering only via the numeric `Pages min` / `Pages max` filter inputs in the filter bar (not via tokens).
  6. Parodies displayed in gallery details will resolve numeric ids to names after `/api/gallery/parody_map` is loaded; may require the mapping to be fetched in background.
  7. `query_type` payload values such as `cache_key`, `id_range`, `ids` are still respected by the server and perform special flows; otherwise server delegates to `scraperapi.Fetch.gallery_ids`.

---

Test checklist (quick verifications)

- [ ] Unquoted prefixed tokens: type `+term` or `-term` (no quotes) into the search input — client should show no results. Also POST to `/api/scraper/search` with `{"query_type":"search","query_value":"+term"}` and confirm server returns empty ids/results.
- [ ] Quoted prefixed tokens: use `+"phrase"` and `-"phrase"` in search input — verify includes and excludes behave as expected.
- [ ] Multiple tokens ANDing: enter multiple space-separated terms (e.g. `foo bar` or `"multi word" baz`) and confirm include tokens are ANDed; test that a creator matches if its name/fields match ALL include tokens OR at least one owned gallery title matches ALL include tokens.
- [ ] Field:value literal: type `tag:romance` into the search input and confirm it is treated as a plain token (colon included) rather than parsed as a tag filter.
- [ ] Pages filters: use the `Pages min` / `Pages max` inputs in the filter bar and confirm galleries and creators are filtered as described (galleries by `page_count`; creators by `min_page_count`/`max_page_count` intersection).
- [ ] Parody numeric IDs: open a gallery whose metadata contains numeric parody IDs and confirm the gallery detail shows names (may require the client to fetch `/api/gallery/parody_map` — open detail and wait briefly or re-open to confirm mapping applied).
- [ ] Server special query types: POST sample payloads to `/api/scraper/search` for `cache_key`, `id_range`, and `ids` flows and confirm the server returns expected ID lists.





# IMPROVEMENTS:

- Remove the "Pages min" and "Pages max" dropdowns

- Upon downloading, galleries are marked as "favourite". The value in the database is 0, and doesn't seem to change. This is incorrect.

- Details Page:
"
(297635) The Strange Creature and I
ID: 297635
Creators: yana, nekoarashi
Parodies: original
Tags: big breasts, pregnant, uncensored, unbirth, breast feeding, living clothes, anal, monster, exhibitionism, inflation, breast expansion, lactation, stockings, parasite, tentacles, eggs, sole female, hidden sex
Languages: english, translated
Pages: 48
Download Status: [1]
"
Download Status: [1] is incorrect. It should be the value of the "status" field from the Galleries table.