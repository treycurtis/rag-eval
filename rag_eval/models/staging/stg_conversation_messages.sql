with source as (
    select * from {{ source('querybot', 'conversation_messages') }}
),

processed as (

    select
        id::integer as id,
        conversation_id::integer as conversation_id,
        message_type,
        content,
        message_metadata,
        tool_use_id,
        is_error::boolean as is_error,
        sequence_number::integer as sequence_number,
        created_at::timestamp_ntz as created_at
    from source
)

select * from processed
