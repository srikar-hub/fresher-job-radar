DROP INDEX jobs_normalized_url_uq;
CREATE INDEX jobs_normalized_url_idx ON jobs(normalized_application_url, company, role);
