with source as (
    select * from {{ source('querybot', 'conversation_runs') }}
),

processed as (

    select
        id::integer as id,
        conversation_id::integer as conversation_id,
        user_message_id::integer as user_message_id,
        num_turns::integer as num_turns,
        duration_ms::integer as duration_ms,
        cost_usd::numeric(10,4) as cost_usd,
        sdk_session_id,
        started_at::timestamp_ntz as started_at,
        completed_at::timestamp_ntz as completed_at
    from source
)

select * from processed
