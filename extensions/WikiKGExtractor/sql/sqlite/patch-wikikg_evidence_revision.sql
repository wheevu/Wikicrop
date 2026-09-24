CREATE INDEX wikikg_evidence_revision ON /*_*/wikikg_evidence (
  wikikg_snapshot_id, wikikg_page_id,
  wikikg_revision_id
);
