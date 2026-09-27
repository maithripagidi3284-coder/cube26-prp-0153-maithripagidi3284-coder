-- Production tenant isolation (spec section 32).
--
-- The dev/demo setup (SQLite, see app/config.py) enforces org scoping only
-- in the application layer (every query filtered by organization_id — see
-- the _scoped() helper in app/main.py). That is NOT sufficient for
-- production: a single missed filter anywhere in the codebase would leak
-- data across tenants. Postgres row-level security makes tenant isolation
-- unconditional at the database layer instead.
--
-- Apply this after running the SQLAlchemy migrations that create the
-- tables in app/models.py. Run as a superuser / table owner.

-- 1. Every tenant-scoped table needs RLS turned on and forced (FORCE means
--    even the table owner is subject to the policy, not just other roles).
ALTER TABLE organizations       ENABLE ROW LEVEL SECURITY;
ALTER TABLE users                ENABLE ROW LEVEL SECURITY;
ALTER TABLE products             ENABLE ROW LEVEL SECURITY;
ALTER TABLE work_orders          ENABLE ROW LEVEL SECURITY;
ALTER TABLE units                ENABLE ROW LEVEL SECURITY;
ALTER TABLE images               ENABLE ROW LEVEL SECURITY;
ALTER TABLE visual_evidence      ENABLE ROW LEVEL SECURITY;
ALTER TABLE compliance_results   ENABLE ROW LEVEL SECURITY;
ALTER TABLE reviews              ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events         ENABLE ROW LEVEL SECURITY;

ALTER TABLE products             FORCE ROW LEVEL SECURITY;
ALTER TABLE work_orders          FORCE ROW LEVEL SECURITY;
ALTER TABLE units                FORCE ROW LEVEL SECURITY;
ALTER TABLE images               FORCE ROW LEVEL SECURITY;
ALTER TABLE visual_evidence      FORCE ROW LEVEL SECURITY;
ALTER TABLE compliance_results   FORCE ROW LEVEL SECURITY;
ALTER TABLE reviews              FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_events         FORCE ROW LEVEL SECURITY;

-- 2. The application connects as a role that has app.current_org_id set
--    per-request (e.g. via `SET LOCAL app.current_org_id = '<org uuid>'`
--    inside the request's transaction, driven by the authenticated
--    request's org claim — never trust a client-supplied header directly
--    in production; resolve it from a verified session/JWT server-side).
CREATE OR REPLACE FUNCTION current_org_id() RETURNS text AS $$
  SELECT current_setting('app.current_org_id', true);
$$ LANGUAGE sql STABLE;

-- 3. One policy per table: rows are visible/writable only when they match
--    the session's org id.
CREATE POLICY org_isolation ON products
  USING (organization_id = current_org_id());
CREATE POLICY org_isolation ON work_orders
  USING (organization_id = current_org_id());
CREATE POLICY org_isolation ON units
  USING (organization_id = current_org_id());
CREATE POLICY org_isolation ON images
  USING (organization_id = current_org_id());
CREATE POLICY org_isolation ON visual_evidence
  USING (organization_id = current_org_id());
CREATE POLICY org_isolation ON compliance_results
  USING (organization_id = current_org_id());
CREATE POLICY org_isolation ON reviews
  USING (organization_id = current_org_id());
CREATE POLICY org_isolation ON audit_events
  USING (organization_id = current_org_id());

-- 4. Image object storage must use signed, time-limited URLs (spec section
--    32) rather than predictable paths — RLS protects the database rows,
--    not files sitting in a bucket. If using S3-compatible storage, use
--    presigned GET URLs with a short expiry (e.g. 5 minutes) generated at
--    request time from the already-tenant-checked Image row, never a
--    long-lived or public bucket URL.

-- 5. Test plan for this policy (spec section 32):
--    - As org A's role/session, attempt to SELECT a unit/image/result
--      belonging to org B by primary key -> must return 0 rows, not an
--      error (RLS silently filters, which is correct — errors would leak
--      existence).
--    - As org A, attempt to UPDATE/DELETE a row owned by org B -> must
--      affect 0 rows.
--    - Attempt to fetch an org B image via a guessed/enumerated storage
--      path -> must fail (this is why step 4 matters independently of RLS).
