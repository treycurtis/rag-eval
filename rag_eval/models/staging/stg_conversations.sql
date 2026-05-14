
with source as (
    select * from {{source('querybot', 'conversations')}}
),

processed as (

    select
        id::integer as id,
        user_id::integer as user_id,
        conversation_uuid,
        sdk_session_id,
        title,
        is_active::boolean as is_active,
        message_count::integer as message_count,
        total_runs::integer as total_runs,
        total_turns::integer as total_turns,
        total_cost_usd::numeric(10,4) as total_cost_usd,
        total_duration_ms::integer as total_duration_ms,
        last_message_at::timestamp_ntz as last_message_at,
        last_run_at::timestamp_ntz as last_run_at,
        learning_extraction_status,
        learning_extracted_at::timestamp_ntz as learning_extracted_at,
        summary,
        created_at::timestamp_ntz as created_at,
        updated_at::timestamp_ntz as updated_at
    from source
)

select * from processed