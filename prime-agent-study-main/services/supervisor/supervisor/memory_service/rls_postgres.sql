-- Postgres RLS for Memory Service (Phase 5).
-- Apply after schema create. Application MUST SET app.tenant on each connection:
--   SELECT set_config('app.tenant', :tenant, true);

ALTER TABLE memory_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE forget_audit ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS memory_session_tenant ON memory_session;
CREATE POLICY memory_session_tenant ON memory_session
  USING (tenant = current_setting('app.tenant', true));

DROP POLICY IF EXISTS memory_item_tenant ON memory_item;
CREATE POLICY memory_item_tenant ON memory_item
  USING (tenant = current_setting('app.tenant', true));

DROP POLICY IF EXISTS forget_audit_tenant ON forget_audit;
CREATE POLICY forget_audit_tenant ON forget_audit
  USING (tenant = current_setting('app.tenant', true));

-- Force RLS for table owners too
ALTER TABLE memory_session FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_item FORCE ROW LEVEL SECURITY;
ALTER TABLE forget_audit FORCE ROW LEVEL SECURITY;
