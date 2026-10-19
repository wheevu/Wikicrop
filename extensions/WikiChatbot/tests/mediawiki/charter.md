# Synthetic chatbot browser charter

- Role/start: anonymous reader on a fresh synthetic SQLite wiki, with WikiChatbot enabled; each scenario uses a fresh Playwright page/context.
- Journey: load the real PHP page, await automatic ResourceLoader readiness, open the actual chat, submit invented queries to the real loopback HTTP stub, and observe rendered answers or refusal/recovery.
- Oracle: literal HTML-shaped content remains text; HTTP 503 and malformed JSON show request failure, while invalid/empty/missing answers show invalid-answer copy.
  All allow a later valid answer.
  The real 30-second timeout shows timeout copy and aborts the actual request.
  After recovery and the server's late write attempt, no late answer appears or replaces an existing reply.
  Two overlapping messages receive replies in reverse order, with each reply beside its own message.
- Data/constraints: bounded hardcoded invented query names, loopback origin/path/body checks and bounded private receipts.
  Two fixed fixture-control actions wait for the late write attempt or release the alpha response after beta is visible.
  They cannot choose responses, files, URLs or application operations.
  The real timeout is not shortened.
  Every scenario denies page requests outside the synthetic wiki and loopback stub.
  No replacement DOM/mw/fetch, application script injection, fulfilled chatbot requests, real backend or user data.
- Stop: all success, refusal/recovery, timeout/late-response, and concurrent-position assertions complete; screenshot retained privately. This proves browser integration wiring, not backend quality.
