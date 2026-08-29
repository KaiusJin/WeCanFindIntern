BEGIN;

-- Expand the long-term memory type whitelist so extraction can place facts
-- into richer categories (skills, education, work experience, plans) instead
-- of only four generic types.

ALTER TABLE agent_memories
    DROP CONSTRAINT IF EXISTS agent_memories_memory_type_check;

ALTER TABLE agent_memories
    ADD CONSTRAINT agent_memories_memory_type_check
        CHECK (memory_type IN (
            'USER_PREFERENCE',
            'CAREER_CONTEXT',
            'JOB_TARGET',
            'EXPLICIT_FACT',
            'SKILL_PROFILE',
            'EDUCATION_PROFILE',
            'WORK_EXPERIENCE',
            'APPLICATION_PLAN'
        ));

COMMIT;
