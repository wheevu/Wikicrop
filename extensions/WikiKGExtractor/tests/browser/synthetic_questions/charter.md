# Connected synthetic questions browser charter

Role and start state: a signed-out visitor opens the synthetic question page, then a synthetic reviewer signs in using a private fixture password.

Journey: confirm the visitor receives no claim evidence; authenticate; submit the server-catalogued supported compound example using the labelled form; inspect visible result status, generation, matched subject, branch, claim snapshots, and source-revision links; submit from keyboard at a narrow viewport and with JavaScript disabled.

Expected evidence: signed-out page contains no claim rows; authenticated response has the contract's result/status and generation attributes; claim rows identify exact snapshot IDs and each source link is local; keyboard and no-JavaScript submission produce the same server-rendered journey.

Data and constraints: disposable synthetic root only; fixture identities and password come from owner-private files; browser requests remain on loopback; no mocked application responses or fetch calls.

Stop point: stop after the supported response and keyboard/no-JavaScript checks. Rejection and graph-outage state transitions are covered by the verifier's later scenarios, not induced by browser setup.
