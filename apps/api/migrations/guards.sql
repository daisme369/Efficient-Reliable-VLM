CREATE FUNCTION thorax_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_ARTIFACT';
END;
$$;

CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events
FOR EACH ROW EXECUTE FUNCTION thorax_immutable();
CREATE TRIGGER images_immutable BEFORE UPDATE OR DELETE ON images
FOR EACH ROW EXECUTE FUNCTION thorax_immutable();
CREATE TRIGGER history_immutable BEFORE UPDATE OR DELETE ON past_reports
FOR EACH ROW EXECUTE FUNCTION thorax_immutable();
CREATE TRIGGER patients_immutable BEFORE UPDATE OR DELETE ON patients
FOR EACH ROW EXECUTE FUNCTION thorax_immutable();
CREATE TRIGGER studies_immutable BEFORE UPDATE OR DELETE ON studies
FOR EACH ROW EXECUTE FUNCTION thorax_immutable();

CREATE FUNCTION thorax_session_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.status = 'final' OR
       (NEW.doctor_id, NEW.patient_id, NEW.study_id) IS DISTINCT FROM
       (OLD.doctor_id, OLD.patient_id, OLD.study_id) THEN
        RAISE EXCEPTION 'IMMUTABLE_SESSION';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER sessions_guard BEFORE UPDATE OR DELETE ON sessions
FOR EACH ROW EXECUTE FUNCTION thorax_session_guard();

CREATE FUNCTION thorax_clinical_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent sessions%ROWTYPE;
    asset images%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'CLINICAL_HARD_DELETE_FORBIDDEN';
    END IF;
    SELECT * INTO parent FROM sessions WHERE id = NEW.session_id FOR UPDATE;
    IF parent.status = 'final' THEN
        RAISE EXCEPTION 'FINAL_READ_ONLY';
    END IF;
    IF TG_TABLE_NAME = 'findings' THEN
        IF TG_OP = 'UPDATE' THEN
            IF OLD.status = 'approved' OR NEW.ai_items IS DISTINCT FROM OLD.ai_items OR
               (NEW.session_id, NEW.version) IS DISTINCT FROM (OLD.session_id, OLD.version) THEN
                RAISE EXCEPTION 'IMMUTABLE_APPROVED_FINDINGS';
            END IF;
        END IF;
        IF NEW.status = 'approved' AND NEW.approved_by != parent.doctor_id THEN
            RAISE EXCEPTION 'INVALID_APPROVER';
        END IF;
    ELSIF TG_TABLE_NAME = 'reports' THEN
        IF TG_OP = 'UPDATE' THEN
            IF OLD.status IN ('final', 'stale') OR
               (NEW.session_id, NEW.findings_version, NEW.ai_request_id) IS DISTINCT FROM
               (OLD.session_id, OLD.findings_version, OLD.ai_request_id) THEN
                RAISE EXCEPTION 'IMMUTABLE_REPORT_SOURCE';
            END IF;
        END IF;
        IF NEW.status = 'final' AND NEW.approved_by != parent.doctor_id THEN
            RAISE EXCEPTION 'INVALID_APPROVER';
        END IF;
    ELSIF TG_TABLE_NAME = 'messages' AND TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'IMMUTABLE_MESSAGE';
    ELSIF TG_TABLE_NAME = 'boxes' THEN
        SELECT * INTO asset FROM images WHERE id = NEW.image_id;
        IF NEW.created_by != parent.doctor_id OR NEW.x2 > asset.width OR NEW.y2 > asset.height OR
           NEW.x1 IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8) OR
           NEW.y1 IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8) OR
           NEW.x2 IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8) OR
           NEW.y2 IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8) THEN
            RAISE EXCEPTION 'INVALID_BOX';
        END IF;
        IF TG_OP = 'UPDATE' THEN
            IF (NEW.session_id, NEW.image_id, NEW.source, NEW.ai_original, NEW.ai_request_id, NEW.created_by)
               IS DISTINCT FROM
               (OLD.session_id, OLD.image_id, OLD.source, OLD.ai_original, OLD.ai_request_id, OLD.created_by) THEN
                RAISE EXCEPTION 'IMMUTABLE_BOX_SOURCE';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER findings_guard BEFORE INSERT OR UPDATE OR DELETE ON findings
FOR EACH ROW EXECUTE FUNCTION thorax_clinical_guard();
CREATE TRIGGER reports_guard BEFORE INSERT OR UPDATE OR DELETE ON reports
FOR EACH ROW EXECUTE FUNCTION thorax_clinical_guard();
CREATE TRIGGER messages_guard BEFORE INSERT OR UPDATE OR DELETE ON messages
FOR EACH ROW EXECUTE FUNCTION thorax_clinical_guard();
CREATE TRIGGER boxes_guard BEFORE INSERT OR UPDATE OR DELETE ON boxes
FOR EACH ROW EXECUTE FUNCTION thorax_clinical_guard();

CREATE FUNCTION thorax_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'CLINICAL_HARD_DELETE_FORBIDDEN';
    END IF;
    IF OLD.status IN ('succeeded','failed','timed_out','cancelled') OR
       (NEW.session_id, NEW.actor_id, NEW.operation, NEW.idempotency_key, NEW.request_hash,
        NEW.input_snapshot, NEW.input_revision, NEW.deadline_at, NEW.retry_of)
       IS DISTINCT FROM
       (OLD.session_id, OLD.actor_id, OLD.operation, OLD.idempotency_key, OLD.request_hash,
        OLD.input_snapshot, OLD.input_revision, OLD.deadline_at, OLD.retry_of) THEN
        RAISE EXCEPTION 'IMMUTABLE_AI_REQUEST';
    END IF;
    IF (OLD.status = 'queued' AND NEW.status NOT IN ('queued','running','timed_out','cancelled')) OR
       (OLD.status = 'running' AND NEW.status NOT IN ('running','succeeded','failed','timed_out','cancelled')) THEN
        RAISE EXCEPTION 'INVALID_JOB_TRANSITION';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER ai_requests_guard BEFORE UPDATE OR DELETE ON ai_requests
FOR EACH ROW EXECUTE FUNCTION thorax_job_guard();

CREATE FUNCTION thorax_assignment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT role FROM users WHERE id = NEW.doctor_id) IS DISTINCT FROM 'doctor' THEN
        RAISE EXCEPTION 'ASSIGNMENT_REQUIRES_DOCTOR';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER assignment_guard BEFORE INSERT OR UPDATE ON doctor_patients
FOR EACH ROW EXECUTE FUNCTION thorax_assignment_guard();

CREATE FUNCTION thorax_study_image_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM images WHERE study_id = NEW.id) THEN
        RAISE EXCEPTION 'STUDY_IMAGE_REQUIRED';
    END IF;
    RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER study_has_image AFTER INSERT ON studies
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION thorax_study_image_guard();
